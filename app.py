# =============================================================================
# Daily nutrient intake log (local version)
# Log what you eat from TKPI (tkpi.csv, the same file as the TKPI lookup app)
# and compare daily totals with AKG 2019 (Permenkes No. 28 Tahun 2019).
#
# Files it keeps next to this script
#   intake_log.csv         everyone's log (each entry carries its user)
#   intake_users.json      users, their AKG group and (optional) hashed password
#   backups/               one dated copy of the log per day (the newest 14 are kept)
#
# Forgotten password: in intake_users.json, empty that user's "salt" and "hash"
# values ("salt": "", "hash": ""); they can then sign in without one.
#
# Run:  streamlit run intake_app.py
# =============================================================================

import csv
import hashlib
import hmac
import html
import io
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Daily nutrient intake", page_icon="🍽️", layout="centered")

if hasattr(st, "cache_data"):
    cache_data = st.cache_data
else:                                   # Streamlit older than 1.18
    cache_data = st.cache

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "tkpi.csv")
LOG_FILE = os.path.join(HERE, "intake_log.csv")
SETTINGS_FILE = os.path.join(HERE, "intake_settings.json")   # AKG choice from before users existed
USERS_FILE = os.path.join(HERE, "intake_users.json")
ALIASES_FILE = os.path.join(HERE, "intake_aliases.json")      # words you used -> the TKPI food you confirmed
BACKUP_DIR = os.path.join(HERE, "backups")
BACKUPS_KEPT = 14
AKG_DEFAULT = "Male, 30–49 years"       # used until you pick your AKG group in the sidebar

MEALS = ["Breakfast", "Lunch", "Dinner", "Snack"]
META_COLS = ["ID", "User", "Date", "Meal", "Code", "Food", "Edible (g)", "As bought (g)"]
NUMERIC_META = ["Edible (g)", "As bought (g)"]

# AKG 2019, Permenkes No. 28 Tahun 2019, Lampiran I (adults, per person per day)
AKG_ROLES = ["energy", "protein", "fat", "carb", "fiber", "calcium", "phosphorus", "iron", "zinc",
             "potassium", "sodium", "copper", "thiamin", "riboflavin", "niacin", "vitc"]
AKG_UNITS = ["kcal", "g", "g", "g", "g", "mg", "mg", "mg", "mg",
             "mg", "mg", "mcg", "mg", "mg", "mg", "mg"]
AKG_TABLE = {
    "Male, 19–29 years":   [2650, 65, 75, 430, 37, 1000, 700, 9, 11, 4700, 1500, 900, 1.2, 1.3, 16, 90],
    "Male, 30–49 years":   [2550, 65, 70, 415, 36, 1000, 700, 9, 11, 4700, 1500, 900, 1.2, 1.3, 16, 90],
    "Male, 50–64 years":   [2150, 65, 60, 340, 30, 1200, 700, 9, 11, 4700, 1300, 900, 1.2, 1.3, 16, 90],
    "Male, 65–80 years":   [1800, 64, 50, 275, 25, 1200, 700, 9, 11, 4700, 1100, 900, 1.2, 1.3, 16, 90],
    "Male, 80+ years":     [1600, 64, 45, 235, 22, 1200, 700, 9, 11, 4700, 1000, 900, 1.2, 1.3, 16, 90],
    "Female, 19–29 years": [2250, 60, 65, 360, 32, 1000, 700, 18, 8, 4700, 1500, 900, 1.1, 1.1, 14, 75],
    "Female, 30–49 years": [2150, 60, 60, 340, 30, 1000, 700, 18, 8, 4700, 1500, 900, 1.1, 1.1, 14, 75],
    "Female, 50–64 years": [1800, 60, 50, 280, 25, 1200, 700, 8, 8, 4700, 1400, 900, 1.1, 1.1, 14, 75],
    "Female, 65–80 years": [1550, 58, 45, 230, 22, 1200, 700, 8, 8, 4700, 1200, 900, 1.1, 1.1, 14, 75],
    "Female, 80+ years":   [1400, 58, 40, 200, 20, 1200, 700, 8, 8, 4700, 1000, 900, 1.1, 1.1, 14, 75],
}

# How TKPI columns are recognised (matched against lower-case column names)
ROLE_PATTERNS = [
    ("energy", r"energi|energy|kkal|kcal|\bkal\b"),
    ("protein", r"protein"),
    ("fat", r"lemak|\bfat\b"),
    ("carb", r"karbo|carb|\bkh\b|\bcho\b"),
    ("fiber", r"serat|fib"),
    ("calcium", r"kalsium|calcium"),
    ("phosphorus", r"fosfor|phosph"),
    ("iron", r"besi|\biron\b"),
    ("sodium", r"natrium|sodium"),
    ("potassium", r"kalium|potass"),
    ("copper", r"tembaga|copper"),
    ("zinc", r"seng|zinc"),
    ("thiamin", r"thiamin|tiamin|\bb1\b"),
    ("riboflavin", r"ribofla|\bb2\b"),
    ("niacin", r"niasin|niacin|\bb3\b"),
    ("vitc", r"vit\.?\s*c\b|vitamin\s*c\b|ascorb|askorb"),
]
ROLE_LABELS = {"energy": "Energy", "protein": "Protein", "fat": "Fat", "carb": "Carbohydrate",
               "fiber": "Fiber", "calcium": "Calcium", "phosphorus": "Phosphorus", "iron": "Iron",
               "sodium": "Sodium", "potassium": "Potassium", "copper": "Copper", "zinc": "Zinc",
               "thiamin": "Thiamin (B1)", "riboflavin": "Riboflavin (B2)", "niacin": "Niacin (B3)",
               "vitc": "Vitamin C"}
OTHER_LABELS = {"air": "Water", "abu": "Ash", "retinol": "Retinol", "b-kar": "Beta-carotene",
                "b-karoten": "Beta-carotene", "β-karoten": "Beta-carotene", "beta-karoten": "Beta-carotene",
                "kar-total": "Total carotene", "karoten total": "Total carotene"}
HIDDEN_PAT = re.compile(r"^(abu|ash)\b|retinol|kar[o\-\s]|carot")   # ash, retinol, beta- and total carotene
NUTRIENT_GROUPS = [("macro", "Energy and macronutrients"), ("mineral", "Minerals"), ("vitamin", "Vitamins")]
MACRO_ROLES = ["energy", "protein", "fat", "carb", "fiber"]
MINERAL_ROLES = ["calcium", "phosphorus", "iron", "sodium", "potassium", "copper", "zinc"]
VITAMIN_ROLES = ["thiamin", "riboflavin", "niacin", "vitc"]
CUSTOM_ROLES = ["energy", "protein", "fat", "carb", "fiber", "sodium"]
# UI palette: mint, sky and blue, plus a violet that continues the same sequence
MINT = "#37E6B4"
SKY = "#37C0E6"
BLUE = "#3769E6"
VIOLET = "#7A37E6"
PINK = "#E93580"                        # marks anything over target
COLORS = {"protein": BLUE, "fat": SKY, "carb": MINT, "fiber": VIOLET}   # energy split

# Bars towards AKG share one colour per status, for every nutrient. Cut-offs follow the usual
# Indonesian tingkat kecukupan categories: normal 90-119.9% of AKG, lebih from 120%.
UNDER_PCT = 90.0
OVER_PCT = 120.0
STATUS_COLORS = {"under": SKY, "met": MINT, "over": PINK}

UNIT_ALIASES = {"kal": "kcal", "kkal": "kcal", "kcal": "kcal", "kj": "kj", "g": "g", "gr": "g",
                "mg": "mg", "mcg": "mcg", "µg": "mcg", "μg": "mcg", "ug": "mcg"}
MASS_IN_MG = {"g": 1000.0, "mg": 1.0, "mcg": 0.001}

GROUP_NAMES = {"A": "Serealia", "B": "Umbi berpati", "C": "Kacang/biji/bean", "D": "Sayuran",
               "E": "Buah", "F": "Daging/unggas", "G": "Ikan/kerang/udang", "H": "Telur", "J": "Susu",
               "K": "Lemak/minyak", "M": "Gula/sirup/konfeksioneri", "N": "Bumbu"}
RAW_TYPE = "Raw or fresh (mentah/segar)"
PROCESSED_TYPE = "Processed (olahan)"
TYPE_ORDER = [RAW_TYPE, PROCESSED_TYPE]
RAW_WORDS = re.compile(r"\b(mentah|segar|fresh)\b", re.IGNORECASE)   # any of these in the name = raw or fresh

CODE_PAT = re.compile(r"^kode|^code")
NAME_PAT = re.compile(r"^nama|^name|^food")
GROUP_PAT = re.compile(r"^kelompok|^golongan|^group|^food group")
TYPE_PAT = re.compile(r"^tipe|^type|^jenis")
BDD_PAT = re.compile(r"bdd|edible")
SKIP_PAT = re.compile(r"^no\.?$|^nomor|^id$|^unnamed|sumber|source|keterangan|^note|^catatan")

ENERGY_NOTE = ("Protein and available carbohydrate count 4 kcal per gram, fat 9 and fiber 2. "
               "Available carbohydrate is TKPI carbohydrate minus fiber, because TKPI carbohydrate "
               "is calculated by difference and includes fiber. The shares can differ slightly from "
               "the energy values TKPI lists for each food.")

CSS = """
<style>
.nl-grid {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.5rem; margin: 0.25rem 0 1rem;}
.nl-cell {border: 1px solid rgba(55, 105, 230, 0.28); border-radius: 0.5rem; padding: 0.45rem 0.55rem;}
.nl-label {font-size: 0.78rem; line-height: 1.2; opacity: 0.8;}
.nl-value {font-size: 1.3rem; font-weight: 700; line-height: 1.3; font-variant-numeric: tabular-nums;}
.nl-track {height: 6px; border-radius: 3px; background: rgba(55, 192, 230, 0.16); overflow: hidden; margin-top: 0.3rem;}
.nl-fill {height: 100%;}
.nl-pct {font-size: 0.74rem; opacity: 0.8; margin-top: 0.15rem; font-variant-numeric: tabular-nums;}
.nl-group {grid-column: 1 / -1; font-weight: 600; font-size: 0.92rem; margin: 0.45rem 0 -0.15rem;}
.nl-group:first-child {margin-top: 0;}
.nl-caption {font-weight: 600; margin: 0.25rem 0 0.35rem;}
.nl-split {display: flex; height: 16px; border-radius: 4px; overflow: hidden; background: rgba(55, 192, 230, 0.16);}
.nl-split span {display: block; height: 100%;}
.nl-legend {display: flex; flex-wrap: wrap; gap: 0.2rem 1rem; font-size: 0.85rem; margin: 0.45rem 0 1rem;}
.nl-swatch {display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 2px; margin-right: 0.35rem; vertical-align: -0.05rem;}
.nl-table {width: 100%; border-collapse: collapse; font-size: 0.9rem; margin: 0 0 1rem; border: none !important;}
.nl-table tr {border: none !important;}
.nl-table th, .nl-table td {padding: 0.35rem 0.5rem; border: none !important; border-bottom: 1px solid rgba(55, 105, 230, 0.12) !important; text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;}
.nl-table th:first-child, .nl-table td:first-child {text-align: left; white-space: normal;}
.nl-table th {font-size: 0.8rem; font-weight: 600; color: #3769E6; background: #EEF7FC; border-bottom: 1px solid rgba(55, 105, 230, 0.35) !important;}
.nl-table tr.nl-meal td {font-weight: 700; background: #F5FBFE; padding-top: 0.55rem;}
.nl-note {display: block; font-size: 0.75rem; opacity: 0.75;}
.nl-scroll {overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 0 0.4rem; border: 1px solid rgba(55, 105, 230, 0.2); border-radius: 0.4rem;}
.nl-table.nl-wide {width: auto; min-width: 100%; margin: 0;}
.nl-wide th {vertical-align: bottom; line-height: 1.2; white-space: nowrap;}
.nl-unit {display: block; font-weight: 400; font-size: 0.72rem; opacity: 0.8;}
.nl-sticky {position: sticky; left: 0; z-index: 1; background: #FFFFFF; min-width: 8.5rem; max-width: 12rem; box-shadow: 1px 0 0 rgba(55, 105, 230, 0.25);}
.nl-table thead th.nl-sticky {background: #EEF7FC;}
.nl-meal-title {font-weight: 700; margin: 0.9rem 0 0.35rem;}
.nl-item {font-weight: 600; margin: 0.7rem 0 0.1rem;}
.nl-item span {display: block; font-weight: 400; font-size: 0.8rem; opacity: 0.75;}
.nl-table th.nl-food {white-space: normal; min-width: 4.8rem; max-width: 8.5rem; text-align: right; vertical-align: bottom; line-height: 1.25;}
.nl-table tr.nl-grouprow td {background: #F5FBFE; font-weight: 600; color: #3769E6; padding-top: 0.5rem;}
.nl-table td.nl-totalcol, .nl-table th.nl-totalcol {font-weight: 700; background: #F5FBFE;}
.nl-table td.nl-over {background: #FBD3E3 !important; color: #B3185C; font-weight: 700;}
.nl-table tr.nl-total td {font-weight: 700; background: #E8F6FB; border-top: 2px solid rgba(55, 105, 230, 0.45) !important; border-bottom: none !important;}
@media print {
.nl-scroll {overflow: visible !important; border: none !important;}
.nl-sticky {position: static !important; box-shadow: none !important;}
.nl-table {font-size: 7.5pt !important; -webkit-print-color-adjust: exact; print-color-adjust: exact;}
.nl-table th, .nl-table td {padding: 0.15rem 0.3rem !important;}
}
.nl-foot {font-size: 0.78rem; opacity: 0.8; margin: -0.5rem 0 1rem;}
.nl-days {margin: 0.25rem 0 0.75rem;}
.nl-day {display: grid; grid-template-columns: 4.8rem minmax(0, 1fr) 7.8rem; gap: 0.5rem; align-items: center; font-size: 0.86rem; padding: 0.22rem 0;}
.nl-bar {position: relative; height: 10px; border-radius: 5px; background: rgba(55, 192, 230, 0.16);}
.nl-bar-fill {position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px;}
.nl-mark {position: absolute; top: -3px; bottom: -3px; width: 2px; background: currentColor; opacity: 0.55;}
.nl-num {text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;}
@media (max-width: 380px) {
.nl-day {grid-template-columns: 4.4rem minmax(0, 1fr) 7rem; font-size: 0.8rem;}
}
h1 {color: #3769E6 !important;}
.nl-notice {border-left: 4px solid; border-radius: 0.4rem; padding: 0.6rem 0.8rem; margin: 0.25rem 0 0.9rem; line-height: 1.45;}
.nl-notice-info {background: rgba(55, 192, 230, 0.12); border-color: #37C0E6;}
.nl-notice-ok {background: rgba(55, 230, 180, 0.15); border-color: #37E6B4;}
.nl-notice-error {background: rgba(233, 53, 128, 0.10); border-color: #E93580;}
button[kind="primary"], button[data-testid="stBaseButton-primary"] {background-color: #3769E6 !important; border-color: #3769E6 !important; color: #FFFFFF !important;}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {background-color: #2C57C4 !important; border-color: #2C57C4 !important;}
@media (max-width: 330px) {
.nl-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
}
</style>
"""


# ----------------------------------------------------------------------------- small helpers

def is_num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(number)


def to_number(value):
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if text in ("", "-", "–", "—") or text.lower() in ("nan", "none", "null", "na", "n/a", "<na>"):
        return float("nan")
    if text.lower() == "tr":                       # trace
        return 0.0
    try:
        return float(text)
    except ValueError:
        return float("nan")


def number_or_zero(value):
    if is_num(value):
        return float(value)
    return 0.0


def fmt(value, unit=""):
    if not is_num(value):
        return "–"
    number = float(value)
    if unit == "kcal" or abs(number) >= 100:
        return f"{number:,.0f}"
    if abs(number) >= 10:
        text = f"{number:.1f}"
    else:
        text = f"{number:.2f}"
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def fmt_grams(value):
    if not is_num(value):
        return "–"
    number = float(value)
    if abs(number - round(number)) < 0.05:
        return f"{number:,.0f}"
    return f"{number:,.1f}"


def fmt_pct(value):
    if not is_num(value):
        return "–"
    return f"{float(value):.0f}%"


def status_color(pct):
    if not is_num(pct) or pct < UNDER_PCT:
        return STATUS_COLORS["under"]
    if pct < OVER_PCT:
        return STATUS_COLORS["met"]
    return STATUS_COLORS["over"]


def status_legend_html():
    items = [("under", f"Below {UNDER_PCT:.0f}% of AKG"),
             ("met", f"{UNDER_PCT:.0f}–{OVER_PCT - 1:.0f}%"),
             ("over", f"{OVER_PCT:.0f}% and above")]
    parts = ""
    for key, text in items:
        parts += ('<span><span class="nl-swatch" style="background:' + STATUS_COLORS[key] + '"></span>'
                  + esc(text) + "</span>")
    return '<div class="nl-legend" style="margin-top:-0.4rem">' + parts + "</div>"


def unit_text(unit):
    if unit == "kcal":
        return "Kcal"
    if unit == "kj":
        return "kJ"
    return unit


def esc(text):
    return html.escape(str(text))


def row_has_text(row):
    for cell in row:
        if str(cell).strip() != "":
            return True
    return False


def pad(row, width):
    cells = list(row[:width])
    while len(cells) < width:
        cells.append("")
    return cells


def new_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + os.urandom(3).hex()


def now_local():
    """Now in the viewer's time zone (browser) when Streamlit knows it, else the machine's."""
    tz_name = None
    try:
        tz_name = st.context.timezone
    except Exception:
        tz_name = None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now()


def meal_index(now):
    minutes = now.hour * 60 + now.minute
    if minutes < 4 * 60:
        return 3                                   # Snack
    if minutes < 10 * 60 + 30:
        return 0                                   # Breakfast
    if minutes < 15 * 60:
        return 1                                   # Lunch
    if minutes < 17 * 60 + 30:
        return 3                                   # Snack
    return 2                                       # Dinner


def long_day(day):
    return day.strftime("%A") + ", " + str(day.day) + " " + day.strftime("%B %Y")


def short_day(day):
    return day.strftime("%a") + " " + str(day.day) + " " + day.strftime("%b")


def radio_row(label, options, key):
    try:
        return st.radio(label, options, key=key, horizontal=True)
    except TypeError:                              # Streamlit without horizontal radios
        return st.radio(label, options, key=key)


def make_tabs(names):
    if hasattr(st, "tabs"):
        return st.tabs(names)
    containers = []
    for name in names:
        st.subheader(name)
        containers.append(st.container())
    return containers


def notice(kind, text):
    """A message box in the app's palette: kind is "info", "ok" or "error"."""
    role = "alert" if kind == "error" else "status"
    st.markdown('<div class="nl-notice nl-notice-' + kind + '" role="' + role + '">' + esc(text) + "</div>",
                unsafe_allow_html=True)


def primary_button(label, **kwargs):
    try:
        return st.button(label, type="primary", **kwargs)
    except TypeError:                              # Streamlit without primary buttons
        return st.button(label, **kwargs)


def set_flash(area, kind, text):
    st.session_state["flash_" + area] = (kind, text)


def show_flash(area):
    key = "flash_" + area
    if key in st.session_state:
        kind, text = st.session_state[key]
        del st.session_state[key]
        if kind == "error":
            notice("error", text)
        else:
            notice("ok", text)


# ----------------------------------------------------------------------------- TKPI table

def read_text(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ["utf-8-sig", "cp1252"]:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def sniff_sep(text):
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(line)
        if len(lines) >= 40:
            break
    best_sep = ","
    best_score = 0
    for sep in [",", ";", "\t"]:
        counts = {}
        for row in csv.reader(lines, delimiter=sep):
            counts[len(row)] = counts.get(len(row), 0) + 1
        for width in counts:
            score = width * counts[width] if width > 1 else 0
            if score > best_score:
                best_score = score
                best_sep = sep
    return best_sep


def find_header(rows):
    for r in range(min(len(rows), 20)):
        has_code = False
        has_name = False
        for cell in rows[r]:
            low = str(cell).strip().lower()
            if CODE_PAT.search(low):
                has_code = True
            if NAME_PAT.search(low):
                has_name = True
        if has_code and has_name:
            return r
    return 0


def find_col(header, pattern, taken):
    for j in range(len(header)):
        if j not in taken and pattern.search(header[j].lower()):
            taken.append(j)
            return j
    return -1


def unit_of(col):
    found = re.findall(r"\(([^)]*)\)", col)
    for k in range(len(found) - 1, -1, -1):
        key = found[k].strip().lower()
        if key in UNIT_ALIASES:
            return UNIT_ALIASES[key]
    return ""


def label_of(col, role):
    if role:
        return ROLE_LABELS[role]
    base = re.sub(r"\s*\([^)]*\)", "", col).strip()
    if base.lower() in OTHER_LABELS:
        return OTHER_LABELS[base.lower()]
    return base[:1].upper() + base[1:]


def code_letters(code):
    match = re.match(r"^ID([A-Z])", code)
    if match:
        return match.group(1), ""
    match = re.match(r"^([A-Z])([RP])?\d", code)
    if match:
        return match.group(1), match.group(2) or ""
    return "", ""


def group_label(value, code):
    value = str(value).strip()
    if len(value) == 1 and value.upper() in GROUP_NAMES:
        return value.upper() + " " + GROUP_NAMES[value.upper()]
    if value:
        return value
    letter = code_letters(code)[0]
    if letter in GROUP_NAMES:
        return letter + " " + GROUP_NAMES[letter]
    return "Other"


def type_label(name):
    """Raw or fresh when the name has the word mentah, segar or fresh; otherwise processed."""
    if RAW_WORDS.search(name):
        return RAW_TYPE
    return PROCESSED_TYPE


@cache_data(show_spinner=False)
def load_tkpi(path, mtime, rules=4):
    text = read_text(path)
    sep = sniff_sep(text)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=sep):
        if row_has_text(row):
            rows.append(row)
    if len(rows) < 2:
        raise ValueError("the file has no data rows")

    h = find_header(rows)
    header = []
    for cell in rows[h]:
        name = " ".join(str(cell).split())
        if name and name in header:
            name = name + " (" + str(len(header) + 1) + ")"
        header.append(name)
    body = []
    for row in rows[h + 1:]:
        body.append(pad(row, len(header)))

    taken = []
    code_i = find_col(header, CODE_PAT, taken)
    group_i = find_col(header, GROUP_PAT, taken)
    type_i = find_col(header, TYPE_PAT, taken)
    bdd_i = find_col(header, BDD_PAT, taken)
    name_i = find_col(header, NAME_PAT, taken)

    nutrient_idx = []
    for j in range(len(header)):
        if j in taken or header[j] == "" or SKIP_PAT.search(header[j].lower()):
            continue
        numeric = 0
        for cells in body:
            if is_num(to_number(cells[j])):
                numeric += 1
        if len(body) > 0 and numeric >= 0.2 * len(body):
            nutrient_idx.append(j)
    if len(nutrient_idx) == 0:
        raise ValueError("no nutrient columns were found; check that one row holds names such as Kode, Nama and Energi")

    nutrients = []
    for j in nutrient_idx:
        nutrients.append(header[j])

    records = []
    for cells in body:
        code = cells[code_i].strip().upper() if code_i >= 0 else ""
        name = cells[name_i].strip() if name_i >= 0 else ""
        if name == "" and code == "":
            continue
        record = {"code": code, "name": name if name else code}
        has_value = False
        for j in nutrient_idx:
            value = to_number(cells[j])
            if is_num(value):
                value = max(0.0, value)
                has_value = True
            record[header[j]] = value
        if not has_value:
            continue
        record["bdd"] = to_number(cells[bdd_i]) if bdd_i >= 0 else float("nan")
        record["group"] = group_label(cells[group_i] if group_i >= 0 else "", code)
        record["type"] = type_label(record["name"])
        record["label"] = record["name"] + " (" + code + ")" if code else record["name"]
        records.append(record)
    if len(records) == 0:
        raise ValueError("no foods with nutrient values were found")

    roles = {}
    role_of = {}
    units = {}
    labels = {}
    for col in nutrients:
        units[col] = unit_of(col)
        role_of[col] = None
        for role, pattern in ROLE_PATTERNS:
            if role not in roles and re.search(pattern, col.lower()):
                roles[role] = col
                role_of[col] = role
                break
        labels[col] = label_of(col, role_of[col])

    groups = []
    types = []
    for name in TYPE_ORDER:
        for record in records:
            if record["type"] == name:
                types.append(name)
                break
    for record in records:
        if record["group"] not in groups:
            groups.append(record["group"])
        if record["type"] not in types:
            types.append(record["type"])

    return {"foods": pd.DataFrame(records), "nutrients": nutrients, "roles": roles, "units": units,
            "labels": labels, "groups": groups, "types": types}


def akg_targets(group, roles, units):
    """AKG values for the group, converted to the units of the matching TKPI columns."""
    targets = {}
    values = AKG_TABLE[group]
    for k in range(len(AKG_ROLES)):
        role = AKG_ROLES[k]
        if role not in roles:
            continue
        col = roles[role]
        target = float(values[k])
        col_unit = units.get(col, "")
        if AKG_UNITS[k] in MASS_IN_MG and col_unit in MASS_IN_MG:
            target = target * MASS_IN_MG[AKG_UNITS[k]] / MASS_IN_MG[col_unit]
        elif AKG_UNITS[k] == "kcal" and col_unit == "kj":
            target = target * 4.184
        targets[col] = target
    return targets


# ----------------------------------------------------------------------------- log storage

def normalize_log(raw, columns, numeric_cols):
    numeric = set(numeric_cols)
    count = len(raw)
    data = {}
    for col in columns:
        if col in raw.columns:
            source = raw[col].tolist()
        else:
            source = [""] * count
        values = []
        for value in source:
            if col in numeric:
                values.append(to_number(value))
            elif value is None or (isinstance(value, float) and math.isnan(value)):
                values.append("")
            else:
                values.append(str(value))
        data[col] = values
    frame = pd.DataFrame(data, columns=columns)
    for col in columns:
        if col in numeric:
            frame[col] = frame[col].astype(float)
        else:
            frame[col] = frame[col].astype(str)
    return frame


def merge_columns(base_columns, header):
    """Current columns first, then any extra columns already in the log, so nothing is dropped."""
    columns = list(base_columns)
    for name in header:
        name = str(name).strip()
        if name and name not in columns:
            columns.append(name)
    return columns


def append_entry(log, entry, columns, numeric_cols):
    row = normalize_log(pd.DataFrame([entry]), columns, numeric_cols)
    if len(log) == 0:
        return row
    return pd.concat([log, row], ignore_index=True)


def load_log(base_columns, numeric_cols):
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        return normalize_log(pd.DataFrame(), list(base_columns), numeric_cols), list(base_columns)
    raw = pd.read_csv(LOG_FILE, dtype=str, keep_default_na=False)
    columns = merge_columns(base_columns, list(raw.columns))
    return normalize_log(raw, columns, numeric_cols), columns


def backup_log():
    """One copy of the log per day, taken before that day's first change; the newest ones are kept."""
    if not os.path.exists(LOG_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, "intake_log_" + datetime.now().strftime("%Y-%m-%d") + ".csv")
    if os.path.exists(target):
        return
    shutil.copy2(LOG_FILE, target)
    names = []
    for name in os.listdir(BACKUP_DIR):
        if name.startswith("intake_log_") and name.endswith(".csv"):
            names.append(name)
    names.sort()
    while len(names) > BACKUPS_KEPT:
        os.remove(os.path.join(BACKUP_DIR, names.pop(0)))


def replace_file(temp, target):
    """Move temp over target. On Windows another program (Excel, antivirus, a sync tool) can hold
    the target for a moment, so retry, then fall back to overwriting it in place."""
    for attempt in range(10):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            time.sleep(0.2)
    try:
        shutil.copyfile(temp, target)
        os.remove(temp)
        return
    except OSError:
        pass
    try:
        os.remove(temp)
    except OSError:
        pass
    raise PermissionError(os.path.basename(target) + " is open in another program, probably Excel. "
                          "Close it and try again.")


def write_log(log):
    try:
        backup_log()
    except Exception:
        pass                                       # a failed backup must not block saving
    temp = LOG_FILE + ".tmp"
    log.to_csv(temp, index=False)
    replace_file(temp, LOG_FILE)                   # swapped in one step, so an interrupted save can't break the log


def save_entry(entry):
    save_entries([entry])


def save_entries(entries):
    state = st.session_state
    numeric_cols = state["numeric_cols"]
    log, columns = load_log(state["log_columns"], numeric_cols)   # re-read: another tab or your phone may have added entries
    for entry in entries:
        log = append_entry(log, entry, columns, numeric_cols)
    write_log(log)
    state["log"] = log
    state["log_columns"] = columns
    state["log_mtime"] = os.path.getmtime(LOG_FILE)


def remove_entry(entry_id):
    state = st.session_state
    log, columns = load_log(state["log_columns"], state["numeric_cols"])
    log = log[log["ID"] != str(entry_id)].reset_index(drop=True)
    write_log(log)
    state["log"] = log
    state["log_columns"] = columns
    state["log_mtime"] = os.path.getmtime(LOG_FILE)


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            settings = json.load(handle)
        if isinstance(settings, dict):
            return settings
    except Exception:
        pass
    return {}


def legacy_akg_group():
    group = load_settings().get("akg_group", AKG_DEFAULT)
    if group in AKG_TABLE:
        return group
    return AKG_DEFAULT if AKG_DEFAULT in AKG_TABLE else list(AKG_TABLE.keys())[0]


# ----------------------------------------------------------------------------- users

def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    users = []
    for user in data.get("users", []):
        if isinstance(user, dict) and user.get("id"):
            users.append(user)
    return users


def save_users(users):
    temp = USERS_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump({"users": users}, handle, ensure_ascii=False, indent=2)
    replace_file(temp, USERS_FILE)


def find_user(users, user_id):
    for user in users:
        if user["id"] == user_id:
            return user
    return None


def make_user_id(name, users):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "user"
    user_id = base
    number = 2
    while find_user(users, user_id) is not None:
        user_id = base + "-" + str(number)
        number += 1
    return user_id


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200000).hex()


def password_ok(user, password):
    if not user.get("hash"):
        return True
    try:
        return hmac.compare_digest(hash_password(password, user.get("salt", "")), user["hash"])
    except ValueError:
        return False


def adopt_entries(user_id):
    """Give the entries logged before users existed (no User value) to this user."""
    state = st.session_state
    log, columns = load_log(state["log_columns"], state["numeric_cols"])
    owners = log["User"].tolist()
    for k in range(len(owners)):
        if owners[k] == "":
            owners[k] = user_id
    log["User"] = owners
    write_log(log)
    state["log"] = log
    state["log_columns"] = columns
    state["log_mtime"] = os.path.getmtime(LOG_FILE)


def on_create_user(legacy_count):
    state = st.session_state
    users = load_users()
    name = " ".join(str(state.get("new_name") or "").split())
    password = state.get("new_pw") or ""
    if name == "":
        set_flash("auth", "error", "Enter a name for the new user.")
        return
    for user in users:
        if user["name"].lower() == name.lower():
            set_flash("auth", "error", name + " already exists. Sign in, or choose another name.")
            return
    if password != (state.get("new_pw2") or ""):
        set_flash("auth", "error", "The two passwords don't match.")
        return
    user = {"id": make_user_id(name, users), "name": name,
            "akg_group": state.get("new_akg") or legacy_akg_group(), "salt": "", "hash": ""}
    if password:
        user["salt"] = os.urandom(16).hex()
        user["hash"] = hash_password(password, user["salt"])
    users.append(user)
    try:
        save_users(users)
    except Exception as err:
        set_flash("auth", "error", "The user was not saved: " + (str(err) or type(err).__name__))
        return
    message = "Created " + name + ". You're signed in."
    if legacy_count > 0 and state.get("adopt_legacy", False):
        try:
            adopt_entries(user["id"])
            message += " Your earlier entries are now under " + name + "."
        except Exception as err:
            reason = str(err) or type(err).__name__
            if not reason.endswith("."):
                reason += "."
            message += " Your earlier entries were not moved. " + reason + " Then use the button at the top."
    state["user"] = user["id"]
    for key in ["new_name", "new_pw", "new_pw2"]:
        state[key] = ""
    set_flash("top", "success", message)


def on_adopt():
    state = st.session_state
    try:
        adopt_entries(state.get("user", ""))
    except Exception as err:
        set_flash("top", "error", "The entries were not moved: " + (str(err) or type(err).__name__))
        return
    set_flash("top", "success", "Your earlier entries are now in your log.")


def on_sign_in():
    state = st.session_state
    user = find_user(load_users(), state.get("login_user"))
    if user is None:
        set_flash("auth", "error", "Choose a user.")
        return
    if not password_ok(user, state.get("login_pw") or ""):
        set_flash("auth", "error", "Wrong password for " + user["name"] + ".")
        return
    state["user"] = user["id"]
    state["login_pw"] = ""


def on_sign_out():
    if "user" in st.session_state:
        del st.session_state["user"]


def save_akg():
    state = st.session_state
    users = load_users()
    user = find_user(users, state.get("user"))
    if user is None:
        return
    user["akg_group"] = state.get("akg", AKG_DEFAULT)
    try:
        save_users(users)
    except Exception:
        pass


def render_create_form(legacy_count):
    akg_names = list(AKG_TABLE.keys())
    st.text_input("Name", key="new_name")
    st.selectbox("AKG group", akg_names, index=akg_names.index(legacy_akg_group()), key="new_akg")
    c1, c2 = st.columns(2)
    c1.text_input("Password (optional)", type="password", key="new_pw")
    c2.text_input("Repeat password", type="password", key="new_pw2")
    if legacy_count > 0:
        st.checkbox("Move the " + str(legacy_count) + " entries logged before users existed to this user",
                    value=True, key="adopt_legacy")
    primary_button("Create user", on_click=on_create_user, args=(legacy_count,), key="create_user")


def render_sign_in(users, legacy_count):
    show_flash("auth")
    if len(users) == 0:
        st.subheader("Create your user")
        st.caption("Each user has their own log and AKG group. Leave the password empty if you "
                   "don't want one.")
        render_create_form(legacy_count)
        return
    st.subheader("Sign in")
    ids = []
    names = {}
    for user in users:
        ids.append(user["id"])
        names[user["id"]] = user["name"]
    chosen = st.selectbox("User", ids, format_func=lambda k: names.get(k, k), key="login_user")
    chosen_user = find_user(users, chosen)
    if chosen_user is not None and chosen_user.get("hash"):
        st.text_input("Password", type="password", key="login_pw")
    primary_button("Sign in", on_click=on_sign_in, key="sign_in")
    with st.expander("Create a new user"):
        render_create_form(legacy_count)


# ----------------------------------------------------------------------------- adding and removing

def make_entry(day_str, meal, code, food, edible, bought, values, nutrients):
    entry = {"ID": new_id(), "Date": day_str, "Meal": meal, "Code": code, "Food": food,
             "Edible (g)": edible, "As bought (g)": bought}
    for col in nutrients:
        value = values.get(col, float("nan"))
        entry[col] = round(float(value), 4) if is_num(value) else float("nan")
    return entry


def tkpi_entry(food, edible, bought, tkpi, day_str, meal):
    values = {}
    for col in tkpi["nutrients"]:
        value = food[col]
        values[col] = float(value) * edible / 100.0 if is_num(value) else float("nan")
    return make_entry(day_str, meal, food["code"], food["name"], round(edible, 2), bought, values, tkpi["nutrients"])


def entry_from_tkpi(state, tkpi, day_str, meal):
    index = state.get("f_food")
    if index is None:
        return None, "Choose a food first."
    food = tkpi["foods"].iloc[int(index)]
    grams = float(state.get("f_grams") or 100.0)
    edible = grams
    bought = float("nan")
    bdd = food["bdd"]
    if state.get("f_bought", False) and is_num(bdd) and 0 < float(bdd) < 100:
        edible = grams * float(bdd) / 100.0
        bought = grams
    entry = tkpi_entry(food, edible, bought, tkpi, day_str, meal)
    return entry, "Added " + food["name"] + ", " + fmt_grams(edible) + " g, to " + meal.lower() + "."


def entry_from_recent(state, day_str, meal):
    entry_id = state.get("r_item")
    log = state["log"]
    match = log[log["ID"] == str(entry_id)]
    if entry_id is None or len(match) == 0:
        return None, "Choose a recent food first."
    entry = match.iloc[0].to_dict()
    entry["ID"] = new_id()
    entry["Date"] = day_str
    entry["Meal"] = meal
    return entry, "Added " + str(entry["Food"]) + " to " + meal.lower() + "."


def entry_from_custom(state, tkpi, day_str, meal):
    name = str(state.get("c_name") or "").strip()
    if name == "":
        return None, "Enter a name for the custom food."
    values = {}
    for role in CUSTOM_ROLES:
        if role in tkpi["roles"]:
            values[tkpi["roles"][role]] = float(state.get("c_" + role) or 0.0)
    grams = float(state.get("c_grams") or 0.0)
    edible = grams if grams > 0 else float("nan")
    entry = make_entry(day_str, meal, "", name, edible, float("nan"), values, tkpi["nutrients"])
    return entry, "Added " + name + " to " + meal.lower() + "."


def on_add(tkpi):
    state = st.session_state
    day = state.get("day")
    day_str = day.isoformat() if day is not None else now_local().date().isoformat()
    meal = state.get("meal") or MEALS[0]
    source = state.get("source") or "TKPI"
    if source == "Describe":
        add_described(state, tkpi, day_str, meal)
        return
    if source == "Recent":
        entry, message = entry_from_recent(state, day_str, meal)
    elif source == "Custom":
        entry, message = entry_from_custom(state, tkpi, day_str, meal)
    else:
        entry, message = entry_from_tkpi(state, tkpi, day_str, meal)
    if entry is None:
        set_flash("add", "error", message)
        return
    entry["User"] = state.get("user", "")
    try:
        save_entry(entry)
    except Exception as err:
        set_flash("add", "error", "Not saved: " + (str(err) or type(err).__name__))
        return
    if source == "Custom":
        state["c_name"] = ""
        state["c_grams"] = 0.0
        for role in CUSTOM_ROLES:
            if "c_" + role in state:
                state["c_" + role] = 0.0
    set_flash("add", "success", message)


def add_described(state, tkpi, day_str, meal):
    entries, learned, message = entries_from_describe(state, tkpi, day_str, meal)
    if len(entries) == 0:
        set_flash("add", "error", message)
        return
    for entry in entries:
        entry["User"] = state.get("user", "")
    try:
        save_entries(entries)
    except Exception as err:
        set_flash("add", "error", "Not saved: " + (str(err) or type(err).__name__))
        return
    try:
        aliases = load_aliases()
        aliases.update(learned)
        save_aliases(aliases)
    except Exception:
        pass                                       # the foods are saved; only the learning was skipped
    state["d_items"] = []
    state["d_text"] = ""
    set_flash("add", "success", message)


def on_delete():
    state = st.session_state
    entry_id = state.get("del_id")
    if not entry_id:
        return
    log = state["log"]
    match = log[(log["ID"] == str(entry_id)) & (log["User"] == state.get("user", ""))]
    if len(match) == 0:
        set_flash("del", "error", "That entry isn't in your log any more. Use Reload log.")
        return
    name = str(match["Food"].iloc[0])
    try:
        remove_entry(entry_id)
    except Exception as err:
        set_flash("del", "error", "Not removed: " + (str(err) or type(err).__name__))
        return
    set_flash("del", "success", "Removed " + name + ".")


def reload_log():
    for key in ["log", "log_columns", "log_mtime"]:   # the signed-in user stays
        if key in st.session_state:
            del st.session_state[key]


# ----------------------------------------------------------------------------- describe a meal (offline reader)
# Reads text such as "nasi putih 1 piring, tempe goreng 2 potong, es teh manis": splits it into foods,
# reads amounts and household units (URT), matches each food to TKPI by spelling similarity, and
# learns from the food you confirm (intake_aliases.json). Everything runs on this computer.

URT_UNITS = {"porsi": 200, "piring": 250, "mangkuk": 200, "gelas": 200, "cangkir": 150, "sdm": 10, "sdt": 5,
             "centong": 100, "potong": 50, "iris": 20, "buah": 100, "butir": 50, "biji": 10, "lembar": 10,
             "bungkus": 100, "sachet": 20, "tusuk": 10, "ekor": 100, "genggam": 30, "keping": 10, "batang": 50,
             "ikat": 100}
# (word in the TKPI name, unit, grams per unit). Approximate household measures; the first match wins,
# so specific foods come before general ones. Edit freely; you can always change the grams before saving.
URT_FOODS = [("nasi goreng", "piring", 250), ("nasi goreng", "porsi", 250), ("nasi uduk", "piring", 200),
             ("nasi", "piring", 150), ("nasi", "porsi", 150), ("nasi", "centong", 100), ("nasi", "gelas", 130),
             ("tempe", "potong", 25), ("tempe", "buah", 25), ("tempe", "biji", 25),
             ("tahu", "potong", 50), ("tahu", "buah", 100), ("tahu", "biji", 100),
             ("telur puyuh", "butir", 10), ("telur puyuh", "buah", 10),
             ("telur", "butir", 55), ("telur", "buah", 55), ("telur", "biji", 55),
             ("ayam", "potong", 60), ("daging", "potong", 35), ("ikan", "potong", 40), ("ikan", "ekor", 100),
             ("roti", "lembar", 25), ("roti", "iris", 25), ("roti", "potong", 25),
             ("mie", "bungkus", 80), ("mi", "bungkus", 80), ("pisang", "buah", 75),
             ("sate", "tusuk", 10), ("bakso", "biji", 10), ("bakso", "buah", 10), ("kerupuk", "buah", 10)]
UNIT_WORDS = {"g": "g", "gr": "g", "gram": "g", "grams": "g", "kg": "kg", "ml": "ml", "liter": "l",
              "porsi": "porsi", "portion": "porsi", "serving": "porsi", "piring": "piring", "plate": "piring",
              "mangkuk": "mangkuk", "mangkok": "mangkuk", "bowl": "mangkuk", "gelas": "gelas", "glass": "gelas",
              "cangkir": "cangkir", "cup": "cangkir", "sdm": "sdm", "tbsp": "sdm", "sendok": "sdm", "sdt": "sdt",
              "tsp": "sdt", "centong": "centong", "potong": "potong", "piece": "potong", "pieces": "potong",
              "iris": "iris", "slice": "iris", "slices": "iris", "buah": "buah", "butir": "butir", "biji": "biji",
              "lembar": "lembar", "bungkus": "bungkus", "pack": "bungkus", "sachet": "sachet", "tusuk": "tusuk",
              "ekor": "ekor", "genggam": "genggam", "keping": "keping", "batang": "batang", "ikat": "ikat"}
NUMBER_WORDS = {"setengah": 0.5, "separuh": 0.5, "seperempat": 0.25, "sepertiga": 1.0 / 3, "satu": 1.0,
                "dua": 2.0, "tiga": 3.0, "empat": 4.0, "lima": 5.0, "enam": 6.0, "tujuh": 7.0, "delapan": 8.0,
                "sembilan": 9.0, "sepuluh": 10.0, "half": 0.5, "one": 1.0, "two": 2.0, "three": 3.0,
                "four": 4.0, "five": 5.0}
FRACTION_CHARS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1.0 / 3}
SIZE_WORDS = {"kecil": 0.7, "small": 0.7, "sedang": 1.0, "medium": 1.0, "besar": 1.4, "large": 1.4, "big": 1.4}
FILLER_WORDS = {"makan", "minum", "tadi", "pagi", "siang", "sore", "malam", "saya", "aku", "habis", "sekitar",
                "kira", "kurang", "lebih", "ukuran", "ada", "juga", "lagi", "x", "i", "had", "ate", "drank",
                "about", "some"}
SPLIT_PAT = re.compile(r"[,;\n+&]|\b(?:dan|pakai|dengan|sama|serta|plus|and|with)\b")
TOKEN_PAT = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?|[½¼¾⅓]|[^\W\d_]+")


def read_number(token):
    if token in FRACTION_CHARS:
        return FRACTION_CHARS[token]
    if token in NUMBER_WORDS:
        return NUMBER_WORDS[token]
    if re.match(r"^\d+/\d+$", token):
        top, bottom = token.split("/")
        return float(top) / float(bottom) if float(bottom) > 0 else None
    if re.match(r"^\d+(?:[.,]\d+)?$", token):
        return float(token.replace(",", "."))
    return None


def parse_segment(text):
    """One food: its words, amount, household unit and size (kecil / sedang / besar)."""
    clean = re.sub(r"sendok\s+makan", "sdm", text.lower())
    clean = re.sub(r"sendok\s+teh", "sdt", clean)
    qty = None
    unit = None
    size = 1.0
    words = []
    previous = ""
    for token in TOKEN_PAT.findall(clean):
        number = read_number(token)
        if number is not None:
            if qty is None:
                qty = number
            previous = "qty"
            continue
        if token in UNIT_WORDS and previous == "qty":
            unit = UNIT_WORDS[token]
            previous = "unit"
            continue
        if len(token) > 3 and token.startswith("se") and token[2:] in UNIT_WORDS:      # sepiring, sebutir ...
            if qty is None:
                qty = 1.0
            unit = UNIT_WORDS[token[2:]]
            previous = "unit"
            continue
        if token in SIZE_WORDS:
            size = SIZE_WORDS[token]
            continue
        if token in FILLER_WORDS:
            continue
        words.append(token)
        previous = "word"
    if len(words) == 0:
        return None
    if unit is None:
        unit = "buah" if qty is not None else "porsi"
    if qty is None:
        qty = 1.0
    return {"text": " ".join(text.split()), "phrase": " ".join(words), "qty": qty, "unit": unit, "size": size}


def split_meal_text(text):
    parts = []
    for part in SPLIT_PAT.split(text):
        if part and part.strip():
            parts.append(part.strip())
    return parts


def portion_grams(qty, unit, size, food_name):
    if unit == "g" or unit == "ml":
        return qty
    if unit == "kg" or unit == "l":
        return qty * 1000.0
    name = food_name.lower()
    grams = None
    for keyword, food_unit, food_grams in URT_FOODS:
        if food_unit == unit and re.search(r"\b" + re.escape(keyword) + r"\b", name):
            grams = food_grams
            break
    if grams is None:
        grams = URT_UNITS.get(unit, 100)
    return qty * grams * size


def clean_text(text):
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def build_matcher(names):
    clean = []
    for name in names:
        clean.append(clean_text(name))
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        return {"clean": clean, "vectorizer": vectorizer, "matrix": vectorizer.fit_transform(clean)}
    except ImportError:                                     # no scikit-learn: plain spelling similarity
        return {"clean": clean, "vectorizer": None, "matrix": None}


def get_matcher(tkpi):
    names = tkpi["foods"]["name"].tolist()
    key = (len(names), names[0], names[-1])
    cached = st.session_state.get("matcher")
    if cached is None or cached[0] != key:
        st.session_state["matcher"] = (key, build_matcher(names))
    return st.session_state["matcher"][1]


def match_scores(matcher, phrase):
    """Similarity of the phrase to every TKPI name: character n-grams plus a bonus per word found."""
    query = clean_text(phrase)
    clean = matcher["clean"]
    scores = []
    if matcher["vectorizer"] is not None:
        similarity = (matcher["matrix"] @ matcher["vectorizer"].transform([query]).T).toarray()
        for k in range(len(clean)):
            scores.append(float(similarity[k][0]))
    else:
        import difflib
        for name in clean:
            scores.append(difflib.SequenceMatcher(None, query, name).ratio())
    words = []
    for word in query.split():
        if len(word) >= 3:
            words.append(word)
    if len(words) > 0:
        for k in range(len(clean)):
            hits = 0
            for word in words:
                if word in clean[k]:
                    hits += 1
            scores[k] += 0.3 * hits / len(words)
    return scores


def load_aliases():
    try:
        with open(ALIASES_FILE, encoding="utf-8") as handle:
            aliases = json.load(handle)
        if isinstance(aliases, dict):
            return aliases
    except Exception:
        pass
    return {}


def save_aliases(aliases):
    temp = ALIASES_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(aliases, handle, ensure_ascii=False, indent=2, sort_keys=True)
    replace_file(temp, ALIASES_FILE)


def find_candidates(matcher, phrase, aliases, code_index, top=5):
    """Up to `top` TKPI rows for the phrase; a food you confirmed before for it comes first."""
    scores = match_scores(matcher, phrase)
    order = sorted(range(len(scores)), key=lambda k: -scores[k])
    candidates = []
    learned = False
    code = aliases.get(clean_text(phrase))
    if code in code_index:
        candidates.append(code_index[code])
        learned = True
    for k in order:
        if len(candidates) >= top or scores[k] <= 0:
            break
        if k not in candidates:
            candidates.append(k)
    return candidates, learned


def on_read(tkpi):
    state = st.session_state
    text = str(state.get("d_text") or "").strip()
    if text == "":
        set_flash("describe", "error", "Write what you ate first.")
        return
    matcher = get_matcher(tkpi)
    aliases = load_aliases()
    code_index = {}
    codes = tkpi["foods"]["code"].tolist()
    for k in range(len(codes)):
        if codes[k]:
            code_index[codes[k]] = k
    items = []
    for part in split_meal_text(text):
        item = parse_segment(part)
        if item is None:
            continue
        candidates, learned = find_candidates(matcher, item["phrase"], aliases, code_index)
        if len(candidates) == 0:
            continue
        item["candidates"] = candidates
        item["learned"] = learned
        items.append(item)
    state["d_items"] = items
    state["d_gen"] = state.get("d_gen", 0) + 1
    if len(items) == 0:
        set_flash("describe", "error", "No foods recognised. Try other words, or pick from the TKPI list.")


def entries_from_describe(state, tkpi, day_str, meal):
    items = state.get("d_items") or []
    gen = str(state.get("d_gen", 0))
    if len(items) == 0:
        return [], {}, "Write what you ate and press Read first."
    entries = []
    learned = {}
    names = []
    for i in range(len(items)):
        key = gen + "_" + str(i)
        if not state.get("d_use_" + key, True):
            continue
        index = state.get("d_food_" + key)
        grams = float(state.get("d_grams_" + key) or 0.0)
        if index is None or grams <= 0:
            continue
        food = tkpi["foods"].iloc[int(index)]
        entries.append(tkpi_entry(food, grams, float("nan"), tkpi, day_str, meal))
        if food["code"]:
            learned[clean_text(items[i]["phrase"])] = food["code"]
        names.append(food["name"])
    if len(entries) == 0:
        return [], {}, "Nothing to add: tick at least one food with more than 0 g."
    return entries, learned, ("Added " + str(len(entries)) + " food" + ("s" if len(entries) > 1 else "")
                              + " to " + meal.lower() + ": " + ", ".join(names) + ".")


def fmt_qty(qty):
    if abs(qty - round(qty)) < 1e-9:
        return str(int(round(qty)))
    return f"{qty:.2f}".rstrip("0").rstrip(".")


def render_describe(tkpi):
    st.text_area("What did you eat?", key="d_text", height=90)
    st.caption("For example: nasi putih 1 piring, tempe goreng 2 potong, es teh manis. "
               "Separate foods with commas or the word dan.")
    st.button("Read", on_click=on_read, args=(tkpi,), key="d_read")
    show_flash("describe")
    items = st.session_state.get("d_items") or []
    gen = str(st.session_state.get("d_gen", 0))
    labels = tkpi["foods"]["label"].tolist()
    names = tkpi["foods"]["name"].tolist()
    for i in range(len(items)):
        item = items[i]
        key = gen + "_" + str(i)
        note = "read as " + fmt_qty(item["qty"]) + " " + item["unit"]
        if item["size"] != 1.0:
            note += " (" + ("small" if item["size"] < 1 else "large") + ")"
        if item["learned"]:
            note += ", first choice learned from you"
        st.markdown('<div class="nl-item">' + str(i + 1) + ". " + esc(item["text"]) + "<span>" + esc(note)
                    + "</span></div>", unsafe_allow_html=True)
        index = st.selectbox("Food", item["candidates"], format_func=lambda k: labels[k], key="d_food_" + key)
        grams = min(portion_grams(item["qty"], item["unit"], item["size"], names[index]), 5000.0)
        c1, c2 = st.columns(2)
        c1.number_input("Grams", min_value=0.0, max_value=5000.0, value=float(round(grams, 1)), step=5.0,
                        key="d_grams_" + key)
        c2.checkbox("Add this food", value=True, key="d_use_" + key)
    if len(items) > 0:
        st.caption("Check each food and its grams, then press Add to log. The foods you confirm are "
                   "remembered for next time.")


# ----------------------------------------------------------------------------- input sections

def portion_preview(food, edible, grams, tkpi):
    roles = tkpi["roles"]
    units = tkpi["units"]
    parts = []
    for role in ["energy", "protein", "fat", "carb"]:
        if role not in roles:
            continue
        col = roles[role]
        if not is_num(food[col]):
            continue
        amount = float(food[col]) * edible / 100.0
        unit = units.get(col, "")
        if role == "energy":
            parts.append(fmt(amount, unit) + " " + unit_text(unit))
        else:
            parts.append(fmt(amount) + " " + unit + " " + ROLE_LABELS[role].lower())
    if len(parts) == 0:
        text = "TKPI has no energy or macronutrient values for this food."
    else:
        text = food["type"] + ". This portion: " + ", ".join(parts) + "."
    if abs(edible - grams) > 1e-9:
        text += " Edible part: " + fmt_grams(edible) + " g."
    return text


def render_tkpi_picker(tkpi):
    foods = tkpi["foods"]
    group_values = foods["group"].tolist()
    type_values = foods["type"].tolist()
    labels = foods["label"].tolist()
    c1, c2 = st.columns(2)
    group = c1.selectbox("Food group", ["All groups"] + tkpi["groups"], key="f_group")
    kind = c2.selectbox("Food type", ["All types"] + tkpi["types"], key="f_type")
    options = []
    for i in range(len(labels)):
        if group != "All groups" and group_values[i] != group:
            continue
        if kind != "All types" and type_values[i] != kind:
            continue
        options.append(i)
    if len(options) == 0:
        notice("info", "No food matches this group and type. Choose another group or type.")
        return
    c3, c4 = st.columns(2)
    index = c3.selectbox("Food", options, format_func=lambda i: labels[i], key="f_food")
    grams = c4.number_input("Portion [g]", min_value=1.0, max_value=5000.0, value=100.0, step=10.0,
                            key="f_grams")
    food = foods.iloc[index]
    edible = grams
    bdd = food["bdd"]
    if is_num(bdd) and 0 < float(bdd) < 100:
        as_bought = st.checkbox("Weight as bought, including inedible parts (edible portion "
                                + fmt_pct(bdd) + ")", key="f_bought")
        if as_bought:
            edible = grams * float(bdd) / 100.0
    st.caption(portion_preview(food, edible, grams, tkpi))


def entry_label(record):
    text = str(record["Food"])
    if is_num(record["Edible (g)"]):
        text += ", " + fmt_grams(record["Edible (g)"]) + " g"
    return text


def recent_items(log, roles, units, limit=40):
    energy_col = roles.get("energy")
    records = log.to_dict("records")
    ids = []
    names = {}
    seen = set()
    for k in range(len(records) - 1, -1, -1):
        record = records[k]
        key = (str(record["Food"]), fmt_grams(record["Edible (g)"]), str(record["Code"]))
        if key in seen:
            continue
        seen.add(key)
        label = entry_label(record)
        if energy_col and is_num(record.get(energy_col)):
            unit = units.get(energy_col, "kcal")
            label += " (" + fmt(record[energy_col], unit) + " " + unit_text(unit) + ")"
        ids.append(record["ID"])
        names[record["ID"]] = label
        if len(ids) >= limit:
            break
    return ids, names


def render_recent_picker(log, roles, units):
    ids, names = recent_items(log, roles, units)
    if len(ids) == 0:
        notice("info", "Foods you log show up here, so you can add them again in one step.")
        return
    st.selectbox("Recent food", ids, format_func=lambda k: names.get(k, k), key="r_item")
    st.caption("Adds the same food and portion to the date and meal above.")


def render_custom_inputs(roles, units):
    st.text_input("Food name", key="c_name")
    fields = [("c_grams", "Portion [g]", 10.0)]
    for role in CUSTOM_ROLES:
        if role in roles:
            unit = units.get(roles[role], "")
            fields.append(("c_" + role, ROLE_LABELS[role] + " [" + unit_text(unit) + "]", 1.0))
    for start in range(0, len(fields), 2):
        columns = st.columns(2)
        for k in range(2):
            if start + k < len(fields):
                key, label, step = fields[start + k]
                columns[k].number_input(label, min_value=0.0, step=step, key=key)
    st.caption("Enter the values for the whole portion, for example from the nutrition label "
               "(Informasi Nilai Gizi).")


# ----------------------------------------------------------------------------- results

def column_totals(frame, columns):
    totals = {}
    missing = {}
    for col in columns:
        total = 0.0
        found = False
        gaps = 0
        for value in frame[col].tolist():
            if is_num(value):
                total += float(value)
                found = True
            else:
                gaps += 1
        totals[col] = total if found else float("nan")
        missing[col] = gaps
    return totals, missing


def group_of(col, role):
    if role in MACRO_ROLES:
        return "macro"
    if role in MINERAL_ROLES:
        return "mineral"
    if role in VITAMIN_ROLES:
        return "vitamin"
    if re.search(r"retinol|kar[o\-\s]|carot|vit", col.lower()):      # retinol, (beta-/total) carotene
        return "vitamin"
    return "macro"                                  # water, ash and anything else


def card_html(label, unit, total, missing, target):
    star = "*" if missing > 0 and is_num(total) else ""
    if not target:                                  # no AKG value: show the amount itself
        return ('<div class="nl-cell"><div class="nl-label">' + esc(label) + '</div><div class="nl-value">'
                + fmt(total, unit) + star + '</div><div class="nl-pct">No AKG value</div></div>')
    pct = 100.0 * float(total) / target if is_num(total) else float("nan")
    if is_num(pct) and pct >= OVER_PCT:             # over target: the card's outline turns pink too
        cell = '<div class="nl-cell" style="border-color:' + PINK + ';box-shadow:0 0 0 1px ' + PINK + '">'
    else:
        cell = '<div class="nl-cell">'
    width = min(pct, 100.0) if is_num(pct) else 0.0
    return (cell + '<div class="nl-label">' + esc(label) + '</div><div class="nl-value">' + fmt_pct(pct)
            + '</div><div class="nl-track"><div class="nl-fill" style="width:' + f"{width:.1f}"
            + "%;background:" + status_color(pct) + '"></div></div><div class="nl-pct">'
            + fmt(total, unit) + star + " / " + fmt(target, unit) + "</div></div>")


def grouped_nutrients(nutrient_cols, roles, targets):
    """[(group title, columns)], with the nutrients that have an AKG value first in each group."""
    role_of = {}
    for role in roles:
        role_of[roles[role]] = role
    groups = []
    for key, title in NUTRIENT_GROUPS:
        with_target = []
        without_target = []
        for col in nutrient_cols:
            if group_of(col, role_of.get(col)) != key:
                continue
            if col in targets:
                with_target.append(col)
            else:
                without_target.append(col)
        if len(with_target) + len(without_target) > 0:
            groups.append((title, with_target + without_target))
    return groups


def summary_html(totals, missing, targets, nutrient_cols, labels, units, roles):
    """Every nutrient as a card: % of AKG first, then the amount out of the AKG value."""
    parts = ""
    any_missing = False
    for title, columns in grouped_nutrients(nutrient_cols, roles, targets):
        parts += '<div class="nl-group">' + esc(title) + "</div>"
        for col in columns:
            unit = units.get(col, "")
            label = labels[col] + (" [" + unit_text(unit) + "]" if unit else "")
            if missing.get(col, 0) > 0 and is_num(totals.get(col)):
                any_missing = True
            parts += card_html(label, unit, totals.get(col), missing.get(col, 0), targets.get(col))
    out = '<div class="nl-grid">' + parts + "</div>"
    if any_missing:
        out += ('<p class="nl-foot">* At least this much: some foods have no TKPI value for this nutrient, '
                "or were added as custom foods.</p>")
    return out


def energy_split_html(totals, roles):
    for role in ["protein", "fat", "carb"]:
        if role not in roles:
            return ""
    protein = number_or_zero(totals.get(roles["protein"]))
    fat = number_or_zero(totals.get(roles["fat"]))
    carb = number_or_zero(totals.get(roles["carb"]))
    fiber = number_or_zero(totals.get(roles["fiber"])) if "fiber" in roles else 0.0
    parts = [("Protein", protein * 4.0, COLORS["protein"]),
             ("Fat", fat * 9.0, COLORS["fat"]),
             ("Carbohydrate", max(carb - fiber, 0.0) * 4.0, COLORS["carb"]),
             ("Fiber", fiber * 2.0, COLORS["fiber"])]
    total = 0.0
    for part in parts:
        total += part[1]
    if total <= 0:
        return ""
    bar = ""
    legend = ""
    described = []
    for name, kcal, color in parts:
        if kcal <= 0:
            continue
        share = 100.0 * kcal / total
        bar += '<span style="width:' + f"{share:.2f}" + "%;background:" + color + '"></span>'
        legend += ('<span><span class="nl-swatch" style="background:' + color + '"></span>'
                   + name + " " + fmt_pct(share) + "</span>")
        described.append(name + " " + fmt_pct(share))
    return ('<div class="nl-caption">Where the energy comes from</div>'
            '<div class="nl-split" role="img" aria-label="' + esc("Energy split: " + ", ".join(described))
            + '">' + bar + '</div><div class="nl-legend">' + legend + "</div>")


def column_decimals(records, col, unit):
    """One number of decimals per nutrient, so the figures line up across tables."""
    if unit == "kcal":
        return 0
    largest = 0.0
    for record in records:
        if is_num(record.get(col)):
            largest = max(largest, abs(float(record[col])))
    if largest >= 100:
        return 0
    if largest >= 10:
        return 1
    return 2


def fixed(value, decimals):
    if not is_num(value):
        return "–"
    return f"{float(value):,.{decimals}f}"


def sum_records(records, col):
    total = 0.0
    found = False
    for record in records:
        if is_num(record.get(col)):
            total += float(record[col])
            found = True
    return total if found else float("nan")


def transposed_html(title, heads, columns, has_total, groups, labels, units, decimals, portions, limits):
    """One table with a row per nutrient and a column per food (or per meal). Any amount above the
    daily AKG value (limits) is highlighted pink."""
    head = '<tr><th class="nl-sticky">Nutrient</th>'
    for k in range(len(heads)):
        css = "nl-food nl-totalcol" if has_total and k == len(heads) - 1 else "nl-food"
        head += '<th class="' + css + '">' + heads[k] + "</th>"
    rows = []
    if portions is not None:
        row = '<tr><td class="nl-sticky">Portion [g]</td>'
        for k in range(len(portions)):
            css = ' class="nl-totalcol"' if has_total and k == len(portions) - 1 else ""
            row += "<td" + css + ">" + portions[k] + "</td>"
        rows.append(row + "</tr>")
    for group_title, group_cols in groups:
        row = '<tr class="nl-grouprow"><td class="nl-sticky">' + esc(group_title) + "</td>"
        for k in range(len(columns)):
            row += "<td></td>"
        rows.append(row + "</tr>")
        for col in group_cols:
            unit = units.get(col, "")
            name = labels[col] + (" [" + unit_text(unit) + "]" if unit else "")
            row = '<tr><td class="nl-sticky">' + esc(name) + "</td>"
            for k in range(len(columns)):
                classes = []
                if has_total and k == len(columns) - 1:
                    classes.append("nl-totalcol")
                value = columns[k].get(col)
                limit = limits.get(col)
                if limit and is_num(value) and float(value) > limit:
                    classes.append("nl-over")
                css = ' class="' + " ".join(classes) + '"' if classes else ""
                row += "<td" + css + ">" + fixed(value, decimals[col]) + "</td>"
            rows.append(row + "</tr>")
    return ('<div class="nl-meal-title">' + esc(title) + '</div><div class="nl-scroll"><table class="nl-table nl-wide">'
            "<thead>" + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def meal_tables_html(day_log, groups, labels, units, roles, targets):
    """A table per meal (foods side by side, plus their total), then the whole day by meal."""
    records = day_log.to_dict("records")
    columns_all = []
    for group_title, group_cols in groups:
        columns_all = columns_all + group_cols
    decimals = {}
    for col in columns_all:
        decimals[col] = column_decimals(records, col, units.get(col, ""))
    portion_decimals = 0
    for record in records:
        grams = record["Edible (g)"]
        if is_num(grams) and abs(float(grams) - round(float(grams))) >= 0.05:
            portion_decimals = 1
    energy_col = roles.get("energy")
    meals = list(MEALS)
    for record in records:
        if record["Meal"] not in meals:
            meals.append(record["Meal"])
    out = ""
    day_heads = []
    day_columns = []
    for meal in meals:
        items = []
        for record in records:
            if record["Meal"] == meal:
                items.append(record)
        if len(items) == 0:
            continue
        heads = []
        columns = []
        portions = []
        for record in items:
            head = esc(record["Food"])
            if is_num(record.get("As bought (g)")):
                head += '<span class="nl-note">' + fmt_grams(record["As bought (g)"]) + " g as bought</span>"
            heads.append(head)
            columns.append(record)
            portions.append(fixed(record["Edible (g)"], portion_decimals))
        totals = {}
        for col in columns_all:
            totals[col] = sum_records(items, col)
        has_total = len(items) > 1
        if has_total:
            heads.append("Total")
            columns.append(totals)
            portions.append("")
        title = meal
        if energy_col and is_num(totals.get(energy_col)):
            unit = units.get(energy_col, "kcal")
            title += " (" + fmt(totals[energy_col], unit) + " " + unit_text(unit) + ")"
        out += transposed_html(title, heads, columns, has_total, groups, labels, units, decimals, portions, targets)
        day_heads.append(esc(meal))
        day_columns.append(totals)
    if len(day_columns) > 1:                        # the whole day, meal by meal
        day_totals = {}
        for col in columns_all:
            day_totals[col] = sum_records(records, col)
        title = "Whole day"
        if energy_col and is_num(day_totals.get(energy_col)):
            unit = units.get(energy_col, "kcal")
            title += " (" + fmt(day_totals[energy_col], unit) + " " + unit_text(unit) + ")"
        out += transposed_html(title, day_heads + ["Total"], day_columns + [day_totals], True, groups, labels,
                               units, decimals, None, targets)
    return out


def nutrient_table_html(values, missing, columns, labels, units, targets, value_title):
    rows = []
    any_missing = False
    for col in columns:
        unit = units.get(col, "")
        name = labels[col] + (" [" + unit_text(unit) + "]" if unit else "")
        value = values.get(col)
        text = fmt(value, unit)
        if missing.get(col, 0) > 0 and is_num(value):
            text += "*"
            any_missing = True
        target = targets.get(col)
        akg = fmt(target, unit) if target else ""
        pct = fmt_pct(100.0 * float(value) / target) if target and is_num(value) else ""
        rows.append("<tr><td>" + esc(name) + "</td><td>" + text + "</td><td>" + akg + "</td><td>"
                    + pct + "</td></tr>")
    table = ('<table class="nl-table"><thead><tr><th>Nutrient</th><th>' + value_title
             + "</th><th>AKG</th><th>%</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    if any_missing:
        table += ('<p class="nl-foot">* Some foods have no TKPI value for this nutrient, or were added '
                  "as custom foods, so the total is a lower bound.</p>")
    return table


def history_html(daily, start, days, roles, units, targets):
    energy_col = roles.get("energy")
    if not energy_col:
        return ""
    unit = units.get(energy_col, "kcal")
    target = targets.get(energy_col)
    scale = 150.0                                   # the bar runs from 0 to 150% of the target
    rows = []
    for k in range(days - 1, -1, -1):
        day = start + timedelta(days=k)
        key = day.isoformat()
        if key in daily.index and is_num(daily.loc[key, energy_col]):
            value = float(daily.loc[key, energy_col])
            text = fmt(value, unit) + " " + unit_text(unit)
            bar = '<div class="nl-bar">'
            if target:
                pct = 100.0 * value / target
                bar += ('<div class="nl-bar-fill" style="width:' + f"{min(pct, scale) / scale * 100.0:.1f}"
                        + "%;background:" + status_color(pct) + '"></div><div class="nl-mark" style="left:'
                        + f"{100.0 / scale * 100.0:.1f}" + '%"></div>')
                text += " (" + fmt_pct(pct) + ")"
            bar += "</div>"
        else:
            bar = '<div class="nl-bar"></div>'
            text = "Not logged"
        rows.append('<div class="nl-day"><span>' + esc(short_day(day)) + "</span>" + bar
                    + '<span class="nl-num">' + text + "</span></div>")
    foot = ""
    if target:
        foot = (status_legend_html() + '<p class="nl-foot">The line marks your AKG energy of '
                + fmt(target, unit) + " " + unit_text(unit) + ".</p>")
    return ('<div class="nl-caption">Energy per day</div><div class="nl-days">' + "".join(rows)
            + "</div>" + foot)


# ----------------------------------------------------------------------------- page

st.markdown(CSS, unsafe_allow_html=True)
st.title("Daily nutrient intake")
st.caption("Foods and nutrient values from Tabel Komposisi Pangan Indonesia (TKPI). "
           "Daily targets from AKG 2019 (Permenkes No. 28 Tahun 2019).")

if not os.path.exists(DATA_FILE):
    notice("error", "tkpi.csv was not found. Put it in the same folder as intake_app.py.")
    st.stop()
try:
    tkpi = load_tkpi(DATA_FILE, os.path.getmtime(DATA_FILE), 4)
except Exception as err:
    notice("error", "tkpi.csv could not be read: " + str(err) + ".")
    st.stop()

nutrient_cols = tkpi["nutrients"]
shown_cols = []                                 # what the cards and tables show; the log keeps everything
for col in nutrient_cols:
    if not HIDDEN_PAT.search(col.lower()):
        shown_cols.append(col)
roles = tkpi["roles"]
units = tkpi["units"]
labels = tkpi["labels"]
numeric_cols = NUMERIC_META + nutrient_cols
st.session_state["numeric_cols"] = numeric_cols

log_mtime = os.path.getmtime(LOG_FILE) if os.path.exists(LOG_FILE) else None
if "log" not in st.session_state or st.session_state.get("log_mtime") != log_mtime:
    try:                                       # first visit, or another device changed the log
        loaded_log, loaded_columns = load_log(META_COLS + nutrient_cols, numeric_cols)
    except Exception as err:
        notice("error", "intake_log.csv could not be read: " + (str(err) or type(err).__name__)
                 + ". Restore a copy from the backups folder, or move the file away to start a new log.")
        st.stop()
    st.session_state["log"] = loaded_log
    st.session_state["log_columns"] = loaded_columns
    st.session_state["log_mtime"] = log_mtime
log = st.session_state["log"]

users = load_users()
current = find_user(users, st.session_state.get("user"))
if current is None:
    legacy_count = int((log["User"] == "").sum())
    render_sign_in(users, legacy_count)
    st.stop()
user_id = current["id"]
user_log = log[log["User"] == user_id]

akg_names = list(AKG_TABLE.keys())
user_group = current.get("akg_group", AKG_DEFAULT)
if user_group not in akg_names:
    user_group = legacy_akg_group()
with st.sidebar:
    st.header("Settings")
    st.caption("Signed in as " + current["name"] + ".")
    st.button("Sign out", on_click=on_sign_out)
    akg_group = st.selectbox("AKG group", akg_names, index=akg_names.index(user_group), key="akg",
                             on_change=save_akg)
    st.caption("Daily targets come from AKG 2019 for this group, saved for " + current["name"] + ".")
    st.subheader("Your log")
    st.caption("Saved in intake_log.csv in the app folder. Once a day a dated copy goes to the "
               "backups folder.")
    st.download_button("Download my log (CSV)", data=user_log.to_csv(index=False).encode("utf-8-sig"),
                       file_name="nutrient_log_" + user_id + ".csv", mime="text/csv")
    st.button("Reload log", on_click=reload_log)

show_flash("top")
unowned = int((log["User"] == "").sum())
if unowned > 0:
    notice("info", str(unowned) + " entries were logged before users existed and don't belong to anyone yet.")
    primary_button("Move them to " + current["name"] + "'s log", on_click=on_adopt, key="adopt")

targets = akg_targets(akg_group, roles, units)
now = now_local()
day = st.date_input("Date", value=now.date(), key="day")
day_str = day.isoformat()

tab_day, tab_history = make_tabs(["Day", "History"])

with tab_day:
    st.subheader("Add food")
    st.selectbox("Meal", MEALS, index=meal_index(now), key="meal")
    source = radio_row("Add from", ["TKPI", "Describe", "Recent", "Custom"], "source")
    if source == "Describe":
        render_describe(tkpi)
    elif source == "Recent":
        render_recent_picker(user_log, roles, units)
    elif source == "Custom":
        render_custom_inputs(roles, units)
    else:
        render_tkpi_picker(tkpi)
    primary_button("Add to log", on_click=on_add, args=(tkpi,), key="add")
    show_flash("add")

    st.subheader(long_day(day))
    show_flash("del")
    day_log = user_log[user_log["Date"] == day_str]
    if len(day_log) == 0:
        notice("info", "Nothing logged for this day yet. Add a food above.")
    else:
        totals, missing = column_totals(day_log, nutrient_cols)
        st.markdown(summary_html(totals, missing, targets, shown_cols, labels, units, roles),
                    unsafe_allow_html=True)
        st.markdown(status_legend_html(), unsafe_allow_html=True)
        st.caption("Percentages are of AKG 2019 for " + akg_group.lower() + ". Change the group in the sidebar.")
        split = energy_split_html(totals, roles)
        if split:
            st.markdown(split, unsafe_allow_html=True)
        st.markdown(meal_tables_html(day_log, grouped_nutrients(shown_cols, roles, targets), labels, units, roles,
                                     targets), unsafe_allow_html=True)
        st.caption("Pink marks any amount above the daily AKG. Tables scroll sideways if they don't fit.")
        with st.expander("Remove an entry"):
            entry_names = {}
            for record in day_log.to_dict("records"):
                entry_names[record["ID"]] = record["Meal"] + ": " + entry_label(record)
            st.selectbox("Entry", list(entry_names.keys()), format_func=lambda k: entry_names.get(k, k),
                         key="del_id")
            st.button("Remove entry", on_click=on_delete, key="del")
        if split:
            with st.expander("How the energy split is calculated"):
                st.markdown(ENERGY_NOTE)

with tab_history:
    period = radio_row("Period", ["7 days", "14 days", "30 days"], "period")
    days = int(str(period).split()[0])
    start = day - timedelta(days=days - 1)
    st.caption("The " + str(days) + " days up to " + long_day(day) + ", the date chosen above.")
    window = user_log[(user_log["Date"] >= start.isoformat()) & (user_log["Date"] <= day_str)]
    if len(window) == 0:
        notice("info", "Nothing logged in these " + str(days) + " days.")
    else:
        daily = window.groupby("Date")[nutrient_cols].sum(min_count=1)
        st.markdown(history_html(daily, start, days, roles, units, targets), unsafe_allow_html=True)
        averages = {}
        with_target = []
        for col in shown_cols:
            averages[col] = daily[col].mean()
            if col in targets:
                with_target.append(col)
        st.markdown("**Average per logged day** (" + str(len(daily)) + " of " + str(days) + " days)")
        st.markdown(nutrient_table_html(averages, {}, with_target, labels, units, targets, "Per day"),
                    unsafe_allow_html=True)
        st.caption("Days with nothing logged are left out of the averages.")

# =============================================================================
# TKPI nutrient lookup — pick a food from the dropdown, get its nutrients
# Data: tkpi.csv in the same folder as this file (repository root)
# Run locally:  streamlit run app.py   (works on Streamlit 1.16 and newer)
# =============================================================================

import base64
import csv
import html
import io
import os
import re
import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageOps

st.set_page_config(page_title="Tabel Komposisi Pangan Indonesia (TKPI) nutrient lookup", page_icon="🍚", layout="centered")

DATA_FILE = "tkpi.csv"
HEADER_IMAGES = ["SHL.png", "i3L.png"]   # next to app.py; shown side by side at the top

DISCLAIMER = "This app is supported by Claude Opus 5.5 Max."
CHOOSE = "Choose a food…"
GROUP_NAMES = {"A": "Serealia", "B": "Umbi berpati", "C": "Kacang/biji/bean",
               "D": "Sayuran", "E": "Buah", "F": "Daging/unggas", "G": "Ikan/kerang/udang",
               "H": "Telur", "J": "Susu", "K": "Lemak/minyak", "M": "Gula/sirup/konfeksioneri",
               "N": "Bumbu", "Q": "Minuman"}
FORM_NAMES = {"R": "Mentah/segar", "P": "Olahan"}
TYPE_COL = r"^tipe|\btipe\b|\btype\b|^jenis|\bjenis\b"
ALL_GROUPS = "All groups"
ALL_TYPES = "All types"
NO_TYPE = "Unspecified"
SKIP_COL = r"^no\.?$|^nomor|^id$|^unnamed|sumber|source|kode|code|nama|name|kelompok|group|tipe|\btype\b|jenis"
CATEGORIES = [
    ("Macronutrients", r"energi|energy|kkal|protein|lemak|\bfat\b|lipid|karbo|carb|\bkh\b|serat|fib|^air\b|water|^abu\b|\bash\b"),
    ("Minerals", r"kalsium|calcium|fosfor|phosph|besi|\biron\b|natrium|sodium|kalium|potass|tembaga|copper|seng|zinc"
                 r"|^(ca|p|fe|na|k|cu|zn)\b"),
    ("Vitamins", r"retinol|karoten|carot|b[-_ ]?kar|β|thiamin|tiamin|ribofla|niasin|niacin|vit|ascorb"),
]
KEY_MACROS = [("Energy", r"energi|energy|kkal"), ("Protein", r"protein"), ("Fat", r"lemak|\bfat\b|lipid"),
              ("Carbohydrate", r"karbo|carb|\bkh\b"), ("Fiber", r"serat|fib")]
# summary grid drawn with HTML so it stays 3 by 2 on phones (st.columns always stacks on narrow screens)
APP_CSS = """
<style>
.tkpi-grid {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.75rem 1rem; margin: 0.25rem 0 1rem;}
.tkpi-cell {display: flex; flex-direction: column; justify-content: space-between; min-width: 0;}
.tkpi-label {font-size: 0.875rem; opacity: 0.75; line-height: 1.3;}
.tkpi-value {font-size: 2.25rem; line-height: 1.2; font-variant-numeric: tabular-nums;}
.tkpi-hero {display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; align-items: center; margin-bottom: 0.75rem;}
.tkpi-hero img {width: 100%; height: auto; border-radius: 8px; display: block;}
.tkpi-row {display: flex; flex-wrap: wrap; gap: 1rem 2rem; margin: 0.5rem 0 0.75rem;}
.tkpi-bar {flex: 1 1 280px; min-width: 0;}
.tkpi-bar-title {font-size: 0.85rem; margin-bottom: 0.35rem;}
.tkpi-track {display: flex; height: 2.1rem; border-radius: 6px; overflow: hidden;}
.tkpi-seg {display: flex; align-items: center; justify-content: center; overflow: hidden; white-space: nowrap;
           font-size: 0.85rem; font-weight: 700; color: #1E2B21;}
.tkpi-legend {display: flex; flex-wrap: wrap; gap: 0.2rem 1.1rem; margin-top: 0.45rem; font-size: 0.85rem;}
.tkpi-key {display: inline-flex; align-items: center; gap: 0.35rem;}
.tkpi-swatch {width: 0.75rem; height: 0.75rem; border-radius: 2px; display: inline-block;}
@media (max-width: 640px) {
  .tkpi-label {font-size: 0.8rem;}
  .tkpi-value {font-size: 1.6rem;}
  .tkpi-name {display: none;}
  .tkpi-seg {font-size: 0.75rem;}
}
</style>
"""
# units shown in the summary labels, e.g. "Energy [Kcal]"
MACRO_UNITS = {"Energy": "Kcal", "Protein": "g", "Fat": "g", "Carbohydrate": "g", "Fiber": "g"}
SPLIT_COLORS = ["#FF5E6C", "#FFE161", "#27F587", "#6CB8FF"]   # protein, fat, carbohydrate, fiber
SPLIT_TEXT = "#1E2B21"   # dark labels stay readable on these light colours
# mineral colours, matched by name so each mineral keeps its colour for every food
MINERAL_COLORS = [
    (r"kalsium|calcium|^ca\b", "#4C97FF"),
    (r"fosfor|phosph|^p\b", "#A877FF"),
    (r"besi|\biron\b|^fe\b", "#FF9E5E"),
    (r"natrium|sodium|^na\b", "#FFB020"),
    (r"kalium|potass|^k\b", "#22CCA0"),
    (r"tembaga|copper|^cu\b", "#F59BD8"),
    (r"seng|zinc|^zn\b", "#D4F06A"),
]
TRACE_MINERAL = r"besi|\biron\b|^fe\b|tembaga|copper|^cu\b|seng|zinc|^zn\b"
EXTRA_COLORS = ["#FFC4A3", "#9FE7F5", "#E2D4FF", "#FFE7A0"]
UNIT_TOKEN = r"^(g|gr|gram|mg|mcg|µg|μg|ug|kal|kkal|kcal|kj|%|iu)$"
# standard TKPI units per 100 g BDD, used when the file does not state a unit
DEFAULT_UNITS = [
    (r"energi|energy|kkal|kalori", "kcal"),
    (r"retinol|karoten|carot|b[-_ ]?kar|β", "mcg"),
    (r"^air\b|water|protein|lemak|\bfat\b|lipid|karbo|carb|\bkh\b|serat|fib|^abu\b|\bash\b", "g"),
    (r"kalsium|calcium|fosfor|phosph|besi|\biron\b|natrium|sodium|kalium|potass|tembaga|copper|seng|zinc"
     r"|^(ca|p|fe|na|k|cu|zn)\b"
     r"|thiamin|tiamin|ribofla|niasin|niacin|vit|ascorb", "mg"),
]
DISPLAY = {"kh": "Karbohidrat", "b-kar": "Beta-karoten", "b kar": "Beta-karoten", "b karoten": "Beta-karoten",
           "b-karoten": "Beta-karoten", "beta karoten": "Beta-karoten", "vit c": "Vitamin C",
           "vitamin c": "Vitamin C", "karoten total": "Karoten total"}


if hasattr(st, "cache_data"):
    cache_data = st.cache_data
else:
    cache_data = st.cache


# ----------------------------- reading the CSV -------------------------------
def read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ["utf-8-sig", "cp1252", "latin-1"]:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sniff_delimiter(text):
    lines = text.splitlines()[:60]
    best, best_score = ",", -1.0
    for d in [";", ",", "\t", "|"]:
        counts = []
        for row in csv.reader(lines, delimiter=d):
            if len(row) > 1:
                counts.append(len(row))
        if len(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 3:
            continue
        score = counts.count(mode) / len(lines) + mode / 1000.0
        if score > best_score:
            best, best_score = d, score
    return best


def is_unit_in_name(name):
    for m in re.finditer(r"[\(\[]([^\)\]]*)[\)\]]", name):
        if is_unit(m.group(1)):
            return True
    return False


@cache_data(show_spinner=False)
def load_table(path, mtime):
    text = read_text(path)
    delim = sniff_delimiter(text)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append(row)

    h = 0
    for r in range(min(20, len(rows))):
        has_code, has_name = False, False
        for cell in rows[r]:
            low = cell.strip().lower()
            if re.search(r"^kode|^code", low):
                has_code = True
            if re.search(r"nama|^name|^food", low):
                has_name = True
        if has_code and has_name:
            h = r
            break

    names = []
    seen = {}
    for cell in rows[h]:
        name = re.sub(r"\s+", " ", cell).strip() or "unnamed"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        names.append(name)
    width = len(names)

    start = h + 1
    if start < len(rows):
        unit_cells = 0
        numeric_cells = 0
        for cell in rows[start]:
            if is_unit(cell):
                unit_cells += 1
            elif re.match(r"^-?\d+([.,]\d+)?$", cell.strip()):
                numeric_cells += 1
        if unit_cells >= 3 and numeric_cells == 0:
            for j in range(min(width, len(rows[start]))):
                cell = rows[start][j]
                if is_unit(cell) and not is_unit_in_name(names[j]):
                    names[j] = f"{names[j]} ({clean_unit(cell)})"
            start += 1

    body = []
    for row in rows[start:]:
        cells = list(row[:width])
        while len(cells) < width:
            cells.append("")
        filled = False
        for c in cells:
            if c.strip() != "":
                filled = True
                break
        if filled:
            body.append(cells)
    return pd.DataFrame(body, columns=names)


# ----------------------------- preparing the data ----------------------------
def name_key(col):
    return re.sub(r"[_\s]+", " ", str(col)).strip().lower()


def find_col(columns, pattern, skip=()):
    for c in columns:
        if c in skip:
            continue
        if re.search(pattern, name_key(c)):
            return c
    return None


def to_numeric(series):
    s = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    s = s.replace({"-": np.nan, "–": np.nan, "": np.nan, "nan": np.nan, "NaN": np.nan,
                   "None": np.nan, "tr": "0", "Tr": "0"})
    return pd.to_numeric(s, errors="coerce")


def is_unit(token):
    t = token.strip().strip("()[]").strip().lower().replace(" ", "")
    return t != "" and re.match(UNIT_TOKEN, t) is not None


def clean_unit(token):
    t = token.strip().strip("()[]").strip().lower().replace(" ", "")
    if t in ("kal", "kkal", "kcal"):
        return "kcal"
    if t in ("µg", "μg", "ug", "mcg"):
        return "mcg"
    if t in ("gr", "gram", "g"):
        return "g"
    return token.strip().strip("()[]").strip()


def unit_of(col):
    # 1) a unit in brackets, e.g. "Energi (Kal)"; element symbols such as "(Ca)" are skipped
    for m in re.finditer(r"[\(\[]([^\)\]]*)[\)\]]", col):
        if is_unit(m.group(1)):
            return clean_unit(m.group(1))
    # 2) a trailing unit, e.g. "protein_g" or "Natrium mg"
    m = re.search(r"[\s_,/]+(g|mg|mcg|µg|ug|kal|kkal|kcal)$", col.strip(), flags=re.IGNORECASE)
    if m is not None:
        return clean_unit(m.group(1))
    # 3) the standard TKPI unit for this nutrient
    low = name_key(col)
    for pat, unit in DEFAULT_UNITS:
        if re.search(pat, low):
            return unit
    return ""


def label_of(col):
    out = col
    for m in re.finditer(r"\s*[\(\[]([^\)\]]*)[\)\]]", col):
        if is_unit(m.group(1)):
            out = out.replace(m.group(0), "")
    out = re.sub(r"[\s_,/]+(g|mg|mcg|µg|ug|kal|kkal|kcal)$", "", out.strip(), flags=re.IGNORECASE)
    out = out.replace("_", " ").strip()
    key = out.lower()
    if key in DISPLAY:
        return DISPLAY[key]
    if out == out.lower():
        return out[:1].upper() + out[1:]
    return out


def category_of(col):
    low = name_key(col)
    for cat, pat in CATEGORIES:
        if re.search(pat, low):
            return cat
    return "Other"


@cache_data(show_spinner=False)
def prepare(df):
    code_col = find_col(df.columns, r"^kode|^code")
    type_col = find_col(df.columns, TYPE_COL, skip=(code_col,))
    name_col = find_col(df.columns, r"nama|^name|^food", skip=(code_col, type_col))
    bdd_col = find_col(df.columns, r"bdd|edible")

    values = {}
    for c in df.columns:
        if c == code_col or c == name_col or c == bdd_col or c == type_col:
            continue
        if re.search(SKIP_COL, name_key(c)):
            continue
        v = to_numeric(df[c])
        if v.notna().mean() >= 0.2:
            values[str(c).strip()] = v.clip(lower=0)
    X = pd.DataFrame(values)

    bdd_all = to_numeric(df[bdd_col]) if bdd_col is not None else pd.Series([np.nan] * len(df))
    rows = []
    for r in range(len(df)):
        code = str(df[code_col].iloc[r]).strip().upper() if code_col is not None else ""
        name = str(df[name_col].iloc[r]).strip() if name_col is not None else f"Food {r + 1}"
        m = re.match(r"^([A-Z])([RP])?\d", code)
        group = m.group(1) if m else "?"
        form = m.group(2) if (m and m.group(2)) else ""
        if type_col is not None:
            ftype = str(df[type_col].iloc[r]).strip()
            if ftype == "" or ftype.lower() in ("nan", "none", "-"):
                ftype = NO_TYPE
        else:
            ftype = FORM_NAMES.get(form, NO_TYPE)
        tag = code if code else f"#{r + 1}"
        rows.append({"code": code, "name": name or code, "group": group, "form": form, "ftype": ftype,
                     "bdd": bdd_all.iloc[r], "label": f"{name or code} ({tag})"})
    info = pd.DataFrame(rows)
    keep = X.notna().any(axis=1).values
    return info[keep].reset_index(drop=True), X[keep].reset_index(drop=True)


def find_file(folder, name):
    # GitHub and Streamlit Cloud are case-sensitive, so match "IMG1.JPG" as well as "img1.jpg"
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return os.path.join(folder, f)
    return None


@cache_data(show_spinner=False)
def image_data_uri(path, mtime, max_width=800):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")   # keep phone photos upright
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def fmt_pct(v):
    return f"{float(v):g}"


def fmt(v):
    if v is None or pd.isna(v):
        return "not reported"
    if v >= 100:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


# ----------------------------- plot ------------------------------------------
def share_bar_html(parts, title, name_min=0.3):
    # parts: list of (short name, value, colour, legend text); returns one stacked bar as HTML
    total = 0.0
    for name, value, color, legend_text in parts:
        total += value
    segs = ""
    keys = ""
    for name, value, color, legend_text in parts:
        share = value / total
        pct = f"{share * 100:.0f}%"
        if share >= name_min:
            text = f'<span class="tkpi-name">{html.escape(name)}&nbsp;</span>{pct}'
        elif share >= 0.08:
            text = pct
        else:
            text = ""
        segs += (f'<div class="tkpi-seg" style="width:{share * 100:.3f}%;background:{color}" '
                 f'title="{html.escape(legend_text)}">{text}</div>')
        keys += (f'<span class="tkpi-key"><span class="tkpi-swatch" style="background:{color}"></span>'
                 f'{html.escape(legend_text)}</span>')
    return (f'<div class="tkpi-bar"><div class="tkpi-bar-title">{html.escape(title)}</div>'
            f'<div class="tkpi-track">{segs}</div><div class="tkpi-legend">{keys}</div></div>')


def to_mg(value, unit):
    u = unit.lower()
    if u == "g":
        return value * 1000.0
    if u in ("mcg", "µg", "ug"):
        return value / 1000.0
    return value


def mineral_color(col, k):
    low = name_key(col)
    for pat, color in MINERAL_COLORS:
        if re.search(pat, low):
            return color
    return EXTRA_COLORS[k % len(EXTRA_COLORS)]


# ============================== app ==========================================
st.markdown(APP_CSS, unsafe_allow_html=True)
here = os.path.dirname(os.path.abspath(__file__))
hero = ""
for img_name in HEADER_IMAGES:
    img_path = find_file(here, img_name)
    if img_path is not None:
        hero += f'<img src="{image_data_uri(img_path, os.path.getmtime(img_path))}" alt="{html.escape(img_name)}">'
if hero:
    st.markdown(f'<div class="tkpi-hero">{hero}</div>', unsafe_allow_html=True)
st.title("Tabel Komposisi Pangan Indonesia (TKPI) nutrient lookup")
with st.expander("About this app", expanded=False):
    st.caption(DISCLAIMER)
    st.caption("Pick a food from the dropdown and set the portion. Values come from Tabel Komposisi "
               "Pangan Indonesia, per 100 g of edible portion (BDD), scaled to your portion.")

path = os.path.join(here, DATA_FILE)
if not os.path.exists(path):
    st.error(f"{DATA_FILE} was not found. Add your TKPI table to the repository root, next to app.py, "
             f"with the file name {DATA_FILE}.")
    st.stop()

df_raw = load_table(path, os.path.getmtime(path))
info, X = prepare(df_raw)
if len(info) == 0 or X.shape[1] == 0:
    st.error(f"No nutrient columns were found in {DATA_FILE}. Check that one row holds column names "
             "such as Kode, Nama, Energi and Protein.")
    st.stop()
cols = list(X.columns)

# ----------------------------- dropdowns -------------------------------------
present = sorted(set(info["group"]))
group_options = [ALL_GROUPS]
for g in present:
    if g in GROUP_NAMES:
        group_options.append(f"{g}: {GROUP_NAMES[g]}")
    elif g != "?":
        group_options.append(g)
if "?" in present:
    group_options.append("Uncoded")
type_values = sorted(set(info["ftype"]))
type_options = [ALL_TYPES]
for t in ["Mentah/segar", "Olahan"]:
    if t in type_values:
        type_options.append(t)
for t in type_values:
    if t not in type_options and t != NO_TYPE:
        type_options.append(t)
if NO_TYPE in type_values:
    type_options.append(NO_TYPE)

f1, f2 = st.columns(2)
with f1:
    group_pick = st.selectbox("Food group", group_options)
with f2:
    type_pick = st.selectbox("Food type (tipe pangan)", type_options)

letter = None
if group_pick != ALL_GROUPS:
    letter = "?" if group_pick == "Uncoded" else group_pick.split(":")[0]
idx_pool = []
for i in range(len(info)):
    if letter is not None and info["group"][i] != letter:
        continue
    if type_pick != ALL_TYPES and info["ftype"][i] != type_pick:
        continue
    idx_pool.append(i)
pool_labels = []
for i in idx_pool:
    pool_labels.append(info["label"][i])
food_options = [CHOOSE]
for lab in pool_labels:
    food_options.append(lab)

# second row: food (left) and portion (right); the BDD checkbox appears under the portion
c1, c2 = st.columns(2)
with c1:
    food_pick = st.selectbox("Food (click, then type to search)", food_options, index=0)
with c2:
    grams = st.number_input("Portion (g)", min_value=1.0, max_value=2000.0, value=100.0, step=10.0)

if food_pick == CHOOSE:
    if len(pool_labels) == 0:
        st.info("No foods match this group and type. Change one of the filters.")
    else:
        st.info(f"Choose a food from the dropdown. {len(pool_labels):,} foods in this list.")
    st.caption(f"Data: {DATA_FILE}, {len(info):,} foods, {len(cols)} nutrient columns.")
    st.stop()

i0 = idx_pool[pool_labels.index(food_pick)]
bdd = info["bdd"][i0]
use_bdd = False
with c2:
    if not pd.isna(bdd) and bdd < 100:
        use_bdd = st.checkbox(f"Weight includes inedible parts (BDD {fmt_pct(bdd)}%)", value=False,
                              help="Tick this if the weight is for the whole food as bought, "
                                   "for example a banana with its peel.")
edible = grams * bdd / 100 if use_bdd else grams
factor = edible / 100.0

# ----------------------------- results ---------------------------------------
meta = info["code"][i0]
if info["group"][i0] in GROUP_NAMES:
    meta += f", {GROUP_NAMES[info['group'][i0]]}"
if info["ftype"][i0] != NO_TYPE:
    meta += f", {info['ftype'][i0]}"
st.subheader(info["name"][i0])
# with st.expander("Food details and edible portion (BDD)", expanded=False):
#     st.caption(f"{meta}. Showing {edible:.0f} g edible portion.")
#     if pd.isna(bdd):
#         st.markdown("**Edible portion (BDD):** not reported in TKPI for this food")
#     elif bdd >= 100:
#         st.markdown("**Edible portion (BDD):** 100%, the whole food as bought is edible")
#     else:
#         st.markdown(f"**Edible portion (BDD):** {fmt_pct(bdd)}% of the food as bought is edible")
#         if use_bdd:
#             st.caption(f"{grams:.0f} g as bought × {fmt_pct(bdd)}% = {edible:.0f} g edible. The nutrients below "
#                        "are for the edible part.")
#         else:
#             st.caption(f"If {grams:.0f} g is the weight as bought (with peel, bones or seeds), tick the box "
#                        f"above to use {grams * bdd / 100:.0f} g edible instead.")

key_cols = {}
for name, pat in KEY_MACROS:
    key_cols[name] = find_col(cols, pat)

# summary numbers in a 3 by 2 grid: Energy, Protein, Fat / Carbohydrate, Fiber, BDD
summary = []
for k in range(len(KEY_MACROS)):
    name = KEY_MACROS[k][0]
    col = key_cols[name]
    unit = MACRO_UNITS[name]
    if col is not None and unit_of(col) not in ("", "kcal"):
        unit = unit_of(col)
    value = "–"
    if col is not None and not pd.isna(X[col][i0]):
        value = fmt(X[col][i0] * factor)
    summary.append((f"{name} [{unit}]", value))
summary.append(("BDD [%]", "–" if pd.isna(bdd) else fmt_pct(bdd)))
grid = '<div class="tkpi-grid">'
for label, value in summary:
    grid += (f'<div class="tkpi-cell"><div class="tkpi-label">{html.escape(label)}</div>'
             f'<div class="tkpi-value">{html.escape(value)}</div></div>')
grid += "</div>"
st.markdown(grid, unsafe_allow_html=True)

# energy bar: protein, fat, carbohydrate and fiber
p_col, f_col, c_col, fb_col = key_cols["Protein"], key_cols["Fat"], key_cols["Carbohydrate"], key_cols["Fiber"]
if p_col is not None and f_col is not None and c_col is not None:
    p, f, c = X[p_col][i0], X[f_col][i0], X[c_col][i0]
    fb = X[fb_col][i0] if fb_col is not None else np.nan
    if not (pd.isna(p) or pd.isna(f) or pd.isna(c)):
        has_fiber = not pd.isna(fb)
        carb_kcal = 4 * max(c - fb, 0.0) if has_fiber else 4 * c
        e_parts = [("Protein", 4 * p, SPLIT_COLORS[0]), ("Fat", 9 * f, SPLIT_COLORS[1]),
                   ("Carbohydrate", carb_kcal, SPLIT_COLORS[2])]
        if has_fiber:
            e_parts.append(("Fiber", 2 * fb, SPLIT_COLORS[3]))
        e_total = 0.0
        for name, kc, color in e_parts:
            e_total += kc
        if e_total > 0:
            bar = []
            for name, kc, color in e_parts:
                bar.append((name, kc, color, f"{name} {100 * kc / e_total:.0f}%"))
            st.markdown('<div class="tkpi-row">' + share_bar_html(bar, "Share of energy from each macronutrient",
                                                                   name_min=0.22) + "</div>",
                        unsafe_allow_html=True)
            # show the calculation for this portion so it can be checked against the Energy figure
            pp, ff, cc = p * factor, f * factor, c * factor
            if has_fiber:
                fbp = fb * factor
                calc = (f"4 × {fmt(pp)} + 9 × {fmt(ff)} + 4 × ({fmt(cc)} − {fmt(fbp)}) + 2 × {fmt(fbp)} "
                        f"= {fmt(e_total * factor)} kcal")
                rule = ("The bar counts 4 kcal per gram of protein, 9 per gram of fat, 4 per gram of carbohydrate "
                        "and 2 per gram of fiber. TKPI carbohydrate includes fiber, so fiber is taken out of "
                        "carbohydrate first.")
            else:
                calc = f"4 × {fmt(pp)} + 9 × {fmt(ff)} + 4 × {fmt(cc)} = {fmt(e_total * factor)} kcal"
                rule = ("The bar counts 4 kcal per gram of protein, 9 per gram of fat and 4 per gram of "
                        "carbohydrate. Fiber is not reported for this food.")
            msg = rule + f" For {edible:.0f} g: {calc}"
            e_col = key_cols["Energy"]
            if e_col is not None and not pd.isna(X[e_col][i0]):
                tkpi_e = X[e_col][i0] * factor
                msg += f"; TKPI lists {fmt(tkpi_e)} kcal."
                if tkpi_e > 0 and abs(e_total * factor - tkpi_e) / tkpi_e > 0.10:
                    msg += (" The two differ because TKPI's energy value is calculated its own way, for example "
                            "counting all carbohydrate, fiber included, at 4 kcal per gram.")
            else:
                msg += "."
            with st.expander("How the energy split is calculated", expanded=False):
                st.write(msg)

# mineral bars: major minerals and trace minerals, each as share of its total mg
major_parts = []
trace_parts = []
m_missing = []
k = 0
for col in cols:
    if category_of(col) != "Minerals":
        continue
    v = X[col][i0]
    short = re.sub(r"\s*\([^)]*\)", "", label_of(col)).strip()
    if pd.isna(v):
        m_missing.append(short)
        continue
    mg = to_mg(v * factor, unit_of(col))
    if mg <= 0:
        continue
    part = (short, mg, mineral_color(col, k), f"{short} {fmt(mg)} mg")
    k += 1
    if re.search(TRACE_MINERAL, name_key(col)):
        trace_parts.append(part)
    else:
        major_parts.append(part)
if len(major_parts) > 0 or len(trace_parts) > 0:
    row = '<div class="tkpi-row">'
    if len(major_parts) > 0:
        row += share_bar_html(major_parts, f"Major minerals in {edible:.0f} g (share of mg)")
    if len(trace_parts) > 0:
        row += share_bar_html(trace_parts, f"Trace minerals in {edible:.0f} g (share of mg)")
    row += "</div>"
    st.markdown(row, unsafe_allow_html=True)
if len(major_parts) > 0 or len(trace_parts) > 0:
    note = ("Trace minerals (iron, zinc, copper) have their own bar because they occur in amounts "
            "far smaller than calcium, phosphorus, sodium and potassium.")
    if len(m_missing) > 0:
        note += " Not reported for this food: " + ", ".join(m_missing) + "."
    with st.expander("About the mineral bars", expanded=False):
        st.caption(note)
elif len(m_missing) > 0:
    st.caption("No mineral values are reported in TKPI for this food, so there are no mineral bars.")

for cat in ["Macronutrients", "Minerals", "Vitamins", "Other"]:
    rows = []
    for col in cols:
        if category_of(col) != cat:
            continue
        v = X[col][i0]
        rows.append({"Nutrient": label_of(col), "Unit": unit_of(col),
                     "Per 100 g": fmt(v), f"Per {edible:.0f} g": fmt(None if pd.isna(v) else v * factor)})
    if len(rows) == 0:
        continue
    st.markdown(f"**{cat}**")
    st.dataframe(pd.DataFrame(rows).set_index("Nutrient"))

bdd_known = int(info["bdd"].notna().sum())
st.caption(f"Data: {DATA_FILE}, {len(info):,} foods, {len(cols)} nutrient columns, BDD for "
           f"{bdd_known:,} foods. Values marked not reported are missing in TKPI for this food.")

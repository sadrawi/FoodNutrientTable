# =============================================================================
# TKPI nutrient lookup — pick a food from the dropdown, get its nutrients
# Data: tkpi.csv in the same folder as this file (repository root)
# Run locally:  streamlit run app.py   (works on Streamlit 1.12 and newer)
# =============================================================================

import csv
import io
import os
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import streamlit as st

st.set_page_config(page_title="TKPI nutrient lookup", page_icon="🍚", layout="centered")

DATA_FILE = "tkpi.csv"
CHOOSE = "Choose a food…"
GROUP_NAMES = {"A": "Serealia", "B": "Umbi berpati", "C": "Kacang/biji/bean",
               "D": "Sayuran", "E": "Buah", "F": "Daging/unggas", "G": "Ikan/kerang/udang",
               "H": "Telur", "J": "Susu", "K": "Lemak/minyak", "M": "Gula/sirup/konfeksioneri",
               "N": "Bumbu"}
FORM_NAMES = {"R": "Mentah/segar", "P": "Olahan"}
TYPE_COL = r"^tipe|\btipe\b|\btype\b|^jenis|\bjenis\b"
ALL_GROUPS = "All groups"
ALL_TYPES = "All types"
NO_TYPE = "Unspecified"
SKIP_COL = r"^no\.?$|^nomor|^id$|^unnamed|sumber|source|kode|code|nama|name|kelompok|group|tipe|\btype\b|jenis"
CATEGORIES = [
    ("Macronutrients", r"energi|energy|kkal|protein|lemak|\bfat\b|lipid|karbo|carb|\bkh\b|serat|fib|^air\b|water|^abu\b|\bash\b"),
    ("Minerals", r"kalsium|calcium|fosfor|phosph|besi|\biron\b|natrium|sodium|kalium|potass|tembaga|copper|seng|zinc"),
    ("Vitamins", r"retinol|karoten|carot|b[-_ ]?kar|β|thiamin|tiamin|ribofla|niasin|niacin|vit|ascorb"),
]
KEY_MACROS = [("Energy", r"energi|energy|kkal"), ("Protein", r"protein"), ("Fat", r"lemak|\bfat\b|lipid"),
              ("Carbohydrate", r"karbo|carb|\bkh\b"), ("Fiber", r"serat|fib")]
#SPLIT_COLORS = ["#27F587", "#FF5E6C", "#FFE161"]
SPLIT_COLORS = ["#FF5E6C", "#27F587", "#FFE161"]
SPLIT_COLORS = ["#27F587", "#FF5E6C", "#FFE161"]
UNIT_TOKEN = r"^(g|gr|gram|mg|mcg|µg|μg|ug|kal|kkal|kcal|kj|%|iu)$"
# standard TKPI units per 100 g BDD, used when the file does not state a unit
DEFAULT_UNITS = [
    (r"energi|energy|kkal|kalori", "kcal"),
    (r"retinol|karoten|carot|b[-_ ]?kar|β", "mcg"),
    (r"^air\b|water|protein|lemak|\bfat\b|lipid|karbo|carb|\bkh\b|serat|fib|^abu\b|\bash\b", "g"),
    (r"kalsium|calcium|fosfor|phosph|besi|\biron\b|natrium|sodium|kalium|potass|tembaga|copper|seng|zinc"
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


def fmt(v):
    if v is None or pd.isna(v):
        return "not reported"
    if v >= 100:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


# ----------------------------- plot ------------------------------------------
def plot_energy_split(protein, fat, carb):
    kcal = [4 * protein, 9 * fat, 4 * carb]
    names = ["Protein", "Fat", "Carbohydrate"]
    total = kcal[0] + kcal[1] + kcal[2]
    plt.figure(figsize=(6.5, 1.5))
    left = 0.0
    for k in range(3):
        share = kcal[k] / total
        plt.barh([0], [share], left=left, color=SPLIT_COLORS[k], height=0.6,
                 label=f"{names[k]} {share * 100:.0f}%")
        if share > 0.12:
            plt.text(left + share / 2, 0, f"{share * 100:.0f}%", ha="center", va="center",
                     color="white", fontsize=10, fontweight="bold")
        left += share
    plt.xlim(0, 1)
    plt.axis("off")
    plt.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 0.05), frameon=False, fontsize=9)
    plt.title("Share of energy from each macronutrient (4, 9, 4 kcal per g)", fontsize=9, loc="left")
    plt.tight_layout()


# ============================== app ==========================================
st.title("TKPI nutrient lookup")
st.caption("Pick a food from the dropdown and set the portion.")
st.caption("Values come from Tabel Komposisi Pangan Indonesia, per 100 g of edible portion (BDD), scaled to your portion.")
st.caption("Supported by Claude Opus 5.5 (Antropic).")

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DATA_FILE)
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

food_pick = st.selectbox("Food (click, then type to search)", food_options, index=0)

c1, c2 = st.columns([1, 1])
with c1:
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
        use_bdd = st.checkbox(f"Weight includes inedible parts (BDD {bdd:.0f}%)", value=False,
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
st.caption(f"{meta}. Showing {edible:.0f} g edible portion.")

key_cols = {}
for name, pat in KEY_MACROS:
    key_cols[name] = find_col(cols, pat)

metric_cols = st.columns(len(KEY_MACROS))
for k in range(len(KEY_MACROS)):
    name = KEY_MACROS[k][0]
    col = key_cols[name]
    with metric_cols[k]:
        if col is None:
            st.metric(name, "–")
        else:
            v = X[col][i0]
            st.metric(name, "–" if pd.isna(v) else f"{fmt(v * factor)} {unit_of(col)}")

with st.expander("Columns used for the numbers above"):
    for name, pat in KEY_MACROS:
        used = key_cols[name]
        st.write(f"{name}: {used if used is not None else 'no matching column found in ' + DATA_FILE}")

p_col, f_col, c_col = key_cols["Protein"], key_cols["Fat"], key_cols["Carbohydrate"]
if p_col is not None and f_col is not None and c_col is not None:
    p, f, c = X[p_col][i0], X[f_col][i0], X[c_col][i0]
    if not (pd.isna(p) or pd.isna(f) or pd.isna(c)) and (4 * p + 9 * f + 4 * c) > 0:
        plot_energy_split(p, f, c)
        st.pyplot(plt.gcf())
        plt.close()

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

st.caption(f"Data: {DATA_FILE}, {len(info):,} foods, {len(cols)} nutrient columns. "
           "Values marked not reported are missing in TKPI for this food.")

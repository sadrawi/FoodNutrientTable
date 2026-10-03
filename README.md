# TKPI nutrient lookup

Streamlit app: pick a food from Tabel Komposisi Pangan Indonesia (TKPI) in a dropdown and see its
energy, macronutrients, minerals and vitamins for any portion size.

## Files

```
app.py                  the app
requirements.txt        Python packages for Streamlit Cloud
tkpi.csv                your TKPI table (add this file)
.streamlit/config.toml  colour theme (optional)
```

## tkpi.csv format

One row per food, with a code column (Kode, e.g. AR001), a name column (e.g. Nama Bahan Makanan)
and nutrient columns per 100 g edible portion, such as `Energi (Kal)`, `Protein (g)`, `Lemak (g)`,
`KH (g)`, `Serat (g)`, `Natrium (mg)`. The unit in brackets is shown in the app.

Comma or semicolon separators, comma decimals, title rows above the header and Excel (Windows)
encoding all work. `-` means not reported. A `BDD (%)` column adds the "weight as bought" option.

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Put `app.py`, `requirements.txt`, `tkpi.csv` and `.streamlit/config.toml` in the root of a GitHub repository.
2. Sign in at https://share.streamlit.io with your GitHub account.
3. Create an app: choose the repository, branch `main` and main file `app.py`, then deploy.

Every push to `main` redeploys the app, so updating `tkpi.csv` on GitHub updates the app.

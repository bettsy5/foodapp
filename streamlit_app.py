from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent
FDC_DIR = ROOT / "FoodData_Central_branded_food_csv_2025-12-18"
FDC_BRANDED = FDC_DIR / "branded_food.csv"
FDC_FOOD = FDC_DIR / "food.csv"
FDC_NUTRIENT = FDC_DIR / "food_nutrient.csv"
OFF_PRODUCTS = ROOT / "en.openfoodfacts.org.products.csv"
CACHE_DIR = ROOT / ".food_grader_cache"
DB_PATH = CACHE_DIR / "food_grader.sqlite"

PRODUCT_COLUMNS = [
    "fdc_id",
    "brand_owner",
    "brand_name",
    "subbrand_name",
    "gtin_upc",
    "ingredients",
    "serving_size",
    "serving_size_unit",
    "household_serving_fulltext",
    "branded_food_category",
    "market_country",
    "modified_date",
]

NUTRIENTS = {
    1008: ("calories", "Energy", "kcal"),
    1003: ("protein_g", "Protein", "g"),
    1004: ("fat_g", "Total fat", "g"),
    1005: ("carbs_g", "Carbohydrate", "g"),
    1079: ("fiber_g", "Fiber", "g"),
    2000: ("sugars_g", "Total sugars", "g"),
    1093: ("sodium_mg", "Sodium", "mg"),
    1258: ("sat_fat_g", "Saturated fat", "g"),
    1257: ("trans_fat_g", "Trans fat", "g"),
    1253: ("cholesterol_mg", "Cholesterol", "mg"),
    1087: ("calcium_mg", "Calcium", "mg"),
    1089: ("iron_mg", "Iron", "mg"),
    1092: ("potassium_mg", "Potassium", "mg"),
}

HARMFUL_TERMS = {
    "artificial color": 7,
    "artificial flavor": 5,
    "high fructose corn syrup": 8,
    "corn syrup": 4,
    "hydrogenated": 10,
    "partially hydrogenated": 15,
    "sodium nitrite": 12,
    "sodium nitrate": 10,
    "potassium bromate": 15,
    "bha": 10,
    "bht": 8,
    "tbhq": 10,
    "propylene glycol": 8,
    "polysorbate": 6,
    "carrageenan": 5,
    "monosodium glutamate": 5,
    "msg": 5,
    "aspartame": 5,
    "sucralose": 4,
    "acesulfame potassium": 5,
    "red 40": 8,
    "yellow 5": 8,
    "yellow 6": 8,
    "blue 1": 6,
    "blue 2": 6,
    "modified food starch": 4,
}

POSITIVE_TERMS = {
    "whole grain": 5,
    "whole wheat": 5,
    "oats": 3,
    "beans": 3,
    "lentils": 3,
    "chickpea": 3,
    "vegetable": 2,
    "fruit": 2,
    "nuts": 3,
    "seeds": 3,
    "olive oil": 3,
    "organic": 2,
}


@dataclass
class Product:
    source: str
    fdc_id: int | None
    name: str
    brand: str
    barcode: str
    category: str
    ingredients: str
    serving: str
    modified_date: str
    extra: dict


def db() -> sqlite3.Connection:
    CACHE_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "select name from sqlite_master where type in ('table','virtual table') and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def fdc_cache_ready() -> bool:
    if not DB_PATH.exists():
        return False
    with db() as con:
        return table_exists(con, "products_fts") and table_exists(con, "nutrients")


def make_match_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
    return " ".join(f"{token}*" for token in tokens[:8])


def normalize_barcode(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return re.sub(r"\D", "", text)


def read_csv_chunks(path: Path, usecols: list[str], chunksize: int) -> Iterable[pd.DataFrame]:
    return pd.read_csv(
        path,
        usecols=usecols,
        chunksize=chunksize,
        dtype=str,
        low_memory=False,
        quoting=csv.QUOTE_MINIMAL,
    )


def build_fdc_cache(max_product_rows: int | None = None, max_nutrient_rows: int | None = None) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    progress = st.progress(0, text="Creating product search index")
    status = st.empty()

    with db() as con:
        con.execute("pragma journal_mode = wal")
        con.execute(
            """
            create table products (
                fdc_id integer primary key,
                description text,
                brand_owner text,
                brand_name text,
                subbrand_name text,
                gtin_upc text,
                ingredients text,
                serving_size real,
                serving_size_unit text,
                household_serving_fulltext text,
                branded_food_category text,
                market_country text,
                modified_date text
            )
            """
        )
        con.execute(
            """
            create virtual table products_fts using fts5(
                fdc_id unindexed,
                search_text,
                tokenize = 'unicode61'
            )
            """
        )

        rows_seen = 0
        for i, chunk in enumerate(read_csv_chunks(FDC_BRANDED, PRODUCT_COLUMNS, 75_000), start=1):
            if max_product_rows:
                chunk = chunk.head(max(0, max_product_rows - rows_seen))
            if chunk.empty:
                break

            chunk["fdc_id"] = pd.to_numeric(chunk["fdc_id"], errors="coerce")
            chunk["serving_size"] = pd.to_numeric(chunk["serving_size"], errors="coerce")
            chunk = chunk.dropna(subset=["fdc_id"])
            chunk["fdc_id"] = chunk["fdc_id"].astype("int64")
            chunk["gtin_upc"] = chunk["gtin_upc"].map(normalize_barcode)
            for col in PRODUCT_COLUMNS:
                if col not in ("fdc_id", "serving_size"):
                    chunk[col] = chunk[col].fillna("").astype(str)

            chunk.insert(1, "description", "")
            chunk.to_sql("products", con, if_exists="append", index=False)
            rows_seen += len(chunk)
            status.caption(f"Indexed {rows_seen:,} branded food rows")
            progress.progress(min(45, i), text="Creating product search index")
            if max_product_rows and rows_seen >= max_product_rows:
                break

        con.execute("create index idx_products_gtin on products(gtin_upc)")

        progress.progress(46, text="Adding product names")
        con.execute("create table food_names (fdc_id integer primary key, description text)")
        food_rows = 0
        for chunk in pd.read_csv(
            FDC_FOOD,
            usecols=["fdc_id", "description"],
            chunksize=100_000,
            dtype={"fdc_id": "int64", "description": "string"},
            low_memory=False,
        ):
            if max_product_rows:
                chunk = chunk.head(max(0, max_product_rows - food_rows))
            if chunk.empty:
                break
            chunk["description"] = chunk["description"].fillna("").astype(str)
            chunk.to_sql("food_names", con, if_exists="append", index=False)
            food_rows += len(chunk)
            status.caption(f"Loaded {food_rows:,} product names")
            if max_product_rows and food_rows >= max_product_rows:
                break

        con.execute(
            """
            update products
            set description = coalesce(
                (select description from food_names where food_names.fdc_id = products.fdc_id),
                ''
            )
            """
        )
        con.execute(
            """
            insert into products_fts (fdc_id, search_text)
            select
                fdc_id,
                coalesce(description, '') || ' ' ||
                coalesce(brand_owner, '') || ' ' ||
                coalesce(brand_name, '') || ' ' ||
                coalesce(subbrand_name, '') || ' ' ||
                coalesce(branded_food_category, '') || ' ' ||
                substr(coalesce(ingredients, ''), 1, 800)
            from products
            """
        )

        progress.progress(50, text="Indexing selected nutrients")
        con.execute(
            """
            create table nutrients (
                fdc_id integer,
                nutrient_id integer,
                amount real
            )
            """
        )
        nutrient_ids = set(NUTRIENTS)
        rows_seen = 0
        kept = 0
        for i, chunk in enumerate(
            pd.read_csv(
                FDC_NUTRIENT,
                usecols=["fdc_id", "nutrient_id", "amount"],
                chunksize=200_000,
                dtype={"fdc_id": "int64", "nutrient_id": "int64", "amount": "float64"},
                low_memory=False,
            ),
            start=1,
        ):
            if max_nutrient_rows:
                chunk = chunk.head(max(0, max_nutrient_rows - rows_seen))
            rows_seen += len(chunk)
            chunk = chunk[chunk["nutrient_id"].isin(nutrient_ids)].dropna(subset=["amount"])
            kept += len(chunk)
            chunk.to_sql("nutrients", con, if_exists="append", index=False)
            status.caption(f"Scanned {rows_seen:,} nutrient rows and kept {kept:,}")
            progress.progress(min(95, 50 + i), text="Indexing selected nutrients")
            if max_nutrient_rows and rows_seen >= max_nutrient_rows:
                break
        con.execute("create index idx_nutrients_fdc on nutrients(fdc_id)")

    progress.progress(100, text="FDC cache ready")


@st.cache_data(show_spinner=False)
def search_fdc(query: str, limit: int = 25) -> list[dict]:
    if not query.strip() or not fdc_cache_ready():
        return []
    match_query = make_match_query(query)
    with db() as con:
        try:
            rows = con.execute(
                """
                select p.*, bm25(products_fts) as rank
                from products_fts
                join products p on p.fdc_id = products_fts.fdc_id
                where products_fts match ?
                order by rank
                limit ?
                """,
                (match_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = con.execute(
                """
                select *
                from products
                where brand_owner like ? or brand_name like ? or branded_food_category like ? or ingredients like ?
                limit ?
                """,
                tuple([f"%{query}%"] * 4) + (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


@st.cache_data(show_spinner=False)
def get_fdc_nutrients(fdc_id: int) -> dict[str, float]:
    with db() as con:
        rows = con.execute(
            "select nutrient_id, amount from nutrients where fdc_id = ?",
            (int(fdc_id),),
        ).fetchall()
    values = {}
    for row in rows:
        key = NUTRIENTS.get(int(row["nutrient_id"]), ("", "", ""))[0]
        if key:
            values[key] = float(row["amount"])
    return values


@st.cache_data(show_spinner=False)
def find_off_by_barcode(barcode: str) -> dict | None:
    if not OFF_PRODUCTS.exists() or not barcode:
        return None

    wanted = normalize_barcode(barcode).lstrip("0")
    usecols = [
        "code",
        "product_name",
        "brands",
        "categories_en",
        "ingredients_text",
        "labels_en",
        "additives_n",
        "nutriscore_score",
        "nutriscore_grade",
        "nova_group",
        "environmental_score_score",
        "environmental_score_grade",
        "energy-kcal_100g",
        "fat_100g",
        "saturated-fat_100g",
        "carbohydrates_100g",
        "sugars_100g",
        "fiber_100g",
        "proteins_100g",
        "salt_100g",
        "sodium_100g",
        "image_small_url",
    ]
    for chunk in pd.read_csv(
        OFF_PRODUCTS,
        sep="\t",
        usecols=lambda col: col in usecols,
        chunksize=100_000,
        dtype=str,
        low_memory=False,
    ):
        codes = chunk["code"].fillna("").map(lambda x: normalize_barcode(x).lstrip("0"))
        match = chunk[codes == wanted]
        if not match.empty:
            return match.iloc[0].fillna("").to_dict()
    return None


def row_to_product(row: dict) -> Product:
    brand = row.get("brand_name") or row.get("brand_owner") or ""
    category = row.get("branded_food_category") or ""
    serving = ""
    if row.get("household_serving_fulltext"):
        serving = row["household_serving_fulltext"]
    elif row.get("serving_size"):
        serving = f"{row.get('serving_size')} {row.get('serving_size_unit', '')}".strip()
    description = row.get("description") or ""
    name_parts = [description, brand]
    name = " - ".join(part for part in name_parts if part) or f"FDC {row.get('fdc_id')}"
    return Product(
        source="USDA FoodData Central",
        fdc_id=int(row["fdc_id"]),
        name=name,
        brand=brand,
        barcode=row.get("gtin_upc", ""),
        category=category,
        ingredients=row.get("ingredients", "") or "",
        serving=serving,
        modified_date=row.get("modified_date", "") or "",
        extra={},
    )


def first_match_terms(text: str, weighted_terms: dict[str, int]) -> list[str]:
    lower = text.lower()
    return [term for term in weighted_terms if term in lower]


def count_ingredients(ingredients: str) -> int:
    cleaned = re.sub(r"\([^)]*\)", "", ingredients or "")
    pieces = re.split(r",|;|\band\b", cleaned, flags=re.IGNORECASE)
    return len([piece.strip() for piece in pieces if len(piece.strip()) > 1])


def clean_score(ingredients: str, off: dict | None = None) -> tuple[int, list[str], list[str]]:
    text = ingredients or ""
    harmful = first_match_terms(text, HARMFUL_TERMS)
    positive = first_match_terms(text, POSITIVE_TERMS)
    ingredient_count = count_ingredients(text)
    penalty = sum(HARMFUL_TERMS[t] for t in harmful)
    penalty += max(0, ingredient_count - 12) * 1.5
    bonus = sum(POSITIVE_TERMS[t] for t in positive)

    if off:
        try:
            additives = float(off.get("additives_n") or 0)
            penalty += additives * 3
        except ValueError:
            pass
        try:
            nova = int(float(off.get("nova_group") or 0))
            penalty += {1: 0, 2: 6, 3: 14, 4: 24}.get(nova, 0)
        except ValueError:
            pass

    score = int(np.clip(100 - penalty + bonus, 0, 100))
    return score, harmful, positive


def macro_score(n: dict[str, float]) -> tuple[int, list[str]]:
    score = 70
    notes = []

    protein = n.get("protein_g")
    fiber = n.get("fiber_g")
    sugar = n.get("sugars_g")
    sodium = n.get("sodium_mg")
    sat = n.get("sat_fat_g")
    trans = n.get("trans_fat_g")
    calories = n.get("calories")

    if protein is not None:
        score += min(12, protein * 1.2)
        notes.append(f"Protein: {protein:g} g")
    if fiber is not None:
        score += min(12, fiber * 2)
        notes.append(f"Fiber: {fiber:g} g")
    if sugar is not None:
        score -= max(0, sugar - 5) * 1.8
        notes.append(f"Sugars: {sugar:g} g")
    if sodium is not None:
        score -= max(0, sodium - 300) / 45
        notes.append(f"Sodium: {sodium:g} mg")
    if sat is not None:
        score -= max(0, sat - 3) * 2.4
        notes.append(f"Saturated fat: {sat:g} g")
    if trans is not None and trans > 0:
        score -= min(20, trans * 12)
        notes.append(f"Trans fat: {trans:g} g")
    if calories is not None:
        if calories > 450:
            score -= (calories - 450) / 30
        notes.append(f"Calories: {calories:g} kcal")

    return int(np.clip(round(score), 0, 100)), notes


def off_to_nutrients(off: dict) -> dict[str, float]:
    mapping = {
        "energy-kcal_100g": "calories",
        "fat_100g": "fat_g",
        "saturated-fat_100g": "sat_fat_g",
        "carbohydrates_100g": "carbs_g",
        "sugars_100g": "sugars_g",
        "fiber_100g": "fiber_g",
        "proteins_100g": "protein_g",
        "sodium_100g": "sodium_mg",
    }
    out = {}
    for raw, clean in mapping.items():
        value = pd.to_numeric(off.get(raw), errors="coerce")
        if pd.notna(value):
            out[clean] = float(value * 1000 if raw == "sodium_100g" else value)
    return out


def grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def verdict(macro: int, clean: int) -> str:
    combined = round(macro * 0.6 + clean * 0.4)
    if combined >= 80:
        return "Strong everyday choice"
    if combined >= 65:
        return "Reasonable choice with tradeoffs"
    if combined >= 50:
        return "Occasional choice; read the label"
    return "High caution based on nutrition and ingredients"


def metric_card(label: str, score: int) -> None:
    st.metric(label, f"{score}/100", grade(score))
    st.progress(score / 100)


def render_product(product: Product, enrich_off: bool) -> None:
    off = find_off_by_barcode(product.barcode) if enrich_off and product.barcode else None
    nutrients = get_fdc_nutrients(product.fdc_id) if product.fdc_id else {}
    if off:
        nutrients = {**nutrients, **off_to_nutrients(off)}
        product.ingredients = off.get("ingredients_text") or product.ingredients

    macro, macro_notes = macro_score(nutrients)
    clean, harmful, positive = clean_score(product.ingredients, off)
    combined = round(macro * 0.6 + clean * 0.4)

    st.subheader(product.name)
    meta = " | ".join(
        part
        for part in [
            product.source,
            f"Barcode {product.barcode}" if product.barcode else "",
            product.serving,
            f"Updated {product.modified_date}" if product.modified_date else "",
        ]
        if part
    )
    st.caption(meta)

    if off and off.get("image_small_url"):
        st.image(off["image_small_url"], width=140)

    cols = st.columns(3)
    with cols[0]:
        metric_card("Macro/Nutrient", macro)
    with cols[1]:
        metric_card("Clean Label", clean)
    with cols[2]:
        metric_card("Overall", combined)
    st.info(verdict(macro, clean))

    st.markdown("#### Nutrition Signals")
    if nutrients:
        rows = []
        for _, (key, label, unit) in NUTRIENTS.items():
            if key in nutrients:
                rows.append({"Nutrient": label, "Amount": round(nutrients[key], 2), "Unit": unit})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.warning("No selected nutrient rows were found for this item in the current cache.")

    st.markdown("#### Ingredient Review")
    st.write(product.ingredients or "No ingredient statement available.")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Ingredient Count", count_ingredients(product.ingredients))
    col_b.metric("Flagged Terms", len(harmful))
    col_c.metric("Positive Terms", len(positive))
    if harmful:
        st.error("Flagged substances: " + ", ".join(harmful))
    if positive:
        st.success("Positive signals: " + ", ".join(positive))

    if off:
        st.markdown("#### OpenFoodFacts Enrichment")
        off_cols = st.columns(4)
        off_cols[0].metric("Nutri-Score", str(off.get("nutriscore_grade") or "NA").upper())
        off_cols[1].metric("NOVA", off.get("nova_group") or "NA")
        off_cols[2].metric("Additives", off.get("additives_n") or "NA")
        off_cols[3].metric("Eco Score", str(off.get("environmental_score_grade") or "NA").upper())
        if off.get("labels_en"):
            st.caption("Labels: " + off["labels_en"])
    elif enrich_off:
        st.caption("No matching OpenFoodFacts row was found for this barcode.")


def main() -> None:
    st.set_page_config(page_title="Food Product Grader", layout="wide")
    st.title("Food Product Grader")
    st.caption("Branded-food search with macro, nutrient, ingredient, and OpenFoodFacts label signals.")

    with st.sidebar:
        st.header("Data")
        st.write(f"FDC cache: {'ready' if fdc_cache_ready() else 'not built'}")
        sample = st.checkbox("Build smaller demo cache", value=False)
        max_product_rows = 150_000 if sample else None
        max_nutrient_rows = 1_000_000 if sample else None
        if st.button("Build or rebuild FDC cache", type="primary"):
            build_fdc_cache(max_product_rows=max_product_rows, max_nutrient_rows=max_nutrient_rows)
            st.cache_data.clear()
            st.rerun()
        st.divider()
        enrich_off = st.checkbox(
            "Enrich selected product from OpenFoodFacts",
            value=False,
            help="Looks up the selected barcode in the 13 GB OpenFoodFacts CSV. This can take time.",
        )
        st.caption("Scores are heuristic decision-support signals, not medical advice.")

    query = st.text_input("Search for a branded food product", placeholder="e.g., Campbell soup, Cheerios, protein bar")
    if not query:
        st.stop()

    if not fdc_cache_ready():
        st.warning("Build the FDC cache from the sidebar before searching.")
        st.stop()

    results = search_fdc(query)
    if not results:
        st.error("No products found. Try a shorter brand or category phrase.")
        st.stop()

    labels = []
    products = []
    for row in results:
        product = row_to_product(row)
        products.append(product)
        labels.append(
            " | ".join(
                part for part in [product.brand, product.category, product.barcode, product.serving] if part
            )
        )

    selected = st.selectbox("Select product", range(len(products)), format_func=lambda i: labels[i])
    render_product(products[selected], enrich_off=enrich_off)


if __name__ == "__main__":
    main()

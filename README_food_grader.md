# Food Product Grader

Streamlit app for grading branded food products with local USDA FoodData Central
and optional OpenFoodFacts enrichment.

## Files used

- `FoodData_Central_branded_food_csv_2025-12-18/branded_food.csv`
- `FoodData_Central_branded_food_csv_2025-12-18/food.csv`
- `FoodData_Central_branded_food_csv_2025-12-18/food_nutrient.csv`
- `en.openfoodfacts.org.products.csv` for optional barcode enrichment

## Run

```powershell
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Use the sidebar button to build the FDC cache. For a quicker demo, check
`Build smaller demo cache` before building. The full cache reads the large CSV
files once and writes a local SQLite database to `.food_grader_cache/`.

This app is intended for internal/local use because the USDA and OpenFoodFacts
CSV exports are too large for normal GitHub and Streamlit Cloud deployment.

## Scoring

- `Macro/Nutrient` uses calories, protein, fiber, sugar, sodium, saturated fat,
  trans fat, and other selected nutrients from FoodData Central.
- `Clean Label` uses ingredient-list length, flagged additive/substance terms,
  positive whole-food terms, and optional OpenFoodFacts additives/NOVA fields.
- `Overall` weights macro/nutrient score at 60% and clean-label score at 40%.

The scores are transparent heuristics intended for project decision support, not
medical or regulatory guidance.

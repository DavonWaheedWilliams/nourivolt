# NouriVanta Elite Suite

NouriVanta is a light, mobile-friendly Streamlit fitness operating system. It keeps the original account, nutrition, water, workout, measurement, goals, readiness, food-photo, barcode, export, and dashboard features. The Elite Suite adds adaptive coaching, verified food search, meal planning, program design, voice logging, wearable imports, family tools, and stronger account security.

## Required repository files

- `app.py`
- `vision_services.py`
- `elite_features.py`
- `elite_services.py`
- `requirements.txt`
- `.streamlit/config.toml`

## Core features preserved

- Account creation and sign-in
- Bcrypt password hashing
- SQLite for local use
- PostgreSQL support for deployment
- Food, water, workout, exercise-set, measurement, goal, and readiness records
- Food-photo macro estimates
- Barcode decoding and Open Food Facts lookup
- U.S. units and MM/DD/YYYY dates
- Circular dashboard progress rings
- Full data export and account deletion

## Elite features

- Adaptive 7, 14, 30, and 90-day coaching reports
- USDA FoodData Central search with editable serving scaling
- Favorites, recent foods, saved meals, meal copying, and full-day diary copying
- Nutrition Facts label photo scanning
- Macro Forecast and Fuel Gap Map
- Next-food recommendations based on remaining macros
- Workout program builder
- Supersets, circuits, warm-up sets, drop sets, AMRAP, tempo, and rest targets
- Client-side rest timer
- Progressive-overload recommendations
- Estimated one-repetition maximum and personal-record summaries
- Exercise substitutions and demonstration-search links
- Muscle recovery map and deload signal
- Seven-day meal planner
- Pantry inventory, grocery list, household servings, exclusions, and budget estimates
- Progress Center with consistency, moving averages, strength volume, and goal forecasts
- Voice food, water, and workout logging
- Wearable CSV imports and manual Apple Health, Health Connect, Garmin, and Fitbit metrics
- Recovery-to-Training Match
- Family profiles and coach notes
- Recovery-code password resets
- Failed-login lockout, automatic session timeout, and login history
- Stripe Payment Link support for future subscriptions

## Local setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Without `DATABASE_URL`, the app creates `data/nourivolt.db`.

## Streamlit Community Cloud secrets

Add secrets in App settings > Secrets. Never commit them to GitHub.

```toml
DATABASE_URL = "your Neon PostgreSQL connection string"
OPENAI_API_KEY = "your OpenAI API key"
FDC_API_KEY = "your USDA FoodData Central API key"
OPEN_FOOD_FACTS_USER_AGENT = "NouriVanta/3.0 (your-email@example.com)"
STRIPE_PAYMENT_LINK = "your optional Stripe subscription payment link"
```

`DATABASE_URL` is required for permanent deployed account data. `OPENAI_API_KEY` activates food-photo, nutrition-label, coaching, and voice intelligence. USDA search uses `FDC_API_KEY`; it falls back to USDA's limited demo key when absent. Barcode product lookup does not require an API key.

## Upgrade without losing data

Keep your current database:

- Local: retain `data/nourivolt.db`
- Streamlit deployment: retain the same Neon `DATABASE_URL`

The Elite Suite creates new tables automatically. It does not rename or delete the original tables.

## Native mobile integrations

The Wearable Bridge supports manual entry and CSV exports now. Direct Apple Health and Android Health Connect permission access requires a native iOS or Android companion application. Garmin and Fitbit live synchronization require their developer approval and OAuth credentials. The Streamlit app keeps connector-ready import workflows so these integrations can be added without changing the core database.

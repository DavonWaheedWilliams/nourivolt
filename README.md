# NouriVolt Future Vision

NouriVolt is a light, futuristic fitness and nutrition tracker built with Python and Streamlit. This build keeps the stable account, nutrition, workout, progress, goal, and export functions while adding camera nutrition tools and adaptive recovery features.

## Core features

- Create an account, sign in, and log out
- Bcrypt password hashing
- Separate private records for every user
- Persistent SQLite storage for local use
- Optional PostgreSQL storage for online deployment
- Dashboard with calories, macros, water, workouts, weight trends, and future signals
- Manual food and water logging
- Workout sessions with exercises, sets, reps, weight, distance, and duration
- Body measurement tracking
- Goal management
- Full account export and account deletion
- Responsive layouts for desktop and phone browsers

## New Smart Scan features

- Take or upload a food photo
- Use OpenAI vision to estimate calories, protein, carbohydrates, fat, fiber, serving size, ingredients, assumptions, and confidence
- Edit every AI estimate before saving it
- Take or upload a barcode photo
- Decode UPC and EAN barcodes locally with ZXing-C++
- Look up packaged-food nutrition through Open Food Facts
- Adjust servings or enter a custom gram amount
- Save barcode results directly to the existing food diary
- Review Smart Scan history
- No food or barcode images are stored in the NouriVolt database

## Original future-focused features

- Fuel Balance score based on daily calories, macros, and hydration
- Next-Meal Engine that converts remaining daily goals into per-meal targets
- Readiness Pulse based on sleep, steps, energy, stress, soreness, mood, protein, and hydration
- Fourteen-day readiness history
- Futuristic light interface with scanning panels, score rings, adaptive cards, and mobile-friendly layouts

## Upgrade without losing current data

Your existing accounts and logs are stored in `data/nourivolt.db`. Keep that file when upgrading.

1. Stop the current Streamlit app.
2. Make a backup copy of `data/nourivolt.db`.
3. Replace `app.py`, `requirements.txt`, `README.md`, and `.streamlit/config.toml`.
4. Add the new `vision_services.py` file.
5. Keep your existing `data/nourivolt.db` file in place.
6. Run `pip install -r requirements.txt`.
7. Restart with `streamlit run app.py`.

NouriVolt creates the new Smart Scan and Readiness database tables automatically. Existing users, food logs, workouts, measurements, and goals remain intact.

## Run locally

1. Install Python 3.11 or 3.12.
2. Open a terminal in this folder.
3. Create and activate a virtual environment.

Windows:

```bat
py -m venv .venv
.venv\Scripts\activate
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Install the packages:

```bash
pip install -r requirements.txt
```

5. Start the app:

```bash
streamlit run app.py
```

Local data is stored in `data/nourivolt.db`.

## Turn on food-photo analysis

Barcode lookup does not require an OpenAI key. Food-photo macro analysis does.

You have two options.

### Option 1. Enter the key inside the app

Open `Smart Scan`, expand `AI connection`, then enter your OpenAI API key. The key remains in the current Streamlit browser session. NouriVolt does not write it to the database.

### Option 2. Set an environment variable

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your-key"
streamlit run app.py
```

Windows Command Prompt:

```bat
set OPENAI_API_KEY=your-key
streamlit run app.py
```

macOS or Linux:

```bash
export OPENAI_API_KEY="your-key"
streamlit run app.py
```

The default vision model is `gpt-5.6`. To use another compatible model, set `OPENAI_VISION_MODEL`.

## Open Food Facts identification

Open Food Facts asks applications to use a custom User-Agent. Before wider public deployment, replace the default contact value with your real app contact:

```powershell
$env:OPEN_FOOD_FACTS_USER_AGENT="NouriVolt/2.0 (you@example.com)"
```

## Deploy for phone and computer access

1. Upload this folder to a GitHub repository.
2. Deploy `app.py` on Streamlit Community Cloud or another Python host.
3. Add a managed PostgreSQL database for durable online storage.
4. Set `DATABASE_URL` in the host environment.
5. Set `OPENAI_API_KEY` when food-photo analysis is needed.
6. Open the deployed Streamlit URL from your phone or computer.

Example PostgreSQL URL:

```text
postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=require
```

Without `DATABASE_URL`, the app uses local SQLite. SQLite is suitable on your computer. Hosted apps should use PostgreSQL for durable account data.

## Accuracy and privacy design

- AI meal macros are estimates. The app requires a review screen before saving.
- Open Food Facts records are community-contributed and sometimes incomplete.
- Barcode images are decoded locally. Only the barcode number is sent for product lookup.
- Food images are sent to OpenAI only after the user presses Analyze.
- Scan images are not stored in NouriVolt.
- API keys are not included in account exports.
- Passwords are stored as bcrypt hashes, never plain text.

## U.S. display units update

- Height is entered and displayed in inches.
- Water entries and daily water targets are entered and displayed in U.S. fluid ounces.
- Workout dates use MM/DD/YYYY.
- Existing saved data remains compatible. The database continues to store legacy metric values internally so prior accounts and records do not require migration.

## Deployment-ready package

This package includes `DEPLOYMENT_GUIDE.md`, `.gitignore`, a Streamlit secrets template, a database verification utility, and an optional SQLite-to-PostgreSQL migration utility.

For durable Streamlit Community Cloud accounts, set the root-level secret `DATABASE_URL` to a managed PostgreSQL connection string. The app accepts both `DATABASE_URL` and `database_url`, but the deployment guide uses uppercase for consistency.

# NouriVolt GitHub, Neon, and Streamlit Deployment Guide

## Final architecture

- GitHub stores the application code.
- Streamlit Community Cloud runs `app.py`.
- Neon PostgreSQL stores accounts, password hashes, nutrition logs, water logs, workouts, measurements, goals, Smart Scan history, and readiness check-ins.
- Local development continues to use `data/nourivolt.db` when no `DATABASE_URL` exists.

Do not upload a real database file, `.env`, or `.streamlit/secrets.toml` to GitHub.

## Part 1. Keep a private local backup

1. Keep the original ZIP in a safe location.
2. Make a second copy of `data/nourivolt.db`.
3. Do not place the database backup inside the GitHub repository.

## Part 2. Create the Neon PostgreSQL database

1. Go to the Neon website and create or sign in to an account.
2. Select **New Project**.
3. Name it `nourivolt`.
4. Select a region near your users.
5. Create the project.
6. Open the project dashboard.
7. Select **Connect**.
8. Turn on **Connection pooling** when available.
9. Select Python or SQLAlchemy as the connection type.
10. Copy the full connection string. It normally starts with `postgresql://` and includes `sslmode=require`.
11. Save the connection string privately. Never paste it into GitHub.

## Part 3. Optional. Move existing local accounts and records to Neon

Skip this part when the local SQLite file contains no records you need online.

Windows PowerShell:

```powershell
cd C:\path\to\NouriVolt_GitHub_Deploy_Ready
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="PASTE_YOUR_NEON_CONNECTION_STRING"
python migrate_sqlite_to_postgres.py --source "C:\path\to\your\private\nourivolt.db"
python verify_database.py
```

The migration script stops when the target already contains data. This prevents accidental duplicates.

## Part 4. Create the GitHub repository through the website

1. Sign in to GitHub.
2. Select the plus sign in the upper-right corner.
3. Select **New repository**.
4. Repository name: `nourivolt`.
5. Choose **Private** while testing. Streamlit Community Cloud must have permission to access it.
6. Do not add a README, `.gitignore`, or license during repository creation because the package already includes them.
7. Select **Create repository**.
8. On the empty repository page, select **uploading an existing file**.
9. Open the unzipped `NouriVolt_GitHub_Deploy_Ready` folder on your computer.
10. Drag the contents of the folder into GitHub. Upload the contents, not the outer folder itself.
11. Confirm these files appear at the repository root:
    - `app.py`
    - `vision_services.py`
    - `requirements.txt`
    - `README.md`
    - `DEPLOYMENT_GUIDE.md`
    - `.gitignore`
    - `.streamlit/config.toml`
12. Confirm these private files do not appear:
    - `data/nourivolt.db`
    - `.streamlit/secrets.toml`
    - `.env`
13. Enter commit message: `Prepare NouriVolt for Streamlit deployment`.
14. Commit directly to `main`.

GitHub's website sometimes hides folders beginning with a period in local file pickers. Dragging the entire set of contents usually includes `.streamlit` and `.gitignore`. After the upload, verify both are visible in the repository.

## Part 5. Deploy on Streamlit Community Cloud

1. Sign in to Streamlit Community Cloud with the GitHub account that owns the repository.
2. Select **Create app**.
3. Choose the existing-app option.
4. Repository: `YOUR_GITHUB_USERNAME/nourivolt`.
5. Branch: `main`.
6. Main file path: `app.py`.
7. Choose a custom app URL such as `nourivolt` when available.
8. Select **Advanced settings**.
9. Set Python to **3.12**.
10. Paste the following into **Secrets**. Replace the placeholder values.

```toml
DATABASE_URL = "PASTE_YOUR_NEON_CONNECTION_STRING"
OPENAI_API_KEY = ""
OPENAI_VISION_MODEL = "gpt-5.6"
OPEN_FOOD_FACTS_USER_AGENT = "NouriVolt/2.0 (your-email@example.com)"
```

11. Keep `OPENAI_API_KEY` blank when food-photo AI analysis is not needed. Barcode lookup still works.
12. Save the advanced settings.
13. Select **Deploy**.
14. Watch the deployment logs. The app creates its PostgreSQL tables during startup.

## Part 6. Verify persistent accounts and data

1. Open the deployed app URL.
2. Create a test account with a unique username and email.
3. Sign in.
4. Add one food entry, one water entry, one workout, and one goal.
5. Log out.
6. Close every browser tab for the app.
7. Reopen the app and sign back in.
8. Confirm the records remain.
9. Open **Manage app** and reboot the app.
10. Sign back in after the reboot.
11. Confirm the records still remain.

Persistence after a Streamlit reboot confirms the app is using Neon instead of the temporary local SQLite file.

## Part 7. Update the app later through GitHub

1. Open the GitHub repository.
2. Select the file you need to replace.
3. Delete or edit the old file.
4. Upload the new file with the same filename and location.
5. Commit the change to `main`.
6. Streamlit detects the GitHub commit and redeploys the app.
7. Do not change or remove `DATABASE_URL` from Streamlit Secrets. The external database stays intact through code updates.

## Troubleshooting

### New accounts disappear

Cause: The app did not receive `DATABASE_URL` and used local SQLite.

Fix:

1. Open the Streamlit app.
2. Select **Manage app**.
3. Open **Settings**.
4. Open **Secrets**.
5. Confirm the key is exactly `DATABASE_URL` and the value is the full Neon connection string.
6. Save.
7. Reboot the app.

### `OperationalError` or SSL connection closed

1. Confirm the Neon project still exists.
2. Copy a new connection string from Neon's **Connect** dialog.
3. Confirm `sslmode=require` appears in the URL.
4. Replace the Streamlit secret.
5. Reboot the app.

The app already uses `pool_pre_ping` and `pool_recycle` to handle suspended Neon connections.

### `ModuleNotFoundError`

1. Confirm `requirements.txt` is in the same GitHub folder as `app.py`.
2. Confirm every package line remains intact.
3. Reboot the app.

### `.streamlit/config.toml` missing on GitHub

1. Open the local folder.
2. Turn on **View > Show > Hidden items** in Windows File Explorer.
3. Upload the `.streamlit` folder to the repository root.

### Python or Altair error

Delete the Streamlit app and redeploy it with Python 3.12 selected in Advanced settings. Streamlit does not change the Python version of an existing deployment in place.

## Security rules

1. Never upload the Neon connection string to GitHub.
2. Never upload an OpenAI API key to GitHub.
3. Never upload `data/nourivolt.db` to GitHub.
4. Keep the GitHub repository private during testing.
5. Use a unique Neon database password.
6. Rotate any credential immediately after accidental exposure.

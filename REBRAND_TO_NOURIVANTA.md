# NouriVanta rebrand

The public brand is now **NouriVanta**.

## Preserved for data compatibility

- The local SQLite filename remains `data/nourivolt.db`.
- Existing PostgreSQL tables remain unchanged.
- User accounts, password hashes, food records, water records, workouts, measurements, goals, readiness records, scans, and settings remain compatible.
- The legacy page name `NouriVolt Elite` remains only as an internal migration key so old browser sessions route correctly.

## Files to update in GitHub

Upload or replace all files in this package. Do not upload `data/nourivolt.db`, `.env`, or `.streamlit/secrets.toml`. Keep database and API credentials in Streamlit Community Cloud Secrets.

Renaming the GitHub repository and Streamlit URL is optional. The app works without changing either one.

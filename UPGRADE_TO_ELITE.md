# Upgrade NouriVolt to Elite without losing records

1. Back up the current project folder.
2. Back up `data/nourivolt.db` when running locally.
3. Extract the Elite package into a new folder.
4. Copy your existing `data/nourivolt.db` into the new package's `data` folder.
5. Replace the GitHub repository files with the extracted Elite files.
6. Keep the same Streamlit `DATABASE_URL` secret when using Neon.
7. Add optional `OPENAI_API_KEY`, `FDC_API_KEY`, `OPEN_FOOD_FACTS_USER_AGENT`, and `STRIPE_PAYMENT_LINK` secrets.
8. Reboot the Streamlit app.
9. Sign in with an existing account.
10. Open NouriVolt Elite from the sidebar and test each tab.

The startup process creates only missing Elite tables. Existing users, food logs, water logs, workouts, sets, measurements, goals, readiness entries, scans, and profile targets remain unchanged.

# NouriVanta Elite feature status

## Working inside this Streamlit package

- Adaptive coaching reports
- USDA food search
- Open Food Facts barcode lookup
- Food-photo macro estimates
- Nutrition-label photo scanning
- Favorites, recent foods, saved meals, diary copy
- Macro Forecast and Fuel Gap Map
- Workout programs, substitutions, rest timer, overload guidance, PRs, recovery map
- Meal planning, pantry, grocery list, budget estimate
- Progress intelligence and goal forecasts
- Voice logging with editable review
- Wearable CSV import and manual metrics
- Family profiles and coach notes
- Recovery codes, lockout, session timeout, access history
- Stripe hosted payment-link display

## Requires external credentials

- Food-photo scanning, label scanning, voice transcription, command parsing, and AI report enhancement require `OPENAI_API_KEY`.
- Full USDA quotas require `FDC_API_KEY`.
- Subscription checkout requires `STRIPE_PAYMENT_LINK`.
- Permanent Streamlit deployment data requires a PostgreSQL `DATABASE_URL` such as Neon.

## Requires a later native companion or approved OAuth application

- Direct Apple Health permission access
- Direct Android Health Connect permission access
- Live Garmin synchronization
- Live Fitbit synchronization
- Background push notifications
- Offline phone entry queues
- Device passkeys

These items cannot run directly inside a server-rendered Streamlit page because they require native device permissions, mobile background services, or provider OAuth approval. The current database and Wearable Bridge are structured to accept those integrations later.

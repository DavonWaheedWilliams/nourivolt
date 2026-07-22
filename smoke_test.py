from __future__ import annotations

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "nourivanta_smoke_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"

at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
at.button[1].click().run()
for widget, value in zip(
    at.text_input,
    ["Smoke Tester", "smoke_tester", "smoke@example.com", "Password1", "Password1"],
):
    widget.set_value(value)
at.button[-1].click().run()
at.button[0].click().run()
at.text_input[0].set_value("smoke_tester")
at.text_input[1].set_value("Password1")
at.button[2].click().run()

pages = [
    "Dashboard",
    "Nutrition",
    "Workouts",
    "Readiness",
    "Progress & Goals",
    "Settings",
]

for page in pages:
    at.radio[0].set_value(page).run()
    if at.exception:
        raise RuntimeError(f"{page} failed: {at.exception}")

print("NouriVanta smoke test passed for all pages.")

from __future__ import annotations

import html
import io
import os
import re
import sys
import zipfile
from functools import wraps
from statistics import mean
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
from pathlib import Path
from typing import Any

import bcrypt
import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from vision_services import (
    VisionServiceError,
    analyze_food_image,
    decode_barcode,
    lookup_open_food_facts,
)
from elite_features import (
    elite_export_files,
    install_elite_models,
    issue_recovery_code,
    login_allowed,
    register_login_result,
    render_adaptive_coach,
    render_elite_progress_center,
    render_family_and_security,
    render_food_intelligence,
    render_meal_planner,
    render_training_lab,
    render_voice_and_wearables,
    reset_password_with_recovery,
    session_timeout_minutes,
)
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

APP_NAME = "NouriVanta"
APP_TAGLINE = "Fuel. Train. Recover. Advance."

DEFAULT_TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "America/Chicago")
AUTO_TIMEZONE_LABEL = "Follow my device automatically"
MANUAL_TIMEZONE_LABEL = "Use a specific time zone"


def _valid_timezone_name(value: str | None) -> str | None:
    """Return a valid IANA time-zone name or None."""
    if not value:
        return None
    candidate = str(value).strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return candidate


def browser_timezone_name() -> str | None:
    """Read the current viewer's browser time zone when Streamlit supports it."""
    try:
        context = getattr(st, "context", None)
        return _valid_timezone_name(getattr(context, "timezone", None))
    except Exception:
        return None


def active_timezone_name() -> str:
    """Resolve manual preference, browser time zone, app fallback, then UTC."""
    try:
        mode = st.session_state.get("timezone_mode", "auto")
        manual_name = _valid_timezone_name(st.session_state.get("manual_timezone_name"))
    except Exception:
        mode = "auto"
        manual_name = None
    if mode == "manual" and manual_name:
        return manual_name
    detected = browser_timezone_name()
    if detected:
        return detected
    return _valid_timezone_name(DEFAULT_TIMEZONE_NAME) or "UTC"


def active_timezone() -> ZoneInfo:
    return ZoneInfo(active_timezone_name())


def local_now() -> datetime:
    """Return the current viewer-local time as a naive datetime for compatibility."""
    return datetime.now(active_timezone()).replace(tzinfo=None)


def local_today() -> date:
    """Return the current viewer's local calendar date."""
    return datetime.now(active_timezone()).date()


def utc_now() -> datetime:
    """Return a naive UTC timestamp for security and session calculations."""
    return datetime.now(UTC).replace(tzinfo=None)


def _streamlit_version_tuple() -> tuple[int, int, int]:
    """Return a numeric Streamlit version tuple without extra dependencies."""
    parts = []
    for piece in str(getattr(st, "__version__", "0.0.0")).split(".")[:3]:
        match = re.match(r"(\d+)", piece)
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _install_streamlit_width_compatibility() -> None:
    """Translate modern width values for Streamlit releases before 1.50."""
    if _streamlit_version_tuple() >= (1, 50, 0):
        return

    component_names = (
        "button",
        "form_submit_button",
        "download_button",
        "dataframe",
        "data_editor",
        "line_chart",
    )

    for component_name in component_names:
        original = getattr(st, component_name, None)
        if original is None:
            continue

        @wraps(original)
        def compatible_component(*args, __original=original, width=None, **kwargs):
            if width == "stretch":
                kwargs["use_container_width"] = True
            elif width == "content":
                kwargs["use_container_width"] = False
            elif width is not None:
                kwargs["width"] = width
            return __original(*args, **kwargs)

        setattr(st, component_name, compatible_component)


_install_streamlit_width_compatibility()
CM_PER_INCH = 2.54
ML_PER_FL_OZ = 29.5735295625

EXERCISE_LIBRARY: dict[str, list[str]] = {
    "Chest": [
        "Barbell Bench Press", "Dumbbell Bench Press", "Incline Barbell Bench Press",
        "Incline Dumbbell Press", "Decline Bench Press", "Chest Press Machine",
        "Push-Up", "Wide-Grip Push-Up", "Diamond Push-Up", "Chest Dip",
        "Dumbbell Fly", "Cable Fly", "Pec Deck Fly", "Landmine Press",
    ],
    "Back": [
        "Deadlift", "Pull-Up", "Chin-Up", "Lat Pulldown", "Barbell Row",
        "Dumbbell Row", "Seated Cable Row", "Chest-Supported Row", "T-Bar Row",
        "Inverted Row", "Straight-Arm Pulldown", "Back Extension", "Superman",
    ],
    "Shoulders": [
        "Overhead Press", "Dumbbell Shoulder Press", "Arnold Press",
        "Machine Shoulder Press", "Push Press", "Lateral Raise", "Front Raise",
        "Rear Delt Fly", "Face Pull", "Upright Row", "Cable Lateral Raise",
        "Reverse Pec Deck", "Shrug", "Handstand Push-Up",
    ],
    "Biceps": [
        "Barbell Curl", "Dumbbell Curl", "Hammer Curl", "Incline Dumbbell Curl",
        "Preacher Curl", "Cable Curl", "Concentration Curl", "EZ-Bar Curl",
        "Reverse Curl", "Spider Curl",
    ],
    "Triceps": [
        "Triceps Pushdown", "Overhead Triceps Extension", "Skull Crusher",
        "Close-Grip Bench Press", "Triceps Dip", "Bench Dip",
        "Dumbbell Kickback", "Cable Overhead Extension", "Diamond Push-Up",
    ],
    "Forearms and Grip": [
        "Wrist Curl", "Reverse Wrist Curl", "Farmer's Carry", "Dead Hang",
        "Plate Pinch", "Towel Pull-Up", "Wrist Roller", "Suitcase Carry",
    ],
    "Quadriceps": [
        "Back Squat", "Front Squat", "Goblet Squat", "Leg Press", "Hack Squat",
        "Leg Extension", "Walking Lunge", "Reverse Lunge",
        "Bulgarian Split Squat", "Step-Up", "Wall Sit",
    ],
    "Hamstrings": [
        "Romanian Deadlift", "Stiff-Leg Deadlift", "Lying Leg Curl",
        "Seated Leg Curl", "Good Morning", "Nordic Hamstring Curl",
        "Single-Leg Romanian Deadlift", "Glute-Ham Raise", "Kettlebell Swing",
    ],
    "Glutes": [
        "Barbell Hip Thrust", "Glute Bridge", "Cable Kickback", "Sumo Squat",
        "Sumo Deadlift", "Bulgarian Split Squat", "Step-Up", "Walking Lunge",
        "Frog Pump", "Clamshell", "Lateral Band Walk", "Donkey Kick",
    ],
    "Calves": [
        "Standing Calf Raise", "Seated Calf Raise", "Single-Leg Calf Raise",
        "Leg Press Calf Raise", "Donkey Calf Raise", "Jump Rope", "Tibialis Raise",
    ],
    "Core": [
        "Plank", "Side Plank", "Crunch", "Bicycle Crunch", "Reverse Crunch",
        "Sit-Up", "Hanging Leg Raise", "Knee Raise", "Russian Twist", "Dead Bug",
        "Bird Dog", "Ab Wheel Rollout", "Cable Crunch", "Pallof Press",
        "Mountain Climber", "V-Up", "Hollow Hold",
    ],
    "Full Body": [
        "Burpee", "Thruster", "Clean and Press", "Power Clean", "Snatch",
        "Kettlebell Swing", "Turkish Get-Up", "Bear Crawl", "Sled Push",
        "Sled Pull", "Battle Ropes", "Medicine Ball Slam", "Farmer's Carry",
    ],
    "Cardio": [
        "Walking", "Brisk Walking", "Treadmill Walking", "Running",
        "Treadmill Running", "Cycling", "Stationary Bike", "Elliptical",
        "Stair Climber", "Rowing Machine", "Swimming", "Jump Rope", "Hiking",
        "Dance Cardio", "Boxing", "Kickboxing",
    ],
    "Mobility and Recovery": [
        "Dynamic Warm-Up", "Foam Rolling", "Hip Flexor Stretch",
        "Hamstring Stretch", "Quadriceps Stretch", "Calf Stretch",
        "Chest Stretch", "Shoulder Stretch", "Thoracic Rotation",
        "World's Greatest Stretch", "Cat-Cow", "Child's Pose",
        "Yoga Flow", "Breathing Exercise",
    ],
    "Sports": [
        "Basketball", "Football", "Soccer", "Tennis", "Volleyball",
        "Pickleball", "Golf", "Martial Arts", "Skating", "Skiing",
    ],
}
CUSTOM_EXERCISE_OPTION = "Custom exercise"

st.set_page_config(
    page_title=f"{APP_NAME} | Fitness and Nutrition",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="auto",
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80), default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    activity_level: Mapped[str] = mapped_column(String(40), default="Moderately active")
    calorie_target: Mapped[int] = mapped_column(Integer, default=2200)
    protein_target: Mapped[int] = mapped_column(Integer, default=150)
    carb_target: Mapped[int] = mapped_column(Integer, default=240)
    fat_target: Mapped[int] = mapped_column(Integer, default=70)
    water_target_ml: Mapped[int] = mapped_column(Integer, default=2500)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    food_logs: Mapped[list[FoodLog]] = relationship(cascade="all, delete-orphan")
    water_logs: Mapped[list[WaterLog]] = relationship(cascade="all, delete-orphan")
    workout_sessions: Mapped[list[WorkoutSession]] = relationship(cascade="all, delete-orphan")
    measurements: Mapped[list[Measurement]] = relationship(cascade="all, delete-orphan")
    goals: Mapped[list[Goal]] = relationship(cascade="all, delete-orphan")
    smart_scans: Mapped[list[SmartScan]] = relationship(cascade="all, delete-orphan")
    daily_checkins: Mapped[list[DailyCheckIn]] = relationship(cascade="all, delete-orphan")


class UserTimezonePreference(Base):
    __tablename__ = "user_timezone_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="auto")
    timezone_name: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class FoodLog(Base):
    __tablename__ = "food_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    meal: Mapped[str] = mapped_column(String(30))
    food_name: Mapped[str] = mapped_column(String(120))
    serving: Mapped[str] = mapped_column(String(80), default="1 serving")
    calories: Mapped[float] = mapped_column(Float, default=0)
    protein_g: Mapped[float] = mapped_column(Float, default=0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0)
    fat_g: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class WaterLog(Base):
    __tablename__ = "water_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    amount_ml: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workout_date: Mapped[date] = mapped_column(Date, index=True)
    workout_name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="Strength")
    duration_min: Mapped[int] = mapped_column(Integer, default=0)
    calories_burned: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    sets: Mapped[list[ExerciseSet]] = relationship(cascade="all, delete-orphan")


class ExerciseSet(Base):
    __tablename__ = "exercise_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    exercise_name: Mapped[str] = mapped_column(String(120))
    set_number: Mapped[int] = mapped_column(Integer, default=1)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    weight_lb: Mapped[float] = mapped_column(Float, default=0)
    distance_miles: Mapped[float] = mapped_column(Float, default=0)
    duration_min: Mapped[float] = mapped_column(Float, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    measurement_date: Mapped[date] = mapped_column(Date, index=True)
    weight_lb: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    waist_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    chest_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    hips_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    arm_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    thigh_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="Fitness")
    target_value: Mapped[float] = mapped_column(Float, default=0)
    current_value: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="")
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class SmartScan(Base):
    __tablename__ = "smart_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scan_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(30), default="Food photo")
    food_name: Mapped[str] = mapped_column(String(160))
    barcode: Mapped[str] = mapped_column(String(40), default="")
    serving: Mapped[str] = mapped_column(String(100), default="1 serving")
    calories: Mapped[float] = mapped_column(Float, default=0)
    protein_g: Mapped[float] = mapped_column(Float, default=0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0)
    fat_g: Mapped[float] = mapped_column(Float, default=0)
    fiber_g: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DailyCheckIn(Base):
    __tablename__ = "daily_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    checkin_date: Mapped[date] = mapped_column(Date, index=True)
    sleep_hours: Mapped[float] = mapped_column(Float, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    energy: Mapped[int] = mapped_column(Integer, default=5)
    stress: Mapped[int] = mapped_column(Integer, default=5)
    soreness: Mapped[int] = mapped_column(Integer, default=5)
    mood: Mapped[int] = mapped_column(Integer, default=5)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


# Elite tables are additive. Existing application tables and records remain unchanged.
ELITE_MODELS = install_elite_models(Base)


def get_database_url() -> str:
    # Use an optional environment variable for online database hosting.
    # Local use falls back to SQLite without reading st.secrets, so a
    # missing secrets.toml path is never rendered in the app.
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return str(env_url)

    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    return f"sqlite:///{data_dir / 'nourivolt.db'}"


DATABASE_URL = get_database_url()
ENGINE_KWARGS: dict[str, Any] = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    ENGINE_KWARGS["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **ENGINE_KWARGS)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(engine)


DATE_WIDGET_KEYS = (
    "dashboard_date",
    "nutrition_date",
    "food_photo_log_date",
    "barcode_log_date",
    "navigator_date",
    "readiness_date",
    "elite_search_date",
    "elite_label_date",
    "favorite_date",
    "saved_meal_source_date",
    "saved_meal_target_date",
    "forecast_date",
    "program_workout_date",
    "voice_entry_date",
)


def reset_date_widget_defaults() -> None:
    """Clear explicit date widget state so defaults follow a changed time zone."""
    for key in DATE_WIDGET_KEYS:
        st.session_state.pop(key, None)


def load_timezone_preference(user_id: int) -> None:
    """Load one user's saved time-zone mode into the current browser session."""
    if st.session_state.get("timezone_loaded_for_user") == user_id:
        return
    with SessionLocal() as session:
        preference = session.scalar(
            select(UserTimezonePreference).where(UserTimezonePreference.user_id == user_id)
        )
    if preference:
        st.session_state.timezone_mode = (
            "manual" if preference.mode == "manual" and _valid_timezone_name(preference.timezone_name) else "auto"
        )
        st.session_state.manual_timezone_name = preference.timezone_name or ""
    else:
        st.session_state.timezone_mode = "auto"
        st.session_state.manual_timezone_name = ""
    st.session_state.timezone_loaded_for_user = user_id


def save_timezone_preference(user_id: int, mode: str, timezone_name: str = "") -> None:
    """Persist automatic or manual time-zone behavior without altering user records."""
    clean_mode = "manual" if mode == "manual" else "auto"
    clean_name = _valid_timezone_name(timezone_name) or ""
    if clean_mode == "manual" and not clean_name:
        raise ValueError("Choose a valid time zone.")
    with SessionLocal() as session:
        preference = session.scalar(
            select(UserTimezonePreference).where(UserTimezonePreference.user_id == user_id)
        )
        if preference is None:
            preference = UserTimezonePreference(user_id=user_id)
            session.add(preference)
        preference.mode = clean_mode
        preference.timezone_name = clean_name if clean_mode == "manual" else ""
        preference.updated_at = utc_now()
        session.commit()
    st.session_state.timezone_mode = clean_mode
    st.session_state.manual_timezone_name = clean_name if clean_mode == "manual" else ""
    st.session_state.timezone_loaded_for_user = user_id
    reset_date_widget_defaults()


def timezone_choices() -> list[str]:
    """Return a sorted list of supported IANA time zones."""
    try:
        names = sorted(available_timezones())
    except Exception:
        names = []
    preferred = [
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Phoenix",
        "America/Anchorage",
        "Pacific/Honolulu",
        "America/Nassau",
        "UTC",
    ]
    ordered = [name for name in preferred if name in names]
    ordered.extend(name for name in names if name not in ordered)
    return ordered or ["UTC"]


def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #172033;
            --muted: #64748B;
            --surface: rgba(255,255,255,.92);
            --line: rgba(99,102,241,.15);
            --violet: #6D5DFB;
            --cyan: #13C4D4;
            --blue: #2F80ED;
            --green: #20B486;
            --coral: #FF6B6B;
            --orange: #FF9F43;
            --pink: #EC4899;
            --gold: #F4C95D;
            --shadow: 0 18px 48px rgba(76,81,155,.13);
        }
        .stApp {
            background:
                radial-gradient(circle at 5% 3%, rgba(109,93,251,.15), transparent 27%),
                radial-gradient(circle at 96% 8%, rgba(19,196,212,.14), transparent 28%),
                radial-gradient(circle at 90% 72%, rgba(255,159,67,.11), transparent 25%),
                radial-gradient(circle at 10% 88%, rgba(236,72,153,.09), transparent 24%),
                linear-gradient(180deg, #FCFCFF 0%, #F5F8FF 48%, #F4FBFC 100%);
            color: var(--ink);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(255,255,255,.98) 0%, rgba(246,243,255,.97) 48%, rgba(235,252,255,.96) 100%);
            border-right: 1px solid rgba(109,93,251,.18);
            box-shadow: 10px 0 32px rgba(76,81,155,.06);
        }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1.2rem; }
        .block-container { max-width: 1320px; padding-top: 1.5rem; padding-bottom: 4rem; }
        h1, h2, h3 { color: var(--ink); letter-spacing: -.03em; }
        .nv-hero {
            padding: 1.5rem 1.6rem;
            border-radius: 24px;
            background: linear-gradient(120deg, rgba(255,255,255,.98) 0%, rgba(244,240,255,.97) 40%, rgba(232,251,255,.96) 72%, rgba(255,246,232,.95) 100%);
            border: 1px solid rgba(109,93,251,.20);
            box-shadow: var(--shadow);
            margin-bottom: 1rem;
            position: relative;
            overflow: hidden;
        }
        .nv-hero:before {
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            top: 0;
            height: 5px;
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan), var(--green), var(--gold), var(--coral), var(--pink));
        }
        .nv-hero:after {
            content: "";
            position: absolute;
            width: 220px; height: 220px; border-radius: 50%;
            right: -80px; top: -120px;
            background: linear-gradient(135deg, rgba(109,93,251,.22), rgba(19,196,212,.18), rgba(255,159,67,.12));
        }
        .nv-kicker { color: var(--violet); text-transform: uppercase; font-size: .75rem; font-weight: 850; letter-spacing: .12em; }
        .nv-title {
            font-size: clamp(1.7rem, 4vw, 2.8rem);
            font-weight: 900;
            line-height: 1.05;
            margin: .35rem 0;
            background: linear-gradient(90deg, #172033 0%, #5548D9 48%, #087E95 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .nv-subtitle { color: var(--muted); font-size: 1rem; max-width: 720px; }
        .nv-card {
            --card-accent: var(--violet);
            --card-accent-end: var(--cyan);
            background: linear-gradient(145deg, rgba(255,255,255,.98), color-mix(in srgb, var(--card-accent) 7%, #FFFFFF));
            border: 1px solid color-mix(in srgb, var(--card-accent) 25%, #DCE3F2);
            border-radius: 20px;
            box-shadow: 0 12px 34px rgba(64,72,120,.08);
            padding: 1rem 1.05rem;
            min-height: 132px;
            position: relative;
            overflow: hidden;
            transition: transform .2s ease, box-shadow .2s ease;
        }
        .nv-card:before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 5px;
            background: linear-gradient(180deg, var(--card-accent), var(--card-accent-end));
        }
        .nv-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 16px 38px rgba(64,72,120,.12);
        }
        .nv-label { color: var(--muted); font-size: .78rem; font-weight: 750; text-transform: uppercase; letter-spacing: .06em; }
        .nv-value { color: var(--ink); font-size: 1.75rem; font-weight: 850; margin-top: .3rem; }
        .nv-meta { color: var(--muted); font-size: .82rem; margin-top: .25rem; }
        .nv-progress {
            background: #E9EDF7;
            height: 11px;
            border-radius: 999px;
            overflow: hidden;
            margin-top: .7rem;
            box-shadow: inset 0 1px 2px rgba(37,49,74,.08);
        }
        .nv-progress > span {
            display: block;
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, var(--metric-color, #6C5CE7), var(--metric-color-end, #16B8C4));
            box-shadow: 0 0 14px color-mix(in srgb, var(--metric-color, #6C5CE7) 36%, transparent);
            animation: nv-bar-fill .8s ease-out both;
        }
        .nv-progress-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: .6rem;
            margin-top: .5rem;
        }
        .nv-progress-percent {
            color: var(--metric-color, #6C5CE7);
            font-size: .76rem;
            font-weight: 850;
        }
        @keyframes nv-bar-fill {
            from { width: 0; }
        }
        .nv-ring-card {
            min-height: 232px;
            padding: 1rem .8rem .9rem;
            border-radius: 22px;
            background: linear-gradient(150deg, rgba(255,255,255,.99), color-mix(in srgb, var(--ring-color) 9%, #FFFFFF));
            border: 1px solid color-mix(in srgb, var(--ring-color) 28%, #DDE4F2);
            box-shadow: 0 12px 34px rgba(64,72,120,.08);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .nv-ring-card:before {
            content: "";
            position: absolute;
            left: 0;
            right: 0;
            top: 0;
            height: 5px;
            background: linear-gradient(90deg, var(--ring-color), color-mix(in srgb, var(--ring-color) 48%, #FFFFFF));
        }
        .nv-ring-card:after {
            content: "";
            position: absolute;
            width: 110px;
            height: 110px;
            border-radius: 50%;
            right: -55px;
            top: -55px;
            background: radial-gradient(circle, color-mix(in srgb, var(--ring-color) 24%, transparent), transparent 68%);
            pointer-events: none;
        }
        .nv-dashboard-ring {
            width: 136px;
            height: 136px;
            margin: .8rem auto .7rem;
            border-radius: 50%;
            display: grid;
            place-items: center;
            position: relative;
            background: #E8EBF5;
            box-shadow: inset 0 0 0 1px rgba(99,102,241,.05);
            transition: background .7s ease, box-shadow .7s ease;
        }
        .nv-dashboard-ring:before {
            content: "";
            position: absolute;
            inset: 13px;
            border-radius: 50%;
            background: linear-gradient(145deg, #FFFFFF, #F8FAFF);
            box-shadow: inset 0 0 0 1px rgba(99,102,241,.08), 0 4px 14px rgba(64,72,120,.08);
            z-index: 1;
        }
        .nv-ring-status {
            width: .52rem;
            height: .52rem;
            border-radius: 999px;
            margin: .45rem auto 0;
        }
        .nv-ring-center {
            position: relative;
            z-index: 3;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            line-height: 1.05;
        }
        .nv-ring-value {
            color: #172033;
            font-size: 1.55rem;
            font-weight: 900;
            letter-spacing: -.04em;
        }
        .nv-ring-unit {
            color: #69758B;
            font-size: .72rem;
            font-weight: 800;
            margin-top: .28rem;
            text-transform: uppercase;
            letter-spacing: .08em;
        }
        .nv-ring-meta {
            color: #64748B;
            font-size: .8rem;
            line-height: 1.35;
            min-height: 2.15rem;
        }
        .nv-brand { font-size: 1.35rem; font-weight: 900; letter-spacing: -.04em; }
        .nv-brand span {
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan));
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .nv-user { color: var(--muted); font-size:.88rem; margin:.2rem 0 1rem; }
        div[data-testid="stForm"] {
            background: linear-gradient(145deg, rgba(255,255,255,.97), rgba(246,244,255,.93) 52%, rgba(238,252,255,.92));
            border: 1px solid rgba(109,93,251,.18);
            border-radius: 20px;
            padding: 1rem;
            box-shadow: 0 12px 32px rgba(64,72,120,.08);
        }
        div[data-testid="stMetric"] { background: rgba(255,255,255,.9); border:1px solid var(--line); padding: .8rem; border-radius: 16px; }
        .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
            border-radius: 12px; min-height: 2.7rem; font-weight: 750; border: 1px solid rgba(108,92,231,.18);
        }
        .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan));
            color: white;
            border: none;
            box-shadow: 0 8px 20px rgba(79,70,229,.20);
        }
        .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
            transform: translateY(-1px);
            border-color: rgba(109,93,251,.38);
            box-shadow: 0 10px 24px rgba(79,70,229,.16);
        }
        .stDownloadButton > button {
            background: linear-gradient(90deg, #FFFFFF, #F1F8FF);
            color: #3E3A9A;
        }
        [data-testid="stTextInput"],
        [data-testid="stNumberInput"],
        [data-testid="stDateInput"],
        [data-testid="stTimeInput"],
        [data-testid="stSelectbox"],
        [data-testid="stMultiSelect"],
        [data-testid="stTextArea"] {
            margin-bottom: .85rem !important;
        }
        [data-testid="stTextInput"] label,
        [data-testid="stNumberInput"] label,
        [data-testid="stDateInput"] label,
        [data-testid="stTimeInput"] label,
        [data-testid="stSelectbox"] label,
        [data-testid="stMultiSelect"] label,
        [data-testid="stTextArea"] label {
            color: #25314A !important;
            font-weight: 750 !important;
            font-size: .92rem !important;
            margin-bottom: .38rem !important;
            opacity: 1 !important;
        }
        [data-baseweb="input"],
        [data-baseweb="base-input"],
        [data-baseweb="select"],
        [data-baseweb="textarea"] {
            width: 100% !important;
            overflow: visible !important;
            box-sizing: border-box !important;
        }
        [data-testid="stTextInput"] [data-baseweb="input"] > div,
        [data-testid="stNumberInput"] [data-baseweb="input"] > div,
        [data-testid="stDateInput"] [data-baseweb="input"] > div,
        [data-testid="stTimeInput"] [data-baseweb="input"] > div,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
        [data-testid="stTextArea"] [data-baseweb="textarea"] > div {
            width: 100% !important;
            min-height: 3.35rem !important;
            height: auto !important;
            padding: .58rem .84rem !important;
            display: flex !important;
            align-items: center !important;
            gap: .35rem !important;
            box-sizing: border-box !important;
            overflow: visible !important;
            border-radius: 12px !important;
            background: linear-gradient(135deg, #FBFCFF, #F5F4FF 58%, #F0FCFD) !important;
            border: 1.5px solid #C8D0EA !important;
            box-shadow: 0 2px 8px rgba(37,49,74,.04) !important;
        }
        [data-testid="stTextArea"] [data-baseweb="textarea"] > div {
            min-height: 7.5rem !important;
            align-items: stretch !important;
            padding-top: .78rem !important;
            padding-bottom: .78rem !important;
        }
        [data-testid="stTextInput"] [data-baseweb="input"] > div:hover,
        [data-testid="stNumberInput"] [data-baseweb="input"] > div:hover,
        [data-testid="stDateInput"] [data-baseweb="input"] > div:hover,
        [data-testid="stTimeInput"] [data-baseweb="input"] > div:hover,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div:hover,
        [data-testid="stTextArea"] [data-baseweb="textarea"] > div:hover {
            border-color: #8E84EE !important;
            background: #FFFFFF !important;
        }
        [data-testid="stTextInput"] [data-baseweb="input"] > div:focus-within,
        [data-testid="stNumberInput"] [data-baseweb="input"] > div:focus-within,
        [data-testid="stDateInput"] [data-baseweb="input"] > div:focus-within,
        [data-testid="stTimeInput"] [data-baseweb="input"] > div:focus-within,
        [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within,
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div:focus-within,
        [data-testid="stTextArea"] [data-baseweb="textarea"] > div:focus-within {
            border-color: var(--violet) !important;
            background: #FFFFFF !important;
            box-shadow: 0 0 0 3px rgba(109,93,251,.13), 0 0 18px rgba(19,196,212,.08) !important;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stTimeInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-testid="stSelectbox"] input,
        [data-testid="stMultiSelect"] input {
            height: auto !important;
            min-height: 1.55rem !important;
            line-height: 1.45 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: none !important;
            color: #172033 !important;
            background: transparent !important;
            -webkit-text-fill-color: #172033 !important;
            caret-color: #6C5CE7 !important;
            font-size: 1rem !important;
            opacity: 1 !important;
        }
        [data-testid="stTextArea"] textarea {
            min-height: 5.5rem !important;
            resize: vertical !important;
        }
        [data-testid="stSelectbox"] [data-baseweb="select"] span,
        [data-testid="stMultiSelect"] [data-baseweb="select"] span,
        [data-testid="stSelectbox"] [data-baseweb="select"] div,
        [data-testid="stMultiSelect"] [data-baseweb="select"] div {
            line-height: 1.35 !important;
            color: #172033 !important;
        }
        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stNumberInput"] input::placeholder,
        [data-testid="stDateInput"] input::placeholder,
        [data-testid="stTimeInput"] input::placeholder,
        [data-testid="stTextArea"] textarea::placeholder,
        [data-testid="stSelectbox"] input::placeholder,
        [data-testid="stMultiSelect"] input::placeholder {
            color: #8290A8 !important;
            opacity: 1 !important;
        }
        [data-testid="stNumberInput"] button,
        [data-testid="stDateInput"] button,
        [data-testid="stTimeInput"] button,
        [data-testid="stSelectbox"] button,
        [data-testid="stMultiSelect"] button,
        [data-testid="stTextInput"] button {
            min-height: 1rem !important;
            align-self: center !important;
            background: transparent !important;
            border: none !important;
            padding: 0 !important;
            margin: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stNumberInput"] button svg,
        [data-testid="stDateInput"] button svg,
        [data-testid="stTimeInput"] button svg,
        [data-testid="stSelectbox"] button svg,
        [data-testid="stMultiSelect"] button svg,
        [data-testid="stTextInput"] button svg {
            width: 1rem !important;
            height: 1rem !important;
            fill: #526078 !important;
            color: #526078 !important;
            opacity: 1 !important;
        }
        [data-testid="stTextInput"] > div,
        [data-testid="stNumberInput"] > div,
        [data-testid="stDateInput"] > div,
        [data-testid="stTimeInput"] > div,
        [data-testid="stSelectbox"] > div,
        [data-testid="stMultiSelect"] > div,
        [data-testid="stTextArea"] > div {
            overflow: visible !important;
        }
        .nv-auth-heading {
            color: #172033;
            font-size: 1.45rem;
            font-weight: 850;
            line-height: 1.2;
            letter-spacing: -.025em;
            margin: .1rem 0 .25rem;
        }
        .nv-auth-copy {
            color: #66738A;
            font-size: .92rem;
            line-height: 1.45;
            margin-bottom: .8rem;
        }
        [data-testid="stFormSubmitButton"] button {
            min-height: 3rem !important;
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan)) !important;
            color: #FFFFFF !important;
            border: none !important;
            opacity: 1 !important;
        }
        [data-testid="stFormSubmitButton"] button p {
            color: #FFFFFF !important;
            opacity: 1 !important;
            font-weight: 750 !important;
        }
        .nv-future-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: .85rem;
            margin: .8rem 0 1.15rem;
        }
        .nv-future-card {
            position: relative;
            overflow: hidden;
            min-height: 132px;
            padding: 1rem 1.05rem;
            border-radius: 20px;
            background: linear-gradient(145deg, rgba(255,255,255,.98), rgba(244,241,255,.94) 52%, rgba(235,252,255,.92));
            border: 1px solid rgba(109,93,251,.20);
            box-shadow: 0 12px 34px rgba(64,72,120,.08);
        }
        .nv-future-card:nth-child(1) { background: linear-gradient(145deg, #FFFFFF, #F2EEFF); border-color: #D9D1FF; }
        .nv-future-card:nth-child(2) { background: linear-gradient(145deg, #FFFFFF, #EAFDFA); border-color: #C7F3EA; }
        .nv-future-card:nth-child(3) { background: linear-gradient(145deg, #FFFFFF, #FFF3E8); border-color: #FFE0C2; }
        .nv-future-card:after {
            content: "";
            position: absolute;
            right: -48px;
            top: -58px;
            width: 130px;
            height: 130px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(22,184,196,.18), rgba(108,92,231,.05) 60%, transparent 72%);
        }
        .nv-scan-shell {
            position: relative;
            overflow: hidden;
            padding: 1.05rem;
            border-radius: 22px;
            background: linear-gradient(135deg, rgba(255,255,255,.98), rgba(244,240,255,.96) 40%, rgba(234,252,255,.95) 72%, rgba(255,246,232,.94));
            border: 1px solid rgba(108,92,231,.22);
            box-shadow: 0 18px 42px rgba(64,72,120,.10);
        }
        .nv-scan-shell:before {
            content: "";
            position: absolute;
            left: 2%;
            right: 2%;
            height: 2px;
            top: 20%;
            background: linear-gradient(90deg, transparent, rgba(22,184,196,.85), transparent);
            box-shadow: 0 0 18px rgba(22,184,196,.45);
            animation: nvscan 4.5s ease-in-out infinite;
            pointer-events: none;
        }
        @keyframes nvscan {
            0%, 100% { transform: translateY(0); opacity: .2; }
            50% { transform: translateY(235px); opacity: .9; }
        }
        .nv-chip {
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            padding: .32rem .58rem;
            margin: .18rem .25rem .18rem 0;
            border-radius: 999px;
            color: #4438A8;
            background: linear-gradient(90deg, #F1EEFF, #EAFBFF);
            border: 1px solid #D7D3FF;
            font-size: .78rem;
            font-weight: 750;
        }
        .nv-score-wrap {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1rem;
            border-radius: 20px;
            background: linear-gradient(135deg, rgba(255,255,255,.96), rgba(244,241,255,.92), rgba(236,252,255,.90));
            border: 1px solid rgba(109,93,251,.18);
            box-shadow: 0 12px 32px rgba(64,72,120,.07);
        }
        .nv-score-ring {
            --score: 0;
            width: 104px;
            height: 104px;
            flex: 0 0 104px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background: conic-gradient(#6C5CE7 calc(var(--score) * 1%), #E8EBF5 0);
            position: relative;
        }
        .nv-score-ring:before {
            content: "";
            position: absolute;
            inset: 10px;
            border-radius: 50%;
            background: #FFFFFF;
        }
        .nv-score-ring strong {
            position: relative;
            z-index: 1;
            color: #172033;
            font-size: 1.45rem;
            font-weight: 900;
        }
        .nv-result-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: .7rem;
            margin: .8rem 0;
        }
        .nv-result-cell {
            padding: .8rem;
            border-radius: 16px;
            background: linear-gradient(145deg, #FFFFFF, #F7F5FF);
            border: 1px solid #DFE3F4;
        }
        .nv-result-cell:nth-child(1) { border-top: 4px solid var(--violet); }
        .nv-result-cell:nth-child(2) { border-top: 4px solid var(--cyan); }
        .nv-result-cell:nth-child(3) { border-top: 4px solid var(--orange); }
        .nv-result-cell:nth-child(4) { border-top: 4px solid var(--pink); }
        .nv-result-cell:nth-child(5) { border-top: 4px solid var(--green); }
        .nv-result-cell b { display:block; color:#172033; font-size:1.12rem; margin-top:.15rem; }
        .nv-muted-panel {
            padding: .9rem 1rem;
            border-radius: 16px;
            background: linear-gradient(135deg, #F3EFFF, #E9FCFF 55%, #FFF5E9);
            border: 1px solid rgba(108,92,231,.14);
            color: #526078;
        }
        [data-testid="stCameraInput"] {
            padding: .75rem;
            border-radius: 18px;
            background: rgba(255,255,255,.88);
            border: 1px solid rgba(108,92,231,.18);
        }
        [data-testid="stFileUploader"] {
            padding: .6rem;
            border-radius: 18px;
            background: rgba(255,255,255,.88);
            border: 1px solid rgba(108,92,231,.15);
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: .4rem;
            padding: .35rem;
            margin-bottom: 1rem;
            min-height: 3rem;
            overflow: visible !important;
            border-radius: 15px;
            background: linear-gradient(90deg, rgba(241,238,255,.88), rgba(233,252,255,.88), rgba(255,245,232,.78));
            border: 1px solid rgba(109,93,251,.14);
        }
        [data-testid="stTabs"] button[role="tab"] {
            min-height: 2.35rem !important;
            padding: .5rem .8rem !important;
            border-radius: 11px !important;
            color: #526078 !important;
            font-weight: 800 !important;
        }
        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan)) !important;
            color: #FFFFFF !important;
            box-shadow: 0 6px 16px rgba(79,70,229,.18);
        }
        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] p { color: #FFFFFF !important; }
        [data-testid="stTabs"] [data-baseweb="tab-highlight"] { display: none !important; }
        /* Main-page radio groups render as clean segmented controls. */
        [data-testid="stMain"] [data-testid="stRadio"] {
            margin: .2rem 0 1rem !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] > div[role="radiogroup"] {
            display: inline-flex !important;
            flex-wrap: wrap !important;
            gap: .35rem !important;
            padding: .35rem !important;
            border-radius: 15px !important;
            background: linear-gradient(90deg, rgba(241,238,255,.94), rgba(233,252,255,.92), rgba(255,245,232,.86)) !important;
            border: 1px solid rgba(109,93,251,.16) !important;
            box-shadow: 0 8px 24px rgba(64,72,120,.06) !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label {
            min-height: 2.55rem !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            padding: .62rem 1rem !important;
            margin: 0 !important;
            border-radius: 11px !important;
            border: 1px solid transparent !important;
            color: #526078 !important;
            font-weight: 800 !important;
            cursor: pointer !important;
            transition: background .18s ease, color .18s ease, box-shadow .18s ease, transform .18s ease !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label:hover {
            background: rgba(255,255,255,.76) !important;
            color: #4438A8 !important;
            transform: translateY(-1px);
        }
        [data-testid="stMain"] [data-testid="stRadio"] label > div:first-child {
            display: none !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label p {
            margin: 0 !important;
            color: inherit !important;
            font-weight: inherit !important;
            line-height: 1.2 !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label:has(input:checked) {
            background: linear-gradient(90deg, var(--violet), var(--blue), var(--cyan)) !important;
            color: #FFFFFF !important;
            border-color: transparent !important;
            box-shadow: 0 7px 18px rgba(79,70,229,.22) !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] label:has(input:checked) p {
            color: #FFFFFF !important;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
            background: linear-gradient(90deg, rgba(109,93,251,.16), rgba(19,196,212,.14));
            border: 1px solid rgba(109,93,251,.16);
            border-radius: 12px;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) p {
            color: #4438A8 !important;
            font-weight: 850 !important;
        }
        [data-testid="stAlert"] {
            border-radius: 15px !important;
            box-shadow: 0 8px 22px rgba(64,72,120,.06);
        }
        details[data-testid="stExpander"] {
            border-radius: 16px !important;
            border-color: rgba(109,93,251,.16) !important;
            background: linear-gradient(145deg, rgba(255,255,255,.96), rgba(245,244,255,.88));
        }
        .nv-empty { padding: 2rem; text-align:center; color:var(--muted); background:linear-gradient(135deg,rgba(248,246,255,.82),rgba(237,252,255,.78)); border:1px dashed rgba(109,93,251,.28); border-radius:18px; }
        /* Responsive behavior across phones, tablets, laptops, and desktops. */
        html, body, [data-testid="stAppViewContainer"] {
            overflow-x: hidden !important;
        }
        [data-testid="stMain"] {
            min-width: 0 !important;
        }
        [data-testid="stMain"] .block-container {
            width: min(100%, 1320px) !important;
        }
        [data-testid="stDataFrame"],
        [data-testid="stTable"],
        [data-testid="stDataEditor"] {
            max-width: 100% !important;
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch;
        }
        [data-testid="stPlotlyChart"],
        [data-testid="stVegaLiteChart"],
        [data-testid="stPyplotGlobalUse"] {
            width: 100% !important;
            max-width: 100% !important;
            overflow: hidden !important;
        }
        [data-testid="stCameraInput"],
        [data-testid="stFileUploader"] {
            width: 100% !important;
            max-width: 100% !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] input[type="radio"] {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            pointer-events: none !important;
        }
        [data-testid="stMain"] [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child,
        [data-testid="stMain"] [data-testid="stRadio"] label > div:first-child {
            display: none !important;
        }
        @media (min-width: 1200px) {
            .block-container {
                padding-left: 2rem !important;
                padding-right: 2rem !important;
            }
            .nv-hero { padding: 1.65rem 1.8rem; }
        }
        @media (min-width: 901px) and (max-width: 1199px) {
            .block-container {
                max-width: 1120px !important;
                padding-left: 1.25rem !important;
                padding-right: 1.25rem !important;
            }
            .nv-title { font-size: clamp(1.75rem, 3.4vw, 2.5rem); }
            .nv-ring-card { min-height: 220px; }
            .nv-dashboard-ring { width: 124px; height: 124px; }
        }
        @media (min-width: 641px) and (max-width: 900px) {
            [data-testid="stSidebar"] {
                min-width: 250px !important;
                max-width: 270px !important;
            }
            .block-container {
                max-width: 100% !important;
                padding: 1rem 1rem 7rem !important;
            }
            .nv-hero {
                padding: 1.25rem 1.35rem !important;
                border-radius: 20px !important;
            }
            .nv-title { font-size: clamp(1.7rem, 4.4vw, 2.35rem); }
            [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                gap: .85rem !important;
            }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                flex: 1 1 calc(50% - .5rem) !important;
                min-width: 260px !important;
            }
            .nv-ring-card { min-height: 215px; }
            .nv-dashboard-ring { width: 118px; height: 118px; }
            .nv-future-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .nv-result-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            [data-testid="stTabs"] [data-baseweb="tab-list"] {
                overflow-x: auto !important;
                flex-wrap: nowrap !important;
                scrollbar-width: thin;
                -webkit-overflow-scrolling: touch;
            }
            [data-testid="stTabs"] button[role="tab"] {
                flex: 0 0 auto !important;
                white-space: nowrap !important;
            }
        }
        @media (max-width: 640px) {
            [data-testid="stAppViewContainer"] {
                padding-bottom: 6.5rem !important;
            }
            .block-container {
                max-width: 100% !important;
                padding: .75rem .72rem 8rem !important;
            }
            [data-testid="stSidebar"] {
                width: min(88vw, 320px) !important;
            }
            [data-testid="stSidebar"] > div:first-child {
                padding-top: .8rem !important;
            }
            .nv-hero {
                padding: 1rem 1rem 1.05rem !important;
                border-radius: 18px !important;
                margin-bottom: .8rem !important;
                min-height: auto !important;
            }
            .nv-hero:after {
                width: 145px !important;
                height: 145px !important;
                right: -62px !important;
                top: -74px !important;
            }
            .nv-kicker { font-size: .68rem !important; }
            .nv-title {
                font-size: clamp(1.65rem, 8vw, 2.15rem) !important;
                line-height: 1.08 !important;
                max-width: 92% !important;
            }
            .nv-subtitle {
                font-size: .93rem !important;
                line-height: 1.55 !important;
                max-width: 96% !important;
            }
            h1 { font-size: 1.75rem !important; }
            h2 { font-size: 1.42rem !important; }
            h3 { font-size: 1.18rem !important; }
            [data-testid="stHorizontalBlock"] {
                flex-direction: column !important;
                gap: .78rem !important;
            }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                width: 100% !important;
                min-width: 0 !important;
                flex: 1 1 100% !important;
            }
            .nv-card {
                min-height: 108px !important;
                padding: .9rem .95rem !important;
                border-radius: 17px !important;
            }
            .nv-ring-card {
                min-height: 205px !important;
                padding: .85rem .7rem !important;
                border-radius: 18px !important;
            }
            .nv-dashboard-ring {
                width: 112px !important;
                height: 112px !important;
                margin: .62rem auto .55rem !important;
            }
            .nv-dashboard-ring:before { inset: 11px !important; }
            .nv-ring-value { font-size: 1.35rem !important; }
            .nv-value { font-size: 1.48rem !important; }
            div[data-testid="stForm"] {
                padding: .82rem !important;
                border-radius: 17px !important;
            }
            [data-testid="stTextInput"],
            [data-testid="stNumberInput"],
            [data-testid="stSelectbox"],
            [data-testid="stMultiSelect"],
            [data-testid="stTextArea"],
            [data-testid="stDateInput"],
            [data-testid="stTimeInput"] {
                margin-bottom: .65rem !important;
            }
            [data-testid="stTextInput"] input,
            [data-testid="stNumberInput"] input,
            [data-testid="stDateInput"] input,
            [data-testid="stTimeInput"] input,
            [data-testid="stTextArea"] textarea {
                font-size: 16px !important;
            }
            .stButton > button,
            .stDownloadButton > button,
            .stFormSubmitButton > button {
                width: 100% !important;
                min-height: 44px !important;
                padding: .65rem .85rem !important;
            }
            [data-testid="stMain"] [data-testid="stRadio"] {
                width: 100% !important;
                margin: .1rem 0 .8rem !important;
            }
            [data-testid="stMain"] [data-testid="stRadio"] > div[role="radiogroup"] {
                display: grid !important;
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
                width: 100% !important;
                gap: .3rem !important;
                padding: .3rem !important;
            }
            [data-testid="stMain"] [data-testid="stRadio"] label {
                width: 100% !important;
                min-width: 0 !important;
                min-height: 44px !important;
                padding: .58rem .45rem !important;
                white-space: nowrap !important;
                font-size: .92rem !important;
            }
            [data-testid="stTabs"] [data-baseweb="tab-list"] {
                display: flex !important;
                overflow-x: auto !important;
                flex-wrap: nowrap !important;
                gap: .2rem !important;
                padding: .25rem !important;
                margin-bottom: .75rem !important;
                scrollbar-width: thin;
                scroll-snap-type: x proximity;
                -webkit-overflow-scrolling: touch;
            }
            [data-testid="stTabs"] button[role="tab"] {
                flex: 0 0 auto !important;
                min-height: 42px !important;
                padding: .5rem .68rem !important;
                white-space: nowrap !important;
                font-size: .88rem !important;
                scroll-snap-align: start;
            }
            .nv-future-grid,
            .nv-result-grid {
                grid-template-columns: 1fr !important;
                gap: .6rem !important;
            }
            .nv-score-wrap {
                align-items: flex-start !important;
                flex-direction: column !important;
            }
            .nv-score-ring {
                width: 92px !important;
                height: 92px !important;
                flex-basis: 92px !important;
            }
            [data-testid="stCameraInput"],
            [data-testid="stFileUploader"] {
                padding: .55rem !important;
                border-radius: 15px !important;
            }
            [data-testid="stDataFrame"],
            [data-testid="stTable"],
            [data-testid="stDataEditor"] {
                font-size: .82rem !important;
            }
            details[data-testid="stExpander"] {
                border-radius: 14px !important;
            }
            [data-testid="stVerticalBlock"] {
                min-width: 0 !important;
            }
        }
        @media (max-width: 390px) {
            .block-container { padding-left: .55rem !important; padding-right: .55rem !important; }
            .nv-title { font-size: 1.58rem !important; }
            [data-testid="stMain"] [data-testid="stRadio"] label {
                font-size: .84rem !important;
                padding-left: .32rem !important;
                padding-right: .32rem !important;
            }
            [data-testid="stTabs"] button[role="tab"] {
                font-size: .82rem !important;
                padding-left: .55rem !important;
                padding-right: .55rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalize_username(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]", "", value.strip().lower())


def valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip()))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def init_state() -> None:
    defaults = {
        "user_id": None,
        "username": None,
        "page": "Dashboard",
        "auth_mode": "Sign in",
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "food_scan_result": None,
        "barcode_product": None,
        "barcode_value": "",
        "elite_food_results": [],
        "elite_coach_report": "",
        "elite_voice_transcript": "",
        "elite_voice_result": None,
        "elite_new_recovery_code": "",
        "timezone_mode": "auto",
        "manual_timezone_name": "",
        "timezone_loaded_for_user": None,
        "last_activity_at": utc_now(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def get_user(session: Session, user_id: int) -> User | None:
    return session.scalar(select(User).where(User.id == user_id))


def create_account(username: str, email: str, password: str, display_name: str) -> tuple[bool, str]:
    username = normalize_username(username)
    email = email.strip().lower()
    if len(username) < 3:
        return False, "Use at least 3 letters or numbers for your username."
    if not valid_email(email):
        return False, "Enter a valid email address."
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "Use at least 8 characters with one letter and one number."

    with SessionLocal() as session:
        duplicate = session.scalar(select(User).where((User.username == username) | (User.email == email)))
        if duplicate:
            return False, "That username or email is already registered."
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            display_name=display_name.strip() or username,
        )
        session.add(user)
        session.commit()
        recovery_code = issue_recovery_code(SessionLocal, ELITE_MODELS, user.id, hash_password)
        st.session_state.elite_new_recovery_code = recovery_code
        return True, "Account created. Save the one-time recovery code shown below, then sign in."


def authenticate(login: str, password: str) -> User | None:
    login = login.strip().lower()
    allowed, message = login_allowed(SessionLocal, ELITE_MODELS, User, login)
    if not allowed:
        st.session_state.auth_error = message
        return None
    with SessionLocal() as session:
        user = session.scalar(select(User).where((User.username == login) | (User.email == login)))
        if user and verify_password(password, user.password_hash):
            user_id = user.id
            session.expunge(user)
            register_login_result(SessionLocal, ELITE_MODELS, User, login, user_id, True)
            st.session_state.auth_error = ""
            return user
    register_login_result(SessionLocal, ELITE_MODELS, User, login, None, False)
    st.session_state.auth_error = "The username, email, or password is incorrect."
    return None


def hero(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="nv-hero">
            <div class="nv-kicker">{kicker}</div>
            <div class="nv-title">{title}</div>
            <div class="nv-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_card(
    label: str,
    value: str,
    meta: str,
    pct: float | None = None,
    accent: str = "#6C5CE7",
    accent_end: str | None = None,
) -> None:
    bar = ""
    if pct is not None:
        safe_pct = max(0.0, min(100.0, float(pct)))
        end_color = accent_end or accent
        bar = (
            f'<div class="nv-progress-row"><span class="nv-progress-percent" '
            f'style="color:{accent}">{safe_pct:.0f}%</span></div>'
            f'<div class="nv-progress">'
            f'<span style="width:{safe_pct:.1f}%;background:linear-gradient(90deg,{accent},{end_color});'
            f'box-shadow:0 0 14px {accent}55"></span></div>'
        )
    st.markdown(
        f'<div class="nv-card" style="--card-accent:{accent};--card-accent-end:{accent_end or accent}"><div class="nv-label">{label}</div><div class="nv-value">{value}</div><div class="nv-meta">{meta}</div>{bar}</div>',
        unsafe_allow_html=True,
    )


def circular_metric_card(
    label: str,
    value: str,
    unit: str,
    meta: str,
    pct: float,
    accent: str,
) -> None:
    safe_pct = max(0.0, min(100.0, float(pct)))
    angle = safe_pct * 3.6
    track = "#E8EBF5"
    if safe_pct <= 0:
        ring_background = track
        ring_shadow = "inset 0 0 0 1px rgba(99,102,241,.05)"
        status_color = "#D9DEEA"
        status_shadow = "none"
    else:
        ring_background = (
            f"conic-gradient(from 0deg, {accent} 0deg, {accent} {angle:.2f}deg, "
            f"{track} {angle:.2f}deg, {track} 360deg)"
        )
        ring_shadow = f"0 0 16px {accent}33, inset 0 0 0 1px rgba(99,102,241,.05)"
        status_color = accent
        status_shadow = f"0 0 12px {accent}88"

    st.markdown(
        f"""
        <div class="nv-ring-card" style="--ring-color:{accent}">
            <div class="nv-label">{label}</div>
            <div class="nv-dashboard-ring" style="background:{ring_background};box-shadow:{ring_shadow}">
                <div class="nv-ring-center">
                    <div class="nv-ring-value">{value}</div>
                    <div class="nv-ring-unit">{unit}</div>
                </div>
            </div>
            <div class="nv-ring-meta">{meta}</div>
            <div class="nv-ring-status" style="background:{status_color};box-shadow:{status_shadow}"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _pct(value: float, target: float) -> float:
    return max(0.0, min(100.0, value / max(target, 1) * 100))


def cm_to_inches(value_cm: float | None) -> float:
    return float(value_cm or 0) / CM_PER_INCH


def inches_to_cm(value_inches: float | None) -> float:
    return float(value_inches or 0) * CM_PER_INCH


def ml_to_fl_oz(value_ml: float | None) -> float:
    return float(value_ml or 0) / ML_PER_FL_OZ


def fl_oz_to_ml(value_oz: float | None) -> int:
    return int(round(float(value_oz or 0) * ML_PER_FL_OZ))


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


@st.cache_data(ttl=86400, show_spinner=False)
def cached_product_lookup(barcode: str) -> dict[str, Any]:
    return lookup_open_food_facts(barcode)


def add_scanned_food(
    user: User,
    log_date: date,
    meal: str,
    food_name: str,
    serving: str,
    calories: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    fiber_g: float,
    source: str,
    confidence: float = 0.0,
    barcode: str = "",
    notes: str = "",
) -> None:
    clean_notes = notes.strip()
    if fiber_g > 0:
        clean_notes = f"{clean_notes}\nFiber: {fiber_g:.1f} g".strip()
    with SessionLocal() as session:
        session.add(
            FoodLog(
                user_id=user.id,
                log_date=log_date,
                meal=meal,
                food_name=food_name.strip() or "Scanned food",
                serving=serving.strip() or "1 serving",
                calories=_safe_float(calories),
                protein_g=_safe_float(protein_g),
                carbs_g=_safe_float(carbs_g),
                fat_g=_safe_float(fat_g),
                notes=clean_notes,
            )
        )
        session.add(
            SmartScan(
                user_id=user.id,
                scan_date=log_date,
                source=source,
                food_name=food_name.strip() or "Scanned food",
                barcode=barcode.strip(),
                serving=serving.strip() or "1 serving",
                calories=_safe_float(calories),
                protein_g=_safe_float(protein_g),
                carbs_g=_safe_float(carbs_g),
                fat_g=_safe_float(fat_g),
                fiber_g=_safe_float(fiber_g),
                confidence=max(0.0, min(1.0, float(confidence or 0))),
                notes=clean_notes,
            )
        )
        session.commit()


def render_macro_result(result: dict[str, Any], source_label: str) -> None:
    confidence = _safe_float(result.get("confidence"), 0.0)
    confidence_text = f"{confidence * 100:.0f}% confidence" if confidence else source_label
    st.markdown(
        f"""
        <div class="nv-score-wrap">
            <div class="nv-score-ring" style="--score:{max(1, confidence * 100):.0f}"><strong>{confidence * 100:.0f}%</strong></div>
            <div>
                <div class="nv-label">{source_label}</div>
                <div style="font-size:1.35rem;font-weight:850;color:#172033;margin:.25rem 0">{_esc(result.get('dish_name') or result.get('product_name') or 'Nutrition result')}</div>
                <div class="nv-meta">{_esc(result.get('serving_description') or result.get('serving_size') or '1 serving')} · {confidence_text}</div>
            </div>
        </div>
        <div class="nv-result-grid">
            <div class="nv-result-cell"><span class="nv-label">Calories</span><b>{_safe_float(result.get('calories')):.0f}</b></div>
            <div class="nv-result-cell"><span class="nv-label">Protein</span><b>{_safe_float(result.get('protein_g')):.1f} g</b></div>
            <div class="nv-result-cell"><span class="nv-label">Carbs</span><b>{_safe_float(result.get('carbs_g')):.1f} g</b></div>
            <div class="nv-result-cell"><span class="nv-label">Fat</span><b>{_safe_float(result.get('fat_g')):.1f} g</b></div>
            <div class="nv-result-cell"><span class="nv-label">Fiber</span><b>{_safe_float(result.get('fiber_g')):.1f} g</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def daily_fuel_score(user: User, totals: dict[str, float]) -> int:
    components = [
        100 - abs(100 - _pct(totals["calories"], user.calorie_target)),
        100 - abs(100 - _pct(totals["protein"], user.protein_target)),
        100 - abs(100 - _pct(totals["carbs"], user.carb_target)),
        100 - abs(100 - _pct(totals["fat"], user.fat_target)),
        _pct(totals["water"], user.water_target_ml),
    ]
    return int(round(max(0.0, min(100.0, mean(components)))))


def readiness_score(checkin: DailyCheckIn | None, totals: dict[str, float], user: User) -> int:
    if not checkin:
        return 0
    sleep_score = min(100.0, checkin.sleep_hours / 8.0 * 100)
    energy_score = checkin.energy / 10 * 100
    mood_score = checkin.mood / 10 * 100
    stress_score = (11 - checkin.stress) / 10 * 100
    soreness_score = (11 - checkin.soreness) / 10 * 100
    nutrition_score = (_pct(totals["protein"], user.protein_target) + _pct(totals["water"], user.water_target_ml)) / 2
    weighted = (
        sleep_score * .30
        + energy_score * .18
        + mood_score * .12
        + stress_score * .14
        + soreness_score * .14
        + nutrition_score * .12
    )
    return int(round(max(0.0, min(100.0, weighted))))


def readiness_label(score: int) -> tuple[str, str]:
    if score >= 80:
        return "High readiness", "A strong day for your planned training. Keep hydration steady."
    if score >= 60:
        return "Balanced readiness", "Train as planned, but keep intensity flexible."
    if score > 0:
        return "Recovery priority", "Choose lighter movement, mobility, and an earlier recovery routine."
    return "Check-in needed", "Log sleep, energy, stress, and soreness to generate your readiness score."


def render_auth() -> None:
    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)
        hero("Private fitness operating system", APP_NAME, APP_TAGLINE)
        st.markdown(
            """
            <div class="nv-card" style="min-height:260px">
                <div class="nv-label">Built for daily consistency</div>
                <div style="font-size:1.35rem;font-weight:820;margin:.55rem 0">One account. Your complete health record.</div>
                <div class="nv-meta" style="font-size:.96rem;line-height:1.7">
                    Track food, macros, water, workouts, strength sets, body measurements, and goals.
                    Your records stay linked to your private account across sessions.
                </div>
                <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem;margin-top:1.1rem">
                    <div style="padding:.8rem;border-radius:14px;background:#F5F3FF"><b>Nutrition</b><br><span class="nv-meta">Calories and macros</span></div>
                    <div style="padding:.8rem;border-radius:14px;background:#ECFEFF"><b>Training</b><br><span class="nv-meta">Sessions and sets</span></div>
                    <div style="padding:.8rem;border-radius:14px;background:#F0FDF4"><b>Progress</b><br><span class="nv-meta">Weight and measurements</span></div>
                    <div style="padding:.8rem;border-radius:14px;background:#FFF7ED"><b>Goals</b><br><span class="nv-meta">Targets and deadlines</span></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)
        st.markdown('<div class="nv-auth-heading">Welcome to NouriVanta</div>', unsafe_allow_html=True)
        st.markdown('<div class="nv-auth-copy">Sign in to continue or create your private account.</div>', unsafe_allow_html=True)

        sign_in_col, create_col = st.columns(2, gap="small")
        with sign_in_col:
            sign_in_clicked = st.button(
                "Sign in",
                key="show_sign_in",
                type="primary" if st.session_state.auth_mode == "Sign in" else "secondary",
                width="stretch",
            )
        with create_col:
            create_clicked = st.button(
                "Create account",
                key="show_create_account",
                type="primary" if st.session_state.auth_mode == "Create account" else "secondary",
                width="stretch",
            )

        if sign_in_clicked and st.session_state.auth_mode != "Sign in":
            st.session_state.auth_mode = "Sign in"
            st.rerun()
        if create_clicked and st.session_state.auth_mode != "Create account":
            st.session_state.auth_mode = "Create account"
            st.rerun()

        if st.session_state.auth_mode == "Sign in":
            with st.form("login_form"):
                login = st.text_input("Username or email")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
            if submitted:
                user = authenticate(login, password)
                if user:
                    st.session_state.user_id = user.id
                    st.session_state.username = user.username
                    st.session_state.page = "Dashboard"
                    st.session_state.timezone_loaded_for_user = None
                    st.session_state.last_activity_at = utc_now()
                    st.rerun()
                else:
                    st.error(st.session_state.get("auth_error") or "The username, email, or password is incorrect.")
            with st.expander("Forgot password or locked out?"):
                with st.form("recovery_reset_form"):
                    recovery_login = st.text_input("Username or email", key="recovery_login")
                    recovery_code = st.text_input("Recovery code", type="password")
                    new_password = st.text_input("New password", type="password", key="recovery_new_password")
                    new_confirm = st.text_input("Confirm new password", type="password", key="recovery_confirm_password")
                    reset_submitted = st.form_submit_button("Reset password", width="stretch")
                if reset_submitted:
                    if new_password != new_confirm:
                        st.error("The new passwords do not match.")
                    elif len(new_password) < 8 or not re.search(r"[A-Za-z]", new_password) or not re.search(r"\d", new_password):
                        st.error("Use at least 8 characters with one letter and one number.")
                    else:
                        ok, message = reset_password_with_recovery(
                            SessionLocal, ELITE_MODELS, User, verify_password, hash_password,
                            recovery_login, recovery_code, new_password,
                        )
                        st.success(message) if ok else st.error(message)
        else:
            with st.form("create_account_form"):
                display_name = st.text_input("Display name")
                username = st.text_input("Username")
                email = st.text_input("Email")
                password = st.text_input(
                    "Password",
                    type="password",
                    help="Use 8 or more characters with at least one letter and one number.",
                )
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create account", type="primary", width="stretch")
            if submitted:
                if password != confirm:
                    st.error("The passwords do not match.")
                else:
                    ok, message = create_account(username, email, password, display_name)
                    if ok:
                        st.success(message)
                        if st.session_state.get("elite_new_recovery_code"):
                            st.warning("Save this recovery code now. It is required if you forget your password.")
                            st.code(st.session_state.elite_new_recovery_code)
                        st.session_state.auth_mode = "Sign in"
                    else:
                        st.error(message)


def sidebar(user: User) -> None:
    with st.sidebar:
        st.markdown('<div class="nv-brand">Nouri<span>Vanta</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="nv-user">Signed in as {user.display_name or user.username}</div>', unsafe_allow_html=True)
        st.caption(f"Local time zone: {active_timezone_name()}")

        # Move users from former standalone pages into the consolidated sections.
        legacy_page_map = {
            "Smart Scan": ("Nutrition", "Smart Scan"),
            "Goals": ("Progress & Goals", "Goals"),
            "Progress": ("Progress & Goals", "Measurements"),
            "NouriVolt Elite": ("Progress & Goals", "Adaptive Coach"),
            "NouriVanta Elite": ("Progress & Goals", "Adaptive Coach"),
            "Profile": ("Settings", "Profile"),
            "Data & account": ("Settings", "Data & account"),
        }
        current_page = st.session_state.get("page", "Dashboard")
        if current_page in legacy_page_map:
            parent_page, subpage = legacy_page_map[current_page]
            st.session_state.page = parent_page
            if parent_page == "Nutrition":
                st.session_state.nutrition_subpage = subpage
            elif parent_page == "Progress & Goals":
                st.session_state.progress_subpage = subpage
            elif parent_page == "Settings":
                st.session_state.settings_subpage = subpage

        pages = [
            "Dashboard",
            "Nutrition",
            "Workouts",
            "Readiness",
            "Progress & Goals",
            "Settings",
        ]
        if st.session_state.page not in pages:
            st.session_state.page = "Dashboard"

        chosen = st.radio(
            "Navigation",
            pages,
            index=pages.index(st.session_state.page),
            label_visibility="collapsed",
        )
        if chosen != st.session_state.page:
            st.session_state.page = chosen
            st.rerun()
        st.divider()
        if st.button("Log out", width="stretch"):
            st.session_state.user_id = None
            st.session_state.username = None
            st.session_state.page = "Dashboard"
            st.session_state.timezone_loaded_for_user = None
            st.session_state.timezone_mode = "auto"
            st.session_state.manual_timezone_name = ""
            st.rerun()


def today_nutrition(session: Session, user: User, selected_date: date) -> dict[str, float]:
    row = session.execute(
        select(
            func.coalesce(func.sum(FoodLog.calories), 0),
            func.coalesce(func.sum(FoodLog.protein_g), 0),
            func.coalesce(func.sum(FoodLog.carbs_g), 0),
            func.coalesce(func.sum(FoodLog.fat_g), 0),
        ).where(FoodLog.user_id == user.id, FoodLog.log_date == selected_date)
    ).one()
    water = session.scalar(
        select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
            WaterLog.user_id == user.id, WaterLog.log_date == selected_date
        )
    )
    return {"calories": float(row[0]), "protein": float(row[1]), "carbs": float(row[2]), "fat": float(row[3]), "water": float(water or 0)}


def render_dashboard(user: User) -> None:
    hero("Today", f"Welcome back, {user.display_name or user.username}", "See your daily targets, recent training, and current progress in one place.")
    selected_date = st.date_input("Dashboard date", value=local_today(), format="MM/DD/YYYY", key="dashboard_date")

    with SessionLocal() as session:
        totals = today_nutrition(session, user, selected_date)
        day_workouts = session.scalars(
            select(WorkoutSession).where(
                WorkoutSession.user_id == user.id,
                WorkoutSession.workout_date == selected_date,
            )
        ).all()
        workout_count = len(day_workouts)
        workout_minutes = 0
        for workout in day_workouts:
            saved_minutes = int(workout.duration_min or 0)
            if saved_minutes > 0:
                workout_minutes += saved_minutes
                continue
            set_minutes = session.scalar(
                select(func.coalesce(func.sum(ExerciseSet.duration_min), 0)).where(
                    ExerciseSet.session_id == workout.id
                )
            ) or 0
            workout_minutes += int(round(float(set_minutes)))
        latest_measurements = session.scalars(
            select(Measurement).where(Measurement.user_id == user.id).order_by(Measurement.measurement_date.desc()).limit(12)
        ).all()
        recent_workouts = session.scalars(
            select(WorkoutSession).where(WorkoutSession.user_id == user.id).order_by(WorkoutSession.workout_date.desc(), WorkoutSession.id.desc()).limit(5)
        ).all()
        daily_checkin = session.scalar(
            select(DailyCheckIn).where(
                DailyCheckIn.user_id == user.id,
                DailyCheckIn.checkin_date == selected_date,
            )
        )

    ready_score = readiness_score(daily_checkin, totals, user)
    ready_title, ready_copy = readiness_label(ready_score)

    # Keep the four core nutrition targets together at the top.
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        circular_metric_card(
            "Calories",
            f"{totals['calories']:.0f}",
            "kcal",
            f"of {user.calorie_target:,} kcal",
            totals["calories"] / max(user.calorie_target, 1) * 100,
            "#6C5CE7",
        )
    with r2:
        circular_metric_card(
            "Protein",
            f"{totals['protein']:.0f}",
            "grams",
            f"of {user.protein_target} g",
            totals["protein"] / max(user.protein_target, 1) * 100,
            "#16B8C4",
        )
    with r3:
        circular_metric_card(
            "Carbohydrates",
            f"{totals['carbs']:.0f}",
            "grams",
            f"of {user.carb_target} g",
            totals["carbs"] / max(user.carb_target, 1) * 100,
            "#F2994A",
        )
    with r4:
        circular_metric_card(
            "Fat",
            f"{totals['fat']:.0f}",
            "grams",
            f"of {user.fat_target} g",
            totals["fat"] / max(user.fat_target, 1) * 100,
            "#D65DB1",
        )

    st.subheader("Daily balance")
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        metric_card(
            "Water",
            f"{ml_to_fl_oz(totals['water']):.0f} fl oz",
            f"of {ml_to_fl_oz(user.water_target_ml):.0f} fl oz",
            totals["water"] / max(user.water_target_ml, 1) * 100,
            accent="#2F80ED",
            accent_end="#56CCF2",
        )
    with b2:
        readiness_accent = "#27AE60" if ready_score >= 80 else "#16B8C4" if ready_score >= 60 else "#F2994A" if ready_score >= 40 else "#EB5757"
        metric_card(
            "Readiness",
            f"{ready_score}",
            ready_title,
            ready_score,
            accent=readiness_accent,
            accent_end=readiness_accent,
        )
    with b3:
        metric_card("Training", f"{workout_minutes} min", f"{workout_count} session{'s' if workout_count != 1 else ''}")
    with b4:
        remaining = max(0, user.calorie_target - totals["calories"])
        metric_card("Calories remaining", f"{remaining:.0f}", "Based on your daily target")

    left, right = st.columns([1.15, .85], gap="large")
    with left:
        st.subheader("Weight trend")
        weight_rows = [m for m in reversed(latest_measurements) if m.weight_lb is not None]
        if weight_rows:
            df = pd.DataFrame({"Date": [m.measurement_date for m in weight_rows], "Weight (lb)": [m.weight_lb for m in weight_rows]}).set_index("Date")
            st.line_chart(df, width="stretch")
        else:
            st.markdown('<div class="nv-empty">Add a body measurement to start your progress chart.</div>', unsafe_allow_html=True)
    with right:
        st.subheader("Recent workouts")
        if recent_workouts:
            for workout in recent_workouts:
                st.markdown(f"**{workout.workout_name}**  \n{workout.workout_date.strftime('%m/%d/%Y')} · {workout.category} · {workout.duration_min} min")
        else:
            st.markdown('<div class="nv-empty">Your recent workouts will appear here.</div>', unsafe_allow_html=True)

    st.subheader("Future signals")
    fuel_score = daily_fuel_score(user, totals)
    calories_left = max(0.0, user.calorie_target - totals["calories"])
    protein_left = max(0.0, user.protein_target - totals["protein"])
    st.markdown(
        f"""
        <div class="nv-future-grid">
            <div class="nv-future-card"><div class="nv-label">Fuel balance</div><div style="font-size:2rem;font-weight:900;margin:.35rem 0">{fuel_score}</div><div class="nv-meta">A live score based on calories, macros, and hydration against your targets.</div></div>
            <div class="nv-future-card"><div class="nv-label">Readiness pulse</div><div style="font-size:1.3rem;font-weight:850;margin:.4rem 0">{ready_title}</div><div class="nv-meta">{ready_copy}</div></div>
            <div class="nv-future-card"><div class="nv-label">Next-meal signal</div><div style="font-size:1.3rem;font-weight:850;margin:.4rem 0">{calories_left:.0f} kcal left</div><div class="nv-meta">Prioritize about {protein_left:.0f} g of protein across your remaining meals.</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_nutrition(user: User) -> None:
    hero("Nutrition", "Fuel your day", "Log meals, monitor calories and macros, and keep hydration visible.")
    selected_date = st.date_input("Log date", value=local_today(), format="MM/DD/YYYY", key="nutrition_date")

    with SessionLocal() as session:
        totals = today_nutrition(session, user, selected_date)
        logs = session.scalars(
            select(FoodLog).where(FoodLog.user_id == user.id, FoodLog.log_date == selected_date).order_by(FoodLog.created_at.desc())
        ).all()
        water_logs = session.scalars(
            select(WaterLog).where(WaterLog.user_id == user.id, WaterLog.log_date == selected_date).order_by(WaterLog.created_at.desc())
        ).all()

    c1, c2, c3, c4 = st.columns(4)
    for col, label, value, target in [
        (c1, "Calories", totals["calories"], user.calorie_target),
        (c2, "Protein", totals["protein"], user.protein_target),
        (c3, "Carbs", totals["carbs"], user.carb_target),
        (c4, "Fat", totals["fat"], user.fat_target),
    ]:
        with col:
            suffix = "kcal" if label == "Calories" else "g"
            metric_card(label, f"{value:.0f} {suffix}", f"Target {target} {suffix}", value / max(target, 1) * 100)

    add_food, add_water = st.tabs(["Add food", "Add water"])
    with add_food:
        with st.form("add_food_form", clear_on_submit=True):
            a, b = st.columns(2)
            meal = a.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"])
            food_name = b.text_input("Food name")
            serving = st.text_input("Serving", value="1 serving")
            c1, c2, c3, c4 = st.columns(4)
            calories = c1.number_input("Calories", min_value=0.0, step=10.0)
            protein = c2.number_input("Protein (g)", min_value=0.0, step=1.0)
            carbs = c3.number_input("Carbs (g)", min_value=0.0, step=1.0)
            fat = c4.number_input("Fat (g)", min_value=0.0, step=1.0)
            notes = st.text_area("Notes", height=80)
            submitted = st.form_submit_button("Save food", type="primary", width="stretch")
        if submitted:
            if not food_name.strip():
                st.error("Enter a food name.")
            else:
                with SessionLocal() as session:
                    session.add(FoodLog(user_id=user.id, log_date=selected_date, meal=meal, food_name=food_name.strip(), serving=serving.strip(), calories=calories, protein_g=protein, carbs_g=carbs, fat_g=fat, notes=notes.strip()))
                    session.commit()
                st.success("Food saved.")
                st.rerun()
    with add_water:
        with st.form("add_water_form", clear_on_submit=True):
            amount_oz = st.number_input("Amount (fl oz)", min_value=1.0, max_value=170.0, value=8.0, step=1.0)
            submitted = st.form_submit_button("Add water", type="primary", width="stretch")
        if submitted:
            with SessionLocal() as session:
                session.add(WaterLog(user_id=user.id, log_date=selected_date, amount_ml=fl_oz_to_ml(amount_oz)))
                session.commit()
            st.success("Water saved.")
            st.rerun()

    st.subheader("Food log")
    if logs:
        df = pd.DataFrame([
            {
                "Date": x.log_date.strftime("%m/%d/%Y"),
                "Meal": x.meal,
                "Food": x.food_name,
                "Serving": x.serving,
                "Calories": x.calories,
                "Protein": x.protein_g,
                "Carbs": x.carbs_g,
                "Fat": x.fat_g,
                "ID": x.id,
            }
            for x in logs
        ])
        display_df = df.drop(columns=["ID"]).copy()
        st.dataframe(display_df, width="stretch", hide_index=True)
        delete_id = st.selectbox("Remove a food entry", options=[None] + [x.id for x in logs], format_func=lambda value: "Select an entry" if value is None else next(f"{x.meal}: {x.food_name}" for x in logs if x.id == value))
        if st.button("Delete selected food", disabled=delete_id is None):
            with SessionLocal() as session:
                session.execute(delete(FoodLog).where(FoodLog.id == delete_id, FoodLog.user_id == user.id))
                session.commit()
            st.rerun()
    else:
        st.markdown('<div class="nv-empty">No food has been logged for this date.</div>', unsafe_allow_html=True)

    st.subheader("Hydration")
    metric_card("Water logged", f"{ml_to_fl_oz(totals['water']):.1f} fl oz", f"Daily target {ml_to_fl_oz(user.water_target_ml):.0f} fl oz", totals["water"] / max(user.water_target_ml, 1) * 100)
    if water_logs:
        if st.button("Remove last water entry"):
            with SessionLocal() as session:
                session.execute(delete(WaterLog).where(WaterLog.id == water_logs[0].id, WaterLog.user_id == user.id))
                session.commit()
            st.rerun()


def render_smart_scan(user: User) -> None:
    hero(
        "Computer vision nutrition",
        "Smart Scan Lab",
        "Photograph a meal, scan a packaged-food barcode, then review and save the macros to your existing diary.",
    )
    st.markdown(
        """
        <div class="nv-future-grid">
            <div class="nv-future-card"><div class="nv-label">Food Vision</div><div style="font-size:1.2rem;font-weight:850;margin:.4rem 0">Plate-to-macro analysis</div><div class="nv-meta">Estimate a visible serving and edit every value before saving.</div></div>
            <div class="nv-future-card"><div class="nv-label">Barcode Vision</div><div style="font-size:1.2rem;font-weight:850;margin:.4rem 0">UPC and EAN lookup</div><div class="nv-meta">Decode a camera image and retrieve packaged-food nutrition.</div></div>
            <div class="nv-future-card"><div class="nv-label">Adaptive Fuel</div><div style="font-size:1.2rem;font-weight:850;margin:.4rem 0">Next-meal targets</div><div class="nv-meta">Turn your remaining daily macros into practical meal targets.</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    photo_tab, barcode_tab, navigator_tab, history_tab = st.tabs(
        ["Food photo", "Barcode", "Next-meal engine", "Scan history"]
    )

    with photo_tab:
        left, right = st.columns([1.05, .95], gap="large")
        with left:
            st.markdown(
                '<div class="nv-scan-shell"><div class="nv-label">Live plate scanner</div><div style="font-size:1.15rem;font-weight:850;margin:.35rem 0">Center the full meal in the frame</div><div class="nv-meta">Use even lighting and show the plate from a slight overhead angle.</div></div>',
                unsafe_allow_html=True,
            )
            food_camera = st.camera_input("Take a clear picture of the entire meal", key="smart_food_camera")
            food_upload = st.file_uploader(
                "Or upload a food photo",
                type=["jpg", "jpeg", "png", "webp"],
                key="smart_food_upload",
            )
            meal_context = st.text_area(
                "Helpful details",
                placeholder="Example: 6-ounce grilled chicken breast, 1 cup rice, sauce on the side",
                key="food_photo_context",
            )
            with st.expander("AI connection", expanded=not bool(st.session_state.openai_api_key)):
                st.text_input(
                    "OpenAI API key",
                    type="password",
                    key="openai_api_key",
                    help="The key stays in this browser session and is not written to the NouriVanta database.",
                )
                model_name = os.getenv("OPENAI_VISION_MODEL", "gpt-5.6")
                status = "Connected" if st.session_state.openai_api_key else "Key required"
                st.caption(f"Status: {status}. Vision model: {model_name}")

            image_file = food_camera or food_upload
            if st.button("Analyze meal photo", type="primary", width="stretch", key="analyze_food_photo"):
                if image_file is None:
                    st.error("Take or upload a food photo first.")
                elif not st.session_state.openai_api_key:
                    st.error("Enter an OpenAI API key in AI connection.")
                else:
                    try:
                        with st.spinner("Analyzing the visible serving and estimating macros..."):
                            st.session_state.food_scan_result = analyze_food_image(
                                image_file.getvalue(),
                                st.session_state.openai_api_key,
                                meal_context=meal_context,
                            )
                        st.success("Analysis complete. Review the estimate before saving.")
                    except VisionServiceError as exc:
                        st.error(str(exc))

        with right:
            result = st.session_state.food_scan_result
            if not result:
                st.markdown(
                    '<div class="nv-empty">Your nutrition estimate will appear here. Use a well-lit photo and keep the full plate visible.</div>',
                    unsafe_allow_html=True,
                )
            else:
                render_macro_result(result, "AI food estimate")
                ingredients = result.get("ingredients") or []
                if ingredients:
                    chips = "".join(f'<span class="nv-chip">{_esc(item)}</span>' for item in ingredients[:10])
                    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)
                assumptions = result.get("assumptions") or []
                if assumptions:
                    st.markdown(
                        '<div class="nv-muted-panel"><b>Estimate assumptions</b><br>'
                        + "<br>".join(f"• {_esc(item)}" for item in assumptions[:6])
                        + "</div>",
                        unsafe_allow_html=True,
                    )

                with st.form("save_food_photo_result"):
                    log_date = st.date_input("Diary date", value=local_today(), format="MM/DD/YYYY", key="food_photo_log_date")
                    meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="food_photo_meal")
                    food_name = st.text_input("Food name", value=str(result.get("dish_name") or "Scanned meal"))
                    serving = st.text_input("Serving", value=str(result.get("serving_description") or "1 serving"))
                    c1, c2, c3 = st.columns(3)
                    calories = c1.number_input("Calories", min_value=0.0, value=_safe_float(result.get("calories")), step=10.0)
                    protein = c2.number_input("Protein (g)", min_value=0.0, value=_safe_float(result.get("protein_g")), step=1.0)
                    carbs = c3.number_input("Carbs (g)", min_value=0.0, value=_safe_float(result.get("carbs_g")), step=1.0)
                    c4, c5 = st.columns(2)
                    fat = c4.number_input("Fat (g)", min_value=0.0, value=_safe_float(result.get("fat_g")), step=1.0)
                    fiber = c5.number_input("Fiber (g)", min_value=0.0, value=_safe_float(result.get("fiber_g")), step=1.0)
                    notes = st.text_area("Notes", value="AI estimate reviewed by user")
                    saved = st.form_submit_button("Save to food diary", type="primary", width="stretch")
                if saved:
                    add_scanned_food(
                        user,
                        log_date,
                        meal,
                        food_name,
                        serving,
                        calories,
                        protein,
                        carbs,
                        fat,
                        fiber,
                        "Food photo",
                        confidence=_safe_float(result.get("confidence")),
                        notes=notes,
                    )
                    st.session_state.food_scan_result = None
                    st.success("Meal saved to your nutrition diary.")
                    st.rerun()

    with barcode_tab:
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown(
                '<div class="nv-scan-shell"><div class="nv-label">Barcode scanner</div><div style="font-size:1.15rem;font-weight:850;margin:.35rem 0">Fill the frame with the bars</div><div class="nv-meta">Keep the package flat, avoid glare, and hold the camera steady.</div></div>',
                unsafe_allow_html=True,
            )
            barcode_camera = st.camera_input("Photograph the barcode", key="barcode_camera")
            barcode_upload = st.file_uploader(
                "Or upload a barcode image",
                type=["jpg", "jpeg", "png", "webp"],
                key="barcode_upload",
            )
            barcode_image = barcode_camera or barcode_upload
            if st.button("Detect barcode", width="stretch", key="detect_barcode"):
                if barcode_image is None:
                    st.error("Take or upload a barcode image first.")
                else:
                    try:
                        detected = decode_barcode(barcode_image.getvalue())
                        st.session_state.detected_barcode = detected.text
                        st.session_state.detected_barcode_format = detected.format_name
                        st.session_state.barcode_product = None
                        st.success(f"Detected {detected.text}")
                    except VisionServiceError as exc:
                        st.error(str(exc))

            detected_code = st.session_state.get("detected_barcode", "")
            if detected_code:
                st.markdown(
                    f'<div class="nv-muted-panel"><b>Detected barcode</b><br>{_esc(detected_code)} · {_esc(st.session_state.get("detected_barcode_format", ""))}</div>',
                    unsafe_allow_html=True,
                )
            manual_barcode = st.text_input("Barcode number", value=detected_code, key="manual_barcode_lookup")
            if st.button("Look up nutrition", type="primary", width="stretch", key="lookup_barcode"):
                try:
                    with st.spinner("Searching packaged-food nutrition..."):
                        st.session_state.barcode_product = cached_product_lookup(manual_barcode)
                    st.success("Product found. Review the serving and macros.")
                except VisionServiceError as exc:
                    st.error(str(exc))

        with right:
            product = st.session_state.barcode_product
            if not product:
                st.markdown(
                    '<div class="nv-empty">The decoded product and nutrition details will appear here. Manual barcode entry remains available when a photo is difficult to read.</div>',
                    unsafe_allow_html=True,
                )
            else:
                image_url = product.get("image_url")
                if image_url:
                    st.image(image_url, width=180)
                st.markdown(
                    f"### {product.get('product_name', 'Product')}\n{product.get('brand', '')}\n\n"
                    f"Barcode: `{product.get('barcode', '')}`",
                )
                grade = product.get("nutrition_grade") or "N/A"
                nova = product.get("nova_group") or "N/A"
                st.markdown(
                    f'<span class="nv-chip">Nutri-Score {_esc(grade)}</span><span class="nv-chip">NOVA {_esc(nova)}</span>',
                    unsafe_allow_html=True,
                )

                basis = st.radio(
                    "Nutrition basis",
                    ["Database serving", "100 g", "Custom grams"],
                    horizontal=True,
                    key="barcode_basis",
                )
                custom_grams = 100.0
                if basis == "Custom grams":
                    custom_grams = st.number_input("Portion grams", min_value=1.0, value=100.0, step=5.0)
                servings = st.number_input("Number of servings", min_value=0.1, value=1.0, step=0.25)

                if basis == "Database serving":
                    source_values = product.get("per_serving") or {}
                    serving_text = product.get("serving_size") or "1 serving"
                    if not any(value is not None for value in source_values.values()):
                        source_values = product.get("per_100g") or {}
                        serving_text = "100 g"
                    factor = servings
                elif basis == "100 g":
                    source_values = product.get("per_100g") or {}
                    serving_text = "100 g"
                    factor = servings
                else:
                    source_values = product.get("per_100g") or {}
                    serving_text = f"{custom_grams:g} g"
                    factor = custom_grams / 100 * servings

                barcode_result = {
                    "product_name": product.get("product_name"),
                    "serving_size": serving_text,
                    "calories": _safe_float(source_values.get("calories")) * factor,
                    "protein_g": _safe_float(source_values.get("protein_g")) * factor,
                    "carbs_g": _safe_float(source_values.get("carbs_g")) * factor,
                    "fat_g": _safe_float(source_values.get("fat_g")) * factor,
                    "fiber_g": _safe_float(source_values.get("fiber_g")) * factor,
                    "confidence": .98,
                }
                render_macro_result(barcode_result, "Product database result")

                with st.form("save_barcode_result"):
                    log_date = st.date_input("Diary date", value=local_today(), format="MM/DD/YYYY", key="barcode_log_date")
                    meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="barcode_meal")
                    food_name = st.text_input("Food name", value=str(product.get("product_name") or "Scanned product"))
                    serving = st.text_input("Serving", value=f"{servings:g} × {serving_text}")
                    c1, c2, c3 = st.columns(3)
                    calories = c1.number_input("Calories", min_value=0.0, value=barcode_result["calories"], step=10.0)
                    protein = c2.number_input("Protein (g)", min_value=0.0, value=barcode_result["protein_g"], step=1.0)
                    carbs = c3.number_input("Carbs (g)", min_value=0.0, value=barcode_result["carbs_g"], step=1.0)
                    c4, c5 = st.columns(2)
                    fat = c4.number_input("Fat (g)", min_value=0.0, value=barcode_result["fat_g"], step=1.0)
                    fiber = c5.number_input("Fiber (g)", min_value=0.0, value=barcode_result["fiber_g"], step=1.0)
                    notes = st.text_area(
                        "Notes",
                        value=f"Barcode {product.get('barcode', '')}. Nutri-Score {grade}. NOVA {nova}.",
                    )
                    saved = st.form_submit_button("Save to food diary", type="primary", width="stretch")
                if saved:
                    add_scanned_food(
                        user,
                        log_date,
                        meal,
                        food_name,
                        serving,
                        calories,
                        protein,
                        carbs,
                        fat,
                        fiber,
                        "Barcode",
                        confidence=.98,
                        barcode=str(product.get("barcode") or ""),
                        notes=notes,
                    )
                    st.success("Product saved to your nutrition diary.")
                    st.rerun()

                ingredients = str(product.get("ingredients_text") or "").strip()
                if ingredients:
                    with st.expander("Ingredients"):
                        st.write(ingredients)

    with navigator_tab:
        target_date = st.date_input("Plan date", value=local_today(), format="MM/DD/YYYY", key="navigator_date")
        with SessionLocal() as session:
            totals = today_nutrition(session, user, target_date)
        remaining_meals = st.slider("Meals remaining", min_value=1, max_value=4, value=2)
        remaining = {
            "calories": max(0.0, user.calorie_target - totals["calories"]),
            "protein": max(0.0, user.protein_target - totals["protein"]),
            "carbs": max(0.0, user.carb_target - totals["carbs"]),
            "fat": max(0.0, user.fat_target - totals["fat"]),
        }
        st.markdown(
            f"""
            <div class="nv-result-grid">
                <div class="nv-result-cell"><span class="nv-label">Calories left</span><b>{remaining['calories']:.0f}</b></div>
                <div class="nv-result-cell"><span class="nv-label">Protein left</span><b>{remaining['protein']:.0f} g</b></div>
                <div class="nv-result-cell"><span class="nv-label">Carbs left</span><b>{remaining['carbs']:.0f} g</b></div>
                <div class="nv-result-cell"><span class="nv-label">Fat left</span><b>{remaining['fat']:.0f} g</b></div>
                <div class="nv-result-cell"><span class="nv-label">Per meal</span><b>{remaining['calories']/remaining_meals:.0f} kcal</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
            <div class="nv-score-wrap">
                <div class="nv-score-ring" style="--score:{min(100, _pct(totals['calories'], user.calorie_target)):.0f}"><strong>{remaining_meals}</strong></div>
                <div>
                    <div class="nv-label">Adaptive meal target</div>
                    <div style="font-size:1.25rem;font-weight:850;margin:.25rem 0">Aim per remaining meal</div>
                    <div class="nv-meta">{remaining['calories']/remaining_meals:.0f} kcal · {remaining['protein']/remaining_meals:.0f} g protein · {remaining['carbs']/remaining_meals:.0f} g carbs · {remaining['fat']/remaining_meals:.0f} g fat</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        p1, p2, p3 = st.columns(3)
        with p1:
            st.markdown('<div class="nv-future-card"><div class="nv-label">Protein anchor</div><div style="font-weight:820;margin:.45rem 0">Chicken, fish, lean beef, tofu, eggs, Greek yogurt</div><div class="nv-meta">Choose one main protein and size it toward your per-meal protein target.</div></div>', unsafe_allow_html=True)
        with p2:
            st.markdown('<div class="nv-future-card"><div class="nv-label">Energy base</div><div style="font-weight:820;margin:.45rem 0">Rice, potatoes, oats, fruit, whole-grain bread</div><div class="nv-meta">Use the carb target to adjust the portion rather than removing the food group.</div></div>', unsafe_allow_html=True)
        with p3:
            st.markdown('<div class="nv-future-card"><div class="nv-label">Finish and fiber</div><div style="font-weight:820;margin:.45rem 0">Vegetables, beans, avocado, nuts, olive oil</div><div class="nv-meta">Add color and fiber, then fit fats to the remaining target.</div></div>', unsafe_allow_html=True)

    with history_tab:
        with SessionLocal() as session:
            scans = session.scalars(
                select(SmartScan)
                .where(SmartScan.user_id == user.id)
                .order_by(SmartScan.created_at.desc())
                .limit(40)
            ).all()
        if scans:
            history_df = pd.DataFrame(
                [
                    {
                        "Date": item.scan_date,
                        "Source": item.source,
                        "Food": item.food_name,
                        "Serving": item.serving,
                        "Calories": item.calories,
                        "Protein": item.protein_g,
                        "Carbs": item.carbs_g,
                        "Fat": item.fat_g,
                        "Confidence": f"{item.confidence * 100:.0f}%" if item.confidence else "",
                    }
                    for item in scans
                ]
            )
            st.dataframe(history_df, width="stretch", hide_index=True)
        else:
            st.markdown('<div class="nv-empty">Food-photo and barcode scans will appear here after you save them.</div>', unsafe_allow_html=True)


def render_readiness(user: User) -> None:
    hero(
        "Adaptive recovery",
        "Readiness Pulse",
        "Combine sleep, energy, stress, soreness, mood, nutrition, and hydration into a daily training signal.",
    )
    selected_date = st.date_input("Check-in date", value=local_today(), format="MM/DD/YYYY", key="readiness_date")
    with SessionLocal() as session:
        existing = session.scalar(
            select(DailyCheckIn).where(
                DailyCheckIn.user_id == user.id,
                DailyCheckIn.checkin_date == selected_date,
            )
        )
        totals = today_nutrition(session, user, selected_date)

    current = existing
    with st.form("daily_readiness_form"):
        c1, c2 = st.columns(2)
        sleep_hours = c1.number_input(
            "Sleep hours",
            min_value=0.0,
            max_value=16.0,
            value=float(current.sleep_hours if current else 8.0),
            step=0.25,
        )
        steps = c2.number_input(
            "Steps",
            min_value=0,
            max_value=100000,
            value=int(current.steps if current else 0),
            step=500,
        )
        c1, c2, c3, c4 = st.columns(4)
        energy = c1.slider("Energy", 1, 10, int(current.energy if current else 5))
        stress = c2.slider("Stress", 1, 10, int(current.stress if current else 5))
        soreness = c3.slider("Soreness", 1, 10, int(current.soreness if current else 5))
        mood = c4.slider("Mood", 1, 10, int(current.mood if current else 5))
        notes = st.text_area("Notes", value=current.notes if current else "")
        saved = st.form_submit_button("Save daily signal", type="primary", width="stretch")
    if saved:
        with SessionLocal() as session:
            owned = session.scalar(
                select(DailyCheckIn).where(
                    DailyCheckIn.user_id == user.id,
                    DailyCheckIn.checkin_date == selected_date,
                )
            )
            if owned:
                owned.sleep_hours = float(sleep_hours)
                owned.steps = int(steps)
                owned.energy = int(energy)
                owned.stress = int(stress)
                owned.soreness = int(soreness)
                owned.mood = int(mood)
                owned.notes = notes.strip()
            else:
                session.add(
                    DailyCheckIn(
                        user_id=user.id,
                        checkin_date=selected_date,
                        sleep_hours=float(sleep_hours),
                        steps=int(steps),
                        energy=int(energy),
                        stress=int(stress),
                        soreness=int(soreness),
                        mood=int(mood),
                        notes=notes.strip(),
                    )
                )
            session.commit()
        st.success("Daily signal saved.")
        st.rerun()

    with SessionLocal() as session:
        current = session.scalar(
            select(DailyCheckIn).where(
                DailyCheckIn.user_id == user.id,
                DailyCheckIn.checkin_date == selected_date,
            )
        )
    score = readiness_score(current, totals, user)
    label, guidance = readiness_label(score)
    st.markdown(
        f"""
        <div class="nv-score-wrap">
            <div class="nv-score-ring" style="--score:{score}"><strong>{score}</strong></div>
            <div>
                <div class="nv-label">NouriVanta readiness</div>
                <div style="font-size:1.35rem;font-weight:850;margin:.25rem 0">{label}</div>
                <div class="nv-meta">{guidance}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if current:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_card("Sleep", f"{current.sleep_hours:.1f} h", "Recovery input", min(100, current.sleep_hours / 8 * 100))
        with c2:
            metric_card("Energy", f"{current.energy}/10", "Daily self-rating", current.energy * 10)
        with c3:
            metric_card("Stress", f"{current.stress}/10", "Lower supports readiness", (11 - current.stress) * 10)
        with c4:
            metric_card("Soreness", f"{current.soreness}/10", "Lower supports readiness", (11 - current.soreness) * 10)

    st.subheader("14-day signal")
    start_date = selected_date - timedelta(days=13)
    with SessionLocal() as session:
        checkins = session.scalars(
            select(DailyCheckIn)
            .where(
                DailyCheckIn.user_id == user.id,
                DailyCheckIn.checkin_date >= start_date,
                DailyCheckIn.checkin_date <= selected_date,
            )
            .order_by(DailyCheckIn.checkin_date)
        ).all()
        rows = []
        for item in checkins:
            day_totals = today_nutrition(session, user, item.checkin_date)
            rows.append(
                {
                    "Date": item.checkin_date,
                    "Readiness": readiness_score(item, day_totals, user),
                    "Sleep": item.sleep_hours,
                    "Steps": item.steps,
                }
            )
    if rows:
        readiness_df = pd.DataFrame(rows).set_index("Date")
        st.line_chart(readiness_df[["Readiness"]], width="stretch")
        st.dataframe(readiness_df.reset_index(), width="stretch", hide_index=True)
    else:
        st.markdown('<div class="nv-empty">Save daily check-ins to build your readiness trend.</div>', unsafe_allow_html=True)

    with SessionLocal() as session:
        saved_checkins = session.scalars(
            select(DailyCheckIn)
            .where(DailyCheckIn.user_id == user.id)
            .order_by(DailyCheckIn.checkin_date.desc(), DailyCheckIn.id.desc())
        ).all()

    if saved_checkins:
        st.markdown("#### Delete a readiness check-in")
        checkin_to_delete = st.selectbox(
            "Choose a readiness item to delete",
            saved_checkins,
            format_func=lambda item: (
                f"{item.checkin_date.strftime('%m/%d/%Y')} · "
                f"Sleep {item.sleep_hours:.1f} h · Energy {item.energy}/10 · "
                f"Stress {item.stress}/10"
            ),
            key="readiness_delete_checkin_select",
        )
        if st.button(
            "Delete selected readiness item",
            key="readiness_delete_checkin_button",
            width="stretch",
        ):
            with SessionLocal() as session:
                owned_checkin = session.scalar(
                    select(DailyCheckIn).where(
                        DailyCheckIn.id == checkin_to_delete.id,
                        DailyCheckIn.user_id == user.id,
                    )
                )
                if owned_checkin:
                    session.delete(owned_checkin)
                    session.commit()
            st.success("Readiness check-in deleted.")
            st.rerun()


def render_workouts(user: User) -> None:
    hero("Training", "Build your workout record", "Save sessions and detailed sets without losing data when you log out.")
    tab_session, tab_set, tab_history = st.tabs(["New session", "Add exercise set", "History"])

    with SessionLocal() as session:
        sessions = session.scalars(
            select(WorkoutSession).where(WorkoutSession.user_id == user.id).order_by(WorkoutSession.workout_date.desc(), WorkoutSession.id.desc()).limit(100)
        ).all()

    with tab_session:
        with st.form("workout_session_form", clear_on_submit=True):
            workout_date = st.date_input("Workout date", value=local_today(), format="MM/DD/YYYY")
            workout_name = st.text_input("Workout name", placeholder="Upper body strength")
            category = st.selectbox("Category", ["Strength", "Cardio", "Mobility", "Sports", "HIIT", "Recovery", "Other"])
            c1, c2 = st.columns(2)
            duration = c1.number_input("Duration (minutes)", min_value=0, max_value=600, value=45)
            calories = c2.number_input("Estimated calories burned", min_value=0, max_value=5000, value=0)
            notes = st.text_area("Notes", height=90)
            submitted = st.form_submit_button("Save workout session", type="primary", width="stretch")
        if submitted:
            if not workout_name.strip():
                st.error("Enter a workout name.")
            else:
                with SessionLocal() as session:
                    session.add(WorkoutSession(user_id=user.id, workout_date=workout_date, workout_name=workout_name.strip(), category=category, duration_min=int(duration), calories_burned=int(calories), notes=notes.strip()))
                    session.commit()
                st.session_state["dashboard_date"] = workout_date
                st.success(f"Workout saved. The dashboard will show {int(duration)} training minute(s) for {workout_date.strftime('%m/%d/%Y')}.")
                st.rerun()

    with tab_set:
        if not sessions:
            st.info("Save a workout session first.")
        else:
            session_options = {s.id: f"{s.workout_date.strftime('%m/%d/%Y')} · {s.workout_name}" for s in sessions}
            session_id = st.selectbox(
                "Workout session",
                list(session_options),
                format_func=session_options.get,
                key="exercise_set_session",
            )

            selector_left, selector_right = st.columns(2)
            body_part = selector_left.selectbox(
                "Body part or activity",
                list(EXERCISE_LIBRARY),
                key="exercise_body_part",
            )
            exercise_options = [*EXERCISE_LIBRARY[body_part], CUSTOM_EXERCISE_OPTION]
            selected_exercise = selector_right.selectbox(
                "Exercise",
                exercise_options,
                key="exercise_library_choice",
            )

            custom_exercise = ""
            if selected_exercise == CUSTOM_EXERCISE_OPTION:
                custom_exercise = st.text_input(
                    "Unique exercise name",
                    placeholder="Enter your exercise name",
                    key="custom_exercise_name",
                )
            exercise_name = custom_exercise.strip() if selected_exercise == CUSTOM_EXERCISE_OPTION else selected_exercise

            st.caption("Choose a standard exercise by body part, or select Custom exercise to enter your own.")
            with st.form("exercise_set_form", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                set_number = c1.number_input("Set number", min_value=1, max_value=50, value=1)
                reps = c2.number_input("Reps", min_value=0, max_value=1000, value=10)
                weight = c3.number_input("Weight (lb)", min_value=0.0, max_value=3000.0, value=0.0, step=2.5)
                c4, c5 = st.columns(2)
                distance = c4.number_input("Distance (miles)", min_value=0.0, max_value=500.0, value=0.0, step=0.1)
                set_duration = c5.number_input("Set duration (minutes)", min_value=0.0, max_value=600.0, value=0.0, step=0.5)
                submitted = st.form_submit_button("Save exercise set", type="primary", width="stretch")
            if submitted:
                if not exercise_name:
                    st.error("Enter a unique exercise name.")
                else:
                    with SessionLocal() as session:
                        owned = session.scalar(select(WorkoutSession).where(WorkoutSession.id == session_id, WorkoutSession.user_id == user.id))
                        if owned:
                            session.add(ExerciseSet(session_id=session_id, exercise_name=exercise_name, set_number=int(set_number), reps=int(reps), weight_lb=float(weight), distance_miles=float(distance), duration_min=float(set_duration)))
                            session.commit()
                    st.success("Exercise set saved.")
                    st.rerun()

    with tab_history:
        if not sessions:
            st.markdown('<div class="nv-empty">No workouts saved yet.</div>', unsafe_allow_html=True)
        else:
            for workout in sessions[:30]:
                with st.expander(f"{workout.workout_date.strftime('%m/%d/%Y')} · {workout.workout_name} · {workout.duration_min} min"):
                    st.write(f"Category: {workout.category}")
                    if workout.notes:
                        st.write(workout.notes)
                    with SessionLocal() as session:
                        sets = session.scalars(select(ExerciseSet).where(ExerciseSet.session_id == workout.id).order_by(ExerciseSet.exercise_name, ExerciseSet.set_number)).all()
                    if sets:
                        df = pd.DataFrame([{"Exercise": x.exercise_name, "Set": x.set_number, "Reps": x.reps, "Weight (lb)": x.weight_lb, "Distance (mi)": x.distance_miles, "Minutes": x.duration_min} for x in sets])
                        st.dataframe(df, width="stretch", hide_index=True)
                    else:
                        st.caption("No detailed exercise sets saved.")
                    if st.button("Delete workout", key=f"delete_workout_{workout.id}"):
                        with SessionLocal() as session:
                            session.execute(delete(WorkoutSession).where(WorkoutSession.id == workout.id, WorkoutSession.user_id == user.id))
                            session.commit()
                        st.rerun()


def render_progress(user: User) -> None:
    hero("Progress", "Measure what changes", "Track weight, body composition, and measurements over time.")
    with st.form("measurement_form", clear_on_submit=True):
        measurement_date = st.date_input("Measurement date", value=local_today(), format="MM/DD/YYYY")
        c1, c2, c3 = st.columns(3)
        weight = c1.number_input("Weight (lb)", min_value=0.0, max_value=1500.0, value=0.0, step=0.1)
        body_fat = c2.number_input("Body fat (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.1)
        waist = c3.number_input("Waist (in)", min_value=0.0, max_value=150.0, value=0.0, step=0.1)
        c4, c5, c6, c7 = st.columns(4)
        chest = c4.number_input("Chest (in)", min_value=0.0, max_value=150.0, value=0.0, step=0.1)
        hips = c5.number_input("Hips (in)", min_value=0.0, max_value=150.0, value=0.0, step=0.1)
        arm = c6.number_input("Arm (in)", min_value=0.0, max_value=80.0, value=0.0, step=0.1)
        thigh = c7.number_input("Thigh (in)", min_value=0.0, max_value=100.0, value=0.0, step=0.1)
        notes = st.text_area("Notes", height=80)
        submitted = st.form_submit_button("Save measurement", type="primary", width="stretch")
    if submitted:
        values = [weight, body_fat, waist, chest, hips, arm, thigh]
        if not any(v > 0 for v in values):
            st.error("Enter at least one measurement.")
        else:
            with SessionLocal() as session:
                session.add(Measurement(user_id=user.id, measurement_date=measurement_date, weight_lb=weight or None, body_fat_pct=body_fat or None, waist_in=waist or None, chest_in=chest or None, hips_in=hips or None, arm_in=arm or None, thigh_in=thigh or None, notes=notes.strip()))
                session.commit()
            st.success("Measurement saved.")
            st.rerun()

    with SessionLocal() as session:
        measurements = session.scalars(select(Measurement).where(Measurement.user_id == user.id).order_by(Measurement.measurement_date.asc(), Measurement.id.asc())).all()
    if measurements:
        df = pd.DataFrame([{"Date": x.measurement_date, "Weight (lb)": x.weight_lb, "Body fat (%)": x.body_fat_pct, "Waist (in)": x.waist_in, "Chest (in)": x.chest_in, "Hips (in)": x.hips_in, "Arm (in)": x.arm_in, "Thigh (in)": x.thigh_in, "ID": x.id} for x in measurements])
        chart_choice = st.selectbox("Chart", ["Weight (lb)", "Body fat (%)", "Waist (in)", "Chest (in)", "Hips (in)"])
        chart_df = df[["Date", chart_choice]].dropna().set_index("Date")
        if not chart_df.empty:
            st.line_chart(chart_df, width="stretch")
        st.dataframe(df.drop(columns=["ID"]), width="stretch", hide_index=True)
        delete_id = st.selectbox("Remove a measurement", [None] + list(reversed(df["ID"].tolist())), format_func=lambda value: "Select an entry" if value is None else f"Measurement #{value}")
        if st.button("Delete selected measurement", disabled=delete_id is None):
            with SessionLocal() as session:
                session.execute(delete(Measurement).where(Measurement.id == delete_id, Measurement.user_id == user.id))
                session.commit()
            st.rerun()
    else:
        st.markdown('<div class="nv-empty">Your measurement history will appear here.</div>', unsafe_allow_html=True)


def render_goals(user: User) -> None:
    hero("Goals", "Turn targets into actions", "Create measurable fitness, nutrition, strength, and body-composition goals.")
    with st.form("goal_form", clear_on_submit=True):
        title = st.text_input("Goal title", placeholder="Bench press 185 lb")
        c1, c2 = st.columns(2)
        category = c1.selectbox("Category", ["Fitness", "Strength", "Nutrition", "Weight", "Body composition", "Habit", "Other"])
        unit = c2.text_input("Unit", placeholder="lb, workouts, days, miles")
        c3, c4, c5 = st.columns(3)
        current = c3.number_input("Current value", value=0.0)
        target = c4.number_input("Target value", value=1.0)
        target_date = c5.date_input("Target date", value=local_today() + timedelta(days=30), format="MM/DD/YYYY")
        submitted = st.form_submit_button("Create goal", type="primary", width="stretch")
    if submitted:
        if not title.strip():
            st.error("Enter a goal title.")
        elif target <= 0:
            st.error("Enter a target greater than zero.")
        else:
            with SessionLocal() as session:
                session.add(Goal(user_id=user.id, title=title.strip(), category=category, target_value=float(target), current_value=float(current), unit=unit.strip(), target_date=target_date))
                session.commit()
            st.success("Goal created.")
            st.rerun()

    with SessionLocal() as session:
        goals = session.scalars(select(Goal).where(Goal.user_id == user.id).order_by(Goal.completed.asc(), Goal.target_date.asc())).all()
    if not goals:
        st.markdown('<div class="nv-empty">Create your first goal to start tracking progress.</div>', unsafe_allow_html=True)
        return

    for goal in goals:
        progress = 100 if goal.completed else max(0, min(100, goal.current_value / max(goal.target_value, .0001) * 100))
        with st.expander(f"{'Completed · ' if goal.completed else ''}{goal.title}", expanded=not goal.completed):
            metric_card(goal.category, f"{goal.current_value:g} / {goal.target_value:g} {goal.unit}", f"Target date: {goal.target_date.strftime('%m/%d/%Y') if goal.target_date else 'None'}", progress)
            new_value = st.number_input("Update current value", value=float(goal.current_value), key=f"goal_value_{goal.id}")
            c1, c2, c3 = st.columns(3)
            if c1.button("Save progress", key=f"save_goal_{goal.id}"):
                with SessionLocal() as session:
                    owned = session.scalar(select(Goal).where(Goal.id == goal.id, Goal.user_id == user.id))
                    if owned:
                        owned.current_value = new_value
                        owned.completed = new_value >= owned.target_value
                        session.commit()
                st.rerun()
            if c2.button("Mark complete", key=f"complete_goal_{goal.id}"):
                with SessionLocal() as session:
                    owned = session.scalar(select(Goal).where(Goal.id == goal.id, Goal.user_id == user.id))
                    if owned:
                        owned.completed = True
                        session.commit()
                st.rerun()
            if c3.button("Delete goal", key=f"delete_goal_{goal.id}"):
                with SessionLocal() as session:
                    session.execute(delete(Goal).where(Goal.id == goal.id, Goal.user_id == user.id))
                    session.commit()
                st.rerun()


def render_profile(user: User) -> None:
    hero("Profile", "Set your daily targets", "Update personal details and nutrition targets used throughout the dashboard.")
    with st.form("profile_form"):
        display_name = st.text_input("Display name", value=user.display_name or "")
        c1, c2 = st.columns(2)
        age = c1.number_input("Age", min_value=0, max_value=120, value=int(user.age or 0))
        height_inches = c2.number_input("Height (inches)", min_value=0.0, max_value=120.0, value=round(cm_to_inches(user.height_cm), 1), step=0.5)
        activity = st.selectbox("Activity level", ["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"], index=["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"].index(user.activity_level) if user.activity_level in ["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"] else 2)
        st.subheader("Daily nutrition targets")
        c3, c4, c5, c6 = st.columns(4)
        calorie_target = c3.number_input("Calories", min_value=500, max_value=10000, value=user.calorie_target)
        protein_target = c4.number_input("Protein (g)", min_value=0, max_value=1000, value=user.protein_target)
        carb_target = c5.number_input("Carbs (g)", min_value=0, max_value=1500, value=user.carb_target)
        fat_target = c6.number_input("Fat (g)", min_value=0, max_value=500, value=user.fat_target)
        water_target_oz = st.number_input("Water target (fl oz)", min_value=8.0, max_value=512.0, value=round(ml_to_fl_oz(user.water_target_ml), 1), step=1.0)
        submitted = st.form_submit_button("Save profile", type="primary", width="stretch")
    if submitted:
        with SessionLocal() as session:
            owned = get_user(session, user.id)
            if owned:
                owned.display_name = display_name.strip() or owned.username
                owned.age = int(age) or None
                owned.height_cm = inches_to_cm(height_inches) or None
                owned.activity_level = activity
                owned.calorie_target = int(calorie_target)
                owned.protein_target = int(protein_target)
                owned.carb_target = int(carb_target)
                owned.fat_target = int(fat_target)
                owned.water_target_ml = fl_oz_to_ml(water_target_oz)
                session.commit()
        st.success("Profile saved.")
        st.rerun()


def dataframe_csv(rows: list[dict[str, Any]]) -> bytes:
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


def create_export(user: User) -> bytes:
    with SessionLocal() as session:
        food = session.scalars(select(FoodLog).where(FoodLog.user_id == user.id)).all()
        water = session.scalars(select(WaterLog).where(WaterLog.user_id == user.id)).all()
        workouts = session.scalars(select(WorkoutSession).where(WorkoutSession.user_id == user.id)).all()
        workout_ids = [x.id for x in workouts]
        sets = session.scalars(select(ExerciseSet).where(ExerciseSet.session_id.in_(workout_ids))).all() if workout_ids else []
        measurements = session.scalars(select(Measurement).where(Measurement.user_id == user.id)).all()
        goals = session.scalars(select(Goal).where(Goal.user_id == user.id)).all()
        smart_scans = session.scalars(select(SmartScan).where(SmartScan.user_id == user.id)).all()
        daily_checkins = session.scalars(select(DailyCheckIn).where(DailyCheckIn.user_id == user.id)).all()
        timezone_preference = session.scalar(
            select(UserTimezonePreference).where(UserTimezonePreference.user_id == user.id)
        )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("profile.csv", dataframe_csv([{"username": user.username, "email": user.email, "display_name": user.display_name, "age": user.age, "height_inches": round(cm_to_inches(user.height_cm), 2) if user.height_cm else None, "height_cm": user.height_cm, "activity_level": user.activity_level, "calorie_target": user.calorie_target, "protein_target": user.protein_target, "carb_target": user.carb_target, "fat_target": user.fat_target, "water_target_fl_oz": round(ml_to_fl_oz(user.water_target_ml), 2), "water_target_ml": user.water_target_ml, "created_at": user.created_at}]))
        archive.writestr("food_logs.csv", dataframe_csv([{c.name: getattr(x, c.name) for c in FoodLog.__table__.columns if c.name != "user_id"} for x in food]))
        archive.writestr("water_logs.csv", dataframe_csv([{**{c.name: getattr(x, c.name) for c in WaterLog.__table__.columns if c.name != "user_id"}, "amount_fl_oz": round(ml_to_fl_oz(x.amount_ml), 2)} for x in water]))
        archive.writestr("workout_sessions.csv", dataframe_csv([{c.name: getattr(x, c.name) for c in WorkoutSession.__table__.columns if c.name != "user_id"} for x in workouts]))
        archive.writestr("exercise_sets.csv", dataframe_csv([{c.name: getattr(x, c.name) for c in ExerciseSet.__table__.columns} for x in sets]))
        measurement_export_rows = []
        for x in measurements:
            row = {c.name: getattr(x, c.name) for c in Measurement.__table__.columns if c.name != "user_id"}
            row["measurement_date"] = x.measurement_date.strftime("%m/%d/%Y")
            measurement_export_rows.append(row)
        archive.writestr("measurements.csv", dataframe_csv(measurement_export_rows))
        goal_export_rows = []
        for x in goals:
            row = {c.name: getattr(x, c.name) for c in Goal.__table__.columns if c.name != "user_id"}
            if x.target_date:
                row["target_date"] = x.target_date.strftime("%m/%d/%Y")
            goal_export_rows.append(row)
        archive.writestr("goals.csv", dataframe_csv(goal_export_rows))
        archive.writestr("smart_scans.csv", dataframe_csv([{c.name: getattr(x, c.name) for c in SmartScan.__table__.columns if c.name != "user_id"} for x in smart_scans]))
        archive.writestr("daily_checkins.csv", dataframe_csv([{c.name: getattr(x, c.name) for c in DailyCheckIn.__table__.columns if c.name != "user_id"} for x in daily_checkins]))
        archive.writestr(
            "timezone_settings.csv",
            dataframe_csv([{
                "mode": timezone_preference.mode if timezone_preference else "auto",
                "timezone_name": timezone_preference.timezone_name if timezone_preference else "",
                "effective_timezone_at_export": active_timezone_name(),
            }]),
        )
        for elite_filename, elite_bytes in elite_export_files(SessionLocal, ELITE_MODELS, user.id).items():
            archive.writestr(f"elite/{elite_filename}", elite_bytes)
    return output.getvalue()


def render_data_account(user: User) -> None:
    hero("Data and account", "Control your information", "Export your records, change your password, or permanently delete your account.")
    st.subheader("Export")
    export_bytes = create_export(user)
    st.download_button("Download account data", data=export_bytes, file_name=f"nourivanta_{user.username}_export.zip", mime="application/zip", width="stretch")

    st.subheader("Change password")
    with st.form("password_form"):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Change password", type="primary", width="stretch")
    if submitted:
        if new != confirm:
            st.error("The new passwords do not match.")
        elif len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
            st.error("Use at least 8 characters with one letter and one number.")
        else:
            with SessionLocal() as session:
                owned = get_user(session, user.id)
                if not owned or not verify_password(current, owned.password_hash):
                    st.error("The current password is incorrect.")
                else:
                    owned.password_hash = hash_password(new)
                    session.commit()
                    st.success("Password changed.")

    st.subheader("Delete account")
    st.warning("This permanently removes your profile and every saved record.")
    with st.form("delete_account_form"):
        confirmation = st.text_input(f"Type DELETE {user.username} to confirm")
        submitted = st.form_submit_button("Delete my account", width="stretch")
    if submitted:
        if confirmation.strip() != f"DELETE {user.username}":
            st.error("The confirmation text does not match.")
        else:
            with SessionLocal() as session:
                session.execute(
                    delete(UserTimezonePreference).where(UserTimezonePreference.user_id == user.id)
                )
                session.execute(delete(User).where(User.id == user.id))
                session.commit()
            st.session_state.user_id = None
            st.session_state.username = None
            st.session_state.page = "Dashboard"
            st.session_state.timezone_loaded_for_user = None
            st.session_state.timezone_mode = "auto"
            st.session_state.manual_timezone_name = ""
            st.success("Account deleted.")
            st.rerun()


def elite_context() -> dict[str, Any]:
    """Build the shared context used by consolidated Elite tools."""
    return {
        "models": ELITE_MODELS,
        "SessionLocal": SessionLocal,
        "User": User,
        "FoodLog": FoodLog,
        "WaterLog": WaterLog,
        "WorkoutSession": WorkoutSession,
        "ExerciseSet": ExerciseSet,
        "Measurement": Measurement,
        "Goal": Goal,
        "DailyCheckIn": DailyCheckIn,
        "EXERCISE_LIBRARY": EXERCISE_LIBRARY,
        "hero": hero,
        "metric_card": metric_card,
        "today_nutrition": today_nutrition,
        "readiness_score": readiness_score,
        "readiness_label": readiness_label,
        "hash_password": hash_password,
        "verify_password": verify_password,
    }


def render_nutrition_center(user: User) -> None:
    """Keep all food, scanning, forecasting, and meal-planning tools together."""
    options = ["Food & water", "Smart Scan", "Nutrition Insights", "Meal Planner"]
    current = st.session_state.get("nutrition_subpage", options[0])
    if current not in options:
        current = options[0]
    selected = st.radio(
        "\u200b",
        options,
        index=options.index(current),
        horizontal=True,
        label_visibility="collapsed",
        key="nutrition_section_selector",
    )
    st.session_state.nutrition_subpage = selected
    if selected == "Smart Scan":
        render_smart_scan(user)
    elif selected == "Nutrition Insights":
        render_food_intelligence(user, elite_context())
    elif selected == "Meal Planner":
        render_meal_planner(user, elite_context())
    else:
        render_nutrition(user)


def render_workouts_center(user: User) -> None:
    """Keep workout logging and advanced training tools together."""
    options = ["Workout Log", "Training Lab"]
    current = st.session_state.get("workouts_subpage", options[0])
    if current not in options:
        current = options[0]
    selected = st.radio(
        "\u200b",
        options,
        index=options.index(current),
        horizontal=True,
        label_visibility="collapsed",
        key="workouts_section_selector",
    )
    st.session_state.workouts_subpage = selected
    if selected == "Training Lab":
        render_training_lab(user, elite_context())
    else:
        render_workouts(user)


def render_readiness_center(user: User) -> None:
    """Keep check-ins, wearable records, voice capture, and recovery matching together."""
    options = ["Daily Readiness", "Voice & Wearables"]
    current = st.session_state.get("readiness_subpage", options[0])
    if current not in options:
        current = options[0]
    selected = st.radio(
        "\u200b",
        options,
        index=options.index(current),
        horizontal=True,
        label_visibility="collapsed",
        key="readiness_section_selector",
    )
    st.session_state.readiness_subpage = selected
    if selected == "Voice & Wearables":
        render_voice_and_wearables(user, elite_context())
    else:
        render_readiness(user)


def render_progress_center(user: User) -> None:
    """Keep measurements, goals, forecasting, and adaptive coaching together."""
    options = ["Measurements", "Goals", "Trends & Forecasts", "Adaptive Coach"]
    current = st.session_state.get("progress_subpage", options[0])
    if current not in options:
        current = options[0]
    selected = st.radio(
        "\u200b",
        options,
        index=options.index(current),
        horizontal=True,
        label_visibility="collapsed",
        key="progress_section_selector",
    )
    st.session_state.progress_subpage = selected
    if selected == "Goals":
        render_goals(user)
    elif selected == "Trends & Forecasts":
        render_elite_progress_center(user, elite_context())
    elif selected == "Adaptive Coach":
        render_adaptive_coach(user, elite_context())
    else:
        render_progress(user)


def render_timezone_settings(user: User) -> None:
    """Let each account follow its device time zone or save a manual override."""
    hero(
        "Local time",
        "Use the correct day wherever you are",
        "NouriVanta uses your device time zone automatically. You can save a specific time zone for this account when needed.",
    )
    detected = browser_timezone_name()
    effective_name = active_timezone_name()
    effective_now = datetime.now(ZoneInfo(effective_name))
    detected_text = detected or "Unavailable in this Streamlit version or browser session"
    st.markdown(
        f"""
        <div class="nv-card">
            <div class="nv-label">Current local date and time</div>
            <div class="nv-value" style="font-size:1.45rem">{effective_now.strftime('%m/%d/%Y · %I:%M %p')}</div>
            <div class="nv-meta">Active time zone: {html.escape(effective_name)}</div>
            <div class="nv-meta">Device detected: {html.escape(detected_text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    saved_mode = st.session_state.get("timezone_mode", "auto")
    current_mode_label = MANUAL_TIMEZONE_LABEL if saved_mode == "manual" else AUTO_TIMEZONE_LABEL
    choices = timezone_choices()
    current_manual = _valid_timezone_name(st.session_state.get("manual_timezone_name"))
    default_manual = current_manual or detected or _valid_timezone_name(DEFAULT_TIMEZONE_NAME) or "UTC"
    if default_manual not in choices:
        choices = [default_manual] + choices

    with st.form("timezone_settings_form"):
        mode_label = st.radio(
            "Time-zone behavior",
            [AUTO_TIMEZONE_LABEL, MANUAL_TIMEZONE_LABEL],
            index=0 if current_mode_label == AUTO_TIMEZONE_LABEL else 1,
        )
        selected_timezone = st.selectbox(
            "Specific time zone",
            choices,
            index=choices.index(default_manual),
            disabled=mode_label == AUTO_TIMEZONE_LABEL,
            help="Use an IANA time zone such as America/Chicago or America/Nassau.",
        )
        submitted = st.form_submit_button("Save time-zone settings", type="primary", width="stretch")

    if submitted:
        mode = "auto" if mode_label == AUTO_TIMEZONE_LABEL else "manual"
        try:
            save_timezone_preference(user.id, mode, selected_timezone)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Time-zone settings saved. Dates now follow the selected local day.")
            st.rerun()

    if detected is None:
        st.info(
            "Automatic detection requires Streamlit 1.43 or newer. Until it is available, "
            f"NouriVanta uses {effective_name}. You can choose a specific time zone above."
        )


def render_settings_center(user: User) -> None:
    """Keep profile, time zone, data, security, and optional billing tools together."""
    options = ["Profile", "Time zone", "Data & account", "Security"]
    current = st.session_state.get("settings_subpage", options[0])
    if current == "Family & Security":
        current = "Security"
    if current not in options:
        current = options[0]
    selected = st.radio(
        "\u200b",
        options,
        index=options.index(current),
        horizontal=True,
        label_visibility="collapsed",
        key="settings_section_selector",
    )
    st.session_state.settings_subpage = selected
    if selected == "Time zone":
        render_timezone_settings(user)
    elif selected == "Data & account":
        render_data_account(user)
    elif selected == "Security":
        render_family_and_security(user, elite_context())
    else:
        render_profile(user)

def render_app() -> None:
    init_state()
    inject_css()
    if not st.session_state.user_id:
        render_auth()
        return

    timeout_minutes = session_timeout_minutes(SessionLocal, ELITE_MODELS, int(st.session_state.user_id))
    last_activity = st.session_state.get("last_activity_at") or utc_now()
    if utc_now() - last_activity > timedelta(minutes=timeout_minutes):
        st.session_state.user_id = None
        st.session_state.username = None
        st.session_state.page = "Dashboard"
        st.session_state.timezone_loaded_for_user = None
        st.session_state.auth_error = "Your session expired. Sign in again."
        st.rerun()
    st.session_state.last_activity_at = utc_now()

    with SessionLocal() as session:
        user = get_user(session, int(st.session_state.user_id))
        if not user:
            st.session_state.user_id = None
            st.session_state.timezone_loaded_for_user = None
            st.rerun()
        session.expunge(user)

    load_timezone_preference(user.id)
    sidebar(user)
    page = st.session_state.page
    if page == "Dashboard":
        render_dashboard(user)
    elif page == "Nutrition":
        render_nutrition_center(user)
    elif page == "Workouts":
        render_workouts_center(user)
    elif page == "Readiness":
        render_readiness_center(user)
    elif page == "Progress & Goals":
        render_progress_center(user)
    elif page == "Settings":
        render_settings_center(user)


if __name__ == "__main__":
    _APP_RENDER_RESULT = render_app()

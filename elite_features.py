from __future__ import annotations

import io
import json
import math
import os
import secrets
import string
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote_plus
from types import SimpleNamespace
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

DEFAULT_TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "America/Chicago")


def _valid_timezone_name(value: str | None) -> str | None:
    if not value:
        return None
    candidate = str(value).strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return candidate


def _browser_timezone_name() -> str | None:
    try:
        context = getattr(st, "context", None)
        return _valid_timezone_name(getattr(context, "timezone", None))
    except Exception:
        return None


def active_timezone_name() -> str:
    mode = st.session_state.get("timezone_mode", "auto")
    manual_name = _valid_timezone_name(st.session_state.get("manual_timezone_name"))
    if mode == "manual" and manual_name:
        return manual_name
    return _browser_timezone_name() or _valid_timezone_name(DEFAULT_TIMEZONE_NAME) or "UTC"


def local_now() -> datetime:
    """Return the current viewer-local time as a naive datetime."""
    return datetime.now(ZoneInfo(active_timezone_name())).replace(tzinfo=None)


def local_today() -> date:
    """Return the current viewer's local calendar date."""
    return datetime.now(ZoneInfo(active_timezone_name())).date()


def utc_now() -> datetime:
    """Return a naive UTC timestamp for security calculations."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_naive_to_local(value: datetime) -> datetime:
    """Convert a stored naive UTC timestamp into the current viewer's local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(active_timezone_name()))


from elite_services import (
    EliteServiceError,
    MEAL_TEMPLATES,
    analyze_nutrition_label,
    fuel_gap_suggestions,
    generate_ai_coach_report,
    local_food_search,
    parse_voice_command,
    search_usda_foods,
    transcribe_audio,
)


def install_elite_models(Base: Any) -> SimpleNamespace:
    class FavoriteFood(Base):
        __tablename__ = "favorite_foods"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        name: Mapped[str] = mapped_column(String(180))
        serving: Mapped[str] = mapped_column(String(100), default="1 serving")
        calories: Mapped[float] = mapped_column(Float, default=0)
        protein_g: Mapped[float] = mapped_column(Float, default=0)
        carbs_g: Mapped[float] = mapped_column(Float, default=0)
        fat_g: Mapped[float] = mapped_column(Float, default=0)
        fiber_g: Mapped[float] = mapped_column(Float, default=0)
        source: Mapped[str] = mapped_column(String(80), default="Manual")
        source_id: Mapped[str] = mapped_column(String(80), default="")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class SavedMeal(Base):
        __tablename__ = "saved_meals"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        name: Mapped[str] = mapped_column(String(180))
        meal_type: Mapped[str] = mapped_column(String(30), default="Meal")
        notes: Mapped[str] = mapped_column(Text, default="")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class SavedMealItem(Base):
        __tablename__ = "saved_meal_items"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        saved_meal_id: Mapped[int] = mapped_column(ForeignKey("saved_meals.id", ondelete="CASCADE"), index=True)
        food_name: Mapped[str] = mapped_column(String(180))
        serving: Mapped[str] = mapped_column(String(100), default="1 serving")
        calories: Mapped[float] = mapped_column(Float, default=0)
        protein_g: Mapped[float] = mapped_column(Float, default=0)
        carbs_g: Mapped[float] = mapped_column(Float, default=0)
        fat_g: Mapped[float] = mapped_column(Float, default=0)
        fiber_g: Mapped[float] = mapped_column(Float, default=0)

    class MealPlanEntry(Base):
        __tablename__ = "meal_plan_entries"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        plan_date: Mapped[date] = mapped_column(Date, index=True)
        meal: Mapped[str] = mapped_column(String(30), default="Meal")
        name: Mapped[str] = mapped_column(String(180))
        servings: Mapped[float] = mapped_column(Float, default=1)
        calories: Mapped[float] = mapped_column(Float, default=0)
        protein_g: Mapped[float] = mapped_column(Float, default=0)
        carbs_g: Mapped[float] = mapped_column(Float, default=0)
        fat_g: Mapped[float] = mapped_column(Float, default=0)
        fiber_g: Mapped[float] = mapped_column(Float, default=0)
        ingredients_json: Mapped[str] = mapped_column(Text, default="[]")
        estimated_cost: Mapped[float] = mapped_column(Float, default=0)
        completed: Mapped[bool] = mapped_column(Boolean, default=False)
        notes: Mapped[str] = mapped_column(Text, default="")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class PantryItem(Base):
        __tablename__ = "pantry_items"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        name: Mapped[str] = mapped_column(String(160))
        quantity: Mapped[float] = mapped_column(Float, default=1)
        unit: Mapped[str] = mapped_column(String(40), default="item")
        category: Mapped[str] = mapped_column(String(60), default="Pantry")
        expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class WorkoutProgram(Base):
        __tablename__ = "workout_programs"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        name: Mapped[str] = mapped_column(String(160))
        goal: Mapped[str] = mapped_column(String(80), default="General fitness")
        days_per_week: Mapped[int] = mapped_column(Integer, default=3)
        active: Mapped[bool] = mapped_column(Boolean, default=True)
        notes: Mapped[str] = mapped_column(Text, default="")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class WorkoutProgramExercise(Base):
        __tablename__ = "workout_program_exercises"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        program_id: Mapped[int] = mapped_column(ForeignKey("workout_programs.id", ondelete="CASCADE"), index=True)
        day_name: Mapped[str] = mapped_column(String(50), default="Day 1")
        order_index: Mapped[int] = mapped_column(Integer, default=1)
        body_part: Mapped[str] = mapped_column(String(80), default="Full Body")
        exercise_name: Mapped[str] = mapped_column(String(160))
        sets: Mapped[int] = mapped_column(Integer, default=3)
        reps_min: Mapped[int] = mapped_column(Integer, default=8)
        reps_max: Mapped[int] = mapped_column(Integer, default=12)
        target_weight_lb: Mapped[float] = mapped_column(Float, default=0)
        rest_seconds: Mapped[int] = mapped_column(Integer, default=90)
        superset_group: Mapped[str] = mapped_column(String(20), default="")
        set_style: Mapped[str] = mapped_column(String(40), default="Standard")
        notes: Mapped[str] = mapped_column(Text, default="")

    class CoachReport(Base):
        __tablename__ = "coach_reports"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        period_start: Mapped[date] = mapped_column(Date)
        period_end: Mapped[date] = mapped_column(Date)
        report_text: Mapped[str] = mapped_column(Text)
        metrics_json: Mapped[str] = mapped_column(Text, default="{}")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class UserPreference(Base):
        __tablename__ = "user_preferences"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
        allergies: Mapped[str] = mapped_column(Text, default="")
        dislikes: Mapped[str] = mapped_column(Text, default="")
        eating_style: Mapped[str] = mapped_column(String(80), default="Balanced")
        weekly_budget: Mapped[float] = mapped_column(Float, default=100)
        household_servings: Mapped[int] = mapped_column(Integer, default=1)
        day_start_hour: Mapped[int] = mapped_column(Integer, default=7)
        day_end_hour: Mapped[int] = mapped_column(Integer, default=22)
        coach_share_code: Mapped[str] = mapped_column(String(32), default="")
        theme: Mapped[str] = mapped_column(String(40), default="Light Spectrum")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class WearableMetric(Base):
        __tablename__ = "wearable_metrics"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        metric_date: Mapped[date] = mapped_column(Date, index=True)
        source: Mapped[str] = mapped_column(String(80), default="Manual")
        sleep_hours: Mapped[float] = mapped_column(Float, default=0)
        sleep_quality: Mapped[int] = mapped_column(Integer, default=5)
        steps: Mapped[int] = mapped_column(Integer, default=0)
        resting_hr: Mapped[float] = mapped_column(Float, default=0)
        hrv_ms: Mapped[float] = mapped_column(Float, default=0)
        active_calories: Mapped[float] = mapped_column(Float, default=0)
        notes: Mapped[str] = mapped_column(Text, default="")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class HouseholdProfile(Base):
        __tablename__ = "household_profiles"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        name: Mapped[str] = mapped_column(String(120))
        relationship: Mapped[str] = mapped_column(String(80), default="Family")
        age: Mapped[int | None] = mapped_column(Integer, nullable=True)
        calorie_target: Mapped[int] = mapped_column(Integer, default=2000)
        protein_target: Mapped[int] = mapped_column(Integer, default=100)
        private_measurements: Mapped[bool] = mapped_column(Boolean, default=True)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class CoachNote(Base):
        __tablename__ = "coach_notes"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
        note_date: Mapped[date] = mapped_column(Date, index=True)
        coach_name: Mapped[str] = mapped_column(String(120), default="Coach")
        category: Mapped[str] = mapped_column(String(80), default="General")
        note: Mapped[str] = mapped_column(Text)
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class SecurityProfile(Base):
        __tablename__ = "security_profiles"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
        recovery_code_hash: Mapped[str] = mapped_column(String(255), default="")
        failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
        locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
        session_timeout_min: Mapped[int] = mapped_column(Integer, default=90)
        plan_tier: Mapped[str] = mapped_column(String(30), default="Core")
        created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    class LoginEvent(Base):
        __tablename__ = "login_events"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
        login_identifier: Mapped[str] = mapped_column(String(255), default="")
        event_time: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
        success: Mapped[bool] = mapped_column(Boolean, default=False)
        client_info: Mapped[str] = mapped_column(String(300), default="")

    return SimpleNamespace(
        FavoriteFood=FavoriteFood,
        SavedMeal=SavedMeal,
        SavedMealItem=SavedMealItem,
        MealPlanEntry=MealPlanEntry,
        PantryItem=PantryItem,
        WorkoutProgram=WorkoutProgram,
        WorkoutProgramExercise=WorkoutProgramExercise,
        CoachReport=CoachReport,
        UserPreference=UserPreference,
        WearableMetric=WearableMetric,
        HouseholdProfile=HouseholdProfile,
        CoachNote=CoachNote,
        SecurityProfile=SecurityProfile,
        LoginEvent=LoginEvent,
    )


def _esc(value: Any) -> str:
    import html
    return html.escape(str(value if value is not None else ""))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_preferences(session: Any, models: SimpleNamespace, user_id: int) -> Any:
    pref = session.scalar(select(models.UserPreference).where(models.UserPreference.user_id == user_id))
    if pref is None:
        pref = models.UserPreference(user_id=user_id)
        session.add(pref)
        session.flush()
    return pref


def _get_security(session: Any, models: SimpleNamespace, user_id: int) -> Any:
    profile = session.scalar(select(models.SecurityProfile).where(models.SecurityProfile.user_id == user_id))
    if profile is None:
        profile = models.SecurityProfile(user_id=user_id)
        session.add(profile)
        session.flush()
    return profile


def _client_info() -> str:
    try:
        headers = getattr(getattr(st, "context", None), "headers", None)
        if headers:
            return str(headers.get("User-Agent") or headers.get("user-agent") or "")[:300]
    except Exception:
        pass
    return "Streamlit client"


def issue_recovery_code(SessionLocal: Any, models: SimpleNamespace, user_id: int, hash_password: Any) -> str:
    alphabet = string.ascii_uppercase + string.digits
    code = "NV-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(6))
    with SessionLocal() as session:
        profile = _get_security(session, models, user_id)
        profile.recovery_code_hash = hash_password(code)
        session.commit()
    return code


def reset_password_with_recovery(
    SessionLocal: Any,
    models: SimpleNamespace,
    User: Any,
    verify_password: Any,
    hash_password: Any,
    login: str,
    recovery_code: str,
    new_password: str,
) -> tuple[bool, str]:
    clean = login.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(User).where((User.username == clean) | (User.email == clean)))
        if not user:
            return False, "The account or recovery code is incorrect."
        profile = _get_security(session, models, user.id)
        if not profile.recovery_code_hash or not verify_password(recovery_code.strip(), profile.recovery_code_hash):
            return False, "The account or recovery code is incorrect."
        user.password_hash = hash_password(new_password)
        profile.failed_login_count = 0
        profile.locked_until = None
        session.commit()
    return True, "Password reset. Sign in with your new password."


def login_allowed(SessionLocal: Any, models: SimpleNamespace, User: Any, login: str) -> tuple[bool, str]:
    clean = login.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(User).where((User.username == clean) | (User.email == clean)))
        if not user:
            return True, ""
        profile = _get_security(session, models, user.id)
        if profile.locked_until and profile.locked_until > utc_now():
            minutes = max(1, math.ceil((profile.locked_until - utc_now()).total_seconds() / 60))
            return False, f"Too many failed attempts. Try again in about {minutes} minute(s)."
        if profile.locked_until and profile.locked_until <= utc_now():
            profile.locked_until = None
            profile.failed_login_count = 0
            session.commit()
    return True, ""


def register_login_result(
    SessionLocal: Any,
    models: SimpleNamespace,
    User: Any,
    login: str,
    user_id: int | None,
    success: bool,
) -> None:
    clean = login.strip().lower()
    with SessionLocal() as session:
        resolved = session.scalar(select(User).where((User.username == clean) | (User.email == clean)))
        resolved_id = user_id or (resolved.id if resolved else None)
        session.add(
            models.LoginEvent(
                user_id=resolved_id,
                login_identifier=clean[:255],
                success=success,
                client_info=_client_info(),
            )
        )
        if resolved:
            profile = _get_security(session, models, resolved.id)
            if success:
                profile.failed_login_count = 0
                profile.locked_until = None
            else:
                profile.failed_login_count += 1
                if profile.failed_login_count >= 5:
                    profile.locked_until = utc_now() + timedelta(minutes=15)
        session.commit()


def session_timeout_minutes(SessionLocal: Any, models: SimpleNamespace, user_id: int) -> int:
    with SessionLocal() as session:
        profile = _get_security(session, models, user_id)
        value = int(profile.session_timeout_min or 90)
        session.commit()
    return max(15, min(value, 720))


def inject_elite_css() -> None:
    st.markdown(
        """
        <style>
        .nv-elite-header {
            padding: 1rem 1.2rem;
            border-radius: 20px;
            background: linear-gradient(120deg, rgba(109,93,251,.13), rgba(19,196,212,.13), rgba(255,159,67,.12));
            border: 1px solid rgba(109,93,251,.20);
            margin-bottom: 1rem;
        }
        .nv-elite-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; margin:.7rem 0 1rem; }
        .nv-elite-tile { background:rgba(255,255,255,.94); border:1px solid rgba(109,93,251,.16); border-radius:16px; padding:.85rem; box-shadow:0 8px 24px rgba(64,72,120,.07); }
        .nv-elite-big { font-size:1.55rem; font-weight:900; margin:.25rem 0; }
        .nv-chip-row { display:flex; flex-wrap:wrap; gap:.45rem; margin:.5rem 0; }
        .nv-chip-elite { padding:.35rem .62rem; border-radius:999px; background:#F0EEFF; color:#5145CD; font-size:.78rem; font-weight:800; }
        .nv-gap-bar { height:12px; border-radius:999px; background:#E8EDF7; overflow:hidden; margin:.35rem 0 .75rem; }
        .nv-gap-bar span { display:block; height:100%; border-radius:999px; background:linear-gradient(90deg,#6D5DFB,#13C4D4); }
        .nv-readiness-high { color:#16875D; }
        .nv-readiness-medium { color:#C97912; }
        .nv-readiness-low { color:#C23A4B; }
        @media(max-width:900px){ .nv-elite-grid{grid-template-columns:repeat(2,minmax(0,1fr));} }
        @media(max-width:600px){ .nv-elite-grid{grid-template-columns:1fr;} }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _period_metrics(ctx: dict[str, Any], user: Any, days: int) -> dict[str, Any]:
    SessionLocal = ctx["SessionLocal"]
    FoodLog = ctx["FoodLog"]
    WaterLog = ctx["WaterLog"]
    WorkoutSession = ctx["WorkoutSession"]
    ExerciseSet = ctx["ExerciseSet"]
    Measurement = ctx["Measurement"]
    DailyCheckIn = ctx["DailyCheckIn"]
    start = local_today() - timedelta(days=days - 1)
    with SessionLocal() as session:
        food = session.scalars(select(FoodLog).where(FoodLog.user_id == user.id, FoodLog.log_date >= start)).all()
        water = session.scalars(select(WaterLog).where(WaterLog.user_id == user.id, WaterLog.log_date >= start)).all()
        workouts = session.scalars(select(WorkoutSession).where(WorkoutSession.user_id == user.id, WorkoutSession.workout_date >= start)).all()
        measurements = session.scalars(select(Measurement).where(Measurement.user_id == user.id, Measurement.measurement_date >= start).order_by(Measurement.measurement_date)).all()
        checkins = session.scalars(select(DailyCheckIn).where(DailyCheckIn.user_id == user.id, DailyCheckIn.checkin_date >= start)).all()
        workout_ids = [w.id for w in workouts]
        sets = session.scalars(select(ExerciseSet).where(ExerciseSet.session_id.in_(workout_ids))).all() if workout_ids else []

    logged_days = len({x.log_date for x in food})
    calories_avg = sum(x.calories for x in food) / max(logged_days, 1)
    protein_avg = sum(x.protein_g for x in food) / max(logged_days, 1)
    water_by_day: dict[date, float] = {}
    for x in water:
        water_by_day[x.log_date] = water_by_day.get(x.log_date, 0.0) + x.amount_ml
    water_avg_oz = sum(water_by_day.values()) / max(len(water_by_day), 1) / 29.5735295625
    workout_minutes = sum(w.duration_min for w in workouts)
    volume = sum(s.weight_lb * s.reps for s in sets if s.completed)
    sleep_avg = sum(c.sleep_hours for c in checkins) / max(len(checkins), 1)
    steps_avg = sum(c.steps for c in checkins) / max(len(checkins), 1)
    start_weight = next((m.weight_lb for m in measurements if m.weight_lb is not None), None)
    end_weight = next((m.weight_lb for m in reversed(measurements) if m.weight_lb is not None), None)
    weight_change = (end_weight - start_weight) if start_weight is not None and end_weight is not None else None
    return {
        "period_days": days,
        "period_start": start,
        "period_end": local_today(),
        "food_logged_days": logged_days,
        "nutrition_consistency_pct": round(logged_days / days * 100),
        "average_calories": round(calories_avg),
        "calorie_target": user.calorie_target,
        "average_protein_g": round(protein_avg, 1),
        "protein_target_g": user.protein_target,
        "average_water_oz": round(water_avg_oz, 1),
        "water_target_oz": round(user.water_target_ml / 29.5735295625, 1),
        "workout_count": len(workouts),
        "workout_minutes": workout_minutes,
        "training_volume_lb_reps": round(volume, 1),
        "average_sleep_hours": round(sleep_avg, 1),
        "average_steps": round(steps_avg),
        "weight_change_lb": round(weight_change, 2) if weight_change is not None else None,
    }


def _deterministic_coach_report(metrics: dict[str, Any]) -> str:
    wins: list[str] = []
    friction: list[str] = []
    next_steps: list[str] = []
    consistency = metrics["nutrition_consistency_pct"]
    protein_ratio = metrics["average_protein_g"] / max(metrics["protein_target_g"], 1)
    water_ratio = metrics["average_water_oz"] / max(metrics["water_target_oz"], 1)
    if consistency >= 70:
        wins.append(f"Nutrition was logged on {consistency}% of days, which gives the trend data a solid base.")
    else:
        friction.append(f"Nutrition was logged on {consistency}% of days, so the weekly averages are incomplete.")
        next_steps.append("Log at least breakfast and dinner every day next week to strengthen the data.")
    if protein_ratio >= .9:
        wins.append("Average protein intake stayed close to the daily target.")
    else:
        friction.append(f"Average protein reached about {protein_ratio * 100:.0f}% of the target.")
        next_steps.append("Add one dependable 25 to 35 gram protein serving each day.")
    if water_ratio >= .85:
        wins.append("Hydration stayed near the daily target.")
    else:
        friction.append("Hydration finished below target on the days with water records.")
        next_steps.append("Use two scheduled water checkpoints before lunch and dinner.")
    if metrics["workout_count"] >= max(2, round(metrics["period_days"] / 7 * 3)):
        wins.append(f"You completed {metrics['workout_count']} workouts during this period.")
    else:
        next_steps.append("Schedule the next three training sessions before the week starts.")
    if metrics["average_sleep_hours"] and metrics["average_sleep_hours"] < 7:
        friction.append(f"Average logged sleep was {metrics['average_sleep_hours']} hours.")
        next_steps.append("Protect a consistent sleep window before increasing training volume.")
    if not wins:
        wins.append("You created enough records to begin identifying patterns.")
    if not friction:
        friction.append("No major consistency gap stood out in the available records.")
    if not next_steps:
        next_steps.append("Keep targets unchanged and repeat the current routine for another week.")
    training_match = "Train as planned and increase only one variable at a time."
    if metrics["average_sleep_hours"] and metrics["average_sleep_hours"] < 6.5:
        training_match = "Use moderate intensity and reduce total sets until sleep improves."
    return (
        "WINS\n- " + "\n- ".join(wins) +
        "\n\nFRICTION\n- " + "\n- ".join(friction) +
        "\n\nNEXT WEEK\n- " + "\n- ".join(next_steps[:4]) +
        "\n\nTRAINING MATCH\n- " + training_match
    )


def _render_coach(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    st.subheader("NouriVanta Adaptive Coach")
    days = st.selectbox("Report window", [7, 14, 30, 90], index=0, format_func=lambda x: f"Last {x} days")
    metrics = _period_metrics(ctx, user, days)
    st.markdown(
        f"""
        <div class="nv-elite-grid">
            <div class="nv-elite-tile"><div class="nv-label">Logging consistency</div><div class="nv-elite-big">{metrics['nutrition_consistency_pct']}%</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Average protein</div><div class="nv-elite-big">{metrics['average_protein_g']} g</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Training</div><div class="nv-elite-big">{metrics['workout_count']} sessions</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Average sleep</div><div class="nv-elite-big">{metrics['average_sleep_hours']} h</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    report = _deterministic_coach_report(metrics)
    if st.button("Generate coaching report", type="primary", width="stretch"):
        st.session_state.elite_coach_report = report
    api_key = st.session_state.get("openai_api_key") or os.getenv("OPENAI_API_KEY", "")
    if st.button("Enhance report with AI", disabled=not bool(api_key), width="stretch"):
        try:
            with st.spinner("Building your coaching narrative..."):
                st.session_state.elite_coach_report = generate_ai_coach_report(metrics, api_key)
        except EliteServiceError as exc:
            st.error(str(exc))
    current_report = st.session_state.get("elite_coach_report") or report
    st.text_area("Coaching report", value=current_report, height=360, key="elite_coach_report_editor")
    if st.button("Save this report", width="stretch"):
        with SessionLocal() as session:
            session.add(models.CoachReport(
                user_id=user.id,
                period_start=metrics["period_start"],
                period_end=metrics["period_end"],
                report_text=st.session_state.elite_coach_report_editor,
                metrics_json=json.dumps(metrics, default=str),
            ))
            session.commit()
        st.success("Coaching report saved.")

    with st.expander("Target recommendation review"):
        calorie_delta = 0
        weight_change = metrics.get("weight_change_lb")
        if weight_change is not None and abs(weight_change) < .25 and metrics["nutrition_consistency_pct"] >= 70:
            calorie_delta = -100
        suggested_calories = max(1200, user.calorie_target + calorie_delta)
        suggested_protein = max(user.protein_target, round(user.protein_target * .95))
        st.write(f"Suggested calories: {suggested_calories:,} kcal")
        st.write(f"Suggested protein: {suggested_protein} g")
        st.caption("Targets never change without your approval.")
        if st.button("Apply reviewed targets", disabled=(suggested_calories == user.calorie_target and suggested_protein == user.protein_target)):
            with SessionLocal() as session:
                owned = session.scalar(select(ctx["User"]).where(ctx["User"].id == user.id))
                if owned:
                    owned.calorie_target = suggested_calories
                    owned.protein_target = suggested_protein
                    session.commit()
            st.success("Targets updated after your approval.")
            st.rerun()


def _save_food_log(ctx: dict[str, Any], user_id: int, log_date: date, meal: str, item: dict[str, Any], scale: float = 1.0, notes: str = "") -> None:
    with ctx["SessionLocal"]() as session:
        session.add(ctx["FoodLog"](
            user_id=user_id,
            log_date=log_date,
            meal=meal,
            food_name=str(item.get("name") or item.get("food_name") or "Food"),
            serving=str(item.get("serving") or item.get("serving_description") or "1 serving"),
            calories=_float(item.get("calories")) * scale,
            protein_g=_float(item.get("protein_g")) * scale,
            carbs_g=_float(item.get("carbs_g")) * scale,
            fat_g=_float(item.get("fat_g")) * scale,
            notes=notes,
        ))
        session.commit()


def _render_food_intelligence(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    FoodLog = ctx["FoodLog"]
    search_tab, label_tab, favorites_tab, meals_tab, forecast_tab = st.tabs(["Verified search", "Nutrition label", "Favorites and recent", "Saved meals and copy", "Macro Forecast"])

    with search_tab:
        c1, c2 = st.columns([3, 1])
        query = c1.text_input("Search food", placeholder="Chicken breast, oatmeal, yogurt, restaurant item")
        source = c2.selectbox("Source", ["USDA verified", "Local quick foods"])
        if st.button("Search foods", type="primary"):
            try:
                if source == "USDA verified":
                    st.session_state.elite_food_results = search_usda_foods(query, os.getenv("FDC_API_KEY"))
                else:
                    st.session_state.elite_food_results = local_food_search(query)
                if not st.session_state.elite_food_results:
                    st.warning("No foods matched that search.")
            except EliteServiceError as exc:
                st.error(str(exc))
        results = st.session_state.get("elite_food_results") or []
        if results:
            selected_index = st.selectbox(
                "Food result",
                options=list(range(len(results))),
                format_func=lambda i: f"{results[i].get('name')} · {results[i].get('brand','')} · {results[i].get('serving_description', results[i].get('serving','1 serving'))}",
            )
            item = results[selected_index]
            scale = st.number_input("Number of servings", min_value=0.1, max_value=20.0, value=1.0, step=0.1)
            st.markdown(
                f"""
                <div class="nv-elite-grid">
                    <div class="nv-elite-tile"><div class="nv-label">Calories</div><div class="nv-elite-big">{_float(item.get('calories')) * scale:.0f}</div></div>
                    <div class="nv-elite-tile"><div class="nv-label">Protein</div><div class="nv-elite-big">{_float(item.get('protein_g')) * scale:.1f} g</div></div>
                    <div class="nv-elite-tile"><div class="nv-label">Carbs</div><div class="nv-elite-big">{_float(item.get('carbs_g')) * scale:.1f} g</div></div>
                    <div class="nv-elite-tile"><div class="nv-label">Fat</div><div class="nv-elite-big">{_float(item.get('fat_g')) * scale:.1f} g</div></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            log_date = st.date_input("Diary date", value=local_today(), format="MM/DD/YYYY", key="elite_search_date")
            meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="elite_search_meal")
            b1, b2 = st.columns(2)
            with b1:
                if st.button("Log selected food", type="primary", width="stretch"):
                    _save_food_log(ctx, user.id, log_date, meal, item, scale, notes=f"Source: {item.get('source','Local')}")
                    st.success("Food logged.")
            with b2:
                if st.button("Save as favorite", width="stretch"):
                    with SessionLocal() as session:
                        session.add(models.FavoriteFood(
                            user_id=user.id,
                            name=str(item.get("name") or "Food"),
                            serving=str(item.get("serving_description") or item.get("serving") or "1 serving"),
                            calories=_float(item.get("calories")),
                            protein_g=_float(item.get("protein_g")),
                            carbs_g=_float(item.get("carbs_g")),
                            fat_g=_float(item.get("fat_g")),
                            fiber_g=_float(item.get("fiber_g")),
                            source=str(item.get("source") or source),
                            source_id=str(item.get("source_id") or ""),
                        ))
                        session.commit()
                    st.success("Favorite saved.")

    with label_tab:
        st.subheader("Nutrition-label photo scan")
        c1, c2 = st.columns(2)
        with c1:
            label_camera = st.camera_input("Photograph the Nutrition Facts panel", key="elite_label_camera")
            label_upload = st.file_uploader("Or upload a label photo", type=["jpg", "jpeg", "png", "webp"], key="elite_label_upload")
            api_key = st.session_state.get("openai_api_key") or os.getenv("OPENAI_API_KEY", "")
            if st.button("Read nutrition label", type="primary", disabled=not bool((label_camera or label_upload) and api_key)):
                try:
                    with st.spinner("Reading the label..."):
                        source_file = label_camera or label_upload
                        st.session_state.elite_label_result = analyze_nutrition_label(source_file.getvalue(), api_key)
                except EliteServiceError as exc:
                    st.error(str(exc))
        with c2:
            label_result = st.session_state.get("elite_label_result")
            if label_result:
                with st.form("elite_label_save"):
                    label_date = st.date_input("Diary date", value=local_today(), format="MM/DD/YYYY", key="elite_label_date")
                    label_meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="elite_label_meal")
                    product_name = st.text_input("Product", value=str(label_result.get("product_name") or "Label-scanned food"))
                    serving = st.text_input("Serving", value=str(label_result.get("serving_size") or "1 serving"))
                    servings = st.number_input("Servings eaten", min_value=0.1, max_value=20.0, value=1.0, step=0.1)
                    c3, c4, c5, c6 = st.columns(4)
                    calories = c3.number_input("Calories", min_value=0.0, value=_float(label_result.get("calories")))
                    protein = c4.number_input("Protein", min_value=0.0, value=_float(label_result.get("protein_g")))
                    carbs = c5.number_input("Carbs", min_value=0.0, value=_float(label_result.get("carbs_g")))
                    fat = c6.number_input("Fat", min_value=0.0, value=_float(label_result.get("fat_g")))
                    save_label = st.form_submit_button("Save label food", type="primary", width="stretch")
                if save_label:
                    _save_food_log(ctx, user.id, label_date, label_meal, {
                        "name": product_name, "serving": serving,
                        "calories": calories, "protein_g": protein, "carbs_g": carbs, "fat_g": fat,
                    }, servings, notes="Nutrition label photo reviewed by user")
                    st.success("Nutrition-label food logged.")
            else:
                st.info("The editable label result will appear here.")

    with favorites_tab:
        with SessionLocal() as session:
            favorites = session.scalars(select(models.FavoriteFood).where(models.FavoriteFood.user_id == user.id).order_by(models.FavoriteFood.created_at.desc())).all()
            recent = session.scalars(select(FoodLog).where(FoodLog.user_id == user.id).order_by(FoodLog.created_at.desc()).limit(20)).all()
        st.subheader("Favorites")
        if favorites:
            favorite = st.selectbox("Favorite food", favorites, format_func=lambda x: f"{x.name} · {x.serving}")
            scale = st.number_input("Favorite servings", min_value=0.1, max_value=20.0, value=1.0, step=0.1, key="favorite_scale")
            c1, c2 = st.columns(2)
            fav_date = c1.date_input("Log date", value=local_today(), format="MM/DD/YYYY", key="favorite_date")
            fav_meal = c2.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], key="favorite_meal")
            if st.button("Log favorite", type="primary"):
                _save_food_log(ctx, user.id, fav_date, fav_meal, {
                    "name": favorite.name,
                    "serving": favorite.serving,
                    "calories": favorite.calories,
                    "protein_g": favorite.protein_g,
                    "carbs_g": favorite.carbs_g,
                    "fat_g": favorite.fat_g,
                }, scale, notes="Favorite food")
                st.success("Favorite logged.")
            if st.button("Delete favorite"):
                with SessionLocal() as session:
                    session.execute(delete(models.FavoriteFood).where(models.FavoriteFood.id == favorite.id, models.FavoriteFood.user_id == user.id))
                    session.commit()
                st.rerun()
        else:
            st.info("Save foods from Verified search to build favorites.")
        st.subheader("Recent foods")
        if recent:
            recent_unique: dict[str, Any] = {}
            for row in recent:
                recent_unique.setdefault(row.food_name.lower(), row)
            chosen = st.selectbox("Recent item", list(recent_unique.values()), format_func=lambda x: f"{x.food_name} · {x.serving}")
            if st.button("Log recent item again"):
                _save_food_log(ctx, user.id, local_today(), chosen.meal, {
                    "name": chosen.food_name,
                    "serving": chosen.serving,
                    "calories": chosen.calories,
                    "protein_g": chosen.protein_g,
                    "carbs_g": chosen.carbs_g,
                    "fat_g": chosen.fat_g,
                }, 1.0, notes="Repeated recent food")
                st.success("Recent food logged.")

    with meals_tab:
        st.subheader("Save a diary meal")
        c1, c2 = st.columns(2)
        source_date = c1.date_input("Source diary date", value=local_today(), format="MM/DD/YYYY", key="saved_meal_source_date")
        source_meal = c2.selectbox("Meal to save", ["Breakfast", "Lunch", "Dinner", "Snack"], key="saved_meal_source_meal")
        meal_name = st.text_input("Saved meal name", value=f"{source_meal} template")
        if st.button("Save meal from diary"):
            with SessionLocal() as session:
                items = session.scalars(select(FoodLog).where(FoodLog.user_id == user.id, FoodLog.log_date == source_date, FoodLog.meal == source_meal)).all()
                if not items:
                    st.error("No food entries exist for that meal and date.")
                else:
                    saved = models.SavedMeal(user_id=user.id, name=meal_name.strip() or f"{source_meal} template", meal_type=source_meal)
                    session.add(saved)
                    session.flush()
                    for x in items:
                        session.add(models.SavedMealItem(
                            saved_meal_id=saved.id,
                            food_name=x.food_name,
                            serving=x.serving,
                            calories=x.calories,
                            protein_g=x.protein_g,
                            carbs_g=x.carbs_g,
                            fat_g=x.fat_g,
                        ))
                    session.commit()
                    st.success("Meal saved.")
        with SessionLocal() as session:
            saved_meals = session.scalars(select(models.SavedMeal).where(models.SavedMeal.user_id == user.id).order_by(models.SavedMeal.created_at.desc())).all()
        if saved_meals:
            chosen_meal = st.selectbox("Saved meal", saved_meals, format_func=lambda x: x.name)
            c1, c2 = st.columns(2)
            target_date = c1.date_input("Log saved meal on", value=local_today(), format="MM/DD/YYYY", key="saved_meal_target_date")
            target_meal = c2.selectbox("Meal category", ["Breakfast", "Lunch", "Dinner", "Snack"], key="saved_meal_target_meal")
            if st.button("Log complete saved meal", type="primary"):
                with SessionLocal() as session:
                    items = session.scalars(select(models.SavedMealItem).where(models.SavedMealItem.saved_meal_id == chosen_meal.id)).all()
                    for x in items:
                        session.add(FoodLog(
                            user_id=user.id,
                            log_date=target_date,
                            meal=target_meal,
                            food_name=x.food_name,
                            serving=x.serving,
                            calories=x.calories,
                            protein_g=x.protein_g,
                            carbs_g=x.carbs_g,
                            fat_g=x.fat_g,
                            notes=f"Saved meal: {chosen_meal.name}",
                        ))
                    session.commit()
                st.success("Saved meal logged.")
        st.subheader("Copy an entire day")
        c1, c2 = st.columns(2)
        copy_from = c1.date_input("Copy from", value=local_today() - timedelta(days=1), format="MM/DD/YYYY")
        copy_to = c2.date_input("Copy to", value=local_today(), format="MM/DD/YYYY")
        include_water = st.checkbox("Copy water entries", value=False)
        if st.button("Copy diary day"):
            with SessionLocal() as session:
                source_foods = session.scalars(select(FoodLog).where(FoodLog.user_id == user.id, FoodLog.log_date == copy_from)).all()
                for x in source_foods:
                    session.add(FoodLog(
                        user_id=user.id,
                        log_date=copy_to,
                        meal=x.meal,
                        food_name=x.food_name,
                        serving=x.serving,
                        calories=x.calories,
                        protein_g=x.protein_g,
                        carbs_g=x.carbs_g,
                        fat_g=x.fat_g,
                        notes=f"Copied from {copy_from.strftime('%m/%d/%Y')}",
                    ))
                if include_water:
                    source_water = session.scalars(select(ctx["WaterLog"]).where(ctx["WaterLog"].user_id == user.id, ctx["WaterLog"].log_date == copy_from)).all()
                    for x in source_water:
                        session.add(ctx["WaterLog"](user_id=user.id, log_date=copy_to, amount_ml=x.amount_ml))
                session.commit()
            st.success(f"Copied {len(source_foods)} food entries.")

    with forecast_tab:
        forecast_date = st.date_input("Forecast date", value=local_today(), format="MM/DD/YYYY", key="forecast_date")
        with SessionLocal() as session:
            row = session.execute(select(
                func.coalesce(func.sum(FoodLog.calories), 0),
                func.coalesce(func.sum(FoodLog.protein_g), 0),
                func.coalesce(func.sum(FoodLog.carbs_g), 0),
                func.coalesce(func.sum(FoodLog.fat_g), 0),
            ).where(FoodLog.user_id == user.id, FoodLog.log_date == forecast_date)).one()
            pref = _get_preferences(session, models, user.id)
            session.commit()
        totals = {"calories": float(row[0]), "protein": float(row[1]), "carbs": float(row[2]), "fat": float(row[3])}
        now_hour = local_now().hour + local_now().minute / 60
        if forecast_date != local_today():
            fraction = 1.0
        else:
            fraction = max(.15, min(1.0, (now_hour - pref.day_start_hour) / max(pref.day_end_hour - pref.day_start_hour, 1)))
        projected = {key: value / fraction for key, value in totals.items()}
        st.markdown(
            f"""
            <div class="nv-elite-grid">
                <div class="nv-elite-tile"><div class="nv-label">Projected calories</div><div class="nv-elite-big">{projected['calories']:.0f}</div></div>
                <div class="nv-elite-tile"><div class="nv-label">Projected protein</div><div class="nv-elite-big">{projected['protein']:.0f} g</div></div>
                <div class="nv-elite-tile"><div class="nv-label">Calories remaining</div><div class="nv-elite-big">{max(0,user.calorie_target-totals['calories']):.0f}</div></div>
                <div class="nv-elite-tile"><div class="nv-label">Protein remaining</div><div class="nv-elite-big">{max(0,user.protein_target-totals['protein']):.0f} g</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        gaps = {
            "protein": max(0, user.protein_target - totals["protein"]),
            "carbs": max(0, user.carb_target - totals["carbs"]),
            "fat": max(0, user.fat_target - totals["fat"]),
            "calories": max(0, user.calorie_target - totals["calories"]),
        }
        st.subheader("Fuel Gap Map")
        for label, value, target in [
            ("Protein", gaps["protein"], user.protein_target),
            ("Carbohydrates", gaps["carbs"], user.carb_target),
            ("Fat", gaps["fat"], user.fat_target),
            ("Calories", gaps["calories"], user.calorie_target),
        ]:
            pct = min(100, value / max(target, 1) * 100)
            st.markdown(f"<b>{label}</b>: {value:.0f} remaining<div class='nv-gap-bar'><span style='width:{pct:.1f}%'></span></div>", unsafe_allow_html=True)
        exclusions = [x.strip() for x in f"{pref.allergies},{pref.dislikes}".split(",") if x.strip()]
        suggestions = fuel_gap_suggestions(gaps["protein"], gaps["carbs"], gaps["fat"], gaps["calories"], exclusions)
        st.subheader("Practical next-food options")
        for item in suggestions:
            st.write(f"**{item['name']}** · {item['calories']} kcal · {item['protein_g']} g protein · {item['carbs_g']} g carbs · {item['fat_g']} g fat")


def _body_part_lookup(exercise_name: str, library: dict[str, list[str]]) -> str:
    needle = exercise_name.strip().lower()
    for part, exercises in library.items():
        if any(needle == x.lower() for x in exercises):
            return part
    for part, exercises in library.items():
        if any(needle in x.lower() or x.lower() in needle for x in exercises):
            return part
    return "Custom"


def _render_training_lab(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    WorkoutSession = ctx["WorkoutSession"]
    ExerciseSet = ctx["ExerciseSet"]
    library = ctx["EXERCISE_LIBRARY"]
    hero = ctx["hero"]
    hero(
        "Training Lab",
        "Build smarter training plans",
        "Create structured programs, plan progressive overload, track personal records, and manage muscle recovery.",
    )
    builder_tab, overload_tab, recovery_tab = st.tabs(["Program", "Progress & PRs", "Recovery"])

    with builder_tab:
        with st.form("elite_program_create"):
            name = st.text_input("Program name", value="My Elite Program")
            c1, c2 = st.columns(2)
            goal = c1.selectbox("Goal", ["General fitness", "Strength", "Muscle gain", "Fat loss", "Endurance", "Mobility"])
            days_per_week = c2.number_input("Days per week", min_value=1, max_value=7, value=3)
            notes = st.text_area("Program notes")
            create = st.form_submit_button("Create program", type="primary", width="stretch")
        if create:
            with SessionLocal() as session:
                session.add(models.WorkoutProgram(user_id=user.id, name=name.strip() or "Program", goal=goal, days_per_week=int(days_per_week), notes=notes.strip()))
                session.commit()
            st.success("Program created.")
            st.rerun()
        with SessionLocal() as session:
            programs = session.scalars(select(models.WorkoutProgram).where(models.WorkoutProgram.user_id == user.id).order_by(models.WorkoutProgram.created_at.desc())).all()
        if programs:
            program = st.selectbox("Program", programs, format_func=lambda x: f"{x.name} · {x.goal}")

            with st.expander("Manage selected program"):
                st.caption(
                    f"Delete {program.name} and all exercises saved inside it. "
                    "Workout sessions already created from this program will remain in your workout history."
                )
                confirm_program_delete = st.checkbox(
                    f"I understand that {program.name} will be permanently deleted.",
                    key=f"confirm_program_delete_{program.id}",
                )
                if st.button(
                    "Delete selected program",
                    key=f"delete_program_{program.id}",
                    type="primary",
                    width="stretch",
                    disabled=not confirm_program_delete,
                ):
                    with SessionLocal() as session:
                        owned_program = session.scalar(
                            select(models.WorkoutProgram).where(
                                models.WorkoutProgram.id == program.id,
                                models.WorkoutProgram.user_id == user.id,
                            )
                        )
                        if owned_program:
                            session.execute(
                                delete(models.WorkoutProgramExercise).where(
                                    models.WorkoutProgramExercise.program_id == owned_program.id
                                )
                            )
                            session.delete(owned_program)
                            session.commit()
                            st.success("Program deleted.")
                        else:
                            st.error("The selected program was not found.")
                    st.rerun()

            st.subheader("Add program exercise")
            c1, c2 = st.columns(2)
            day_name = c1.text_input("Training day", value="Day 1")
            body_part = c2.selectbox("Body part or activity", list(library.keys()))
            exercise_options = library[body_part] + ["Custom exercise"]
            selected = st.selectbox("Exercise", exercise_options)
            exercise_name = st.text_input("Custom exercise name") if selected == "Custom exercise" else selected
            c1, c2, c3, c4 = st.columns(4)
            sets = c1.number_input("Sets", min_value=1, max_value=20, value=3)
            reps_min = c2.number_input("Minimum reps", min_value=1, max_value=100, value=8)
            reps_max = c3.number_input("Maximum reps", min_value=1, max_value=100, value=12)
            rest = c4.number_input("Rest seconds", min_value=0, max_value=600, value=90, step=15)
            c5, c6, c7 = st.columns(3)
            target_weight = c5.number_input("Starting weight (lb)", min_value=0.0, value=0.0, step=5.0)
            superset = c6.text_input("Superset or circuit group", placeholder="A, B, Circuit 1")
            set_style = c7.selectbox("Set style", ["Standard", "Warm-up", "Drop set", "AMRAP", "Tempo", "Circuit"])
            if st.button("Add exercise to program", type="primary"):
                if not exercise_name.strip():
                    st.error("Enter an exercise name.")
                else:
                    with SessionLocal() as session:
                        count = session.scalar(select(func.count(models.WorkoutProgramExercise.id)).where(models.WorkoutProgramExercise.program_id == program.id)) or 0
                        session.add(models.WorkoutProgramExercise(
                            program_id=program.id,
                            day_name=day_name.strip() or "Day 1",
                            order_index=count + 1,
                            body_part=body_part,
                            exercise_name=exercise_name.strip(),
                            sets=int(sets),
                            reps_min=int(reps_min),
                            reps_max=max(int(reps_min), int(reps_max)),
                            target_weight_lb=float(target_weight),
                            rest_seconds=int(rest),
                            superset_group=superset.strip(),
                            set_style=set_style,
                        ))
                        session.commit()
                    st.success("Exercise added.")
                    st.rerun()
            with SessionLocal() as session:
                planned = session.scalars(select(models.WorkoutProgramExercise).where(models.WorkoutProgramExercise.program_id == program.id).order_by(models.WorkoutProgramExercise.day_name, models.WorkoutProgramExercise.order_index)).all()
            if planned:
                st.dataframe(pd.DataFrame([{
                    "Day": x.day_name,
                    "Exercise": x.exercise_name,
                    "Body part": x.body_part,
                    "Sets": x.sets,
                    "Rep range": f"{x.reps_min}-{x.reps_max}",
                    "Target weight": x.target_weight_lb,
                    "Rest": x.rest_seconds,
                    "Group": x.superset_group,
                    "Set style": x.set_style,
                } for x in planned]), width="stretch", hide_index=True)

                st.markdown("#### Delete a program exercise")
                exercise_to_delete = st.selectbox(
                    "Choose an exercise to delete",
                    planned,
                    format_func=lambda x: (
                        f"{x.day_name} · {x.exercise_name} · "
                        f"{x.sets} sets × {x.reps_min}-{x.reps_max} reps"
                    ),
                    key=f"program_exercise_delete_select_{program.id}",
                )
                if st.button(
                    "Delete selected exercise",
                    key=f"program_exercise_delete_button_{program.id}",
                    width="stretch",
                ):
                    with SessionLocal() as session:
                        owned_exercise = session.scalar(
                            select(models.WorkoutProgramExercise)
                            .join(
                                models.WorkoutProgram,
                                models.WorkoutProgramExercise.program_id == models.WorkoutProgram.id,
                            )
                            .where(
                                models.WorkoutProgramExercise.id == exercise_to_delete.id,
                                models.WorkoutProgram.user_id == user.id,
                            )
                        )
                        if owned_exercise:
                            session.delete(owned_exercise)
                            session.flush()
                            remaining = session.scalars(
                                select(models.WorkoutProgramExercise)
                                .where(models.WorkoutProgramExercise.program_id == program.id)
                                .order_by(
                                    models.WorkoutProgramExercise.day_name,
                                    models.WorkoutProgramExercise.order_index,
                                    models.WorkoutProgramExercise.id,
                                )
                            ).all()
                            for new_index, item in enumerate(remaining, start=1):
                                item.order_index = new_index
                            session.commit()
                    st.success("Program exercise deleted.")
                    st.rerun()

                days = sorted({x.day_name for x in planned})
                log_day = st.selectbox("Program day to start", days)
                workout_date = st.date_input("Workout date", value=local_today(), format="MM/DD/YYYY", key="program_workout_date")
                if st.button("Create workout session from this program day", type="primary", width="stretch"):
                    day_exercises = [x for x in planned if x.day_name == log_day]
                    with SessionLocal() as session:
                        workout = WorkoutSession(
                            user_id=user.id,
                            workout_date=workout_date,
                            workout_name=f"{program.name} · {log_day}",
                            category=program.goal,
                            duration_min=0,
                            calories_burned=0,
                            notes="Created from Elite Program Builder",
                        )
                        session.add(workout)
                        session.flush()
                        for ex in day_exercises:
                            for set_number in range(1, ex.sets + 1):
                                session.add(ExerciseSet(
                                    session_id=workout.id,
                                    exercise_name=ex.exercise_name,
                                    set_number=set_number,
                                    reps=ex.reps_min,
                                    weight_lb=ex.target_weight_lb,
                                    completed=False,
                                ))
                        session.commit()
                    st.success("Workout session created with planned sets.")

        st.subheader("Rest timer")
        timer_seconds = st.number_input("Rest duration in seconds", min_value=10, max_value=600, value=90, step=5, key="elite_rest_seconds")
        if st.button("Start rest timer", key="elite_start_rest_timer"):
            components.html(
                f"""
                <div style="font-family:Arial,sans-serif;padding:14px;border-radius:14px;background:#f5f3ff;border:1px solid #d8d2ff;text-align:center">
                  <div style="font-size:13px;font-weight:700;color:#6d5dfb">REST TIMER</div>
                  <div id="nvTimer" style="font-size:40px;font-weight:900;color:#172033;margin:6px 0">{int(timer_seconds)}</div>
                  <div id="nvTimerText" style="color:#64748b">Breathe and prepare for the next set.</div>
                </div>
                <script>
                  let remaining = {int(timer_seconds)};
                  const timer = document.getElementById('nvTimer');
                  const text = document.getElementById('nvTimerText');
                  const id = setInterval(() => {{
                    remaining -= 1;
                    timer.textContent = remaining;
                    if (remaining <= 0) {{
                      clearInterval(id);
                      timer.textContent = 'GO';
                      text.textContent = 'Rest complete. Start the next set.';
                    }}
                  }}, 1000);
                </script>
                """,
                height=130,
            )

    with overload_tab:
        with SessionLocal() as session:
            workout_ids = session.scalars(select(WorkoutSession.id).where(WorkoutSession.user_id == user.id)).all()
            sets = session.scalars(select(ExerciseSet).where(ExerciseSet.session_id.in_(workout_ids)).order_by(ExerciseSet.id.desc())).all() if workout_ids else []
        names = sorted({x.exercise_name for x in sets})
        if not names:
            st.info("Complete exercise sets to generate overload recommendations and personal records.")
        else:
            exercise = st.selectbox("Exercise history", names)
            exercise_part = _body_part_lookup(exercise, library)
            alternatives = [x for x in library.get(exercise_part, []) if x != exercise][:6]
            if alternatives:
                st.caption("Substitutions: " + ", ".join(alternatives))
            st.link_button("Open exercise demonstration search", "https://www.youtube.com/results?search_query=" + quote_plus(exercise + " exercise form"))
            history = [x for x in sets if x.exercise_name == exercise and x.completed]
            history.sort(key=lambda x: x.id)
            if history:
                latest = history[-1]
                best_weight = max(x.weight_lb for x in history)
                best_reps = max(x.reps for x in history)
                best_e1rm = max((x.weight_lb * (1 + x.reps / 30)) for x in history if x.weight_lb > 0 and x.reps > 0) if any(x.weight_lb > 0 and x.reps > 0 for x in history) else 0
                if latest.reps >= 12 and latest.weight_lb > 0:
                    recommendation = f"Try {latest.weight_lb + 5:g} lb for {max(6, latest.reps - 3)} to {latest.reps - 1} reps."
                elif latest.weight_lb > 0:
                    recommendation = f"Keep {latest.weight_lb:g} lb and target {latest.reps + 1} reps before adding weight."
                else:
                    recommendation = f"Add one repetition or a slightly harder variation of {exercise}."
                st.markdown(
                    f"""
                    <div class="nv-elite-grid">
                        <div class="nv-elite-tile"><div class="nv-label">Best weight</div><div class="nv-elite-big">{best_weight:g} lb</div></div>
                        <div class="nv-elite-tile"><div class="nv-label">Best reps</div><div class="nv-elite-big">{best_reps}</div></div>
                        <div class="nv-elite-tile"><div class="nv-label">Estimated 1RM</div><div class="nv-elite-big">{best_e1rm:.0f} lb</div></div>
                        <div class="nv-elite-tile"><div class="nv-label">Next target</div><div style="font-weight:850;margin-top:.35rem">{_esc(recommendation)}</div></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                volume_df = pd.DataFrame({"Set": list(range(1, len(history) + 1)), "Volume": [x.weight_lb * x.reps for x in history]}).set_index("Set")
                st.line_chart(volume_df, width="stretch")

    with recovery_tab:
        with SessionLocal() as session:
            workouts = session.scalars(select(WorkoutSession).where(WorkoutSession.user_id == user.id).order_by(WorkoutSession.workout_date.desc())).all()
            workout_ids = [w.id for w in workouts]
            sets = session.scalars(select(ExerciseSet).where(ExerciseSet.session_id.in_(workout_ids))).all() if workout_ids else []
        workout_by_id = {w.id: w for w in workouts}
        latest_by_part: dict[str, date] = {}
        volume_by_week: dict[str, float] = {}
        for set_row in sets:
            workout = workout_by_id.get(set_row.session_id)
            if not workout:
                continue
            part = _body_part_lookup(set_row.exercise_name, library)
            latest_by_part[part] = max(latest_by_part.get(part, date.min), workout.workout_date)
            week = workout.workout_date.strftime("%Y-W%W")
            volume_by_week[week] = volume_by_week.get(week, 0.0) + set_row.weight_lb * set_row.reps
        rows = []
        for part in library.keys():
            last = latest_by_part.get(part)
            days_ago = (local_today() - last).days if last else None
            status = "Ready" if days_ago is None or days_ago >= 3 else "Moderate" if days_ago == 2 else "Recovering"
            rows.append({"Body part": part, "Last trained": last.strftime("%m/%d/%Y") if last else "No data", "Days ago": days_ago, "Status": status})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        if volume_by_week:
            volume_values = list(volume_by_week.values())
            recent = volume_values[-1]
            prior_avg = sum(volume_values[-4:-1]) / max(len(volume_values[-4:-1]), 1)
            deload = recent > prior_avg * 1.3 and len(volume_values) >= 3
            st.info("Deload signal: Reduce volume 20 to 30% this week." if deload else "Deload signal: Current volume does not require a planned reduction based on available data.")
            st.bar_chart(pd.DataFrame({"Weekly volume": volume_by_week}), width="stretch")


def _render_meal_planner(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    FoodLog = ctx["FoodLog"]
    settings_tab, plan_tab, grocery_tab = st.tabs(["Preferences", "7-day plan", "Grocery and pantry"])
    with settings_tab:
        with SessionLocal() as session:
            pref = _get_preferences(session, models, user.id)
            values = {
                "allergies": pref.allergies,
                "dislikes": pref.dislikes,
                "eating_style": pref.eating_style,
                "weekly_budget": pref.weekly_budget,
                "household_servings": pref.household_servings,
                "day_start_hour": pref.day_start_hour,
                "day_end_hour": pref.day_end_hour,
            }
            session.commit()
        with st.form("elite_preferences"):
            allergies = st.text_input("Allergies or exclusions", value=values["allergies"], placeholder="Peanuts, shellfish")
            dislikes = st.text_input("Foods you dislike", value=values["dislikes"], placeholder="Mushrooms, tuna")
            c1, c2, c3 = st.columns(3)
            style = c1.selectbox("Eating style", ["Balanced", "High protein", "Vegetarian", "Lower carbohydrate", "Mediterranean"], index=["Balanced", "High protein", "Vegetarian", "Lower carbohydrate", "Mediterranean"].index(values["eating_style"]) if values["eating_style"] in ["Balanced", "High protein", "Vegetarian", "Lower carbohydrate", "Mediterranean"] else 0)
            budget = c2.number_input("Weekly grocery budget", min_value=0.0, value=float(values["weekly_budget"]), step=10.0)
            servings = c3.number_input("Household servings", min_value=1, max_value=20, value=int(values["household_servings"]))
            c4, c5 = st.columns(2)
            day_start = c4.number_input("Typical day starts", min_value=0, max_value=23, value=int(values["day_start_hour"]))
            day_end = c5.number_input("Typical day ends", min_value=1, max_value=24, value=int(values["day_end_hour"]))
            save = st.form_submit_button("Save preferences", type="primary", width="stretch")
        if save:
            with SessionLocal() as session:
                pref = _get_preferences(session, models, user.id)
                pref.allergies = allergies.strip()
                pref.dislikes = dislikes.strip()
                pref.eating_style = style
                pref.weekly_budget = budget
                pref.household_servings = int(servings)
                pref.day_start_hour = int(day_start)
                pref.day_end_hour = max(int(day_start) + 1, int(day_end))
                session.commit()
            st.success("Preferences saved.")

    with plan_tab:
        week_start = st.date_input("Plan week starts", value=local_today() - timedelta(days=local_today().weekday()), format="MM/DD/YYYY")
        with SessionLocal() as session:
            pref = _get_preferences(session, models, user.id)
            session.commit()
        exclusions = [x.strip().lower() for x in f"{pref.allergies},{pref.dislikes}".split(",") if x.strip()]
        if st.button("Generate 7-day macro-aware plan", type="primary", width="stretch"):
            eligible = [m for m in MEAL_TEMPLATES if not any(term in (m["name"] + " " + " ".join(m["ingredients"])).lower() for term in exclusions)]
            if not eligible:
                st.error("No templates remain after applying exclusions. Remove an exclusion or add meals manually.")
            else:
                with SessionLocal() as session:
                    for offset in range(7):
                        plan_date = week_start + timedelta(days=offset)
                        existing = session.scalar(select(func.count(models.MealPlanEntry.id)).where(models.MealPlanEntry.user_id == user.id, models.MealPlanEntry.plan_date == plan_date)) or 0
                        if existing:
                            continue
                        for meal_type in ["Breakfast", "Lunch", "Dinner", "Snack"]:
                            candidates = [m for m in eligible if m["meal"] == meal_type] or eligible
                            template = candidates[offset % len(candidates)]
                            session.add(models.MealPlanEntry(
                                user_id=user.id,
                                plan_date=plan_date,
                                meal=meal_type,
                                name=template["name"],
                                servings=float(pref.household_servings),
                                calories=template["calories"],
                                protein_g=template["protein_g"],
                                carbs_g=template["carbs_g"],
                                fat_g=template["fat_g"],
                                fiber_g=template["fiber_g"],
                                ingredients_json=json.dumps(template["ingredients"]),
                                estimated_cost=template["estimated_cost"] * pref.household_servings,
                            ))
                    session.commit()
                st.success("Plan created. Existing days were preserved.")
                st.rerun()
        with st.expander("Add a custom planned meal"):
            with st.form("custom_plan_meal"):
                c1, c2 = st.columns(2)
                pdate = c1.date_input("Plan date", value=week_start, format="MM/DD/YYYY")
                pmeal = c2.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"])
                pname = st.text_input("Meal name")
                c1, c2, c3, c4 = st.columns(4)
                pcal = c1.number_input("Calories", min_value=0.0)
                ppro = c2.number_input("Protein", min_value=0.0)
                pcarb = c3.number_input("Carbs", min_value=0.0)
                pfat = c4.number_input("Fat", min_value=0.0)
                ingredients = st.text_input("Ingredients, separated by commas")
                cost = st.number_input("Estimated cost", min_value=0.0)
                add = st.form_submit_button("Add to plan", type="primary", width="stretch")
            if add:
                with SessionLocal() as session:
                    session.add(models.MealPlanEntry(
                        user_id=user.id, plan_date=pdate, meal=pmeal, name=pname.strip() or "Custom meal",
                        servings=1, calories=pcal, protein_g=ppro, carbs_g=pcarb, fat_g=pfat,
                        ingredients_json=json.dumps([x.strip() for x in ingredients.split(",") if x.strip()]), estimated_cost=cost,
                    ))
                    session.commit()
                st.success("Meal added.")
                st.rerun()
        with SessionLocal() as session:
            entries = session.scalars(select(models.MealPlanEntry).where(models.MealPlanEntry.user_id == user.id, models.MealPlanEntry.plan_date >= week_start, models.MealPlanEntry.plan_date < week_start + timedelta(days=7)).order_by(models.MealPlanEntry.plan_date, models.MealPlanEntry.meal)).all()
        if entries:
            st.dataframe(pd.DataFrame([{
                "Date": x.plan_date.strftime("%m/%d/%Y"), "Meal": x.meal, "Plan": x.name, "Servings": x.servings,
                "Calories": x.calories, "Protein": x.protein_g, "Cost": x.estimated_cost, "Logged": x.completed,
            } for x in entries]), width="stretch", hide_index=True)
            selected = st.selectbox("Planned meal action", entries, format_func=lambda x: f"{x.plan_date.strftime('%m/%d/%Y')} · {x.meal} · {x.name}")
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("Log planned meal", type="primary"):
                    with SessionLocal() as session:
                        session.add(FoodLog(
                            user_id=user.id, log_date=selected.plan_date, meal=selected.meal, food_name=selected.name,
                            serving=f"{selected.servings:g} serving(s)", calories=selected.calories,
                            protein_g=selected.protein_g, carbs_g=selected.carbs_g, fat_g=selected.fat_g,
                            notes="Logged from Elite Meal Planner",
                        ))
                        owned = session.scalar(select(models.MealPlanEntry).where(models.MealPlanEntry.id == selected.id, models.MealPlanEntry.user_id == user.id))
                        if owned:
                            owned.completed = True
                        session.commit()
                    st.success("Meal logged.")
                    st.rerun()
            with c2:
                if st.button("Mark complete"):
                    with SessionLocal() as session:
                        owned = session.scalar(select(models.MealPlanEntry).where(models.MealPlanEntry.id == selected.id, models.MealPlanEntry.user_id == user.id))
                        if owned:
                            owned.completed = True
                            session.commit()
                    st.rerun()
            with c3:
                if st.button("Delete planned meal"):
                    with SessionLocal() as session:
                        session.execute(delete(models.MealPlanEntry).where(models.MealPlanEntry.id == selected.id, models.MealPlanEntry.user_id == user.id))
                        session.commit()
                    st.rerun()

    with grocery_tab:
        st.subheader("Pantry inventory")
        with st.form("add_pantry"):
            c1, c2, c3 = st.columns(3)
            item_name = c1.text_input("Pantry item")
            quantity = c2.number_input("Quantity", min_value=0.0, value=1.0)
            unit = c3.text_input("Unit", value="item")
            c4, c5 = st.columns(2)
            category = c4.selectbox("Category", ["Produce", "Protein", "Dairy", "Frozen", "Pantry", "Bakery", "Other"])
            track_expiration = st.checkbox("Track an expiration date", value=False)
            expires = c5.date_input("Expires", value=local_today() + timedelta(days=14), format="MM/DD/YYYY", disabled=not track_expiration)
            add = st.form_submit_button("Add pantry item", type="primary", width="stretch")
        if add and item_name.strip():
            with SessionLocal() as session:
                session.add(models.PantryItem(user_id=user.id, name=item_name.strip(), quantity=quantity, unit=unit.strip(), category=category, expires_on=expires if track_expiration else None))
                session.commit()
            st.rerun()
        with SessionLocal() as session:
            pantry = session.scalars(select(models.PantryItem).where(models.PantryItem.user_id == user.id).order_by(models.PantryItem.category, models.PantryItem.name)).all()
            future_entries = session.scalars(select(models.MealPlanEntry).where(models.MealPlanEntry.user_id == user.id, models.MealPlanEntry.plan_date >= local_today(), models.MealPlanEntry.plan_date <= local_today() + timedelta(days=7))).all()
        if pantry:
            st.dataframe(pd.DataFrame([{"Item": x.name, "Quantity": x.quantity, "Unit": x.unit, "Category": x.category, "Expires": x.expires_on.strftime("%m/%d/%Y") if x.expires_on else ""} for x in pantry]), width="stretch", hide_index=True)
        pantry_names = {x.name.strip().lower() for x in pantry}
        grocery: dict[str, int] = {}
        estimated_cost = 0.0
        for entry in future_entries:
            estimated_cost += entry.estimated_cost
            try:
                ingredients = json.loads(entry.ingredients_json or "[]")
            except json.JSONDecodeError:
                ingredients = []
            for ingredient in ingredients:
                key = str(ingredient).strip()
                if key and key.lower() not in pantry_names:
                    grocery[key] = grocery.get(key, 0) + 1
        st.subheader("Automatic grocery list")
        if grocery:
            grocery_df = pd.DataFrame([{"Ingredient": key, "Planned uses": count} for key, count in sorted(grocery.items())])
            st.dataframe(grocery_df, width="stretch", hide_index=True)
            st.download_button("Download grocery list", data=grocery_df.to_csv(index=False).encode(), file_name="nourivanta_grocery_list.csv", mime="text/csv", width="stretch")
        else:
            st.info("Generate a meal plan or add custom meals with ingredients to build the grocery list.")
        st.metric("Estimated planned grocery cost", f"${estimated_cost:,.2f}")


def _combined_readiness(user: Any, checkin: Any, wearable: Any, totals: dict[str, float], ctx: dict[str, Any]) -> int:
    base = ctx["readiness_score"](checkin, totals, user)
    if wearable is None:
        return base
    score = float(base)
    if wearable.sleep_quality:
        score += (wearable.sleep_quality - 5) * 2
    if wearable.sleep_hours:
        score += max(-8, min(8, (wearable.sleep_hours - 7) * 4))
    if wearable.hrv_ms:
        score += max(-5, min(8, (wearable.hrv_ms - 35) / 5))
    if wearable.resting_hr:
        score += max(-6, min(4, (75 - wearable.resting_hr) / 5))
    return int(max(0, min(100, round(score))))


def _render_progress_center(user: Any, ctx: dict[str, Any]) -> None:
    days = st.selectbox("Progress window", [7, 30, 90, 180], index=1, format_func=lambda x: f"Last {x} days", key="progress_center_days")
    metrics = _period_metrics(ctx, user, days)
    st.markdown(
        f"""
        <div class="nv-elite-grid">
            <div class="nv-elite-tile"><div class="nv-label">Nutrition consistency</div><div class="nv-elite-big">{metrics['nutrition_consistency_pct']}%</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Workout minutes</div><div class="nv-elite-big">{metrics['workout_minutes']}</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Weight change</div><div class="nv-elite-big">{metrics['weight_change_lb'] if metrics['weight_change_lb'] is not None else 'No trend'}{'' if metrics['weight_change_lb'] is None else ' lb'}</div></div>
            <div class="nv-elite-tile"><div class="nv-label">Training volume</div><div class="nv-elite-big">{metrics['training_volume_lb_reps']:,.0f}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    start = local_today() - timedelta(days=days - 1)
    with ctx["SessionLocal"]() as session:
        measurements = session.scalars(select(ctx["Measurement"]).where(ctx["Measurement"].user_id == user.id, ctx["Measurement"].measurement_date >= start).order_by(ctx["Measurement"].measurement_date)).all()
        workouts = session.scalars(select(ctx["WorkoutSession"]).where(ctx["WorkoutSession"].user_id == user.id, ctx["WorkoutSession"].workout_date >= start).order_by(ctx["WorkoutSession"].workout_date)).all()
    weights = [(m.measurement_date, m.weight_lb) for m in measurements if m.weight_lb is not None]
    if weights:
        df = pd.DataFrame(weights, columns=["Date", "Weight"]).set_index("Date")
        df["7-entry moving average"] = df["Weight"].rolling(7, min_periods=1).mean()
        st.line_chart(df, width="stretch")
    if workouts:
        weekly: dict[str, int] = {}
        for workout in workouts:
            key = workout.workout_date.strftime("%Y-W%W")
            weekly[key] = weekly.get(key, 0) + workout.duration_min
        st.bar_chart(pd.DataFrame({"Training minutes": weekly}), width="stretch")
    st.subheader("Goal forecast")
    with ctx["SessionLocal"]() as session:
        goals = session.scalars(select(ctx["Goal"]).where(ctx["Goal"].user_id == user.id, ctx["Goal"].completed.is_(False))).all()
    if goals:
        forecast_rows = []
        for goal in goals:
            progress = goal.current_value / max(goal.target_value, 1)
            elapsed = max((local_today() - goal.created_at.date()).days, 1)
            daily_rate = goal.current_value / elapsed
            days_left = (goal.target_value - goal.current_value) / daily_rate if daily_rate > 0 else None
            forecast_date = local_today() + timedelta(days=math.ceil(days_left)) if days_left is not None and days_left >= 0 else None
            forecast_rows.append({"Goal": goal.title, "Progress": f"{progress*100:.0f}%", "Forecast": forecast_date.strftime("%m/%d/%Y") if forecast_date else "More data needed", "Target": goal.target_date.strftime("%m/%d/%Y") if goal.target_date else ""})
        st.dataframe(pd.DataFrame(forecast_rows), width="stretch", hide_index=True)
    else:
        st.info("Add an active goal to generate a forecast.")


def _render_voice_wearables(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    voice_tab, wearable_tab = st.tabs(["Voice logging", "Wearable Bridge"])
    with voice_tab:
        api_key = st.session_state.get("openai_api_key") or os.getenv("OPENAI_API_KEY", "")
        audio = None
        if hasattr(st, "audio_input"):
            audio = st.audio_input("Record a food, water, or workout command")
        else:
            st.info("Your Streamlit version does not provide live audio recording. Upload an audio file or type the command.")
        uploaded = st.file_uploader("Upload voice recording", type=["wav", "mp3", "m4a", "ogg"], key="elite_voice_upload")
        transcript = st.text_area("Transcript or typed command", value=st.session_state.get("elite_voice_transcript", ""), placeholder="Log two scrambled eggs and one slice of toast for breakfast")
        if st.button("Transcribe recording", disabled=not bool(audio or uploaded) or not bool(api_key)):
            source = audio or uploaded
            try:
                with st.spinner("Transcribing..."):
                    st.session_state.elite_voice_transcript = transcribe_audio(source.getvalue(), api_key, getattr(source, "name", "voice.wav"))
                st.rerun()
            except EliteServiceError as exc:
                st.error(str(exc))
        if st.button("Interpret command", disabled=not bool((transcript or st.session_state.get("elite_voice_transcript")) and api_key), type="primary"):
            try:
                with st.spinner("Building editable entry..."):
                    st.session_state.elite_voice_result = parse_voice_command(transcript or st.session_state.elite_voice_transcript, api_key)
            except EliteServiceError as exc:
                st.error(str(exc))
        result = st.session_state.get("elite_voice_result")
        if result:
            command_type = result.get("command_type")
            st.info(f"Detected command: {command_type}")
            log_date = st.date_input("Entry date", value=local_today(), format="MM/DD/YYYY", key="voice_entry_date")
            if command_type == "food":
                food = result.get("food") or {}
                with st.form("voice_food_form"):
                    meal = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"], index=["Breakfast", "Lunch", "Dinner", "Snack"].index(food.get("meal")) if food.get("meal") in ["Breakfast", "Lunch", "Dinner", "Snack"] else 0)
                    name = st.text_input("Food", value=str(food.get("food_name") or "Voice food"))
                    serving = st.text_input("Serving", value=str(food.get("serving") or "1 serving"))
                    c1, c2, c3, c4 = st.columns(4)
                    calories = c1.number_input("Calories", min_value=0.0, value=_float(food.get("calories")))
                    protein = c2.number_input("Protein", min_value=0.0, value=_float(food.get("protein_g")))
                    carbs = c3.number_input("Carbs", min_value=0.0, value=_float(food.get("carbs_g")))
                    fat = c4.number_input("Fat", min_value=0.0, value=_float(food.get("fat_g")))
                    save = st.form_submit_button("Save voice food", type="primary", width="stretch")
                if save:
                    _save_food_log(ctx, user.id, log_date, meal, {"name": name, "serving": serving, "calories": calories, "protein_g": protein, "carbs_g": carbs, "fat_g": fat}, notes="Voice entry reviewed by user")
                    st.success("Voice food logged.")
            elif command_type == "water":
                ounces = st.number_input("Water ounces", min_value=0.0, value=_float(result.get("water_oz")))
                if st.button("Save voice water", type="primary"):
                    with SessionLocal() as session:
                        session.add(ctx["WaterLog"](user_id=user.id, log_date=log_date, amount_ml=round(ounces * 29.5735295625)))
                        session.commit()
                    st.success("Water logged.")
            elif command_type == "workout":
                workout = result.get("workout") or {}
                with st.form("voice_workout_form"):
                    workout_name = st.text_input("Workout name", value=str(workout.get("workout_name") or "Voice workout"))
                    category = st.text_input("Category", value=str(workout.get("category") or "Strength"))
                    exercise = st.text_input("Exercise", value=str(workout.get("exercise_name") or "Exercise"))
                    c1, c2, c3 = st.columns(3)
                    sets = c1.number_input("Sets", min_value=1, value=max(1, int(_float(workout.get("sets"), 1))))
                    reps = c2.number_input("Reps", min_value=0, value=max(0, int(_float(workout.get("reps")))))
                    weight = c3.number_input("Weight", min_value=0.0, value=_float(workout.get("weight_lb")))
                    duration = st.number_input("Duration minutes", min_value=0.0, value=_float(workout.get("duration_min")))
                    save = st.form_submit_button("Save voice workout", type="primary", width="stretch")
                if save:
                    with SessionLocal() as session:
                        workout_row = ctx["WorkoutSession"](user_id=user.id, workout_date=log_date, workout_name=workout_name, category=category, duration_min=round(duration), notes="Voice entry reviewed by user")
                        session.add(workout_row)
                        session.flush()
                        for index in range(1, int(sets) + 1):
                            session.add(ctx["ExerciseSet"](session_id=workout_row.id, exercise_name=exercise, set_number=index, reps=int(reps), weight_lb=weight, completed=True))
                        session.commit()
                    st.success("Voice workout logged.")

    with wearable_tab:
        st.markdown("**Wearable Bridge** accepts manual records and CSV exports from health platforms. Direct Apple Health and Health Connect permission access requires a native mobile companion app.")
        with st.form("wearable_manual"):
            c1, c2 = st.columns(2)
            metric_date = c1.date_input("Metric date", value=local_today(), format="MM/DD/YYYY")
            source = c2.selectbox("Source", ["Manual", "Apple Health export", "Health Connect export", "Garmin export", "Fitbit export", "Other"])
            c1, c2, c3 = st.columns(3)
            sleep = c1.number_input("Sleep hours", min_value=0.0, max_value=24.0, value=7.0, step=0.1)
            quality = c2.slider("Sleep quality", 1, 10, 7)
            steps = c3.number_input("Steps", min_value=0, value=0, step=500)
            c4, c5, c6 = st.columns(3)
            resting_hr = c4.number_input("Resting heart rate", min_value=0.0, value=0.0)
            hrv = c5.number_input("HRV (ms)", min_value=0.0, value=0.0)
            active_calories = c6.number_input("Active calories", min_value=0.0, value=0.0)
            save = st.form_submit_button("Save wearable metrics", type="primary", width="stretch")
        if save:
            with SessionLocal() as session:
                existing = session.scalar(select(models.WearableMetric).where(models.WearableMetric.user_id == user.id, models.WearableMetric.metric_date == metric_date, models.WearableMetric.source == source))
                if existing:
                    existing.sleep_hours = sleep
                    existing.sleep_quality = quality
                    existing.steps = int(steps)
                    existing.resting_hr = resting_hr
                    existing.hrv_ms = hrv
                    existing.active_calories = active_calories
                else:
                    session.add(models.WearableMetric(user_id=user.id, metric_date=metric_date, source=source, sleep_hours=sleep, sleep_quality=quality, steps=int(steps), resting_hr=resting_hr, hrv_ms=hrv, active_calories=active_calories))
                session.commit()
            st.success("Wearable metrics saved.")
        upload = st.file_uploader("Import wearable CSV", type=["csv"], key="wearable_csv")
        if upload is not None:
            try:
                df = pd.read_csv(upload)
                st.dataframe(df.head(20), width="stretch")
                st.caption("Supported columns: date, source, sleep_hours, sleep_quality, steps, resting_hr, hrv_ms, active_calories")
                if st.button("Import displayed CSV rows"):
                    imported = 0
                    with SessionLocal() as session:
                        for _, row in df.iterrows():
                            parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
                            if pd.isna(parsed_date):
                                continue
                            session.add(models.WearableMetric(
                                user_id=user.id,
                                metric_date=parsed_date.date(),
                                source=str(row.get("source") or "CSV import"),
                                sleep_hours=_float(row.get("sleep_hours")),
                                sleep_quality=max(1, min(10, int(_float(row.get("sleep_quality"), 5)))),
                                steps=int(_float(row.get("steps"))),
                                resting_hr=_float(row.get("resting_hr")),
                                hrv_ms=_float(row.get("hrv_ms")),
                                active_calories=_float(row.get("active_calories")),
                            ))
                            imported += 1
                        session.commit()
                    st.success(f"Imported {imported} wearable records.")
            except Exception as exc:
                st.error(f"CSV import failed: {exc}")
        with SessionLocal() as session:
            latest = session.scalar(select(models.WearableMetric).where(models.WearableMetric.user_id == user.id).order_by(models.WearableMetric.metric_date.desc(), models.WearableMetric.id.desc()))
            checkin = session.scalar(select(ctx["DailyCheckIn"]).where(ctx["DailyCheckIn"].user_id == user.id, ctx["DailyCheckIn"].checkin_date == local_today()))
            nutrition_row = session.execute(select(
                func.coalesce(func.sum(ctx["FoodLog"].calories), 0), func.coalesce(func.sum(ctx["FoodLog"].protein_g), 0),
                func.coalesce(func.sum(ctx["FoodLog"].carbs_g), 0), func.coalesce(func.sum(ctx["FoodLog"].fat_g), 0),
            ).where(ctx["FoodLog"].user_id == user.id, ctx["FoodLog"].log_date == local_today())).one()
            water = session.scalar(select(func.coalesce(func.sum(ctx["WaterLog"].amount_ml), 0)).where(ctx["WaterLog"].user_id == user.id, ctx["WaterLog"].log_date == local_today())) or 0
        totals = {"calories": float(nutrition_row[0]), "protein": float(nutrition_row[1]), "carbs": float(nutrition_row[2]), "fat": float(nutrition_row[3]), "water": float(water)}
        score = _combined_readiness(user, checkin, latest, totals, ctx)
        if score >= 80:
            recommendation = "High readiness. A heavy strength or higher-intensity session fits the current data."
            css = "nv-readiness-high"
        elif score >= 60:
            recommendation = "Moderate readiness. Train normally and keep one or two repetitions in reserve."
            css = "nv-readiness-medium"
        else:
            recommendation = "Recovery priority. Use mobility, walking, technique work, or reduced volume."
            css = "nv-readiness-low"
        st.markdown(f"<div class='nv-elite-header'><div class='nv-label'>Recovery-to-Training Match</div><div class='nv-elite-big {css}'>{score}/100</div><div>{_esc(recommendation)}</div></div>", unsafe_allow_html=True)


def _render_family_security(user: Any, ctx: dict[str, Any]) -> None:
    models = ctx["models"]
    SessionLocal = ctx["SessionLocal"]
    family_tab, coach_tab, security_tab, premium_tab = st.tabs(["Family profiles", "Coach mode", "Security", "Premium-ready"])
    with family_tab:
        with st.form("household_profile"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Profile name")
            relationship = c2.text_input("Relationship", value="Family")
            c3, c4, c5 = st.columns(3)
            age = c3.number_input("Age", min_value=0, max_value=120, value=0)
            calories = c4.number_input("Calorie target", min_value=500, max_value=10000, value=2000)
            protein = c5.number_input("Protein target", min_value=0, max_value=1000, value=100)
            private = st.checkbox("Keep measurements private", value=True)
            add = st.form_submit_button("Add family profile", type="primary", width="stretch")
        if add and name.strip():
            with SessionLocal() as session:
                session.add(models.HouseholdProfile(owner_user_id=user.id, name=name.strip(), relationship=relationship.strip(), age=int(age) or None, calorie_target=int(calories), protein_target=int(protein), private_measurements=private))
                session.commit()
            st.rerun()
        with SessionLocal() as session:
            profiles = session.scalars(select(models.HouseholdProfile).where(models.HouseholdProfile.owner_user_id == user.id)).all()
        if profiles:
            st.dataframe(pd.DataFrame([{"Name": x.name, "Relationship": x.relationship, "Age": x.age, "Calories": x.calorie_target, "Protein": x.protein_target, "Private measurements": x.private_measurements} for x in profiles]), width="stretch", hide_index=True)
    with coach_tab:
        with SessionLocal() as session:
            pref = _get_preferences(session, models, user.id)
            if not pref.coach_share_code:
                pref.coach_share_code = secrets.token_urlsafe(8)
            share_code = pref.coach_share_code
            session.commit()
        st.code(share_code)
        st.caption("Share this code only with someone you trust. It identifies the export you choose to share. It does not expose your password.")
        if st.button("Rotate coach share code"):
            with SessionLocal() as session:
                pref = _get_preferences(session, models, user.id)
                pref.coach_share_code = secrets.token_urlsafe(8)
                session.commit()
            st.rerun()
        with st.form("coach_note"):
            c1, c2 = st.columns(2)
            note_date = c1.date_input("Note date", value=local_today(), format="MM/DD/YYYY")
            coach_name = c2.text_input("Coach or reviewer", value="Coach")
            category = st.selectbox("Category", ["General", "Nutrition", "Training", "Recovery", "Goal"])
            note = st.text_area("Coach note")
            save = st.form_submit_button("Save coach note", type="primary", width="stretch")
        if save and note.strip():
            with SessionLocal() as session:
                session.add(models.CoachNote(user_id=user.id, note_date=note_date, coach_name=coach_name.strip() or "Coach", category=category, note=note.strip()))
                session.commit()
            st.success("Coach note saved.")
        with SessionLocal() as session:
            notes = session.scalars(select(models.CoachNote).where(models.CoachNote.user_id == user.id).order_by(models.CoachNote.note_date.desc())).all()
        if notes:
            st.dataframe(pd.DataFrame([{"Date": x.note_date.strftime("%m/%d/%Y"), "Coach": x.coach_name, "Category": x.category, "Note": x.note} for x in notes]), width="stretch", hide_index=True)
    with security_tab:
        with SessionLocal() as session:
            security = _get_security(session, models, user.id)
            timeout_value = security.session_timeout_min
            events = session.scalars(select(models.LoginEvent).where(models.LoginEvent.user_id == user.id).order_by(models.LoginEvent.event_time.desc()).limit(20)).all()
            session.commit()
        timeout = st.number_input("Automatic sign-out after inactive minutes", min_value=15, max_value=720, value=int(timeout_value), step=15)
        if st.button("Save session timeout"):
            with SessionLocal() as session:
                security = _get_security(session, models, user.id)
                security.session_timeout_min = int(timeout)
                session.commit()
            st.success("Session timeout saved.")
        if st.button("Generate a new recovery code"):
            code = issue_recovery_code(SessionLocal, models, user.id, ctx["hash_password"])
            st.session_state.elite_new_recovery_code = code
        if st.session_state.get("elite_new_recovery_code"):
            st.warning("Save this recovery code in a secure place. Generating another code invalidates this one.")
            st.code(st.session_state.elite_new_recovery_code)
        st.subheader("Recent account access")
        if events:
            st.dataframe(pd.DataFrame([{"Date": utc_naive_to_local(x.event_time).strftime("%m/%d/%Y %I:%M %p"), "Success": x.success, "Client": x.client_info} for x in events]), width="stretch", hide_index=True)
    with premium_tab:
        payment_link = os.getenv("STRIPE_PAYMENT_LINK", "").strip()
        st.markdown("**Premium structure is ready for a hosted Stripe subscription link.** Core app data and features stay available even when billing is not configured.")
        if payment_link:
            st.link_button("Open NouriVanta Elite subscription", payment_link)
        else:
            st.info("Set STRIPE_PAYMENT_LINK in your hosting secrets to display a live subscription checkout button.")
        st.markdown("Planned premium controls: unlimited AI scans, advanced coaching history, wearable connectors, trainer access, family planning, and extended reports.")


def render_adaptive_coach(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_coach(user, ctx)


def render_food_intelligence(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_food_intelligence(user, ctx)


def render_training_lab(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_training_lab(user, ctx)


def render_meal_planner(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_meal_planner(user, ctx)


def render_elite_progress_center(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_progress_center(user, ctx)


def render_voice_and_wearables(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_voice_wearables(user, ctx)


def render_family_and_security(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    _render_family_security(user, ctx)


def render_elite_hub(user: Any, ctx: dict[str, Any]) -> None:
    inject_elite_css()
    ctx["hero"](
        "NouriVanta Elite",
        "Personal intelligence for nutrition, training, and recovery",
        "Use adaptive coaching, verified food search, macro forecasting, program design, meal planning, voice logging, wearable imports, family tools, and account security from one workspace.",
    )
    st.markdown(
        """
        <div class="nv-elite-header">
            <div class="nv-label">Elite operating system</div>
            <div style="font-size:1.2rem;font-weight:900;margin:.25rem 0">Your data stays in the existing NouriVanta account.</div>
            <div class="nv-meta">Every recommendation remains reviewable. Targets and records change only after you confirm an action.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.tabs([
        "Adaptive Coach",
        "Food Intelligence",
        "Training Lab",
        "Meal Planner",
        "Progress Center",
        "Voice and Wearables",
        "Family and Security",
    ])
    with tabs[0]:
        _render_coach(user, ctx)
    with tabs[1]:
        _render_food_intelligence(user, ctx)
    with tabs[2]:
        _render_training_lab(user, ctx)
    with tabs[3]:
        _render_meal_planner(user, ctx)
    with tabs[4]:
        _render_progress_center(user, ctx)
    with tabs[5]:
        _render_voice_wearables(user, ctx)
    with tabs[6]:
        _render_family_security(user, ctx)


def elite_export_files(SessionLocal: Any, models: SimpleNamespace, user_id: int) -> dict[str, bytes]:
    model_map = {
        "favorite_foods.csv": models.FavoriteFood,
        "saved_meals.csv": models.SavedMeal,
        "meal_plan_entries.csv": models.MealPlanEntry,
        "pantry_items.csv": models.PantryItem,
        "workout_programs.csv": models.WorkoutProgram,
        "coach_reports.csv": models.CoachReport,
        "wearable_metrics.csv": models.WearableMetric,
        "household_profiles.csv": models.HouseholdProfile,
        "coach_notes.csv": models.CoachNote,
        "login_events.csv": models.LoginEvent,
    }
    files: dict[str, bytes] = {}
    with SessionLocal() as session:
        for filename, model in model_map.items():
            user_column = getattr(model, "user_id", None)
            if user_column is None:
                user_column = getattr(model, "owner_user_id", None)
            if user_column is None:
                continue
            rows = session.scalars(select(model).where(user_column == user_id)).all()
            payload = []
            for row in rows:
                payload.append({column.name: getattr(row, column.name) for column in model.__table__.columns if column.name not in {"user_id", "owner_user_id", "recovery_code_hash"}})
            files[filename] = pd.DataFrame(payload).to_csv(index=False).encode("utf-8")
    return files

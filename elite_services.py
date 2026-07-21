from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image

FDC_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"


class EliteServiceError(RuntimeError):
    """Raised when an optional external elite service fails."""


@dataclass(slots=True)
class NormalizedFood:
    source: str
    source_id: str
    name: str
    brand: str
    serving_description: str
    serving_grams: float
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "name": self.name,
            "brand": self.brand,
            "serving_description": self.serving_description,
            "serving_grams": self.serving_grams,
            "calories": self.calories,
            "protein_g": self.protein_g,
            "carbs_g": self.carbs_g,
            "fat_g": self.fat_g,
            "fiber_g": self.fiber_g,
        }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_usda_nutrients(food: dict[str, Any]) -> dict[str, float]:
    nutrients: dict[str, float] = {
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": 0.0,
    }
    for nutrient in food.get("foodNutrients") or []:
        if not isinstance(nutrient, dict):
            continue
        nutrient_name = str(nutrient.get("nutrientName") or nutrient.get("name") or "").lower()
        nutrient_number = str(nutrient.get("nutrientNumber") or nutrient.get("number") or "")
        value = _num(nutrient.get("value") if "value" in nutrient else nutrient.get("amount"))
        if nutrient_number == "1008" or "energy" in nutrient_name and "kcal" in str(nutrient.get("unitName", "")).lower():
            nutrients["calories"] = value
        elif nutrient_number == "1003" or nutrient_name == "protein":
            nutrients["protein_g"] = value
        elif nutrient_number == "1005" or "carbohydrate, by difference" in nutrient_name or nutrient_name == "carbohydrate":
            nutrients["carbs_g"] = value
        elif nutrient_number == "1004" or "total lipid" in nutrient_name or nutrient_name == "fat":
            nutrients["fat_g"] = value
        elif nutrient_number == "1079" or "fiber" in nutrient_name:
            nutrients["fiber_g"] = value
    return nutrients


def search_usda_foods(query: str, api_key: str | None = None, page_size: int = 15) -> list[dict[str, Any]]:
    clean_query = query.strip()
    if len(clean_query) < 2:
        raise EliteServiceError("Enter at least two characters to search foods.")

    key = (api_key or os.getenv("FDC_API_KEY") or "DEMO_KEY").strip()
    payload = {
        "query": clean_query,
        "pageSize": max(1, min(int(page_size), 30)),
        "pageNumber": 1,
        "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"],
        "sortBy": "dataType.keyword",
        "sortOrder": "asc",
    }
    try:
        response = requests.post(
            FDC_SEARCH_URL,
            params={"api_key": key},
            json=payload,
            timeout=25,
        )
    except requests.RequestException as exc:
        raise EliteServiceError("USDA FoodData Central could not be reached.") from exc

    if response.status_code >= 400:
        detail = response.text[:300]
        raise EliteServiceError(f"USDA search failed: {detail}")

    try:
        data = response.json()
    except ValueError as exc:
        raise EliteServiceError("USDA returned an unreadable response.") from exc

    results: list[dict[str, Any]] = []
    for food in data.get("foods") or []:
        if not isinstance(food, dict):
            continue
        nutrients = _extract_usda_nutrients(food)
        serving_size = _num(food.get("servingSize"), 100.0)
        serving_unit = str(food.get("servingSizeUnit") or "g")
        is_per_100g = not food.get("servingSize")
        multiplier = serving_size / 100.0 if not is_per_100g and serving_unit.lower() in {"g", "gram", "grams"} else 1.0
        normalized = NormalizedFood(
            source="USDA FoodData Central",
            source_id=str(food.get("fdcId") or ""),
            name=str(food.get("description") or "Food").title(),
            brand=str(food.get("brandOwner") or food.get("brandName") or ""),
            serving_description=(
                f"{serving_size:g} {serving_unit}" if food.get("servingSize") else "100 g"
            ),
            serving_grams=serving_size if serving_unit.lower().startswith("g") else 100.0,
            calories=nutrients["calories"] * multiplier,
            protein_g=nutrients["protein_g"] * multiplier,
            carbs_g=nutrients["carbs_g"] * multiplier,
            fat_g=nutrients["fat_g"] * multiplier,
            fiber_g=nutrients["fiber_g"] * multiplier,
        )
        results.append(normalized.as_dict())
    return results


def transcribe_audio(audio_bytes: bytes, api_key: str, filename: str = "voice.wav") -> str:
    if not api_key.strip():
        raise EliteServiceError("An OpenAI API key is required for voice transcription.")
    if not audio_bytes:
        raise EliteServiceError("Record or upload an audio message first.")

    model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
    files = {"file": (filename, io.BytesIO(audio_bytes), "audio/wav")}
    data = {"model": model, "response_format": "json"}
    try:
        response = requests.post(
            OPENAI_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            files=files,
            data=data,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise EliteServiceError("The voice transcription service could not be reached.") from exc

    if response.status_code >= 400:
        raise EliteServiceError(f"Voice transcription failed: {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise EliteServiceError("The voice transcription response was unreadable.") from exc
    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise EliteServiceError("No speech was detected in the recording.")
    return transcript


def _extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    pieces: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                pieces.append(content["text"])
    return "\n".join(pieces).strip()


def parse_voice_command(transcript: str, api_key: str) -> dict[str, Any]:
    if not api_key.strip():
        raise EliteServiceError("An OpenAI API key is required to interpret voice commands.")
    clean = transcript.strip()
    if not clean:
        raise EliteServiceError("Enter or transcribe a command first.")

    schema = {
        "type": "object",
        "properties": {
            "command_type": {"type": "string", "enum": ["food", "workout", "water", "unknown"]},
            "food": {
                "type": "object",
                "properties": {
                    "meal": {"type": "string"},
                    "food_name": {"type": "string"},
                    "serving": {"type": "string"},
                    "calories": {"type": "number"},
                    "protein_g": {"type": "number"},
                    "carbs_g": {"type": "number"},
                    "fat_g": {"type": "number"},
                },
                "required": ["meal", "food_name", "serving", "calories", "protein_g", "carbs_g", "fat_g"],
                "additionalProperties": False,
            },
            "workout": {
                "type": "object",
                "properties": {
                    "workout_name": {"type": "string"},
                    "category": {"type": "string"},
                    "exercise_name": {"type": "string"},
                    "sets": {"type": "integer"},
                    "reps": {"type": "integer"},
                    "weight_lb": {"type": "number"},
                    "duration_min": {"type": "number"},
                    "distance_miles": {"type": "number"},
                },
                "required": ["workout_name", "category", "exercise_name", "sets", "reps", "weight_lb", "duration_min", "distance_miles"],
                "additionalProperties": False,
            },
            "water_oz": {"type": "number"},
            "notes": {"type": "string"},
        },
        "required": ["command_type", "food", "workout", "water_oz", "notes"],
        "additionalProperties": False,
    }
    body = {
        "model": os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6"),
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Convert this fitness logging command into structured data. Estimate food nutrition only when the user did not provide it. "
                            "Return zero values when a field does not apply. Keep food meal names to Breakfast, Lunch, Dinner, or Snack when possible. "
                            f"Command: {clean}"
                        ),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "nourivolt_voice_command",
                "strict": True,
                "schema": schema,
            }
        },
    }
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise EliteServiceError("The command interpretation service could not be reached.") from exc
    if response.status_code >= 400:
        raise EliteServiceError(f"Command interpretation failed: {response.text[:300]}")
    try:
        payload = response.json()
        text = _extract_response_text(payload)
        return json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise EliteServiceError("The command interpretation result was unreadable.") from exc


def generate_ai_coach_report(metrics: dict[str, Any], api_key: str) -> str:
    if not api_key.strip():
        raise EliteServiceError("An OpenAI API key is required for the AI coaching narrative.")
    body = {
        "model": os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6"),
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Write a concise weekly fitness coaching report based only on the supplied metrics. "
                            "Use four sections: Wins, Friction, Next Week, Training Match. Do not diagnose medical conditions. "
                            "Never change targets automatically. Make practical recommendations and state uncertainty when data is sparse.\n\n"
                            + json.dumps(metrics, default=str)
                        ),
                    }
                ],
            }
        ],
    }
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise EliteServiceError("The coaching service could not be reached.") from exc
    if response.status_code >= 400:
        raise EliteServiceError(f"AI coaching failed: {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise EliteServiceError("The coaching response was unreadable.") from exc
    text = _extract_response_text(payload)
    if not text:
        raise EliteServiceError("The coaching response was empty.")
    return text


def analyze_nutrition_label(image_bytes: bytes, api_key: str) -> dict[str, Any]:
    if not api_key.strip():
        raise EliteServiceError("An OpenAI API key is required for nutrition-label scanning.")
    if not image_bytes:
        raise EliteServiceError("Take or upload a nutrition-label photo first.")
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise EliteServiceError("The nutrition-label image could not be opened.") from exc
    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    image_url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    schema = {
        "type": "object",
        "properties": {
            "product_name": {"type": "string"},
            "serving_size": {"type": "string"},
            "servings_per_container": {"type": "number"},
            "calories": {"type": "number"},
            "protein_g": {"type": "number"},
            "carbs_g": {"type": "number"},
            "fat_g": {"type": "number"},
            "fiber_g": {"type": "number"},
            "sugar_g": {"type": "number"},
            "sodium_mg": {"type": "number"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["product_name", "serving_size", "servings_per_container", "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg", "confidence"],
        "additionalProperties": False,
    }
    body = {
        "model": os.getenv("OPENAI_VISION_MODEL", "gpt-5.6"),
        "store": False,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Read the visible Nutrition Facts label. Return the values per labeled serving. Use zero only when a value is absent or unreadable. Do not invent a brand or product name."},
                {"type": "input_image", "image_url": image_url, "detail": "high"},
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "nutrition_label", "strict": True, "schema": schema}},
    }
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise EliteServiceError("The nutrition-label service could not be reached.") from exc
    if response.status_code >= 400:
        raise EliteServiceError(f"Nutrition-label scan failed: {response.text[:300]}")
    try:
        payload = response.json()
        return json.loads(_extract_response_text(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise EliteServiceError("The nutrition-label result was unreadable.") from exc


LOCAL_FOOD_CATALOG: list[dict[str, Any]] = [
    {"name": "Grilled chicken breast", "serving": "4 oz", "calories": 187, "protein_g": 35, "carbs_g": 0, "fat_g": 4, "fiber_g": 0, "tags": ["high protein", "low carb"]},
    {"name": "Nonfat Greek yogurt", "serving": "1 cup", "calories": 130, "protein_g": 23, "carbs_g": 9, "fat_g": 0, "fiber_g": 0, "tags": ["high protein", "snack"]},
    {"name": "Cottage cheese", "serving": "1 cup", "calories": 206, "protein_g": 28, "carbs_g": 8, "fat_g": 9, "fiber_g": 0, "tags": ["high protein", "snack"]},
    {"name": "Tuna in water", "serving": "1 can", "calories": 120, "protein_g": 26, "carbs_g": 0, "fat_g": 1, "fiber_g": 0, "tags": ["high protein", "low carb"]},
    {"name": "Large egg", "serving": "1 egg", "calories": 72, "protein_g": 6, "carbs_g": 0.4, "fat_g": 5, "fiber_g": 0, "tags": ["protein", "breakfast"]},
    {"name": "Cooked brown rice", "serving": "1 cup", "calories": 216, "protein_g": 5, "carbs_g": 45, "fat_g": 2, "fiber_g": 3.5, "tags": ["carbs", "fiber"]},
    {"name": "Cooked oatmeal", "serving": "1 cup", "calories": 166, "protein_g": 6, "carbs_g": 28, "fat_g": 4, "fiber_g": 4, "tags": ["carbs", "fiber", "breakfast"]},
    {"name": "Banana", "serving": "1 medium", "calories": 105, "protein_g": 1.3, "carbs_g": 27, "fat_g": 0.4, "fiber_g": 3.1, "tags": ["carbs", "fruit"]},
    {"name": "Apple", "serving": "1 medium", "calories": 95, "protein_g": 0.5, "carbs_g": 25, "fat_g": 0.3, "fiber_g": 4.4, "tags": ["carbs", "fruit", "fiber"]},
    {"name": "Avocado", "serving": "1/2 fruit", "calories": 120, "protein_g": 1.5, "carbs_g": 6, "fat_g": 11, "fiber_g": 5, "tags": ["healthy fat", "fiber"]},
    {"name": "Almonds", "serving": "1 oz", "calories": 164, "protein_g": 6, "carbs_g": 6, "fat_g": 14, "fiber_g": 3.5, "tags": ["healthy fat", "snack"]},
    {"name": "Black beans", "serving": "1 cup", "calories": 227, "protein_g": 15, "carbs_g": 41, "fat_g": 0.9, "fiber_g": 15, "tags": ["fiber", "plant protein"]},
    {"name": "Broccoli", "serving": "1 cup", "calories": 55, "protein_g": 3.7, "carbs_g": 11, "fat_g": 0.6, "fiber_g": 5.1, "tags": ["vegetable", "fiber"]},
    {"name": "Sweet potato", "serving": "1 medium", "calories": 112, "protein_g": 2, "carbs_g": 26, "fat_g": 0.1, "fiber_g": 4, "tags": ["carbs", "fiber"]},
    {"name": "Whey protein shake", "serving": "1 scoop with water", "calories": 130, "protein_g": 25, "carbs_g": 4, "fat_g": 2, "fiber_g": 1, "tags": ["high protein", "quick"]},
]


MEAL_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Protein oats and berries",
        "meal": "Breakfast",
        "calories": 430,
        "protein_g": 31,
        "carbs_g": 56,
        "fat_g": 10,
        "fiber_g": 9,
        "estimated_cost": 3.75,
        "ingredients": ["rolled oats", "Greek yogurt", "berries", "chia seeds", "milk"],
        "tags": ["high protein", "vegetarian"],
    },
    {
        "name": "Egg and avocado breakfast wrap",
        "meal": "Breakfast",
        "calories": 470,
        "protein_g": 25,
        "carbs_g": 41,
        "fat_g": 23,
        "fiber_g": 9,
        "estimated_cost": 4.25,
        "ingredients": ["eggs", "whole wheat tortilla", "avocado", "spinach", "salsa"],
        "tags": ["balanced"],
    },
    {
        "name": "Chicken rice power bowl",
        "meal": "Lunch",
        "calories": 620,
        "protein_g": 48,
        "carbs_g": 72,
        "fat_g": 16,
        "fiber_g": 10,
        "estimated_cost": 6.25,
        "ingredients": ["chicken breast", "brown rice", "black beans", "broccoli", "salsa"],
        "tags": ["high protein", "meal prep"]
    },
    {
        "name": "Tuna crunch wrap",
        "meal": "Lunch",
        "calories": 450,
        "protein_g": 38,
        "carbs_g": 44,
        "fat_g": 14,
        "fiber_g": 7,
        "estimated_cost": 4.75,
        "ingredients": ["tuna", "whole wheat tortilla", "lettuce", "tomato", "Greek yogurt"],
        "tags": ["high protein", "quick"]
    },
    {
        "name": "Turkey chili",
        "meal": "Dinner",
        "calories": 560,
        "protein_g": 45,
        "carbs_g": 58,
        "fat_g": 17,
        "fiber_g": 16,
        "estimated_cost": 5.50,
        "ingredients": ["lean ground turkey", "kidney beans", "black beans", "tomatoes", "onion", "chili seasoning"],
        "tags": ["high protein", "fiber", "meal prep"]
    },
    {
        "name": "Salmon sweet potato plate",
        "meal": "Dinner",
        "calories": 650,
        "protein_g": 44,
        "carbs_g": 52,
        "fat_g": 29,
        "fiber_g": 10,
        "estimated_cost": 8.50,
        "ingredients": ["salmon", "sweet potato", "green beans", "olive oil", "lemon"],
        "tags": ["omega-3", "balanced"]
    },
    {
        "name": "Greek yogurt fruit bowl",
        "meal": "Snack",
        "calories": 260,
        "protein_g": 24,
        "carbs_g": 32,
        "fat_g": 4,
        "fiber_g": 6,
        "estimated_cost": 3.00,
        "ingredients": ["Greek yogurt", "banana", "berries", "granola"],
        "tags": ["high protein", "vegetarian"]
    },
    {
        "name": "Apple almond snack",
        "meal": "Snack",
        "calories": 260,
        "protein_g": 7,
        "carbs_g": 31,
        "fat_g": 14,
        "fiber_g": 8,
        "estimated_cost": 2.25,
        "ingredients": ["apple", "almonds"],
        "tags": ["fiber", "vegetarian"]
    },
]


def local_food_search(query: str) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if not needle:
        return LOCAL_FOOD_CATALOG.copy()
    return [
        item.copy()
        for item in LOCAL_FOOD_CATALOG
        if needle in item["name"].lower() or any(needle in tag for tag in item.get("tags", []))
    ]


def fuel_gap_suggestions(
    protein_gap: float,
    carb_gap: float,
    fat_gap: float,
    calorie_gap: float,
    exclusions: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    excluded = {term.strip().lower() for term in exclusions or [] if term.strip()}
    candidates: list[tuple[float, dict[str, Any]]] = []
    p_target = max(protein_gap, 1.0)
    c_target = max(carb_gap, 1.0)
    f_target = max(fat_gap, 1.0)
    kcal_target = max(calorie_gap, 1.0)
    for item in LOCAL_FOOD_CATALOG:
        haystack = f"{item['name']} {' '.join(item.get('tags', []))}".lower()
        if any(term in haystack for term in excluded):
            continue
        p_fit = abs(item["protein_g"] - min(p_target, 45)) / max(p_target, 15)
        c_fit = abs(item["carbs_g"] - min(c_target, 60)) / max(c_target, 20)
        f_fit = abs(item["fat_g"] - min(f_target, 20)) / max(f_target, 8)
        kcal_fit = abs(item["calories"] - min(kcal_target, 650)) / max(kcal_target, 250)
        score = p_fit * 0.38 + c_fit * 0.22 + f_fit * 0.16 + kcal_fit * 0.24
        candidates.append((score, item.copy()))
    candidates.sort(key=lambda row: row[0])
    return [item for _, item in candidates[:limit]]

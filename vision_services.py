from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image, ImageEnhance, ImageOps

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPEN_FOOD_FACTS_V3 = "https://world.openfoodfacts.org/api/v3.6/product/{barcode}.json"
OPEN_FOOD_FACTS_V2 = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
OPEN_FOOD_FACTS_USER_AGENT = os.getenv(
    "OPEN_FOOD_FACTS_USER_AGENT",
    "NouriVanta/3.0 (nutrition-app@example.com)",
)


class VisionServiceError(RuntimeError):
    """Raised when a camera, AI, or product-data operation fails."""


@dataclass(slots=True)
class BarcodeResult:
    text: str
    format_name: str


def _compressed_image(image_bytes: bytes, max_dimension: int = 1600) -> tuple[bytes, str]:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise VisionServiceError("The image could not be opened. Use a clear JPG, PNG, or WEBP image.") from exc

    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue(), "image/jpeg"


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    pieces: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                pieces.append(text)
    return "\n".join(pieces).strip()


def analyze_food_image(
    image_bytes: bytes,
    api_key: str,
    meal_context: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    if not api_key.strip():
        raise VisionServiceError("Enter an OpenAI API key before analyzing a food photo.")

    compressed, mime_type = _compressed_image(image_bytes)
    image_url = f"data:{mime_type};base64,{base64.b64encode(compressed).decode('ascii')}"
    model_name = model or os.getenv("OPENAI_VISION_MODEL", "gpt-5.6")

    schema = {
        "type": "object",
        "properties": {
            "dish_name": {"type": "string"},
            "serving_description": {"type": "string"},
            "calories": {"type": "number", "minimum": 0},
            "protein_g": {"type": "number", "minimum": 0},
            "carbs_g": {"type": "number", "minimum": 0},
            "fat_g": {"type": "number", "minimum": 0},
            "fiber_g": {"type": "number", "minimum": 0},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "ingredients": {
                "type": "array",
                "items": {"type": "string"},
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "dish_name",
            "serving_description",
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "fiber_g",
            "confidence",
            "ingredients",
            "assumptions",
        ],
        "additionalProperties": False,
    }

    context_text = meal_context.strip() or "No extra serving or ingredient information was supplied."
    instructions = (
        "Analyze the visible edible food and estimate nutrition for the entire visible portion. "
        "Return a practical logging estimate, not a range. Use visual portion clues and common recipes. "
        "If preparation method, oil, sauces, or portion size are uncertain, make conservative assumptions and list them. "
        "Do not identify people, provide medical advice, or claim laboratory accuracy. "
        f"User context: {context_text}"
    )

    body = {
        "model": model_name,
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instructions},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "food_macro_estimate",
                "strict": True,
                "schema": schema,
            }
        },
    }

    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise VisionServiceError("The AI nutrition service could not be reached. Check your internet connection.") from exc

    if response.status_code >= 400:
        try:
            error_payload = response.json()
            message = error_payload.get("error", {}).get("message") or response.text
        except Exception:
            message = response.text
        raise VisionServiceError(f"Food analysis failed: {message[:300]}")

    payload = response.json()
    output_text = _extract_response_text(payload)
    if not output_text:
        raise VisionServiceError("The AI response did not contain a nutrition estimate.")

    try:
        result = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise VisionServiceError("The AI response was not valid nutrition data. Try the photo again.") from exc

    return result


def decode_barcode(image_bytes: bytes) -> BarcodeResult:
    try:
        import zxingcpp
    except ImportError as exc:
        raise VisionServiceError("Barcode support is not installed. Run pip install -r requirements.txt.") from exc

    try:
        original = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise VisionServiceError("The barcode image could not be opened.") from exc

    attempts: list[Image.Image] = [original]
    grayscale = ImageOps.grayscale(original)
    attempts.append(grayscale)
    attempts.append(ImageEnhance.Contrast(grayscale).enhance(2.0))
    attempts.append(ImageEnhance.Sharpness(grayscale).enhance(2.0))

    for image in attempts:
        try:
            results = zxingcpp.read_barcodes(image)
        except Exception:
            continue
        for result in results:
            text = str(getattr(result, "text", "")).strip()
            if text:
                format_name = str(getattr(result, "format", "Barcode"))
                return BarcodeResult(text=text, format_name=format_name)

    raise VisionServiceError(
        "No barcode was detected. Fill the frame with the barcode, keep it flat, and avoid glare. You may also type the number manually."
    )


def _first_number(mapping: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def _parse_grams(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*g\b", text.lower())
    return float(match.group(1)) if match else None


def _normalize_product(barcode: str, product: dict[str, Any]) -> dict[str, Any]:
    nutriments = product.get("nutriments") or {}
    serving_size = str(product.get("serving_size") or "").strip()
    serving_grams = _parse_grams(serving_size)

    per_serving = {
        "calories": _first_number(nutriments, ["energy-kcal_serving", "energy-kcal_value"]),
        "protein_g": _first_number(nutriments, ["proteins_serving", "proteins_value"]),
        "carbs_g": _first_number(nutriments, ["carbohydrates_serving", "carbohydrates_value"]),
        "fat_g": _first_number(nutriments, ["fat_serving", "fat_value"]),
        "fiber_g": _first_number(nutriments, ["fiber_serving", "fiber_value"]),
    }
    per_100g = {
        "calories": _first_number(nutriments, ["energy-kcal_100g", "energy-kcal"]),
        "protein_g": _first_number(nutriments, ["proteins_100g", "proteins"]),
        "carbs_g": _first_number(nutriments, ["carbohydrates_100g", "carbohydrates"]),
        "fat_g": _first_number(nutriments, ["fat_100g", "fat"]),
        "fiber_g": _first_number(nutriments, ["fiber_100g", "fiber"]),
    }

    if serving_grams and not any(value is not None for value in per_serving.values()):
        per_serving = {
            key: (value * serving_grams / 100 if value is not None else None)
            for key, value in per_100g.items()
        }

    return {
        "barcode": barcode,
        "product_name": product.get("product_name") or product.get("product_name_en") or "Unknown product",
        "brand": product.get("brands") or "",
        "serving_size": serving_size or (f"{serving_grams:g} g" if serving_grams else "1 serving"),
        "serving_grams": serving_grams,
        "image_url": product.get("image_front_small_url") or product.get("image_front_url") or product.get("image_url"),
        "nutrition_grade": str(product.get("nutrition_grades") or product.get("nutriscore_grade") or "").upper(),
        "nova_group": product.get("nova_group"),
        "ingredients_text": product.get("ingredients_text_en") or product.get("ingredients_text") or "",
        "allergens": product.get("allergens") or "",
        "per_serving": per_serving,
        "per_100g": per_100g,
    }


def lookup_open_food_facts(barcode: str) -> dict[str, Any]:
    normalized = re.sub(r"\D", "", barcode)
    if len(normalized) < 6:
        raise VisionServiceError("Enter a valid UPC or EAN barcode number.")

    fields = ",".join(
        [
            "code",
            "product_name",
            "product_name_en",
            "brands",
            "serving_size",
            "nutriments",
            "nutrition_grades",
            "nutriscore_grade",
            "nova_group",
            "ingredients_text",
            "ingredients_text_en",
            "allergens",
            "image_front_small_url",
            "image_front_url",
            "image_url",
        ]
    )
    headers = {"User-Agent": OPEN_FOOD_FACTS_USER_AGENT}

    urls = [
        OPEN_FOOD_FACTS_V3.format(barcode=normalized),
        OPEN_FOOD_FACTS_V2.format(barcode=normalized),
    ]
    last_error = ""
    for url in urls:
        try:
            response = requests.get(url, params={"fields": fields}, headers=headers, timeout=25)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if response.status_code == 404:
            continue
        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code}"
            continue
        try:
            data = response.json()
        except ValueError:
            last_error = "invalid response"
            continue
        product = data.get("product") if isinstance(data, dict) else None
        status = data.get("status") if isinstance(data, dict) else None
        if isinstance(product, dict) and product and status != 0:
            return _normalize_product(normalized, product)

    if last_error:
        raise VisionServiceError(f"The product database could not complete the lookup: {last_error}")
    raise VisionServiceError("This barcode was not found in Open Food Facts. You may enter the nutrition manually.")

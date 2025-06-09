# Updated data_pipeline.py using GPT-4 Vision as fallback for OCR

import base64
import os
import re
import json
import logging
from io import BytesIO
from typing import Dict, Any, Optional, List

import numpy as np
import httpx
import requests
from PIL import Image
from difflib import SequenceMatcher
from dotenv import load_dotenv
import openai

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SWAGGER_API_BASE_URL = os.getenv("SWAGGER_API_BASE_URL")
AUTH_EMAIL = os.getenv("AUTH_EMAIL")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def authenticate() -> Optional[str]:
    url = f"{SWAGGER_API_BASE_URL}/login"
    payload = {
        "email": AUTH_EMAIL,
        "password": AUTH_PASSWORD,
        "deviceId": "1",
        "deviceType": "1"
    }
    headers = {
        "Content-Type": "application/json",
        "Access": "1"  
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json().get("token")
    except requests.RequestException as e:
        logger.error("Auth failed", exc_info=e)
        return None


def fetch_report_data(report_id: str, token: str) -> Dict[str, Any]:
    url = f"{SWAGGER_API_BASE_URL}/request/detail_by_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Access": "1",
        "deviceType": "1",
        "Version": "1"
    }
    payload = {"id": int(report_id)}

    logger.info(f"Fetching report data for ID: {report_id}")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=5)
        resp.raise_for_status()
        report_data = resp.json()
        if isinstance(report_data, dict):
            if "data" in report_data:
                return report_data["data"]
            if "payload" in report_data:
                return report_data["payload"]
        return report_data
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to fetch report {report_id}", exc_info=e)
        return {}



def fuzzy_match(a: str, b: str) -> int:
    return round(SequenceMatcher(None, a, b).ratio() * 10)


def perform_ocr_gpt_vision(image: Image.Image, label: str) -> str:
    try:
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        b64_img = base64.b64encode(buffered.getvalue()).decode("utf-8")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Extract the {label} number from this image. Return only the number."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}
                ]
            }
        ]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=50,
        )

        return re.sub(r"[^A-Z0-9\-]", "", response.choices[0].message.content.strip().upper())

    except Exception as e:
        logger.warning(f"GPT Vision OCR failed for {label}", exc_info=e)
        return ""

def perform_ocr_on_image(image_url: Optional[str], token: str, label: str = "chassis") -> Dict[str, Any]:
    if not image_url:
        logger.warning(f"No {label} image URL provided.")
        return {"text": "", "confidence": 0}
    try:
        logger.info(f"Fetching {label} image from: {image_url}")
        #headers = {"Authorization": f"Bearer {token}"}
        resp = httpx.get(image_url, timeout=10)
        if resp.status_code != 200:
            logger.error(f"❌ {label.title()} image fetch failed with {resp.status_code}: {image_url}")
            return {"text": "", "confidence": 0}
        img = Image.open(BytesIO(resp.content))

        text = perform_ocr_gpt_vision(img, label)
        score = 10 if text else 0

        logger.info(f"{label.title()} OCR - GPT Vision with confidence {score}/10")
        return {"text": text, "confidence": score}
    except Exception as e:
        logger.error(f"OCR failed for {label} image: {image_url}", exc_info=e)
        return {"text": "", "confidence": 0}


def try_multiple_ocr_sources(images: List[Optional[str]], token: str, expected: str, label: str) -> tuple[str, int]:
    """
    Tries multiple image URLs for OCR and picks the result with the best fuzzy match against the expected value.
    """
    best_score, best_text = 0, ""
    for img_url in filter(None, images):
        res = perform_ocr_on_image(img_url, token, label)
        score = fuzzy_match(expected, res['text'])
        if score > best_score:
            best_text, best_score = res['text'], score
    logger.info(f"Best OCR match for {label}: {best_text} with score {best_score}/10")
    return best_text, best_score


def decode_vin(vin: str) -> Dict[str, Any]:
    if not vin:
        logger.warning("No VIN provided for decoding.")
        return {}

    try:
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
        r = requests.get(url)
        r.raise_for_status()
        d = r.json().get("Results", [])

        return {
            "make": next((i["Value"] for i in d if i["Variable"] == "Make"), None),
            "model": next((i["Value"] for i in d if i["Variable"] == "Model"), None),
            "year": next((i["Value"] for i in d if i["Variable"] == "Model Year"), None),
        }

    except Exception as e:
        logger.error(f"VIN decode failed for: {vin}", exc_info=e)
        return {}

def get_vehicle_data(report_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    token = token or authenticate()
    report = fetch_report_data(report_id, token)
    if not report:
        return {}

    ch_urls = [report.get(k) for k in ["f_chasis_no_img", "chassis_number_on_frame_img", "log_book_img"]]
    en_urls = [report.get(k) for k in ["engine_img", "log_book_img"]]

    ch_expected = report.get("chassis_no", "")
    en_expected = report.get("engine_no", "")

    ch_text, ch_conf = try_multiple_ocr_sources(ch_urls, token, ch_expected, "chassis")
    en_text, en_conf = try_multiple_ocr_sources(en_urls, token, en_expected, "engine")

    vin_info = decode_vin(report.get("vin", ""))

    return {
        "vehicle_make": report.get("make", ""),
        "vehicle_model": report.get("model", ""),
        "vin": report.get("vin", ""),
        "chassis_number_logbook": ch_expected,
        "engine_number_logbook": en_expected,
        "chassis_number_ocr": ch_text,
        "engine_number_ocr": en_text,
        "image_quality_notes": f"Chassis confidence: {ch_conf}/10, Engine confidence: {en_conf}/10",
        "production_year": vin_info.get("year", "") or report.get("year", ""),
        "decoded_features": [f"{k}: {v}" for k, v in vin_info.items() if v],
        "known_issues": report.get("comments", ""),
        "parts_availability": report.get("parts_availability", ""),
        "mileage": str(report.get("odo_reading", "")),
        "condition_notes": report.get("comments", ""),
        "market_data": report.get("market_data", []),
    }

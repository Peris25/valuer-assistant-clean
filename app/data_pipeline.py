# app/data_pipeline.py

import re
import easyocr
import numpy as np
from difflib import SequenceMatcher
import json
import os
import logging
from typing import Dict, Any, Optional, List
from io import BytesIO

import httpx
import requests
from PIL import Image
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logger setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Env vars
SWAGGER_API_BASE_URL = os.getenv("SWAGGER_API_BASE_URL")
AUTH_EMAIL = os.getenv("AUTH_EMAIL")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# EasyOCR Reader
reader = easyocr.Reader(['en'], gpu=False)

def authenticate() -> Optional[str]:
    login_url = f"{SWAGGER_API_BASE_URL}/login"
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
        resp = requests.post(login_url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            logger.error("❌ Login successful but token missing.")
        return token
    except requests.exceptions.RequestException as e:
        logger.error("❌ Failed to authenticate with Swagger API", exc_info=e)
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

def fuzzy_match(target: str, ocr_text: str) -> int:
    if not target or not ocr_text:
        return 0
    score = SequenceMatcher(None, target, ocr_text).ratio()
    return round(score * 10)

def perform_ocr_on_image(image_url: Optional[str], token: Optional[str] = None) -> Dict[str, Any]:
    if not image_url or "not-available.jpg" in image_url.lower():
        logger.warning(f"[OCR] Skipping default or empty image: {image_url}")
        return {"text": "", "confidence": 0}

    try:
        logger.info(f"[OCR] Fetching image from: {image_url}")
        headers = {"User-Agent": "Mozilla/5.0"}
        response = httpx.get(image_url, headers=headers, timeout=10)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content)).convert("RGB")
        img_np = np.array(img)
        results = reader.readtext(img_np)

        ocr_text = "".join([res[1] for res in results]).upper().replace(" ", "")
        cleaned = re.sub(r"[^A-Z0-9\-]", "", ocr_text)

        logger.info(f"[EasyOCR] ✅ Text: {cleaned or '[EMPTY]'}")
        return {"text": cleaned, "confidence": 0}

    except Exception as e:
        logger.error(f"[EasyOCR] ❌ Failed: {e}", exc_info=True)
        return {"text": "", "confidence": 0}

def decode_vin(vin: str) -> Dict[str, Any]:
    if not vin:
        return {}
    try:
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        results = resp.json().get("Results", [])
        return {
            "make": next((i["Value"] for i in results if i["Variable"] == "Make"), None),
            "model": next((i["Value"] for i in results if i["Variable"] == "Model"), None),
            "year": next((i["Value"] for i in results if i["Variable"] == "Model Year"), None),
            "body_class": next((i["Value"] for i in results if i["Variable"] == "Body Class"), None),
        }
    except requests.RequestException as e:
        logger.warning(f"⚠️ VIN decoding failed for {vin}. Proceeding without VIN details.")
        return {}

def try_multiple_ocr_sources(candidates: List[Optional[str]], token: str, expected: str = "") -> tuple[str, int]:
    best_score = 0
    best_match = ""

    for url in filter(None, candidates):
        result = perform_ocr_on_image(url, token)
        score = fuzzy_match(expected, result["text"])
        logger.info(f"[Match Score] {result['text']} vs {expected} → {score}/10")

        if score > best_score:
            best_score = score
            best_match = result["text"]

        if score == 10:
            break

    return best_match, best_score

def get_vehicle_data(report_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    token = token or authenticate()
    if not token:
        logger.error("❌ Authentication failed. No token.")
        return {}

    report = fetch_report_data(report_id, token)
    if not report:
        return {}

    chassis_candidates = [
        report.get("f_chasis_no_img"),
        report.get("chasis_no_img"),
        report.get("chassis_number_on_frame_img"),
        report.get("f_chassis_number_on_frame_img"),
        report.get("log_book_img"),
    ]
    expected_chassis = report.get("chassis_no", "")
    chassis_text, chassis_conf = try_multiple_ocr_sources(chassis_candidates, token, expected_chassis)

    engine_candidates = [
        report.get("f_engine_img"),
        report.get("engine_img"),
        report.get("log_book_img"),
    ]
    expected_engine = report.get("engine_no", "")
    engine_text, engine_conf = try_multiple_ocr_sources(engine_candidates, token, expected_engine)

    vin_info = decode_vin(report.get("vin", ""))

    market_data_flat = []
    market_raw = report.get("market_data", [])
    if isinstance(market_raw, list):
        for item in market_raw:
            if isinstance(item, dict):
                market_data_flat.append(", ".join(f"{k}: {v}" for k, v in item.items()))
            else:
                market_data_flat.append(str(item))
    if report.get("force_sales_value") and str(report["force_sales_value"]).strip() != "0":
        market_data_flat.append(f"Reported Force Sales Value in report: {report['force_sales_value']} KES")

    result = {
        "vehicle_make": report.get("make", ""),
        "vehicle_model": report.get("model", ""),
        "vin": report.get("vin") or report.get("vin_no") or "",
        "chassis_number_logbook": report.get("chassis_no", ""),
        "engine_number_logbook": report.get("engine_no", ""),
        "chassis_number_ocr": chassis_text,
        "engine_number_ocr": engine_text,
        "image_quality_notes": f"OCR confidence score - Chassis: {chassis_conf}/10, Engine: {engine_conf}/10",
        "production_year": vin_info.get("year") or report.get("year", ""),
        "decoded_features": [f"{k}: {v}" for k, v in vin_info.items() if v],
        "known_issues": report.get("known_issues", "") or report.get("mechanical_comments", "") or report.get("comments", ""),
        "parts_availability": report.get("parts_availability", ""),
        "mileage": str(report.get("odo_reading", "")),
        "condition_notes": report.get("comments", "") or report.get("mechanical_comments", ""),
        "market_data": market_data_flat,
    }

    logger.info("Final vehicle data being passed to GPT:\n%s", json.dumps(result, indent=2))
    return result

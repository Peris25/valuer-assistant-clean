# app/data_pipeline.py

import json
import os
import logging
from typing import Dict, Any, Optional, List
from io import BytesIO

import requests
from PIL import Image
import pytesseract
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


def authenticate() -> Optional[str]:
    """
    Authenticate via the /insurance-login endpoint and return the token.
    """
    login_url = f"{SWAGGER_API_BASE_URL}/insurance-login"
    try:
        resp = requests.post(login_url, json={"email": AUTH_EMAIL, "password": AUTH_PASSWORD}, timeout=5)
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            logger.error("❌ Login successful but token missing.")
        return token
    except requests.exceptions.RequestException as e:
        logger.error("❌ Failed to authenticate with Swagger API", exc_info=e)
        return None


def fetch_report_data(report_id: str, token: str) -> Dict[str, Any]:
    """
    Call POST /api/request/detail with report_id and return the data payload.
    """
    url = f"{SWAGGER_API_BASE_URL}/request/detail?id={report_id}"
    headers = {"Authorization": f"{token}"}
    logger.info(f"Fetching report data for ID: {report_id}")
    logger.info(f"Request URL: {url}")
    #logger.info(f"Request Headers: {headers}")
    try:
        resp = requests.post(url, headers=headers, timeout=5)
        resp.raise_for_status()

        #visibility of the raw response
        report_data = resp.json()
        #print("🔍 Raw API report_data:\n", json.dumps(report_data, indent=2))

        # Detect if the actual useful data is nested inside a key like 'data' or 'payload'
        if isinstance(report_data, dict):
            # Example checks, adjust according to actual API response shape
            if "data" in report_data:
                return report_data["data"]
            if "payload" in report_data:
                return report_data["payload"]

        return report_data
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Failed to fetch report {report_id}", exc_info=e)
        return {}


def perform_ocr_on_image(image_url: Optional[str], token: Optional[str] = None) -> str:
    if not image_url:
        return ""
    try:
        print(f"[OCR] Fetching image from URL: {image_url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        if token:
            headers["Authorization"] = f"{token}"

        response = requests.get(image_url, headers=headers, timeout=5)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        text = pytesseract.image_to_string(img)
        cleaned = " ".join(text.split())
        print(f"[OCR] Extracted text: {cleaned[:200]}...")

        return cleaned.strip()
    except Exception as e:
        logger.warning(f"⚠️ OCR failed for image: {image_url}", exc_info=e)
        return ""


def decode_vin(vin: str) -> Dict[str, Any]:
    if not vin:
        return {"production_year": None, "decoded_features": []}
    try:
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/{vin}?format=json"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        results = resp.json().get("Results", [])
        model_year = None
        features = []
        for item in results:
            var = item.get("Variable")
            val = item.get("Value")
            if var == "Model Year":
                model_year = val
            elif val:
                features.append(f"{var}: {val}")
        return {"production_year": model_year, "decoded_features": features}
    except requests.exceptions.RequestException:
        return {"production_year": None, "decoded_features": []}


def get_vehicle_data(report_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    token = token or authenticate()
    if not token:
        logger.error("❌ Authentication failed. No token obtained.")
        return {}

    report = fetch_report_data(report_id, token)
    #logger.info(f"Fetched report data for report_id={report_id}: {report}")
    if not report:
        logger.error("❌ Failed to fetch report data for report_id=%s", report_id)
        return {}

    if not report.get("vin"):
        logger.warning(f"⚠️ VIN missing from report: {report_id}")

    # OCR fields
    chassis_ocr = perform_ocr_on_image(report.get("logbook_chassis_url"), token)
    engine_ocr = perform_ocr_on_image(report.get("logbook_engine_url"), token)
    logger.info(f"Chassis OCR: {chassis_ocr}")
    logger.info(f"Engine OCR: {engine_ocr}")
    if not chassis_ocr and not engine_ocr:
        logger.warning("⚠️ OCR failed for both chassis and engine images.")

    # VIN decoding
    vin_info = decode_vin(report.get("vin", ""))

    # Market data
    market_data_raw = report.get("market_data", [])
    market_data_flat = []
    if isinstance(market_data_raw, list):
        for item in market_data_raw:
            if isinstance(item, dict):
                line = ", ".join(f"{k}: {v}" for k, v in item.items())
                market_data_flat.append(line)
            else:
                market_data_flat.append(str(item))

    # Smart image quality notes
    if not chassis_ocr and not engine_ocr:
        image_quality_notes = "OCR failed for all chassis and engine images, suggesting poor visibility and clarity. Confidence score: 0/10."
    else:
        image_quality_notes = "OCR successful on some images. Confidence score: 7/10."

    result = {
        "vehicle_make": report.get("make", ""),
        "vehicle_model": report.get("model", ""),
        "vin": report.get("vin") or report.get("vin_no") or "",
        "chassis_number_logbook": report.get("chassis_no", ""),
        "engine_number_logbook": report.get("engine_no", ""),
        "chassis_number_ocr": chassis_ocr,
        "engine_number_ocr": engine_ocr,
        "image_quality_notes": image_quality_notes,
        "production_year": vin_info.get("production_year"),
        "decoded_features": vin_info.get("decoded_features"),
        "known_issues": report.get("known_issues") or report.get("mechanical_comments") or report.get("comments", ""),
        "parts_availability": report.get("parts_availability", "") or "Unknown; not provided in report.",
        "mileage": str(report.get("odo_reading", "")),
        "condition_notes": report.get("comments", "") or report.get("mechanical_comments", ""),
        "market_data": market_data_flat,
    }

    logger.debug("Final vehicle data being passed to GPT:\n%s", json.dumps(result, indent=2))
    return result
# Updated data_pipeline.py using GPT-4 Vision as fallback for OCR

import base64
import os
import re
import logging
from io import BytesIO
from typing import Dict, Any, Optional, List

import httpx
import requests
from PIL import Image
from difflib import SequenceMatcher
from dotenv import load_dotenv
import openai
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from functools import lru_cache

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SWAGGER_API_BASE_URL = os.getenv("SWAGGER_API_BASE_URL")
AUTH_EMAIL = os.getenv("AUTH_EMAIL")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_fx_cache = {"rates": None, "timestamp": None}

#load sources
GRAPH_METHOD_PATH = "/mnt/data/graph_method.xlsx"
ECONOMIC_LIFE_PATH = "/mnt/data/economic_life_chart.xlsx"

try:
    graph_df = pd.read_excel(GRAPH_METHOD_PATH)
    econ_df = pd.read_excel(ECONOMIC_LIFE_PATH)

    # --- Ensure correct numeric dtypes and drop rows with missing critical data ---
    # Standardize column names (in case Excel had extra spaces)
    graph_df.columns = [c.strip() for c in graph_df.columns]
    econ_df.columns = [c.strip() for c in econ_df.columns]

    # Coerce expected numeric columns to numeric types
    for col in ["Mileage", "MileageDepreciation", "AgeMonths", "AgeDepreciation"]:
        if col in graph_df.columns:
            graph_df[col] = pd.to_numeric(graph_df[col], errors="coerce")

    for col in ["AgeYears", "DepreciationPercent"]:
        if col in econ_df.columns:
            econ_df[col] = pd.to_numeric(econ_df[col], errors="coerce")

    # Drop rows that do not have the required numeric values
    if "Mileage" in graph_df.columns and "AgeMonths" in graph_df.columns:
        graph_df = graph_df.dropna(subset=["Mileage", "AgeMonths", "MileageDepreciation", "AgeDepreciation"]).reset_index(drop=True)

    if "AgeYears" in econ_df.columns:
        econ_df = econ_df.dropna(subset=["AgeYears", "DepreciationPercent"]).reset_index(drop=True)

    # Sort for predictable nearest matching (optional but helpful)
    graph_df = graph_df.sort_values(["AgeMonths", "Mileage"]).reset_index(drop=True)
    econ_df = econ_df.sort_values("AgeYears").reset_index(drop=True)

    logger.info("👌 Depreciation tables loaded and normalized.")
except Exception as e:
    logger.warning("⚠️ Depreciation tables could not be loaded", exc_info=e)
    graph_df, econ_df = None, None


IMPORT_SOURCES = {
    "japan": "https://www.beforward.jp",
    "uk": "https://www.autotrader.co.uk",
    "south africa": "https://www.autotrader.co.za"
}


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

# ================== Depreciation Helpers ==================
def get_age_months(reg_date: str) -> int:
    """Calculate vehicle age in months from registration date to today."""
    try:
        reg = datetime.strptime(reg_date, "%Y-%m-%d")
        today = datetime.today()
        return (today.year - reg.year) * 12 + (today.month - reg.month)
    except Exception:
        return 0

def get_age_years(reg_date: str) -> int:
    return get_age_months(reg_date) // 12

def get_age_from_production_year(prod_year: int) -> int:
    try:
        return datetime.today().year - int(prod_year)
    except (TypeError, ValueError):
        return 0

"""def apply_graph_method(base_price: float, mileage: float, age_months: int) -> float:
    
    if graph_df is None or graph_df.empty:
        return base_price

    # Pick the nearest combination by absolute difference
    row = graph_df.iloc[((graph_df["AgeMonths"] - age_months).abs() +
                         (graph_df["Mileage"] - mileage).abs()).argsort()[:1]]

    retention_percent = float(row["DepreciationPercent"].values[0])  # this is now retention %
    return round(base_price * (retention_percent / 100.0), 2)

def apply_economic_life(base_price: float, age_years: int) -> float:
    if econ_df is None or econ_df.empty:
        return base_price
    row = econ_df.iloc[(econ_df["AgeYears"] - age_years).abs().argsort()[:1]]
    dep_percent = row["DepreciationPercent"].values[0]
    return round(base_price * (1 - dep_percent / 100), 2)

def calculate_local_value(report: Dict[str, Any], base_price: float, mileage: float, reg_date: str) -> float:
    reg_date = report.get("registration_date", "")
    age_months = get_age_months(reg_date)
    if age_months < 36:
        return apply_graph_method(base_price, mileage, age_months)
    else:
        age_years = age_months // 12
        return apply_economic_life(base_price, age_years)
"""

def apply_graph_method(base_price: float, mileage: float, age_months: int) -> float:
    """
    Apply Graph Method. Match mileage and age independently to nearest rows
    and average their retention percentages.
    """
    if graph_df is None or graph_df.empty:
        logger.warning("Graph method table is missing or empty.")
        return base_price

    # Make sure incoming values are numeric
    try:
        mileage = float(mileage or 0)
        age_months = int(age_months or 0)
    except Exception:
        mileage = 0.0
        age_months = 0

    usage_label = "not-asked"
    max_mileage_threshold = 10000.0

    if mileage > 10000:
        # Only now do we need to know private vs commercial
        try:
            usage_raw = input(
                "Mileage is above 15,000 km. Is the vehicle used for private or commercial purposes? (private/commercial): "
            ).strip().lower()
            if usage_raw == "commercial":
                usage_label = "commercial"
                max_mileage_threshold = 20000.0
            else:
                # Treat anything else as private in terms of cap
                usage_label = "private"
                max_mileage_threshold = 10000.0
        except Exception:
            # If input fails, fall back to private cap (15k)
            usage_label = "private"
            max_mileage_threshold = 10000.0
    else:
        # ≤15k: we don't care about usage, behaviour is the same for both.
        usage_label = "≤15k-band"
        max_mileage_threshold = 10000.0

    # Nearest age row (returns a Series)
    age_idx = (graph_df["AgeMonths"] - age_months).abs().argsort().iat[0]
    age_row = graph_df.iloc[age_idx]
    age_retention = float(age_row["AgeDepreciation"])

    # Nearest mileage row (returns a Series)
    mileage_idx = (graph_df["Mileage"] - mileage).abs().argsort().iat[0]
    mileage_row = graph_df.iloc[mileage_idx]
    mileage_retention = float(mileage_row["MileageDepreciation"])

    if mileage > max_mileage_threshold:
        # High mileage above cap → ignore mileage band, use age-only
        avg_retention = age_retention
        logger.info(
            f"Graph Method (usage={usage_label}) | Mileage {mileage} km > cap {max_mileage_threshold} km; "
            f"using age-only retention {age_retention}%."
        )
    else:
        # Under or equal to cap → average age + mileage
        avg_retention = (mileage_retention + age_retention) / 2.0
        logger.info(
            f"Graph Method (usage={usage_label}) | Age: {age_months} months | Mileage: {mileage} km | "
            f"Age Ret%: {age_retention}% | Mileage Ret%: {mileage_retention}% | "
            f"Avg Ret%: {avg_retention}% | "
            f"MatchedRows -> AgeMonths={int(age_row['AgeMonths'])}, Mileage={int(mileage_row['Mileage'])}"
        )

    return round(base_price * (avg_retention / 100.0), 2)


def apply_economic_life(base_price: float, reg_date: str) -> float:
    """
    Applies the Economic Life method automatically using the Excel chart.
    Matches age in years to retention %.
    """
    if econ_df is None or econ_df.empty:
        logger.warning("Economic life table is missing or empty.")
        return base_price
    
    age_years = get_age_years(reg_date)

    # Match nearest age in table
    idx = (econ_df["AgeYears"] - age_years).abs().argsort().iat[0]
    row = econ_df.iloc[idx]
    # row["DepreciationPercent"] is the percent depreciated; retention = 100 - dep%
    dep_percent = float(row["DepreciationPercent"])
    retention_percent = max(dep_percent)

    logger.info(f"Economic Life: Age={age_years}yrs, Dep%={dep_percent}, Ret%={retention_percent}")
    return round(base_price * (retention_percent / 100.0), 2)


def calculate_local_value(report: Dict[str, Any], base_price: float, mileage: float, reg_date: str) -> float:
    age_months = get_age_months(reg_date)

    if age_months < 3:
        logger.info(
            f"Local vehicle age {age_months} months (<3m); "
            "no depreciation applied (100% retention)."
        )
        return round(base_price, 2)

    # <3 years → Graph Method
    if age_months < 36:
        return apply_graph_method(base_price, mileage, age_months)
    # ≥3 years → Economic Life
    return apply_economic_life(base_price, reg_date)


@lru_cache(maxsize=1)
def get_fx_rates() -> dict:
    """
    Fetch USD-based FX rates with 24h caching.
    Primary: exchangerate.host
    Fallback: open.er-api.com
    Ultimate fallback: fixed manual rate (USD→KES=129.0)
    """

    global _fx_cache
    now = datetime.utcnow()

    # Use cached result if still fresh
    if _fx_cache["rates"] and _fx_cache["timestamp"]:
        if (now - _fx_cache["timestamp"]) < timedelta(hours=24):
            logger.info("💾 Using cached FX rates.")
            return _fx_cache["rates"]

    # Primary source (highly reliable, no API key)
    try:
        resp = requests.get("https://api.exchangerate.host/latest?base=USD", timeout=6)
        data = resp.json()
        if "rates" in data and "KES" in data["rates"]:
            rate = data["rates"]["KES"]
            logger.info(f"🌍 Live FX rates fetched successfully (USD→KES: {rate})")
            _fx_cache = {"rates": data["rates"], "timestamp": now}
            return data["rates"]
    except Exception as e:
        logger.warning("Primary FX source (exchangerate.host) failed, falling back...", exc_info=e)

    # Secondary fallback
    try:
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=6)
        data = resp.json()
        if "rates" in data and "KES" in data["rates"]:
            rate = data["rates"]["KES"]
            logger.info(f"✅ Fallback FX rates fetched (USD→KES: {rate})")
            _fx_cache = {"rates": data["rates"], "timestamp": now}
            return data["rates"]
    except Exception as e:
        logger.error("❌ All FX rate sources failed", exc_info=e)

    # Ultimate manual fallback
    logger.warning("⚠️ Using fallback constant rate USD→KES=129.0")
    return {"KES": 129.0}


def convert_to_kes(price: float, currency: str = "USD") -> float:
    """
    Convert a price from the given currency to KES using the cached or live FX rate.
    """
    if not price:
        return 0.0

    rates = get_fx_rates()
    cur = (currency or "USD").upper()

    # No conversion needed
    if cur == "KES":
        return round(price, 2)

    # Default fallback rate if KES missing
    rate = rates.get("KES", 129.0)
    kes_value = round(price * rate, 2)
    logger.info(f"💱 Converting {price} {cur} → {kes_value} KES @ {rate}")
    return kes_value


# ================== Imported Vehicle Logic Manual input ==================
def calculate_imported_value(report: Dict[str, Any]) -> Optional[float]:
    """
    Calculate the estimated value for imported vehicles.

    Rules:
    - 0-7 years: CNF in USD → convert to KES
    - 8-14 years: CNF already in KES, no conversion
    - Depreciation always from registration date
    - Face value: only if production age ≥ 14
    """
    duty_kes = float(report.get("duty") or 0)
    profit_margin = float(report.get("profit_margin") or 0.10)
    mileage = float(report.get("odo_reading") or 0)

    # Determine vehicle age (registration date)
    reg_date = report.get("registration_date", "")
    age_months = get_age_months(reg_date)
    age_years = age_months / 12.0 if age_months else 0.0

    # Determine production age for face value check
    prod_year = report.get("production_year")
    manufacture_age_years = None
    if prod_year:
        try:
            manufacture_age_years = datetime.today().year - int(prod_year)
        except Exception:
            manufacture_age_years = None

    # ----------- FACE VALUE (imported) -----------
    if manufacture_age_years and manufacture_age_years >= 14:
        # Use face_value_average + aftermarket_additions from report (set by prompt)
        face_value = float(report.get("face_value_average") or 0)
        aftermarket_additions = float(report.get("aftermarket_additions") or 0)
        report["depreciation_method"] = "Face Value Method (manufacture_age ≥ 14)"
        logger.info(
            f"💎 Imported Face Value applied (prod_age={manufacture_age_years}yrs): "
            f"face={face_value}, aftermarket={aftermarket_additions}"
        )
        return round(face_value + aftermarket_additions, 2)

    # ----------- NON–FACE-VALUE IMPORTS -----------
    base_price = float(report.get("base_price") or 0)
    if base_price <= 0:
        logger.warning("No base_price provided for imported vehicle.")
        return None

    # 0–7 yrs from registration → CNF in USD → convert
    if age_years <= 7:
        landed_kes = convert_to_kes(base_price, "USD")
    else:
        # 8–14 yrs from registration → CNF already in KES
        landed_kes = base_price

    # Add duty and profit margin
    total_kes = (landed_kes + duty_kes) * (1 + profit_margin)

    if age_years <= 7 and age_months < 3:
        report["depreciation_method"] = "Imported New Entry (no depreciation)"
        logger.info(
            f"🚘 Imported new entry (<7yrs, reg={reg_date}, age_months={age_months}) – "
            f"using total_kes={total_kes} with no depreciation."
        )
        return round(total_kes, 2)

    # Mileage-based annual depreciation rate
    if mileage < 10_000:
        dep_rate = 0.05
    elif mileage <= 20_000:
        dep_rate = 0.075
    else:
        dep_rate = 0.10

    # Apply depreciation from registration date
    depreciated_value = total_kes * ((1 - dep_rate) ** age_years)
    return round(depreciated_value, 2)

    
def get_vehicle_data(report_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    token = token or authenticate()
    report = fetch_report_data(report_id, token)
    if not report:
        return {}
    

    ch_urls = [report.get(k) for k in ["chassis_number_on_frame_img", "log_book_img"]]
    en_urls = [report.get(k) for k in ["engine_img", "log_book_img"]]

    ch_expected = report.get("chassis_number_on_frame_img", "")
    en_expected = report.get("engine_no", "")

    ch_text, ch_conf = try_multiple_ocr_sources(ch_urls, token, ch_expected, "chassis")
    en_text, en_conf = try_multiple_ocr_sources(en_urls, token, en_expected, "engine")

    vin_info = decode_vin(report.get("vin", ""))

    origin_country = (report.get("origin_country") or "").strip()
    origin_type = "local" if origin_country.lower() == "kenya" else "imported"

    normalized_prod_year = vin_info.get("year") or report.get("production_year") or report.get("year")
    if normalized_prod_year:
        report["production_year"] = str(normalized_prod_year)

    # Estimated value
    estimated_value = None
    try:
        # ensure numeric defaults
        report.setdefault("face_value_average", 0.0)
        report.setdefault("aftermarket_additions", 0.0)

        base_price = float(report.get("base_price") or 0)
        mileage = float(report.get("odo_reading", 0) or 0)
        reg_date = report.get("registration_date", "")

        # production-based age (production year -> years old)
        prod_year = report.get("production_year")
        prod_age_years = get_age_from_production_year(prod_year)

        if origin_type == "local":
            # 1) Face value candidate for manufacture-age ≥ 14 (local vehicles)
            if prod_age_years >= 14:
                # Do NOT calculate a numeric value here, because face_value_average and
                # aftermarket_additions are collected later in the interactive layer.
                report["depreciation_method"] = "Face Value Method (manufacture_age ≥ 14)"
                logger.info(
                    f"💎 Local face-value candidate (prod_age={prod_age_years}yrs). "
                    "Final value will be based on face_value_average + aftermarket_additions."
                )
                # Leave estimated_value as None

            # 2) Non-face-value local → Graph or Economic Life
            elif base_price > 0 and reg_date:
                estimated_value = calculate_local_value(report, base_price, mileage, reg_date)
            else:
                logger.info("Local vehicle missing base_price or registration_date; valuation deferred.")

        else:
            # IMPORTED vehicles – including their own face-value handling.
            estimated_value = calculate_imported_value(report)

    except Exception as e:
        logger.warning(f"⚠️ Failed to calculate estimated value for report ID: {report_id}", exc_info=e)

    if report.get("final_depreciated_value") is not None:
        logger.info("Final depreciated value already set (face-value/imported). Skipping Graph/Economic Life diagnostics.")
    else:
        reg_date = report.get("registration_date", "")
        if not reg_date:
            logger.info("No registration_date; skipping Graph/Economic Life diagnostics.")
        elif origin_type != "local":
            logger.info("Imported vehicle; skipping Graph/Economic Life diagnostics.")
        else:
            # Local-only diagnostics for non–face-value vehicles
            prod_year = report.get("production_year")
            prod_age_years = get_age_from_production_year(prod_year)

            # Face-value locals (manufacture_age ≥ 14) should not use Graph/Economic Life tables
            if prod_age_years >= 14:
                logger.info(
                    f"Local face-value vehicle (prod_age={prod_age_years}yrs); "
                    "skipping Graph/Economic Life diagnostics."
                )
            else:
                age_months = get_age_months(reg_date)
                mileage = float(report.get("odo_reading", 0) or 0)

                # -------- GRAPH METHOD for <3 years --------
                if age_months < 36:
                    report["depreciation_method"] = "Graph Method (<3 years)"
                    if graph_df is not None and not graph_df.empty:
                        try:
                            # Use apply_graph_method with a dummy base price of 100.
                            # The returned value will then equal the retention %.
                            retention_from_graph = apply_graph_method(100.0, mileage, age_months)
                            retention_from_graph = round(retention_from_graph, 2)

                            report["retention_percent_used"] = retention_from_graph
                            logger.info(
                                f"📉 Graph Method diagnostics | Age={age_months}m, Mileage={mileage} | "
                                f"Retention Used={retention_from_graph}% (via apply_graph_method)"
                            )
                        except Exception as e:
                            logger.warning("Failed to compute graph method reporting rows", exc_info=e)

                # -------- ECONOMIC LIFE for ≥3 yrs --------
                else:
                    report["depreciation_method"] = "Economic Life Method (≥3 years)"
                    if econ_df is not None and not econ_df.empty:
                        age_years_reg = get_age_years(reg_date)  # registration-based age
                        row = econ_df.iloc[(econ_df["AgeYears"] - age_years_reg).abs().argsort()[:1]]

                        dep_percent = float(row["DepreciationPercent"].values[0])
                        retention = dep_percent

                        report["retention_percent_used"] = retention
                        logger.info(
                            f"📘 Economic Life Used | Age={age_years_reg}yrs | Dep%={dep_percent} | Ret%={retention}"
                        )

    if estimated_value is not None:
        report["final_depreciated_value"] = estimated_value
        if origin_type == "local":
            base_price = float(report.get("base_price") or 0)
            if base_price:
                depreciation_percent = round((1 - (estimated_value / base_price)) * 100, 2)
                retention_percent = round(100 - depreciation_percent, 2)
                report["depreciation_percent_used"] = depreciation_percent
                report["retention_percent_used"] = retention_percent
                logger.info(f"🧮 Depreciation Calculated: {depreciation_percent}% | Final Value: {estimated_value}")

        
    return {
        "vehicle_make": report.get("make", ""),
        "vehicle_model": report.get("model", ""),
        "vin": report.get("vin", ""),
        "chassis_number_logbook": ch_expected,
        "engine_number_logbook": en_expected,
        "chassis_number_ocr": ch_text,
        "engine_number_ocr": en_text,
        "image_quality_notes": f"Chassis confidence: {ch_conf}/10, Engine confidence: {en_conf}/10",
        "production_year": report.get("production_year", ""),
        "registration_date": report.get("registration_date", ""),       
        "origin_country": origin_country,
        "origin_type": origin_type,
        "known_issues": report.get("comments", ""),
        "parts_availability": report.get("parts_availability", ""),
        "mileage": str(report.get("odo_reading", "")),
        "condition_notes": report.get("comments", ""),
        "decoded_features": [f"{k}: {v}" for k, v in vin_info.items() if v],
        "market_data": report.get("market_data", []),      
        "estimated_value": estimated_value,
        "depreciation_method": report.get("depreciation_method", "Auto-selected"),
        "retention_percent_used": report.get("retention_percent_used", "Not available"),
        "final_depreciated_value": report.get("final_depreciated_value", "Not calculated"),
    }

from datetime import datetime
from app.data_pipeline import convert_to_kes, get_age_months

def get_system_prompt():
    return """
You are Vehicle Valuer Assistant AI, designed to support professional vehicle valuers by reviewing inspection reports and providing concise, structured summaries.
You do NOT replace the human valuer; you highlight key points and apply valuation logic consistently.

You must operate using a layered approach:

1. **NO ASSUMPTIONS**
   - Never assume or estimate depreciation percentages, FX rates, or valuation adjustments.
   - Always use the exact numeric values already provided in the data:
       • Base Price (KES or USD)
       • Depreciation Method (Graph / Economic Life / Imported)
       • Retention Percent Used
       • Final Depreciated Value
   - If these are present, they override any internal calculation or reasoning.

2. **Logbook Verification**
   - Use OCR data first to check chassis/engine numbers.
   - If OCR fails, fall back to logbook values.
   - If both fail, skip and note that human review is needed.

3. **Global Information**
   - Use VIN decoding if available (features, make, model, production year).
   - Based on make, mode and year of production: give information on where the model is still in production or discontinued.
   - Note where the model sits in its lifecycle.
   - Mention known issues, trim levels, and parts availability (Kenya/Uganda context).


4. **Valuation**
   - Ignore report-provided historical valuations (valuation_with_duty / without_duty / forced_sale_value).
   - Always derive independently using ALL of the following:

   **A) Base price**
   -Ignore any base price if manufacture age ≥14 years (face-value cases).
   - For *Local* vehicles: use Kenyan dealer price at registration.
   - For *Imported* vehicles:
       1. Use the origin-country auction or dealer price.
       2. Convert to Kenyan Shillings (KES) using the provided live exchange rate (fetched automatically via `convert_to_kes`, e.g., 1 USD = 129.14 KES). Do NOT assume or invent other rates.
       3. Add **freight (if not included)** and apply **KRA import duty** (as per the public calculator, e.g. Carluv/KRA portal).
       4. Add a **10% profit margin** after duty.
   - Include aftermarket additions ONLY for vehicles older than 14 years (face-value cases).

   **B) Depreciation**
   - Local (<3 yrs): use the **Graph Method** (age in months + mileage).
   - Local (≥3 yrs): use the **Economic Life Chart** (age in years).
   - The field **`Retention Used (%)`** already shows the **remaining value** of the car after depreciation:
        - Example: if depreciation is 35%, `Retention Used (%) = 65`.  
        - Therefore, always multiply **Base Price × (Retention Used ÷ 100)**.  
        - **Never multiply by the depreciation percentage.**
   - When describing, say “retention of 65%” (not “depreciation of 35%”) if that field is used.
   - Imported: apply 5–10 percent annual depreciation **based on age since **registration year**.
   - Always calculate depreciation based on the time elapsed since the **registration date** 
   - Even newly registered vehicles (e.g., less than one year old) should show minimal but not non-zero depreciation.
   - Do not describe depreciation as “minimal,” “moderate,” or “estimated.” Use the provided figures directly.
   - For imported or local vehicles over 14 years (manufacture age >14):
       • Only apply aftermarket additions in face value cases (manufacture age >14 years).
       • Ignore aftermarket additions and high_demand when manufacture_age_years ≤ 14.
   - High-demand flag:
       • If high_demand=True → lower annual depreciation.
       • If high_demand=False → full depreciation.
   - Only apply condition adjustment for:
       • Imported vehicles  
       • Local vehicles older than five years  
       • After vehicle age >12 months  
       • Never apply condition adjustment on Graph Method paths.


   **C) Final valuation**
   - Combine base → FX → duty → margin → depreciation → condition adjustment.
   - Always output a **single numeric estimate in KES** (no placeholders or ranges).
   - Skip ONLY if make, model, and mileage are all missing.

Your output must have three sections:

**LOGBOOK VERIFICATION NOTES**
- Compare OCR vs logbook chassis/engine numbers.
- Flag mismatches, unreadable values, or missing data.
- Confidence score /10.
- Prompt human review where unclear.

**GLOBAL INFORMATION NOTES**
- Summarize VIN features (if available).
- Model lifecycle stage.
- Known issues & parts availability (Kenya/Uganda).
- Production status, production ongoing or discontinued.

**VALUATION NOTES**
- Stepwise reasoning: base price → FX conversion (if applicable) → duty → margin → depreciation → condition adjustment.
- Final numeric estimate in KES.
- End with: “Estimated value: KES <number>”.

Keep response:
- Under 500 words.
- Bullet points only.
- Professional and consistent tone.
"""

def format_list(items):
    if not items or not isinstance(items, list):
        return "Not available"
    return "\n  - " + "\n  - ".join(str(i) for i in items)

def safe_val(val):
    return val if val not in [None, "", "0", 0] else "Not available"

def build_user_prompt(data):
    """
    Production-safe: builds the user-facing prompt WITHOUT input().
    The Streamlit UI supplies required fields via override_data.

    Demand adjustment is universal (all vehicles) and applied AFTER a value basis exists:
      - Face-value cases (manufacture age >= 14): basis = face_value_average + aftermarket_additions
      - Non-face-value cases (< 14): basis priority:
          (1) final_depreciated_value if available
          (2) else base_price * (retention_percent_used/100) if both available
    """

    def to_float_or_none(x):
        try:
            if x in [None, "", "Not available", "Not calculated"]:
                return None
            return float(x)
        except Exception:
            return None

    def apply_percent_adjustment(value, pct):
        return value + (value * (pct / 100.0))

    origin_type = (data.get("origin_type") or "").lower()
    reg_date = data.get("registration_date")
    age_months = get_age_months(reg_date) if reg_date else 0
    age_years_reg = age_months / 12.0 if age_months else 0.0

    # --- Determine manufacture_age_years safely ---
    manufacture_age_years = 0
    prod_year = data.get("production_year")
    if prod_year:
        try:
            manufacture_age_years = datetime.today().year - int(prod_year)
        except Exception:
            manufacture_age_years = 0

    # --- Universal demand inputs (supplied by UI) ---
    demand_level = (data.get("demand_level") or "low").strip().lower()
    if demand_level not in ["low", "high", "very high"]:
        demand_level = "low"

    demand_adjust_percent = to_float_or_none(data.get("demand_adjust_percent"))
    if demand_adjust_percent is None:
        demand_adjust_percent = 0.0

    # Force 0% when low demand
    if demand_level == "low":
        demand_adjust_percent = 0.0

    data["demand_level"] = demand_level
    data["demand_adjust_percent"] = demand_adjust_percent
    data["high_demand"] = demand_level in ["high", "very high"]  # backwards compat

    # --- Determine basis value BEFORE demand adjustment ---
    value_before_demand = None
    basis_label = None

    if manufacture_age_years >= 14:
        fva = to_float_or_none(data.get("face_value_average")) or 0.0
        ama = to_float_or_none(data.get("aftermarket_additions")) or 0.0
        value_before_demand = fva + ama
        basis_label = "Face Value Average + Aftermarket Additions"
    else:
        # Priority 1: final_depreciated_value if already computed upstream
        fdv = to_float_or_none(data.get("final_depreciated_value"))
        if fdv is not None and fdv > 0:
            value_before_demand = fdv
            basis_label = "Final Depreciated Value"
        else:
            # Priority 2: base_price × (retention_percent_used ÷ 100)
            bp = to_float_or_none(data.get("base_price"))
            ret = to_float_or_none(data.get("retention_percent_used"))
            if bp is not None and bp > 0 and ret is not None and ret > 0:
                value_before_demand = bp * (ret / 100.0)
                basis_label = "Base Price × (Retention Used ÷ 100)"

    if value_before_demand is not None:
        data["value_before_demand"] = round(value_before_demand, 2)
        data["value_after_demand"] = round(
            apply_percent_adjustment(value_before_demand, demand_adjust_percent), 2
        )
    else:
        data["value_before_demand"] = "Not available"
        data["value_after_demand"] = "Not available"
        basis_label = "Not available (missing basis inputs)"

    # --- Compute age since registration for display ---
    if reg_date:
        try:
            reg = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.today()
            age_years = (today.year - reg.year) + ((today.month - reg.month) / 12)
            data["age_since_registration_years"] = round(age_years, 1)
        except Exception:
            data["age_since_registration_years"] = "Not available"
    else:
        data["age_since_registration_years"] = "Not available"

    chassis_ocr = data.get("chassis_number_ocr")
    engine_ocr = data.get("engine_number_ocr")
    fx_note = data.get("fx_rate_note", "Use the provided exchange rate for conversions.")

    return f"""
Below is structured inspection data for a used vehicle. Review it and return a clear summary as per system instructions.
{fx_note}

=== LOGBOOK DETAILS ===
- OCR Data: Available for { 'both' if chassis_ocr and engine_ocr else 'one' if chassis_ocr or engine_ocr else 'none' }
- Logbook Chassis Number (fallback): {data.get('chassis_number_logbook') or 'Not available'}
- Logbook Engine Number (fallback): {data.get('engine_number_logbook') or 'Not available'}
- OCR Chassis Number (from image): {chassis_ocr or 'Not available'}
- OCR Engine Number (from image): {engine_ocr or 'Not available'}
- Image Quality Notes: {data.get('image_quality_notes') or 'Not available'}

=== VEHICLE INFORMATION ===
- Make: {data.get('vehicle_make') or 'Not available'}
- Model: {data.get('vehicle_model') or 'Not available'}
- VIN: {data.get('vin') or 'Not available'}
- Production Year: {data.get('production_year') or 'Not available'}
- Registration Date: {data.get('registration_date') or 'Not available'}
- Age Since Registration: {data.get('age_since_registration_years', 'Not available')} years
- Origin Country: {data.get('origin_country') or 'Not available'}
- Origin Type: {data.get('origin_type') or 'Not available'}
- Base Price: {data.get('base_price')} {'USD' if origin_type == 'imported' and age_years_reg <= 7 else 'KES'}
- Depreciation Method: {data.get('depreciation_method', 'Auto-selected')}
- Retention Used (%): {data.get('retention_percent_used', 'Not available')}
- Final Depreciated Value: {data.get('final_depreciated_value', 'Not calculated')}
- Duty: {data.get('duty', 'N/A')} KES
- Profit Margin: {data.get('profit_margin', '0.10')}
- Face Value Average (if applicable): {data.get('face_value_average', 'Not available')} KES
- Aftermarket Additions (if applicable): {data.get('aftermarket_additions', 'Not available')} KES

=== DEMAND ADJUSTMENT (UNIVERSAL) ===
- Demand Level: {data.get('demand_level', 'low')}
- Demand Adjustment Percent: {data.get('demand_adjust_percent', 0)}
- Demand Basis Used: {basis_label}
- Value Before Demand Adjustment: {data.get('value_before_demand', 'Not available')}
- Value After Demand Adjustment: {data.get('value_after_demand', 'Not available')}

- Decoded Features:
  {format_list(data.get('decoded_features'))}
- Known Issues: {data.get('known_issues') or 'Not available'}
- Parts Availability: {data.get('parts_availability') or 'Not available'}

=== CONDITION & MARKET DATA ===
- Mileage: {data.get('mileage') or 'Not available'}
- Condition Notes: {data.get('condition_notes') or 'Not available'}
- Comparable Market Listings:
  {format_list(data.get('market_data'))}
  
=== VALUATION DATA (FOR REFERENCE ONLY) ===
- Forced Sale Value (if available): {safe_val(data.get('force_sales_value'))}
- Valuation With Duty (if available): {safe_val(data.get('valuation_with_duty'))}
- Valuation Without Duty (if available): {safe_val(data.get('valuation_without_duty'))}

Please return your response with each section clearly labeled in bold, followed by bullet points:

**LOGBOOK VERIFICATION NOTES**
- [first point]

**GLOBAL INFORMATION NOTES**
- [first point]

**VALUATION NOTES**
- [stepwise reasoning: base price → depreciation → condition adjustment → market comparison]
- [final single numeric value in KES, grounded in at least 2 comparative market examples where applicable]

=== INSTRUCTIONS ===
1. In **LOGBOOK VERIFICATION NOTES**:
   - Compare OCR vs Logbook chassis/engine numbers.
   - If OCR data is missing, skip and only verify logbook.
   - Flag mismatches, unclear numbers, or missing data.
   - Give a confidence score out of 10.
   - Prompt human review where needed.

2. In **GLOBAL INFORMATION NOTES**:
   - Summarize VIN features (if available).
   - Comment on where model sits in lifecycle.
   - Mention known issues and parts availability (Kenya/Uganda).
   - Flag trim-level differences if relevant.

3. In **VALUATION NOTES**:
   - For local vehicles:
        - Use inputs for base price at registration where applicable.
   - For imported vehicles:
        - Convert the provided base price (C$F) to KES using the current exchange rate, then add the duty and profit margin as per inputs.
   - For manufacture age ≥14 years (face-value cases, local or imported):
        - Completely ignore Base Price, Graph Method, and Economic Life charts.
        - Treat Face Value Average as the already-derived average of at least 3 comparable local market prices.
        - Add any Aftermarket Additions directly to get:
          • ValueBeforeDemand = Face Value Average + Aftermarket Additions.
   - Universal demand adjustment (ALL vehicles, applied AFTER the value is computed):
        - Demand Level is one of: low / high / very high.
        - If low → DemandAdjust% = 0.
        - If high/very high → use the explicitly provided DemandAdjust%.
        - Apply to the computed basis value:
          • Final = ValueBeforeDemand + (DemandAdjust% ÷ 100 × ValueBeforeDemand)
   - Apply ±0–10% for condition/market adjustment where relevant as per *Depreciation* rules.
   - Always provide a **final numeric estimated fair market value in KES**.
   - Do not use placeholders, ranges, or “to be determined”.
   - Skip ONLY if make, model, and mileage are all missing.
   - If `Final Depreciated Value` and `Retention Used (%)` are already provided, do NOT recompute the valuation. 
     Instead, explain the valuation path and confirm the final figure using these values.

"""
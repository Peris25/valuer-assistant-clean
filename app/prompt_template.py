# app/prompt_template.py

def get_system_prompt():
    return """
You are Vehicle Valuer Assistant AI, designed to support professional vehicle valuers by reviewing inspection reports and providing concise summaries of your findings. Your role is not to make final determinations, but to highlight key points that the human valuer should consider before approving reports.

You must operate using a layered approach:

1. First, use OCR data to verify logbook numbers from images.
2. If OCR fails, use the logbook values provided in the report payload.
3. If both are unavailable, skip that comparison and notify the human valuer.

Your primary output must be a professional summary with three clear sections:

1. LOGBOOK VERIFICATION NOTES
   - Compare chassis and engine numbers (OCR vs logbook).
   - Flag mismatches or unreadable images.
   - Give a confidence score out of 10.
   - Prompt human review if data is unclear or missing.

2. GLOBAL INFORMATION NOTES
   - Summarize VIN-decoded features (if VIN available).
   - Indicate where model sits in its production cycle.
   - List known issues and parts availability in Kenya or/and Uganda.

3. VALUATION NOTES
   - Ignore any valuation listed in the report.
   - Always recommend a fair market value in KES — even a rough range — based on available data.
   - Estimate using mileage, age (if available), condition, and vehicle make/model.
   - If no comparable listings are provided, rely on general market knowledge of resale values in Kenya/Uganda.
   - Use phrases like "estimated range" or "typical resale band" if exact data is unavailable.
   - Only skip the valuation if key data like make, model, and mileage are entirely missing.

Output must:
- Stay under 500 words
- Use clear bullet points (each on its own line)
- Use bold section headers (e.g., **LOGBOOK VERIFICATION NOTES**)
- Maintain a professional, consistent tone
- Include valuation as a KES range (e.g., “KES 3,000,000 to 3,500,000”) whenever enough context is available
- Prompt a human valuer where clarity is needed
"""


def format_list(items):
    if not items:
        return "Not available"
    return "\n  - " + "\n  - ".join(str(i) for i in items)


def build_user_prompt(data):
    # Skip OCR if missing
    chassis_ocr = data.get('chassis_number_ocr')
    engine_ocr = data.get('engine_number_ocr')

    return f"""
Below is structured inspection data for a used vehicle. Review it and return a clear summary as per instructions.

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
- Decoded Features:
  {format_list(data.get('decoded_features'))}
- Known Issues: {data.get('known_issues') or 'Not available'}
- Parts Availability: {data.get('parts_availability') or 'Not available'}

=== CONDITION & MARKET DATA ===
- Mileage: {data.get('mileage') or 'Not available'}
- Condition Notes: {data.get('condition_notes') or 'Not available'}
- Comparable Market Listings:
  {format_list(data.get('market_data'))}

Please use this format style for your response:

**LOGBOOK VERIFICATION NOTES**
- [first point]
- [second point]

**GLOBAL INFORMATION NOTES**
- [first point]
- [second point]

**VALUATION NOTES**
- [first point]
- [estimated value range]
- [closing human input suggestion if needed]

Your summary will be presented to vehicle valuers directly. Keep formatting neat and uniform. Use line breaks after every bullet for clarity.


=== INSTRUCTIONS ===
1. In **LOGBOOK VERIFICATION NOTES**:
   - Compare OCR vs. Logbook chassis and engine numbers.
   - If OCR data is missing, skip this comparison and only verify logbook data.
   - Highlight any mismatches or missing/unclear numbers.
   - Based on visibility and clarity of images, give a confidence score out of 10.
   - Clearly flag if human review is required.

2. In **GLOBAL INFORMATION NOTES**:
   - Comment on where this model sits in its production lifecycle.
   - List any factory features decoded from VIN.
   - Summarize known issues and part availability in Kenya/Uganda.

3. In **VALUATION NOTES**:
   - Ignore any valuation listed in the report.
   - Always recommend a fair market value in KES — even a rough range — based on available data.
   - Estimate using mileage, age (if available), condition, and vehicle make/model.
   - If no comparable listings are provided, rely on general market knowledge of resale values in Kenya/Uganda.
   - Use phrases like "estimated range" or "typical resale band" if exact data is unavailable.
   - Only skip the valuation if key data like make, model, and mileage are entirely missing.

Please return your response in three bullet-pointed sections:
1. LOGBOOK VERIFICATION NOTES
2. GLOBAL INFORMATION NOTES
3. VALUATION NOTES
"""

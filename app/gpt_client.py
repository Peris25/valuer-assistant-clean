# --- app/gpt_client.py ---

import json
import os
import logging
from openai import OpenAI
from app.prompt_template import get_system_prompt, build_user_prompt 
from app.data_pipeline import get_vehicle_data, convert_to_kes, get_fx_rates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise EnvironmentError("Missing OPENAI_API_KEY in environment variables.")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

def generate_summary(report_id: str, override_data: dict = None) -> str:
    """
    Generate a structured vehicle valuation summary using GPT-4.
    Prompts valuers for base price/duty manually if missing.
    """
    try:
        # Step 1: Fetch vehicle data (either override or from API)
        data = override_data if override_data else get_vehicle_data(report_id)
        if not data:
            return "⚠️ No data found for this report."
        
        
        fx_rate = get_fx_rates().get("KES", 129.0)
        data["fx_rate_kes"] = fx_rate
        data["fx_rate_note"] = f"For all currency conversions, use the current live rate of 1 USD = {fx_rate:.2f} KES (auto-fetched)."

        # Step 2: Prompt valuer for missing inputs (manual input)
        logger.info("🔧 Checking for manual base price/duty/profit margin inputs...")
        user_prompt = build_user_prompt(data)  # <- This both gathers input and returns formatted text

        # Step 3: Prepare GPT prompts
        system_prompt = (
            get_system_prompt() 
        + f"\n\nIMPORTANT: Always use the provided live FX rate (1 USD = {fx_rate:.2f} KES). "
              "Do NOT invent or assume other rates."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # Step 4: Call GPT-4
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.2,
            max_tokens=800
        )

        # Step 5: Extract and log content
        content = response.choices[0].message.content.strip()
        logger.info(f"GPT raw content: {content!r}")
        if not content:
            logger.warning("⚠️ GPT returned an empty response.")
        return content

    except Exception as e:
        logger.exception("Error during GPT summary generation.")
        logger.info(f"Report ID: {report_id}, Override: {override_data is not None}")
        return "⚠️ Failed to generate summary. Please try again or check logs."

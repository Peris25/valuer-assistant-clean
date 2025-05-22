# --- app/gpt_client.py ---

import os
import logging
from openai import OpenAI
from app.prompt_template import get_system_prompt, build_user_prompt
from app.data_pipeline import get_vehicle_data

logger = logging.getLogger(__name__)

# Load from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)


def generate_summary(report_id: str, override_data: dict = None) -> str:
    """
    Generate a structured vehicle valuation summary using GPT-4.

    Args:
        report_id: Report ID to pull data for from the Swagger API.
        override_data: Optional dictionary to bypass API and test prompt logic.

    Returns:
        GPT-generated summary or error message.
    """
    try:
        # Step 1: Get vehicle data (either override or real)
        data = override_data if override_data else get_vehicle_data(report_id)
        #logger.info(f"Data: {data}")       

        if not data:
            return "⚠️ No data found for the report. Check if the report ID is valid."

        # Step 2: Prepare GPT prompts
        try:

            system_prompt = get_system_prompt()
            user_prompt = build_user_prompt(data)
            logger.debug("System and user prompts successfully built.")
        except Exception as e:
            logger.exception("Error building prompts from vehicle data.")
            return "⚠️ Failed to build summary prompts. Please check the input data format."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # Step 3: Call GPT-4
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.2,
            max_tokens=800
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print("Error generating GPT summary:", str(e))
        logger.info(f"Override: {override_data is not None}, Report ID: {report_id}")
        logger.debug(f"Final vehicle data: {get_vehicle_data}")
        return "⚠️ Failed to generate summary. Please try again or check logs."

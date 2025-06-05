# app/main.py

import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.gpt_client import generate_summary
from app.data_pipeline import get_vehicle_data
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Vehicle Valuation Assistant",
    description="Generates GPT summaries using inspection reports from Solvit Swagger API",
    version="1.0.0"
)

# Request and Response models
class ValuationRequest(BaseModel):
    report_id: Optional[str] = None
    override_data: Optional[Dict[str, Any]] = None


class ValuationResponse(BaseModel):
    summary: str


@app.post("/generate-valuation-summary", response_model=ValuationResponse)
async def generate_valuation_summary(request: ValuationRequest):
    """
    Accepts either a report_id to pull from API or override_data for testing,
    and returns a GPT-generated vehicle valuation summary.
    """
    try:
        if not request.report_id and not request.override_data:
            raise HTTPException(
                status_code=400,
                detail="❌ Provide either a report_id or override_data."
            )

        logger.info(f"Received valuation request. Report ID: {request.report_id}, Override Data Provided: {request.override_data is not None}")

        summary_text = generate_summary(
            report_id=request.report_id,
            override_data=request.override_data
        )

        if not summary_text or "⚠️" in summary_text:
            logger.warning("⚠️ Summary generation returned empty text.")
            raise HTTPException(
                status_code=404,
                detail="No summary generated. Check input data."
            )

        logger.info("✅ GPT summary generated.")
        return ValuationResponse(summary=summary_text)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("❌ GPT summary generation failed", exc_info=e)
        raise HTTPException(
            status_code=500,
            detail="Failed to generate summary. Check logs."
        )

# app/main.py

import os
import time
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
from app.gpt_client import generate_summary
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Vehicle Valuation Assistant",
    description="Generates GPT summaries using inspection reports from Solvit Swagger API",
    version="1.0.0",
    openapi_tags=[
        {"name": "Valuation", "description": "Endpoints related to GPT-powered vehicle summaries"},
        {"name": "Monitoring", "description": "Health and uptime checks"},
    ]
)

# CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with allowed origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class ValuationRequest(BaseModel):
    report_id: Optional[str] = None
    override_data: Optional[Dict[str, Any]] = None

class ValuationResponse(BaseModel):
    summary: str

#@app.get("/health", tags=["Monitoring"])
#async def health_check():
#    return {"status": "ok"}

@app.post("/generate-valuation-summary", response_model=ValuationResponse, tags=["Valuation"])
async def generate_valuation_summary(request: ValuationRequest):
    start_time = time.time()

    try:
        if not request.report_id and not request.override_data:
            raise HTTPException(status_code=400, detail="❌ Provide either a report_id or override_data.")

        if request.report_id and not request.report_id.isdigit():
            raise HTTPException(status_code=400, detail="Report ID must be a numeric string.")

        logger.info(f"Received valuation request. Report ID: {request.report_id}, Override Data: {request.override_data is not None}")

        summary_text = generate_summary(report_id=request.report_id, override_data=request.override_data)

        duration = round(time.time() - start_time, 2)
        logger.info(f"✅ GPT summary generated in {duration}s.")

        if not summary_text or "⚠️" in summary_text:
            logger.warning("⚠️ Summary generation returned empty or invalid text.")
            raise HTTPException(status_code=404, detail="No summary generated. Check input data.")

        return ValuationResponse(summary=summary_text)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("❌ GPT summary generation failed", exc_info=e)
        raise HTTPException(status_code=500, detail="Failed to generate summary. Check logs.")

# Ensure it works on Render (which sets the PORT env var)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"🚀 Starting app on port {port}")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)

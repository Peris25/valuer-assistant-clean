# streamlit_app.py
import streamlit as st
from datetime import datetime

from app.data_pipeline import get_vehicle_data
from app.gpt_client import generate_summary


st.set_page_config(
    page_title="AI Vehicle Valuation Assistant",
    page_icon="🚗",
    layout="wide",
)

# ---------- Small helpers ----------
def is_number(x):
    try:
        float(x)
        return True
    except Exception:
        return False

def to_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def pill(label: str, value: str):
    st.markdown(
        f"""
        <div style="display:inline-block;padding:6px 10px;border-radius:999px;
                    border:1px solid #e5e7eb;background:#f9fafb;margin-right:8px;">
            <span style="font-size:13px;color:#111827;"><b>{label}:</b> {value}</span>
        </div>
        """,
        unsafe_allow_html=True
    )

def section_header(title: str, subtitle: str = ""):
    st.markdown(
        f"""
        <div style="padding:14px 16px;border-radius:14px;background:#0f172a;margin:10px 0 14px 0;">
            <div style="color:#fff;font-size:20px;font-weight:700;">{title}</div>
            <div style="color:#cbd5e1;font-size:13px;margin-top:4px;">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

def card(title: str, body: str):
    st.markdown(
        f"""
        <div style="padding:16px;border-radius:14px;border:1px solid #e5e7eb;background:white;">
            <div style="font-weight:700;font-size:16px;margin-bottom:6px;">{title}</div>
            <div style="color:#111827;font-size:14px;line-height:1.5;">{body}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ---------- State ----------
if "fetched" not in st.session_state:
    st.session_state.fetched = False
if "report_id" not in st.session_state:
    st.session_state.report_id = ""
if "vehicle_data" not in st.session_state:
    st.session_state.vehicle_data = {}
if "override_data" not in st.session_state:
    st.session_state.override_data = {}
if "summary" not in st.session_state:
    st.session_state.summary = ""

# ---------- UI ----------
st.title("🚗 AI Vehicle Valuation Assistant")
st.caption("Presentation-friendly step-by-step valuation wizard (local/imported/face-value + demand adjustment).")

colA, colB = st.columns([1.2, 1])

with colA:
    section_header("Step 1 — Load Report", "Enter a report ID to fetch inspection data and auto-detected valuation context.")
    with st.form("fetch_form"):
        report_id = st.text_input("Report ID", value=st.session_state.report_id, placeholder="e.g., 12345")
        fetch_btn = st.form_submit_button("Fetch report")

    if fetch_btn:
        report_id = (report_id or "").strip()
        if not report_id.isdigit():
            st.error("Report ID must be a numeric string.")
        else:
            with st.spinner("Fetching report + OCR + FX + depreciation diagnostics..."):
                data = get_vehicle_data(report_id)
            if not data:
                st.error("No report data found. Check the report ID or API connectivity.")
            else:
                st.session_state.report_id = report_id
                st.session_state.vehicle_data = data
                st.session_state.override_data = {}  # reset overrides
                st.session_state.summary = ""
                st.session_state.fetched = True
                st.success("Report loaded. Continue to Step 2.")

with colB:
    section_header("Live Demo Notes", "Use this wizard like a human valuer: discover the unit, then answer only what’s required next.")
    card(
        "How it works",
        "1) Fetch the report. 2) Review detected details. 3) Fill mandatory valuation inputs shown for this unit only. "
        "4) Generate the GPT summary."
    )

st.divider()

# ---------- Step 2: show detected details + collect required inputs ----------
if st.session_state.fetched:
    data = st.session_state.vehicle_data
    origin_type = (data.get("origin_type") or "").lower()
    make = data.get("vehicle_make") or "Not available"
    model = data.get("vehicle_model") or "Not available"
    prod_year = data.get("production_year") or ""
    reg_date = data.get("registration_date") or ""
    mileage = data.get("mileage") or data.get("odo_reading") or "Not available"

    # Manufacture age (for face-value decision)
    manufacture_age_years = 0
    try:
        manufacture_age_years = datetime.today().year - int(prod_year) if str(prod_year).strip() else 0
    except Exception:
        manufacture_age_years = 0

    is_face_value = manufacture_age_years >= 14 if manufacture_age_years else False

    section_header("Step 2 — Review & Complete Required Inputs", "Fields appear dynamically based on what the report reveals.")

    # --- Summary pills ---
    pill_row = st.container()
    with pill_row:
        pill("Make", str(make))
        pill("Model", str(model))
        pill("Origin", origin_type or "unknown")
        pill("Prod Year", str(prod_year or "unknown"))
        pill("Reg Date", str(reg_date or "unknown"))
        pill("Mileage", str(mileage))

    st.write("")

    # Show quick detected context
    left, right = st.columns([1.15, 0.85])

    with left:
        st.subheader("Detected vehicle context")
        st.write(
            f"""
- **Depreciation method (auto):** {data.get('depreciation_method', 'Auto-selected')}
- **Retention used (%):** {data.get('retention_percent_used', 'Not available')}
- **Final depreciated value:** {data.get('final_depreciated_value', 'Not calculated')}
- **Face-value case (manufacture age ≥14):** {"✅ Yes" if is_face_value else "❌ No"}
            """
        )

    with right:
        st.subheader("Logbook verification snapshot")
        st.write(
            f"""
- OCR chassis: **{data.get('chassis_number_ocr') or 'Not available'}**
- OCR engine: **{data.get('engine_number_ocr') or 'Not available'}**
- Logbook chassis: **{data.get('chassis_number_logbook') or 'Not available'}**
- Logbook engine: **{data.get('engine_number_logbook') or 'Not available'}**
- Image notes: {data.get('image_quality_notes') or 'Not available'}
            """
        )

    st.write("")
    st.markdown("---")

    # ---------- Dynamic input panel ----------
    st.subheader("Mandatory valuation inputs (for this unit)")

    # We'll collect into override_data and pass to generate_summary(report_id, override_data).
    override = dict(st.session_state.override_data)

    # Always required: demand_level; percent only if high/very high
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        demand_level = st.selectbox(
            "Market demand level *",
            ["low", "high", "very high"],
            index=["low", "high", "very high"].index(override.get("demand_level", "low"))
            if override.get("demand_level") in ["low", "high", "very high"] else 0
        )
        override["demand_level"] = demand_level

    with c2:
        if demand_level in ["high", "very high"]:
            override["demand_adjust_percent"] = st.number_input(
                "Demand adjustment % *",
                min_value=0.0,
                max_value=100.0,
                value=float(override.get("demand_adjust_percent", 5.0)),
                step=0.5,
                help="Required when demand is high/very high. Example: 5 means +5%."
            )
        else:
            override["demand_adjust_percent"] = 0.0
            st.text_input("Demand adjustment %", value="0 (low demand)", disabled=True)

    with c3:
        st.info("Demand is applied after the value basis is computed.", icon="ℹ️")

    st.write("")

    # Face-value branch (>=14 years): require face_value_average + aftermarket_additions
    if is_face_value:
        st.markdown("#### Face-value inputs (manufacture age ≥ 14 years)")
        fv1, fv2 = st.columns([1, 1])
        with fv1:
            override["face_value_average"] = st.number_input(
                "Face Value Average (KES) *",
                min_value=0.0,
                value=float(override.get("face_value_average", 0.0)),
                step=5000.0,
                help="Average market value from 3 comparable vehicles."
            )
        with fv2:
            override["aftermarket_additions"] = st.number_input(
                "Aftermarket additions (KES) *",
                min_value=0.0,
                value=float(override.get("aftermarket_additions", 0.0)),
                step=5000.0,
                help="Use 0 if none."
            )

    else:
        # Non-face-value: base price and (if imported) duty + profit margin
        if origin_type == "local":
            st.markdown("#### Local vehicle inputs")
            override["base_price"] = st.number_input(
                "Base Price (KES) *",
                min_value=0.0,
                value=float(override.get("base_price", 0.0)),
                step=10000.0,
                help="Kenyan dealer price at registration (required)."
            )

            # Usage type only needed if mileage thresholds trigger the cap logic.
            # Your current graph method uses > 10,000km to ask private/commercial.
            # We'll show it only when mileage is numeric and > 10k.
            if is_number(mileage) and float(mileage) > 10000:
                override["usage_type"] = st.selectbox(
                    "Usage type (required for high-mileage Graph Method) *",
                    ["private", "commercial"],
                    index=["private", "commercial"].index(override.get("usage_type", "private"))
                    if override.get("usage_type") in ["private", "commercial"] else 0
                )
            else:
                override["usage_type"] = override.get("usage_type", "private")

        elif origin_type == "imported":
            st.markdown("#### Imported vehicle inputs")
            override["base_price"] = st.number_input(
                "C&F / Base price *",
                min_value=0.0,
                value=float(override.get("base_price", 0.0)),
                step=1000.0,
                help="Provide CNF/C&F price (USD for younger imports, KES for older imports based on your logic)."
            )
            override["duty"] = st.number_input(
                "KRA duty (KES) *",
                min_value=0.0,
                value=float(override.get("duty", 0.0)),
                step=10000.0
            )
            override["profit_margin"] = st.number_input(
                "Profit margin (decimal) *",
                min_value=0.0,
                max_value=1.0,
                value=float(override.get("profit_margin", 0.10)),
                step=0.01,
                help="Example: 0.10 means 10%."
            )

        else:
            st.warning("Origin type could not be determined (local/imported). Please verify the report.", icon="⚠️")

    st.markdown("---")

    # ---------- Step 3: Generate ----------
    section_header("Step 3 — Generate Summary", "This will call GPT and produce the valuation notes output.")
    gen_col1, gen_col2 = st.columns([1, 1])

    with gen_col1:
        st.session_state.override_data = override

        # Basic front-end validation to prevent “empty required fields”
        validation_errors = []

        # demand level always present; if high/very high demand_adjust_percent must be > = 0 (can be 0 if user insists)
        if override.get("demand_level") in ["high", "very high"] and override.get("demand_adjust_percent") is None:
            validation_errors.append("Demand adjustment % is required when demand is high/very high.")

        if is_face_value:
            if to_float(override.get("face_value_average"), 0.0) <= 0:
                validation_errors.append("Face Value Average (KES) is required for manufacture age ≥ 14.")
            if override.get("aftermarket_additions") is None:
                validation_errors.append("Aftermarket additions is required (0 if none).")
        else:
            if origin_type in ["local", "imported"] and to_float(override.get("base_price"), 0.0) <= 0:
                validation_errors.append("Base price is required and must be > 0.")
            if origin_type == "imported":
                if override.get("duty") is None:
                    validation_errors.append("Duty is required for imported vehicles.")
                if override.get("profit_margin") is None:
                    validation_errors.append("Profit margin is required for imported vehicles.")
            if origin_type == "local" and is_number(mileage) and float(mileage) > 10000:
                if override.get("usage_type") not in ["private", "commercial"]:
                    validation_errors.append("Usage type is required for high mileage (private/commercial).")

        if validation_errors:
            st.error("Please fix the following before generating:\n- " + "\n- ".join(validation_errors))
            st.stop()

        if st.button("🚀 Generate GPT Valuation Summary", type="primary", use_container_width=True):
            with st.spinner("Generating summary with GPT..."):
                payload = dict(st.session_state.vehicle_data)   # start with full fetched context
                payload.update(override)                        # overlay user inputs

                # --- Recompute value basis if base_price is now provided ---
                try:
                    origin_type2 = (payload.get("origin_type") or "").lower()
                    reg_date2 = payload.get("registration_date") or ""
                    mileage2 = float(payload.get("mileage") or 0)
                    base_price2 = float(payload.get("base_price") or 0)

                    # local non-face-value: compute final_depreciated_value if missing
                    if origin_type2 == "local" and base_price2 > 0 and reg_date2:
                        from app.data_pipeline import calculate_local_value
                        fdv = calculate_local_value(payload, base_price2, mileage2, reg_date2)
                        payload["final_depreciated_value"] = fdv

                        # Optional: keep retention_percent_used aligned if you want
                        # (only if you want it displayed consistently)
                except Exception as e:
                    st.warning(f"Could not recompute final value: {e}")

                summary = generate_summary(
                    report_id=st.session_state.report_id,
                    override_data=payload
                )
            st.session_state.summary = summary

    with gen_col2:
        st.markdown("#### What will be sent to the model")
        st.code(
            {
                "report_id": st.session_state.report_id,
                "override_data": st.session_state.override_data
            },
            language="json"
        )

    st.write("")
    if st.session_state.summary:
        section_header("Result", "GPT output (copy/paste for your report).")
        st.markdown(st.session_state.summary)

        st.download_button(
            "Download summary as .txt",
            data=st.session_state.summary,
            file_name=f"valuation_summary_{st.session_state.report_id}.txt",
            mime="text/plain",
            use_container_width=True,
        )
else:
    section_header("Ready", "Enter a report ID above to begin.")
    st.info("Once you fetch a report, the wizard will guide you through the required inputs step-by-step.", icon="✅")

"""
app.py
------
Streamlit front end for the passport data extractor: upload a passport
photo/scan (image) or PDF, run OCR + MRZ decoding, and display every
extracted field on screen, from first name through the raw MRZ.

Run with:
    streamlit run src/app.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import streamlit as st
import pytesseract

from extractor import PassportRecord, process_file
from llm_extractor import GEMINI_MODEL_NAME, extract_passport_with_gemini

# On Windows, Tesseract's installer doesn't reliably add itself to PATH.
_DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if not shutil.which("tesseract") and _DEFAULT_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_DEFAULT_TESSERACT)

STATUS_EMOJI = {"ok": "🟢", "partial": "🟡", "failed": "🔴"}
CHECK_EMOJI = {True: "✅", False: "❌", None: "—"}
METHOD_LABELS = {"ocr": "Local OCR (Tesseract)", "gemini": f"Gemini API ({GEMINI_MODEL_NAME})"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
UPLOAD_TYPES = ["pdf"] + sorted(ext.lstrip(".") for ext in IMAGE_EXTENSIONS)

st.set_page_config(page_title="Passport Data Extractor", page_icon="🛂", layout="wide")

st.title("🛂 Passport Data Extractor")
st.caption(
    "Upload a passport photo, scan, or PDF. The bio-page MRZ is decoded and "
    "checksum-verified per ICAO 9303; every field is displayed below."
)

with st.sidebar:
    st.header("Extraction method")
    method = st.radio(
        "How should files be read?",
        ["Local OCR (free, offline)", "Gemini API (cloud, more accurate)"],
        help=(
            "Local OCR runs entirely on this machine via Tesseract. "
            "Gemini reads the document directly with a vision model, which "
            "handles varied passport layouts and scripts far better, but "
            "sends the image to Google's API."
        ),
    )
    use_gemini = method.startswith("Gemini")

    api_key = ""
    if use_gemini:
        st.caption(
            "Free API key from [Google AI Studio](https://aistudio.google.com/apikey). "
            "⚠️ Passport images are sent to Google's Gemini API in this mode."
        )
        api_key = st.text_input(
            "Gemini API key",
            value=os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")),
            type="password",
        )

uploaded = st.file_uploader(
    "Drop a passport image or PDF here",
    type=UPLOAD_TYPES,
    accept_multiple_files=True,
)

input_paths: list[Path] = []
if uploaded:
    upload_dir = Path(tempfile.mkdtemp(prefix="passport_extractor_upload_"))
    for f in uploaded:
        dest = upload_dir / f.name
        dest.write_bytes(f.getvalue())
        input_paths.append(dest)

run = st.button(
    "Extract data", type="primary",
    disabled=not input_paths or (use_gemini and not api_key),
)
if use_gemini and not api_key and input_paths:
    st.warning("Enter a Gemini API key in the sidebar to extract with Gemini.")

if run:
    records: list[PassportRecord] = []
    progress = st.progress(0.0)
    for i, path in enumerate(input_paths):
        with st.spinner(f"Processing {path.name}..."):
            if use_gemini:
                records.append(extract_passport_with_gemini(path, api_key))
            else:
                records.append(process_file(path))
        progress.progress((i + 1) / len(input_paths))
    progress.empty()
    st.session_state["records"] = records

records: list[PassportRecord] | None = st.session_state.get("records")

FIELD_LABELS = [
    ("given_names", "First / Given Name(s)"),
    ("surname", "Last Name / Surname"),
    ("full_name", "Full Name"),
    ("sex", "Sex"),
    ("date_of_birth", "Date of Birth"),
    ("nationality", "Nationality"),
    ("passport_number", "Passport Number"),
    ("issuing_country", "Issuing Country"),
    ("document_type", "Document Type"),
    ("date_of_expiry", "Date of Expiry"),
    ("personal_number", "Personal Number"),
    ("date_of_issue", "Date of Issue"),
    ("place_of_birth", "Place of Birth"),
    ("authority", "Issuing Authority"),
]

if records:
    ok = sum(1 for r in records if r.status == "ok")
    partial = sum(1 for r in records if r.status == "partial")
    failed = sum(1 for r in records if r.status == "failed")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Processed", len(records))
    c2.metric("OK", ok)
    c3.metric("Partial", partial)
    c4.metric("Failed", failed)

    st.divider()

    for record in records:
        status_badge = f"{STATUS_EMOJI.get(record.status, '')} {record.status.upper()}"
        with st.container(border=True):
            header_col, badge_col = st.columns([4, 1])
            header_col.subheader(record.file_name)
            badge_col.markdown(f"**{status_badge}**")
            st.caption(f"via {METHOD_LABELS.get(record.extraction_method, record.extraction_method)}")

            if record.notes:
                st.caption(record.notes)

            field_cols = st.columns(3)
            for i, (attr, label) in enumerate(FIELD_LABELS):
                value = getattr(record, attr) or "—"
                field_cols[i % 3].metric(label, value)

            if record.mrz_found:
                st.markdown("**MRZ (Machine Readable Zone)**")
                st.code(f"{record.mrz_line1}\n{record.mrz_line2}", language=None)

                valid_label = CHECK_EMOJI.get(record.mrz_valid, "—")
                st.markdown(f"Overall checksum valid: {valid_label}")

                check_cols = st.columns(len(record.mrz_checks) or 1)
                for i, (name, result) in enumerate(record.mrz_checks.items()):
                    check_cols[i].markdown(
                        f"{CHECK_EMOJI.get(result, '—')} {name.replace('_', ' ')}"
                    )
            else:
                st.info("No MRZ located — fields above (if any) came from the visual zone.")

            if record.extraction_method == "ocr":
                with st.expander("Raw OCR text"):
                    st.code(record.raw_text_preview or "(no text extracted)")
else:
    st.info("Upload one or more passport images or PDFs and click **Extract data** to get started.")

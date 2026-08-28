"""
llm_extractor.py
----------------
Gemini-backed extraction for passport bio pages.

OCR + regex struggles across the huge variety of real-world passport
layouts, scripts, and photo quality. A vision-capable LLM is far better at
the actual reading than Tesseract, but an LLM's own free-text field guesses
aren't independently checkable. So this module asks Gemini only to
*transcribe* the document accurately (structured output keeps this
reliable), and then hands the two MRZ lines it read off to the exact same
ICAO 9303 checksum-verified decoder used by the local OCR path
(extractor.build_record_from_mrz_pair) — the trust layer doesn't care
whether the raw characters came from Tesseract or Gemini.

Requires a free Gemini API key from https://aistudio.google.com/apikey,
passed in or set as the GEMINI_API_KEY / GOOGLE_API_KEY environment
variable. Passport images are sent to Google's API in this mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from extractor import PassportRecord, _clean_and_pad_mrz_line, build_record_from_mrz_pair

GEMINI_MODEL_NAME = "gemini-3.7-flash"

_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_PROMPT = """You are transcribing a passport bio page. Read every field exactly as \
printed — do not correct, normalize, guess, or auto-complete anything.

For the two-line Machine Readable Zone (MRZ) at the bottom of the page, \
transcribe it character-for-character exactly as printed, including every \
'<' filler character. Do not add, drop, or "fix" any character even if it \
looks like a typo — an incorrect transcription here must look wrong, not be \
silently corrected. If the MRZ is not visible or not present, leave those \
two fields empty.

For every other field, use what's printed on the visual (non-MRZ) part of \
the page. Leave a field empty (not a placeholder like "N/A") if it isn't \
present or legible. Dates should be in YYYY-MM-DD format where you can \
determine the full date; otherwise transcribe as printed."""


class PassportLLMFields(BaseModel):
    document_type: Optional[str] = None
    issuing_country: Optional[str] = None
    surname: Optional[str] = None
    given_names: Optional[str] = None
    sex: Optional[str] = None
    nationality: Optional[str] = None
    date_of_birth: Optional[str] = None
    date_of_expiry: Optional[str] = None
    date_of_issue: Optional[str] = None
    place_of_birth: Optional[str] = None
    passport_number: Optional[str] = None
    personal_number: Optional[str] = None
    authority: Optional[str] = None
    mrz_line1: Optional[str] = None
    mrz_line2: Optional[str] = None


class GeminiExtractionError(Exception):
    """Raised when the Gemini API call or response parsing fails."""


def _call_gemini(file_path: Path, api_key: str, *, model: str = GEMINI_MODEL_NAME) -> PassportLLMFields:
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiExtractionError(
            "google-genai is not installed. Run: pip install google-genai"
        ) from exc

    mime_type = _MIME_TYPES.get(file_path.suffix.lower())
    if mime_type is None:
        raise GeminiExtractionError(f"Unsupported file type for Gemini: {file_path.suffix}")

    content_type = "document" if mime_type == "application/pdf" else "image"

    try:
        client = genai.Client(api_key=api_key)

        # Passing the path directly (rather than raw bytes) lets the SDK read
        # and base64-encode the file itself — passing bytes straight through
        # does *not* get base64-encoded and silently corrupts the upload.
        interaction = client.interactions.create(
            model=model,
            input=[
                {"type": "text", "text": _PROMPT},
                {"type": content_type, "data": file_path, "mime_type": mime_type},
            ],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": PassportLLMFields.model_json_schema(),
            },
        )
        return PassportLLMFields.model_validate_json(interaction.output_text)
    except GeminiExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any API/parse failure on the record
        raise GeminiExtractionError(f"Gemini API error: {exc}") from exc


def _record_from_visual_fields(fields: PassportLLMFields, file_name: str) -> PassportRecord:
    record = PassportRecord(file_name=file_name, extraction_method="gemini")
    data = fields.model_dump(exclude={"mrz_line1", "mrz_line2"})
    for key, value in data.items():
        if not value:
            continue
        if key == "sex":
            value = {"M": "Male", "F": "Female"}.get(value.upper(), value)
        setattr(record, key, value)

    if record.given_names or record.surname:
        record.full_name = " ".join(
            part for part in (record.given_names, record.surname) if part
        ).strip()

    has_any_field = any(data.values())
    if has_any_field:
        record.status = "partial"
        record.notes = "MRZ not read by the model; fields from the visual zone only (lower confidence)."
    else:
        record.status = "failed"
        record.notes = "Gemini could not read any recognizable passport fields from this file."
    return record


def extract_passport_with_gemini(
    file_path: Path,
    api_key: str,
    *,
    model: str = GEMINI_MODEL_NAME,
) -> PassportRecord:
    """End-to-end: send the file to Gemini, then decode+verify any MRZ it read."""
    if not api_key:
        record = PassportRecord(file_name=file_path.name, extraction_method="gemini", status="failed")
        record.notes = "No Gemini API key provided."
        return record

    try:
        fields = _call_gemini(file_path, api_key, model=model)
    except GeminiExtractionError as exc:
        record = PassportRecord(file_name=file_path.name, extraction_method="gemini", status="failed")
        record.notes = str(exc)
        return record

    if fields.mrz_line1 and fields.mrz_line2:
        line1, exact1 = _clean_and_pad_mrz_line(fields.mrz_line1)
        line2, exact2 = _clean_and_pad_mrz_line(fields.mrz_line2)
        # A real MRZ line 1 always has the mandatory surname/given-names "<<"
        # separator; if that's missing the model likely mistranscribed
        # something else as the MRZ, so fall back to its visual-zone fields.
        if line1.startswith("P") and line1.count("<") >= 2:
            record = build_record_from_mrz_pair(
                line1, line2, file_path.name,
                exact_length=exact1 and exact2,
                extraction_method="gemini",
            )
            # Visual-zone-only fields the MRZ doesn't encode.
            for key in ("date_of_issue", "place_of_birth", "authority"):
                value = getattr(fields, key, None)
                if value:
                    setattr(record, key, value)
            return record

    return _record_from_visual_fields(fields, file_path.name)

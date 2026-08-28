"""
extractor.py
------------
Core logic for pulling text out of passport images/PDFs and decoding
structured identity data out of that text.

Two extraction paths are supported:
  1. Native text extraction via pdfplumber (rare for passports, but kept
     for consistency when someone uploads a digitally-generated PDF).
  2. OCR via pdf2image + pytesseract, used for photos/scans, which is the
     common case for passport bio pages.

Two data paths are supported once text is available:
  1. MRZ (Machine Readable Zone) decoding — the two 44-character lines at
     the bottom of a passport bio page, per ICAO Doc 9303. This is
     authoritative, checksum-verified, and doesn't depend on layout, so it
     is preferred whenever it can be found.
  2. A regex fallback over the "visual zone" (the printed labels above the
     MRZ) for when the MRZ can't be located or OCR'd cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber
from pdf2image import convert_from_path
import pytesseract
from PIL import Image, ImageOps

# Minimum characters of native text before we consider a PDF page "readable"
# without falling back to OCR.
MIN_NATIVE_TEXT_LEN = 20

MRZ_LINE_LEN = 44
_MRZ_CHAR_RE = re.compile(r"^[A-Z0-9<]+$")

_SEX_LABELS = {"M": "Male", "F": "Female"}


@dataclass
class PassportRecord:
    """Structured result for a single processed passport file."""

    file_name: str

    document_type: Optional[str] = None
    issuing_country: Optional[str] = None
    surname: Optional[str] = None
    given_names: Optional[str] = None
    full_name: Optional[str] = None
    passport_number: Optional[str] = None
    nationality: Optional[str] = None
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None
    date_of_expiry: Optional[str] = None
    personal_number: Optional[str] = None
    date_of_issue: Optional[str] = None
    place_of_birth: Optional[str] = None
    authority: Optional[str] = None

    mrz_line1: Optional[str] = None
    mrz_line2: Optional[str] = None
    mrz_found: bool = False
    mrz_checks: dict = field(default_factory=dict)
    mrz_valid: Optional[bool] = None

    used_ocr: bool = False
    extraction_method: str = "ocr"  # "ocr" | "gemini"
    status: str = "ok"  # "ok" | "partial" | "failed"
    notes: str = ""
    raw_text_preview: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> tuple[str, bool]:
    """Return (text, used_ocr) for the given PDF file."""
    text_chunks: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_chunks.append(page_text)
    except Exception:
        text_chunks = []

    native_text = "\n".join(text_chunks).strip()

    if len(native_text) >= MIN_NATIVE_TEXT_LEN and _find_mrz_lines(native_text):
        return native_text, False

    ocr_text = _ocr_pdf(pdf_path)
    # Prefer whichever extraction actually found an MRZ; otherwise prefer OCR
    # since passport PDFs are almost always scanned images.
    if not ocr_text and native_text:
        return native_text, False
    return ocr_text, True


def _ocr_pdf(pdf_path: Path) -> str:
    try:
        images = convert_from_path(str(pdf_path), dpi=300)
    except Exception:
        return ""

    text_chunks = [_ocr_image(image) for image in images]
    return "\n".join(text_chunks).strip()


def _ocr_image(image: Image.Image) -> str:
    """OCR a single image, with light preprocessing to help MRZ recognition."""
    processed = ImageOps.grayscale(image)
    processed = ImageOps.autocontrast(processed)
    return pytesseract.image_to_string(processed)


def extract_text_from_image(image_path: Path) -> str:
    with Image.open(image_path) as image:
        return _ocr_image(image)


# ---------------------------------------------------------------------------
# MRZ (Machine Readable Zone) decoding — ICAO Doc 9303, TD3 (passport) format
# ---------------------------------------------------------------------------

def _clean_mrz_line(line: str) -> str:
    return re.sub(r"[^A-Za-z0-9<]", "", line).upper()


# Real MRZ lines are a single unbroken token of A-Z0-9< with no internal
# spaces — printed header/label text ("Passport Type : P Country Code : SAU")
# is space-heavy. If we strip spaces from a label line before checking it, it
# can collapse into something that coincidentally matches the MRZ charset, so
# we screen out space-heavy lines using the *original* text, before cleaning.
_MAX_SPACE_RATIO = 0.15


def _mrz_candidates(text: str) -> list[str]:
    candidates = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.count(" ") / len(stripped) > _MAX_SPACE_RATIO:
            continue
        cleaned = _clean_mrz_line(stripped)
        if len(cleaned) >= 40 and _MRZ_CHAR_RE.match(cleaned):
            candidates.append(cleaned)
    return candidates


def _clean_and_pad_mrz_line(line: str) -> tuple[str, bool]:
    """Clean a raw MRZ line and pad/truncate it to 44 chars.

    Returns (padded_line, exact_length) where exact_length is False if the
    cleaned line wasn't already exactly 44 characters. Padding/truncating a
    misaligned line to force it to 44 chars is a guess — a dropped or extra
    character anywhere in the line shifts every fixed-width field after it,
    so callers must treat exact_length=False as "field alignment unverified",
    not silently trust the padded result.
    """
    cleaned = _clean_mrz_line(line)
    exact_length = len(cleaned) == MRZ_LINE_LEN
    padded = (cleaned + "<" * MRZ_LINE_LEN)[:MRZ_LINE_LEN]
    return padded, exact_length


def _find_mrz_lines(text: str) -> Optional[tuple[str, str, bool]]:
    """Locate the two 44-char TD3 MRZ lines in a block of OCR'd text.

    Returns (line1, line2, exact_length) — see _clean_and_pad_mrz_line.
    """
    candidates = _mrz_candidates(text)

    for i in range(len(candidates) - 1):
        line1, line2 = candidates[i], candidates[i + 1]
        # The surname/given-names separator "<<" is mandatory in line 1 per
        # ICAO 9303, even for a maximally long name — a real MRZ line 1 always
        # has it. This is what rules out stripped-of-spaces label text.
        if line1.startswith("P") and line1.count("<") >= 2:
            line1, exact1 = _clean_and_pad_mrz_line(line1)
            line2, exact2 = _clean_and_pad_mrz_line(line2)
            return line1, line2, exact1 and exact2
    return None


def _char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if ch == "<":
        return 0
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    return 0


def _check_digit(data: str) -> int:
    weights = (7, 3, 1)
    total = sum(_char_value(ch) * weights[i % 3] for i, ch in enumerate(data))
    return total % 10


def _verify(data: str, check_char: str) -> Optional[bool]:
    """Verify an MRZ check digit.

    Returns None only when the field is legitimately unused (both the data
    and its check digit are filler) — e.g. an optional personal number left
    blank. A non-digit check character paired with *non-blank* data is not
    "not applicable", it means something is broken (OCR noise, a misaligned
    read), so that case must return False rather than being reported as
    inconclusive.
    """
    data_is_blank = set(data) == {"<"}
    if not check_char.isdigit():
        return None if data_is_blank else False
    if data_is_blank:
        return None
    return _check_digit(data) == int(check_char)


def _parse_mrz_date(raw: str, *, is_birth: bool) -> Optional[str]:
    if not re.match(r"^\d{6}$", raw):
        return None
    yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    if is_birth:
        current_yy = datetime.now().year % 100
        century = 1900 if yy > current_yy else 2000
    else:
        century = 2000
    try:
        return datetime(century + yy, mm, dd).strftime("%Y-%m-%d")
    except ValueError:
        return raw


def _strip_ocr_filler_artifacts(name: str) -> str:
    """Drop trailing name tokens that are a single character repeated 4+
    times — a run of '<' padding chevrons that OCR misread as a letter (most
    often 'K') rather than genuine name text, which real names never contain."""
    tokens = name.split()
    while tokens and len(tokens[-1]) >= 4 and len(set(tokens[-1])) == 1:
        tokens.pop()
    return " ".join(tokens)


def decode_mrz(line1: str, line2: str) -> tuple[dict, dict]:
    """Decode a TD3 MRZ pair into fields, plus a dict of per-field check results."""
    doc_type = line1[0:2].replace("<", "").strip() or line1[0]
    issuing_country = line1[2:5].replace("<", "")

    name_field = line1[5:44]
    surname_raw, _, given_raw = name_field.partition("<<")
    surname = _strip_ocr_filler_artifacts(re.sub(r"<+", " ", surname_raw).strip())
    given_names = _strip_ocr_filler_artifacts(re.sub(r"<+", " ", given_raw).strip())

    passport_number_raw = line2[0:9]
    passport_number_check = line2[9]
    passport_number = passport_number_raw.replace("<", "").strip()

    nationality = line2[10:13].replace("<", "")

    dob_raw = line2[13:19]
    dob_check = line2[19]
    date_of_birth = _parse_mrz_date(dob_raw, is_birth=True)

    sex_char = line2[20]
    sex = _SEX_LABELS.get(sex_char, "Unspecified")

    expiry_raw = line2[21:27]
    expiry_check = line2[27]
    date_of_expiry = _parse_mrz_date(expiry_raw, is_birth=False)

    personal_number_raw = line2[28:42]
    personal_number_check = line2[42]
    personal_number = personal_number_raw.replace("<", "").strip()

    composite_check_char = line2[43]
    composite_data = line2[0:10] + line2[13:20] + line2[21:43]

    fields = {
        "document_type": doc_type,
        "issuing_country": issuing_country,
        "surname": surname or None,
        "given_names": given_names or None,
        "full_name": f"{given_names} {surname}".strip() or None,
        "passport_number": passport_number or None,
        "nationality": nationality or None,
        "date_of_birth": date_of_birth,
        "sex": sex,
        "date_of_expiry": date_of_expiry,
        "personal_number": personal_number or None,
    }

    checks = {
        "passport_number": _verify(passport_number_raw, passport_number_check),
        "date_of_birth": _verify(dob_raw, dob_check),
        "date_of_expiry": _verify(expiry_raw, expiry_check),
        "personal_number": _verify(personal_number_raw, personal_number_check),
        "composite": _verify(composite_data, composite_check_char),
    }

    return fields, checks


# ---------------------------------------------------------------------------
# Visual zone fallback (used only when no MRZ can be located)
# ---------------------------------------------------------------------------

_VISUAL_PATTERNS = {
    "surname": r"(?:surname|last\s*name)[ \t]*[:\-]?[ \t]*([A-Z][A-Za-z'\-]+(?:[ \t][A-Z][A-Za-z'\-]+)*)",
    "given_names": r"(?:given\s*names?|first\s*name)[ \t]*[:\-]?[ \t]*([A-Z][A-Za-z'\-]+(?:[ \t][A-Z][A-Za-z'\-]+)*)",
    "passport_number": r"(?:passport\s*(?:no\.?|number)|document\s*no\.?)\s*[:\-]?\s*([A-Z0-9]{6,9})",
    "nationality": r"nationality\s*[:\-]?\s*([A-Z][A-Za-z]+)",
    "sex": r"\bsex\s*[:\-]?\s*([MF])\b",
    "date_of_birth": r"(?:date\s*of\s*birth|born|dob)\s*[:\-]?\s*(\d{1,2}[\s/\-][A-Za-z0-9]{2,9}[\s/\-]\d{2,4})",
    "date_of_expiry": r"(?:date\s*of\s*expiry|expir\w*)\s*[:\-]?\s*(\d{1,2}[\s/\-][A-Za-z0-9]{2,9}[\s/\-]\d{2,4})",
}


def _parse_visual_date(raw: str) -> Optional[str]:
    formats = ["%d/%m/%Y", "%d-%m-%Y", "%d %m %Y", "%m/%d/%Y", "%Y-%m-%d",
               "%d %b %Y", "%d %B %Y", "%d/%m/%y"]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _parse_visual_zone(text: str) -> dict:
    fields: dict = {}
    for name, pattern in _VISUAL_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        if name in ("date_of_birth", "date_of_expiry"):
            value = _parse_visual_date(value)
        elif name == "sex":
            value = _SEX_LABELS.get(value.upper(), value)
        fields[name] = value
    if fields.get("given_names") or fields.get("surname"):
        fields["full_name"] = " ".join(
            part for part in (fields.get("given_names"), fields.get("surname")) if part
        ).strip() or None
    return fields


# ---------------------------------------------------------------------------
# End-to-end record building
# ---------------------------------------------------------------------------

def build_record_from_mrz_pair(
    line1: str,
    line2: str,
    file_name: str,
    *,
    exact_length: bool = True,
    extraction_method: str = "ocr",
) -> PassportRecord:
    """Build a checksum-verified PassportRecord from an already-cleaned,
    already-padded-to-44-chars MRZ pair (see _clean_and_pad_mrz_line).

    Shared by both extraction backends: the local OCR pipeline (which has to
    locate the MRZ lines itself via _find_mrz_lines) and the Gemini backend
    (which is told by the model which two lines are the MRZ). Either way, the
    ICAO 9303 checksum math is the same objective trust layer regardless of
    how the raw characters were read off the page. `exact_length` must be
    computed by the caller *before* padding — padding always yields exactly
    44 chars, so it can't be recovered from line1/line2 at this point.
    """
    record = PassportRecord(file_name=file_name, extraction_method=extraction_method)

    fields, checks = decode_mrz(line1, line2)

    record.mrz_line1 = line1
    record.mrz_line2 = line2
    record.mrz_found = True
    record.mrz_checks = checks
    for key, value in fields.items():
        setattr(record, key, value)

    relevant_checks = [v for v in checks.values() if v is not None]
    record.mrz_valid = all(relevant_checks) if relevant_checks else None

    notes = []
    if not exact_length:
        # A dropped/extra character anywhere in the line shifts every
        # fixed-width field after it — the checksums below will usually
        # catch this, but call it out explicitly since it's the actual
        # root cause, not just "a checksum happened to fail".
        record.status = "partial"
        notes.append(
            "MRZ line length did not match the expected 44 characters "
            "(a character was likely dropped or added during reading) — "
            "decoded fields may be shifted; verify against the printed page."
        )

    missing = [name for name in ("surname", "given_names", "passport_number",
                                  "date_of_birth", "date_of_expiry")
               if not getattr(record, name)]
    if missing:
        record.status = "partial"
        notes.append(f"MRZ decoded but missing: {', '.join(missing)}")
    elif record.mrz_valid is False:
        record.status = "partial"
        failed_checks = [k for k, v in checks.items() if v is False]
        notes.append(f"MRZ checksum mismatch on: {', '.join(failed_checks)}")

    record.notes = " ".join(notes)
    return record


def parse_fields(text: str, file_name: str, used_ocr: bool) -> PassportRecord:
    record = PassportRecord(file_name=file_name, used_ocr=used_ocr)
    record.raw_text_preview = text[:500]

    if not text.strip():
        record.status = "failed"
        record.notes = "No text could be extracted (empty or unreadable file)."
        return record

    mrz_pair = _find_mrz_lines(text)

    if mrz_pair:
        line1, line2, exact_length = mrz_pair
        mrz_record = build_record_from_mrz_pair(line1, line2, file_name, exact_length=exact_length)
        mrz_record.used_ocr = used_ocr
        mrz_record.raw_text_preview = record.raw_text_preview
        return mrz_record

    # No MRZ located — fall back to the printed visual zone.
    visual_fields = _parse_visual_zone(text)
    for key, value in visual_fields.items():
        setattr(record, key, value)

    if visual_fields:
        record.status = "partial"
        record.notes = "MRZ not found; fields extracted from visual zone only (lower confidence)."
    else:
        record.status = "failed"
        record.notes = "Could not locate MRZ or recognizable passport fields."

    return record


def process_file(path: Path) -> PassportRecord:
    """End-to-end: extract text (OCR for images, native+OCR for PDFs), then parse."""
    try:
        if path.suffix.lower() == ".pdf":
            text, used_ocr = extract_text_from_pdf(path)
        else:
            text = extract_text_from_image(path)
            used_ocr = True
    except Exception as exc:  # noqa: BLE001 - report any failure on the record
        record = PassportRecord(file_name=path.name, status="failed")
        record.notes = f"Extraction error: {exc}"
        return record

    return parse_fields(text, path.name, used_ocr)

"""
Unit tests for llm_extractor.py.

These never call the real Gemini API — _call_gemini is monkeypatched so the
tests only exercise: (1) that a returned MRZ pair is routed through the same
checksum-verified decoder as the OCR path, and (2) the visual-zone fallback
and error-handling behavior when no usable MRZ is returned.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import llm_extractor  # noqa: E402
from llm_extractor import (  # noqa: E402
    GeminiExtractionError,
    PassportLLMFields,
    extract_passport_with_gemini,
)

# Reuse the known-good ICAO worked example.
MRZ_LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
MRZ_LINE2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def test_extract_uses_mrz_checksum_path_when_model_reads_mrz(monkeypatch, tmp_path):
    monkeypatch.setattr(
        llm_extractor,
        "_call_gemini",
        lambda *a, **kw: PassportLLMFields(mrz_line1=MRZ_LINE1, mrz_line2=MRZ_LINE2),
    )
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.extraction_method == "gemini"
    assert record.mrz_found is True
    assert record.mrz_valid is True
    assert record.status == "ok"
    assert record.surname == "ERIKSSON"
    assert record.given_names == "ANNA MARIA"
    assert record.passport_number == "L898902C3"
    assert record.date_of_expiry == "2012-04-15"


def test_extract_flags_tampered_mrz_from_model(monkeypatch, tmp_path):
    tampered_line2 = MRZ_LINE2[:9] + "9" + MRZ_LINE2[10:]
    monkeypatch.setattr(
        llm_extractor,
        "_call_gemini",
        lambda *a, **kw: PassportLLMFields(mrz_line1=MRZ_LINE1, mrz_line2=tampered_line2),
    )
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.mrz_valid is False
    assert record.status == "partial"


def test_extract_falls_back_to_visual_fields_without_mrz(monkeypatch, tmp_path):
    monkeypatch.setattr(
        llm_extractor,
        "_call_gemini",
        lambda *a, **kw: PassportLLMFields(
            surname="SMITH",
            given_names="JOHN",
            sex="M",
            nationality="AMERICAN",
            passport_number="X1234567",
        ),
    )
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.mrz_found is False
    assert record.status == "partial"
    assert record.extraction_method == "gemini"
    assert record.surname == "SMITH"
    assert record.given_names == "JOHN"
    assert record.sex == "Male"
    assert record.full_name == "JOHN SMITH"


def test_extract_marks_failed_when_model_returns_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_extractor, "_call_gemini", lambda *a, **kw: PassportLLMFields())
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.status == "failed"


def test_extract_surfaces_api_errors_without_raising(monkeypatch, tmp_path):
    def raise_error(*a, **kw):
        raise GeminiExtractionError("Gemini API error: 429 rate limited")

    monkeypatch.setattr(llm_extractor, "_call_gemini", raise_error)
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.status == "failed"
    assert "rate limited" in record.notes


def test_extract_requires_api_key(tmp_path):
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="")

    assert record.status == "failed"
    assert "API key" in record.notes


def test_extract_ignores_garbage_mrz_and_falls_back(monkeypatch, tmp_path):
    # Model hallucinated something into mrz_line1/2 that isn't a real MRZ
    # (missing the mandatory "<<" name separator) — must not be decoded as
    # if it were trustworthy, must fall back to visual-zone fields instead.
    monkeypatch.setattr(
        llm_extractor,
        "_call_gemini",
        lambda *a, **kw: PassportLLMFields(
            mrz_line1="NOT REALLY AN MRZ LINE AT ALL HERE",
            mrz_line2="ALSO NOT ONE",
            surname="FALLBACK",
        ),
    )
    file_path = tmp_path / "passport.jpg"
    file_path.write_bytes(b"fake image bytes")

    record = extract_passport_with_gemini(file_path, api_key="fake-key")

    assert record.mrz_found is False
    assert record.surname == "FALLBACK"

"""
Unit tests for MRZ decoding and field-parsing logic in extractor.py.
Run with: pytest tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from extractor import (  # noqa: E402
    decode_mrz,
    parse_fields,
    _check_digit,
    _find_mrz_lines,
    _strip_ocr_filler_artifacts,
    _verify,
)

# Classic ICAO Doc 9303 worked example (Anna Maria Eriksson).
MRZ_LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
MRZ_LINE2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"

MRZ_TEXT = f"""
REPUBLIC OF UTOPIA
PASSPORT

Surname: ERIKSSON
Given Names: ANNA MARIA

{MRZ_LINE1}
{MRZ_LINE2}
"""

# Regression: a printed bilingual header line, once spaces/colons are OCR'd
# away, can collapse into something that coincidentally matches the MRZ
# charset and length — this must not be picked over the real MRZ below it.
LABEL_LINE_FALSE_POSITIVE_TEXT = f"""
Passport Type : P Country Code : SAU Passport No : D183145
Name: H.H. Prince ADEEB BIN SAUD BIN THINYYAN AL SAUD
Date Of Birth 01/02/1971 Sex M Place Of Birth Riyadh

{MRZ_LINE1}
{MRZ_LINE2}
"""

# Regression: a real Chinese passport where OCR dropped the leading "E" of
# the passport number in line 2 (shifting every fixed-width field after it
# by one character) and misread trailing "<" filler chevrons in line 1 as a
# run of "K" letters.
CN_MRZ_LINE1_CORRECT = "POCHNMA<<YIMING" + "<" * 29
CN_MRZ_LINE1_CORRUPTED = "POCHNMA<<YIMING" + "<" * 18 + "K" * 9 + "<" * 2
CN_MRZ_LINE2_CORRECT = "EJ43556345CHN1101111M2512299MCONNCMBMDPHAO86"
CN_MRZ_LINE2_MISSING_LEADING_CHAR = "J43556345CHN1101111M2512299MCONNCMBMDPHAO86"

VISUAL_ONLY_TEXT = """
PASSPORT
Surname: SMITH
Given Names: JOHN
Nationality: AMERICAN
Sex: M
Date of Birth: 15 06 1985
Passport No: X1234567
"""


def test_check_digit_matches_icao_example():
    # Passport number field "L898902C3" with check digit "6" from the spec.
    assert _check_digit("L898902C3<") == 6


def test_find_mrz_lines_locates_pair_in_noisy_text():
    line1, line2, exact_length = _find_mrz_lines(MRZ_TEXT)
    assert line1 == MRZ_LINE1
    assert line2 == MRZ_LINE2
    assert exact_length is True


def test_find_mrz_lines_ignores_space_stripped_label_text():
    line1, line2, exact_length = _find_mrz_lines(LABEL_LINE_FALSE_POSITIVE_TEXT)
    assert line1 == MRZ_LINE1
    assert line2 == MRZ_LINE2
    assert exact_length is True


def test_find_mrz_lines_flags_dropped_character_as_inexact():
    text = f"{CN_MRZ_LINE1_CORRUPTED}\n{CN_MRZ_LINE2_MISSING_LEADING_CHAR}"
    line1, line2, exact_length = _find_mrz_lines(text)
    assert exact_length is False
    # Padded back to 44 chars, but the leading char is gone and everything
    # after it in line2 is now shifted.
    assert line2.startswith("J43556345CHN")


def test_decode_mrz_extracts_expected_fields():
    fields, checks = decode_mrz(MRZ_LINE1, MRZ_LINE2)
    assert fields["surname"] == "ERIKSSON"
    assert fields["given_names"] == "ANNA MARIA"
    assert fields["issuing_country"] == "UTO"
    assert fields["passport_number"] == "L898902C3"
    assert fields["nationality"] == "UTO"
    assert fields["date_of_birth"] == "1974-08-12"
    assert fields["sex"] == "Female"
    assert fields["date_of_expiry"] == "2012-04-15"
    assert all(checks.values())


def test_parse_fields_full_pipeline_ok_status():
    record = parse_fields(MRZ_TEXT, "passport.png", used_ocr=True)
    assert record.status == "ok"
    assert record.mrz_found is True
    assert record.mrz_valid is True
    assert record.given_names == "ANNA MARIA"
    assert record.surname == "ERIKSSON"
    assert record.full_name == "ANNA MARIA ERIKSSON"


def test_parse_fields_detects_tampered_checksum():
    tampered_line2 = MRZ_LINE2[:9] + "9" + MRZ_LINE2[10:]  # corrupt passport check digit
    text = f"{MRZ_LINE1}\n{tampered_line2}"
    record = parse_fields(text, "passport.png", used_ocr=True)
    assert record.mrz_found is True
    assert record.mrz_checks["passport_number"] is False
    assert record.mrz_valid is False
    assert record.status == "partial"


def test_parse_fields_falls_back_to_visual_zone_without_mrz():
    record = parse_fields(VISUAL_ONLY_TEXT, "passport.png", used_ocr=True)
    assert record.mrz_found is False
    assert record.status == "partial"
    assert record.surname == "SMITH"
    assert record.given_names == "JOHN"
    assert record.sex == "Male"
    assert record.passport_number == "X1234567"


def test_verify_returns_false_not_none_when_data_present_but_check_char_broken():
    # A non-digit check character with real (non-filler) data means the read
    # is broken, not "this optional field is unused" — must not be masked as None.
    assert _verify("J43556345", "C") is False


def test_verify_returns_none_only_when_field_genuinely_unused():
    assert _verify("<" * 14, "<") is None


def test_strip_ocr_filler_artifacts_drops_trailing_repeated_letter_run():
    # Trailing "<" padding chevrons OCR'd as a run of one repeated letter.
    assert _strip_ocr_filler_artifacts("YIMING KKKKKKKKK") == "YIMING"
    assert _strip_ocr_filler_artifacts("ANNA MARIA") == "ANNA MARIA"


def test_parse_fields_flags_dropped_character_shift():
    text = f"{CN_MRZ_LINE1_CORRUPTED}\n{CN_MRZ_LINE2_MISSING_LEADING_CHAR}"
    record = parse_fields(text, "passport.jpg", used_ocr=True)

    assert record.mrz_found is True
    assert record.status == "partial"
    assert "44 characters" in record.notes
    # The garbage "K" run must not leak into the given name.
    assert record.given_names == "YIMING"
    # With the E dropped, the checksum layer must catch the corruption
    # instead of reporting it as merely "not applicable".
    assert record.mrz_valid is False
    assert record.mrz_checks["passport_number"] is False


def test_parse_fields_decodes_correctly_formed_cn_mrz():
    text = f"{CN_MRZ_LINE1_CORRECT}\n{CN_MRZ_LINE2_CORRECT}"
    record = parse_fields(text, "passport.jpg", used_ocr=True)

    # No length-mismatch warning when both lines are the expected 44 chars.
    assert "44 characters" not in record.notes
    assert record.given_names == "YIMING"
    assert record.surname == "MA"
    assert record.passport_number == "EJ4355634"
    assert record.nationality == "CHN"
    assert record.date_of_birth == "2011-01-11"
    assert record.sex == "Male"
    assert record.date_of_expiry == "2025-12-29"
    assert record.mrz_checks["passport_number"] is True
    assert record.mrz_checks["date_of_birth"] is True
    assert record.mrz_checks["date_of_expiry"] is True


def test_empty_text_marks_failed():
    record = parse_fields("", "blank.png", used_ocr=True)
    assert record.status == "failed"


def test_no_mrz_no_visual_fields_marks_failed():
    record = parse_fields("some unrelated scanned gibberish", "blank.png", used_ocr=True)
    assert record.status == "failed"

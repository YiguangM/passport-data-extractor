"""
main.py
-------
CLI entry point for the Passport Data Extractor.

Usage:
    python src/main.py --input sample_passports --output output/report.xlsx
    python src/main.py -i sample_passports -o output/report.xlsx -v
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from extractor import process_file
from llm_extractor import extract_passport_with_gemini
from report import write_report

INPUT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract structured identity data from passport images/PDFs "
                    "(MRZ-decoded and checksum-verified) into a single Excel report."
    )
    parser.add_argument(
        "-i", "--input", type=Path, default=Path("sample_passports"),
        help="Folder containing passport images/PDFs (default: sample_passports)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("output/report.xlsx"),
        help="Path to write the Excel report to (default: output/report.xlsx)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print per-file extraction status as it runs.",
    )
    parser.add_argument(
        "--gemini", action="store_true",
        help="Extract via the Gemini API instead of local OCR (needs GEMINI_API_KEY "
             "or GOOGLE_API_KEY set, or --api-key). Sends files to Google's API.",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Gemini API key (overrides the GEMINI_API_KEY/GOOGLE_API_KEY env vars).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.input.exists():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        return 1

    files = sorted(p for p in args.input.iterdir() if p.suffix.lower() in INPUT_EXTENSIONS)
    if not files:
        print(f"No passport images/PDFs found in {args.input}", file=sys.stderr)
        return 1

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if args.gemini and not api_key:
        print("--gemini requires an API key: pass --api-key or set GEMINI_API_KEY.", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for path in files:
        record = extract_passport_with_gemini(path, api_key) if args.gemini else process_file(path)
        records.append(record)
        if args.verbose:
            ocr_tag = " [OCR]" if record.used_ocr else ""
            mrz_tag = " [MRZ]" if record.mrz_found else ""
            print(f"[{record.status.upper():7s}]{ocr_tag}{mrz_tag} {path.name} "
                  f"-> name={record.full_name!r} passport_no={record.passport_number!r}")

    write_report(records, args.output)

    ok = sum(1 for r in records if r.status == "ok")
    partial = sum(1 for r in records if r.status == "partial")
    failed = sum(1 for r in records if r.status == "failed")
    print(f"\nProcessed {len(records)} file(s): {ok} ok, {partial} partial, "
          f"{failed} failed.")
    print(f"Report written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

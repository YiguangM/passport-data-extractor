# Passport Data Extractor

Upload a passport bio-page — as a photo, scan, or PDF — and get every field
extracted and displayed on screen: names, sex, date of birth, nationality,
passport number, issuing country, and dates of expiry, plus the raw MRZ.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## Why this exists

Every ICAO-compliant passport encodes its bio-page data twice: once in
print, and once in the two 44-character lines at the bottom (the Machine
Readable Zone). The MRZ is standardized, checksum-protected, and doesn't
depend on layout or font — so instead of guessing fields with regex over a
photo of arbitrary text, this tool decodes the MRZ directly and verifies
it's internally consistent before trusting it.

## What it does

Two extraction backends, selectable in the app sidebar or via CLI flag:

- **Local OCR** (`pytesseract` + `pdf2image`, `pdfplumber` for native-text
  PDFs) — free, fully offline, but Tesseract's character-level reads can
  struggle with varied fonts, scripts, and photo quality across the huge
  range of real-world passport layouts.
- **Gemini API** (`google-genai`) — sends the document to a Gemini vision
  model, which reads it far more reliably across formats and languages.
  Requires a free API key from
  [Google AI Studio](https://aistudio.google.com/apikey). **Passport images
  are sent to Google's API in this mode** — use local OCR if that's not
  acceptable for your data.

Either way, the two MRZ lines that come back (from Tesseract or from
Gemini) go through the exact same ICAO 9303 checksum-verified decoder — an
LLM's own free-text field guesses aren't independently checkable, but the
MRZ checksum math is, regardless of which backend did the reading.

- **Locates and decodes the MRZ** (ICAO Doc 9303, TD3/passport format):
  document type, issuing country, surname, given names, passport number,
  nationality, date of birth, sex, date of expiry, and personal number
- **Verifies each MRZ check digit** (passport number, DOB, expiry, personal
  number, and the composite check) so you can see whether the read is
  trustworthy or was corrupted
- **Falls back to the visual zone** (regex for OCR, the model's own reading
  for Gemini) if no MRZ can be located, clearly flagged as lower-confidence
- **Flags confidence per file**: `ok`, `partial` (missing fields or a failed
  checksum), or `failed`
- **Displays every field on screen** via a Streamlit UI, including the raw
  MRZ lines and a green/red check-digit breakdown
- **Exports a styled Excel report** (`Passports` + `Summary` sheets) from
  the CLI

## Quick start

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

This project needs two system tools for OCR support:

- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) — `brew install tesseract` (macOS) / `sudo apt install tesseract-ocr` (Linux) / [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)
- [Poppler](https://poppler.freedesktop.org/) (for `pdf2image`, PDF uploads only) — `brew install poppler` (macOS) / `sudo apt install poppler-utils` (Linux) / [Windows binaries](https://github.com/oschwartz10612/poppler-windows)

### Run the app

```bash
streamlit run src/app.py
```

Upload a passport image (PNG/JPG/TIFF/BMP/WEBP/GIF) or PDF, choose an
extraction method in the sidebar, click **Extract data**, and every field is
displayed on screen along with the decoded MRZ and its checksum validity.

To use Gemini, get a free key from
[Google AI Studio](https://aistudio.google.com/apikey) and either paste it
into the sidebar or set it as an environment variable first:

```bash
export GEMINI_API_KEY=your-key-here   # Windows: set GEMINI_API_KEY=your-key-here
```

### Run from the command line

```bash
python src/main.py --input sample_passports --output output/report.xlsx --verbose

# Or via Gemini instead of local OCR:
python src/main.py --input sample_passports --output output/report.xlsx --gemini
```

### Run the tests

```bash
pytest tests/ -v
```

## How it works

```
Passport image / PDF
   │
   ├── Local OCR ──► pdfplumber (PDF) / tesseract OCR ──► locate two 44-char
   │                 candidate lines in the raw text
   │
   └── Gemini API ──► vision model transcribes the page, told explicitly
                       which two lines (if any) are the MRZ
   │
   ▼
MRZ candidate found? ──Yes──► decode fields + verify each ICAO 9303 check digit
   │
   │ No / checksums say the read is broken
   ▼
fall back to visual-zone fields (regex for OCR, the model's own reading for Gemini)
```

## Project structure

```
passport-data-extractor/
├── src/
│   ├── extractor.py       # OCR, MRZ decoding + checksum verification, visual-zone fallback
│   ├── llm_extractor.py   # Gemini backend — same checksum decoder, different reader
│   ├── report.py          # Excel report generation
│   ├── main.py            # CLI entry point
│   └── app.py             # Streamlit UI
├── tests/
│   ├── test_extractor.py      # Uses the ICAO Doc 9303 worked example as a known-good fixture
│   └── test_llm_extractor.py  # Mocks the Gemini call; no network/API key needed to test
├── output/                 # generated reports land here (gitignored)
├── requirements.txt
└── run_app.bat
```

## Limitations & possible extensions

MRZ decoding follows the TD3 (passport) format only — TD1/TD2 (ID cards)
use a different line layout and aren't handled here. A read can still be
corrupted (OCR noise, or a model mistranscription); the checksum
verification is what surfaces that instead of silently returning wrong
data. Natural next steps:

- Support TD1 (ID card) and TD2 MRZ formats
- Face-photo region cropping/preview alongside the extracted data
- Batch folder processing with a combined report, like the CLI already
  supports
- Local vision-model backend (e.g. via Ollama) for teams that can't send
  passport images to a cloud API at all

## License

MIT.

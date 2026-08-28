const fileInput = document.querySelector('#file-input');
const dropZone = document.querySelector('#drop-zone');
const results = document.querySelector('#results');
const resultGrid = document.querySelector('#result-grid');
const statusText = document.querySelector('#status-text');
const scanStatus = document.querySelector('#scan-status');
const mrzLines = document.querySelector('#mrz-lines');
const mrzValidation = document.querySelector('#mrz-validation');
const rawText = document.querySelector('#raw-text');
const resetButton = document.querySelector('#reset-button');
const copyButton = document.querySelector('#copy-button');
const engineRadios = document.querySelectorAll('input[name="engine"]');
const apiKeyField = document.querySelector('#api-key-field');
const apiKeyInput = document.querySelector('#api-key-input');
const privacyNoteText = document.querySelector('#privacy-note-text');
const footerNote = document.querySelector('#footer-note');

const GEMINI_MODEL = 'gemini-3.7-flash';
const GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions';
const GEMINI_PROMPT = "You are transcribing a passport bio page. Read every field exactly as printed — do not correct, normalize, guess, or auto-complete anything. Transcribe ALL visible text on the page, preserving line breaks. For the two-line Machine Readable Zone (MRZ) at the bottom, transcribe it character-for-character exactly as printed on its own two lines, including every '<' filler character — do not fix anything there even if it looks like a typo. Output only the transcribed text, with no commentary before or after it.";
const STORAGE_KEY_API = 'passage.geminiApiKey';
const STORAGE_KEY_ENGINE = 'passage.extractionEngine';

const fields = [
  ['documentType', 'Document type'], ['issuingCountry', 'Issuing country'], ['surname', 'Surname'],
  ['givenNames', 'First and middle names'], ['passportNumber', 'Passport number'], ['nationality', 'Nationality'],
  ['dateOfBirth', 'Date of birth'], ['sex', 'Sex'], ['dateOfExpiry', 'Date of expiry'],
  ['personalNumber', 'Personal number'], ['placeOfBirth', 'Place of birth'], ['authority', 'Authority'],
];

fileInput.addEventListener('change', () => fileInput.files[0] && startExtraction(fileInput.files[0]));
['dragenter', 'dragover'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.add('is-dragging');
}));
['dragleave', 'drop'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.remove('is-dragging');
}));
dropZone.addEventListener('drop', (event) => {
  const file = event.dataTransfer.files[0];
  if (file) startExtraction(file);
});
resetButton.addEventListener('click', resetApp);
copyButton.addEventListener('click', copyResults);

// Restore engine choice + API key from this browser's localStorage (never sent anywhere but Google's API).
try {
  const savedEngine = localStorage.getItem(STORAGE_KEY_ENGINE);
  if (savedEngine) {
    const radio = document.querySelector(`input[name="engine"][value="${savedEngine}"]`);
    if (radio) radio.checked = true;
  }
  apiKeyInput.value = localStorage.getItem(STORAGE_KEY_API) || '';
} catch { /* localStorage unavailable (e.g. private browsing) — fall back to defaults */ }
updateEngineUi();

engineRadios.forEach((radio) => radio.addEventListener('change', () => {
  try { localStorage.setItem(STORAGE_KEY_ENGINE, radio.value); } catch { /* ignore */ }
  updateEngineUi();
}));
apiKeyInput.addEventListener('input', () => {
  try { localStorage.setItem(STORAGE_KEY_API, apiKeyInput.value); } catch { /* ignore */ }
});

function currentEngine() { return document.querySelector('input[name="engine"]:checked').value; }

function updateEngineUi() {
  const gemini = currentEngine() === 'gemini';
  apiKeyField.hidden = !gemini;
  privacyNoteText.textContent = gemini ? 'Your document is sent to Google’s Gemini API' : 'Your document stays in this browser';
  footerNote.textContent = gemini ? 'GEMINI MODE · KEY STORED LOCALLY' : 'NO UPLOADS · NO STORAGE';
}

async function startExtraction(file) {
  if (file.size > 15 * 1024 * 1024) return showError('That file is larger than 15 MB.');
  if (!['application/pdf', 'image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return showError('Please choose a PNG, JPG, WEBP, or PDF file.');
  const engine = currentEngine();
  const apiKey = apiKeyInput.value.trim();
  if (engine === 'gemini' && !apiKey) return showError('Enter a Gemini API key above, or switch to Local OCR.');
  results.hidden = false;
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  setStatus('Preparing your scan…');
  try {
    const text = engine === 'gemini'
      ? await extractWithGemini(file, apiKey)
      : await recognizeImages(file.type === 'application/pdf' ? await renderPdf(file) : [await imageFromFile(file)]);
    const extracted = parsePassport(text);
    renderResults(extracted, text);
    setStatus('Scan complete', true);
  } catch (error) {
    console.error(error);
    setStatus(error.message || 'The scan could not be completed.');
    scanStatus.classList.add('error');
  }
}

async function extractWithGemini(file, apiKey) {
  setStatus('Sending to Gemini…');
  const base64 = await fileToBase64(file);
  const contentType = file.type === 'application/pdf' ? 'document' : 'image';
  let response;
  try {
    response = await fetch(GEMINI_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
      body: JSON.stringify({
        model: GEMINI_MODEL,
        input: [
          { type: 'text', text: GEMINI_PROMPT },
          { type: contentType, data: base64, mime_type: file.type },
        ],
      }),
    });
  } catch {
    throw new Error('Could not reach the Gemini API. Check your connection and try again.');
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).error?.message || detail; } catch { /* body wasn't JSON */ }
    throw new Error(`Gemini API error (${response.status}): ${detail}`);
  }
  const payload = await response.json();
  const text = extractOutputText(payload);
  if (!text) throw new Error('Gemini returned no readable text for this document.');
  return text;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('Could not read the file.'));
    reader.readAsDataURL(file);
  });
}

// The API's raw response holds a `steps` list; only the SDK derives a convenience
// `output_text` field, so mirror that: take the last model_output step's text content.
function extractOutputText(payload) {
  const steps = Array.isArray(payload.steps) ? payload.steps : [];
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (step.type === 'user_input') break;
    if (step.type !== 'model_output') continue;
    const content = Array.isArray(step.content) ? step.content : [];
    const textParts = content.filter((item) => item.type === 'text').map((item) => item.text || '');
    if (textParts.length) return textParts.join('');
  }
  return '';
}

async function renderPdf(file) {
  if (!window.pdfjsLib) throw new Error('PDF engine is still loading. Please try again in a moment.');
  const pdf = await window.pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
  const images = [];
  for (let pageNumber = 1; pageNumber <= Math.min(pdf.numPages, 10); pageNumber += 1) {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 2 });
    const canvas = document.createElement('canvas');
    canvas.width = viewport.width; canvas.height = viewport.height;
    await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
    images.push(canvas);
  }
  return images;
}

function imageFromFile(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('This image could not be opened.'));
    image.src = URL.createObjectURL(file);
  });
}

async function recognizeImages(images) {
  if (!window.Tesseract) throw new Error('OCR engine is still loading. Please try again in a moment.');
  let combined = '';
  for (let index = 0; index < images.length; index += 1) {
    setStatus(`Reading page ${index + 1} of ${images.length}…`);
    const result = await Tesseract.recognize(images[index], 'eng', {
      logger: (message) => {
        if (message.status === 'recognizing text') setStatus(`Reading page ${index + 1} · ${Math.round(message.progress * 100)}%…`);
      },
    });
    combined += `${result.data.text}\n`;
  }
  return combined;
}

function parsePassport(text) {
  const normalized = text.toUpperCase().replace(/[«]/g, '<');
  const lines = normalized.split(/\r?\n/).map((line) => line.replace(/[^A-Z0-9<]/g, '')).filter(Boolean);
  const mrz = findMrz(lines);
  const data = {
    documentType: '', issuingCountry: '', surname: '', givenNames: '', passportNumber: '', nationality: '',
    dateOfBirth: '', sex: '', dateOfExpiry: '', personalNumber: '', placeOfBirth: '', authority: '',
  };
  let checks = {};
  if (mrz.length >= 2) checks = parseMrz(mrz, data);
  const visible = text.replace(/\s+/g, ' ');
  data.documentType ||= findValue(visible, /(?:type|document type)\s*[:\-]?\s*([A-Z]{1,3})/i);
  data.issuingCountry ||= findValue(visible, /(?:issuing state|country code|country)\s*[:\-]?\s*([A-Z]{3})/i);
  data.passportNumber ||= findValue(visible, /(?:passport no\.?|passport number|document no\.?)\s*[:\-]?\s*([A-Z0-9]{6,12})/i);
  data.nationality ||= findValue(visible, /nationality\s*[:\-]?\s*([A-Z][A-Z ]{2,20})/i);
  data.dateOfBirth ||= findValue(visible, /date of birth\s*[:\-]?\s*([0-9A-Z./-]{6,12})/i);
  data.dateOfExpiry ||= findValue(visible, /date of expiry\s*[:\-]?\s*([0-9A-Z./-]{6,12})/i);

  const formatOk = mrz.length === 2 && mrz.every((line) => line.length === 44) && mrz[0].startsWith('P');
  const relevantChecks = Object.values(checks).filter((value) => value !== null);
  const valid = formatOk && relevantChecks.length > 0 && relevantChecks.every(Boolean);
  return { data, mrz, checks, valid };
}

function findMrz(lines) {
  const candidates = lines.filter((line) => line.length >= 25 && (line.includes('<') || /^P[A-Z0-9<]{20,}$/.test(line)));
  const first = candidates.findIndex((line) => line.startsWith('P'));
  return first >= 0 ? candidates.slice(first, first + 2).map((line) => line.padEnd(44, '<').slice(0, 44)) : candidates.slice(-2).map((line) => line.padEnd(44, '<').slice(0, 44));
}

function parseMrz(mrz, data) {
  const first = mrz[0] || '';
  const second = mrz[1] || '';
  data.documentType = first[0] === 'P' ? 'Passport' : first[0];
  data.issuingCountry = first.slice(2, 5).replace(/</g, '');
  const names = first.slice(5).split('<<');
  data.surname = cleanMrzName(names[0]);
  data.givenNames = cleanMrzName(names[1] || '');

  const passportNumberRaw = second.slice(0, 9);
  const passportNumberCheck = second[9];
  data.passportNumber = passportNumberRaw.replace(/</g, '');

  data.nationality = second.slice(10, 13).replace(/</g, '');

  const dobRaw = second.slice(13, 19);
  const dobCheck = second[19];
  data.dateOfBirth = formatMrzDate(dobRaw);

  data.sex = ({ M: 'Male', F: 'Female', '<': 'Unspecified' })[second[20]] || second[20] || '';

  const expiryRaw = second.slice(21, 27);
  const expiryCheck = second[27];
  data.dateOfExpiry = formatMrzDate(expiryRaw);

  const personalNumberRaw = second.slice(28, 42);
  const personalNumberCheck = second[42];
  data.personalNumber = personalNumberRaw.replace(/</g, '');

  const compositeCheck = second[43];
  const compositeData = second.slice(0, 10) + second.slice(13, 20) + second.slice(21, 43);

  return {
    passportNumber: verifyMrzCheckDigit(passportNumberRaw, passportNumberCheck),
    dateOfBirth: verifyMrzCheckDigit(dobRaw, dobCheck),
    dateOfExpiry: verifyMrzCheckDigit(expiryRaw, expiryCheck),
    personalNumber: verifyMrzCheckDigit(personalNumberRaw, personalNumberCheck),
    composite: verifyMrzCheckDigit(compositeData, compositeCheck),
  };
}

// ICAO Doc 9303 check-digit algorithm: weights 7/3/1 repeating, '<' = 0, A-Z = 10-35.
function mrzCharValue(ch) {
  if (ch >= '0' && ch <= '9') return ch.charCodeAt(0) - 48;
  if (ch === '<') return 0;
  if (ch >= 'A' && ch <= 'Z') return ch.charCodeAt(0) - 65 + 10;
  return 0;
}
function mrzCheckDigit(data) {
  const weights = [7, 3, 1];
  let total = 0;
  for (let i = 0; i < data.length; i += 1) total += mrzCharValue(data[i]) * weights[i % 3];
  return total % 10;
}
// Returns null only when the field is legitimately unused (data AND its check digit
// are both filler) — a non-digit check paired with real data means something's broken,
// so that must come back false, not "not applicable" (mirrors extractor.py's _verify).
function verifyMrzCheckDigit(data, checkChar) {
  const dataIsBlank = [...data].every((ch) => ch === '<');
  if (!/^[0-9]$/.test(checkChar || '')) return dataIsBlank ? null : false;
  if (dataIsBlank) return null;
  return mrzCheckDigit(data) === Number(checkChar);
}

function cleanMrzName(value) { return value.replace(/</g, ' ').replace(/\s+/g, ' ').trim().replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatMrzDate(value) { return /^\d{6}$/.test(value) ? `${value.slice(4, 6)}/${value.slice(2, 4)}/${Number(value.slice(0, 2)) > 30 ? '19' : '20'}${value.slice(0, 2)}` : value.replace(/</g, ''); }
function findValue(text, pattern) { const match = text.match(pattern); return match ? match[1].trim() : ''; }

function renderResults(result, text) {
  resultGrid.innerHTML = fields.map(([key, label]) => `<dl class="data-field${result.data[key] ? '' : ' missing'}"><dt>${label}</dt><dd>${escapeHtml(result.data[key] || 'Not detected')}</dd></dl>`).join('');
  mrzLines.innerHTML = result.mrz.length ? result.mrz.map((line) => `<div>${escapeHtml(line)}</div>`).join('') : '<span>MRZ was not detected. Try a higher-resolution scan.</span>';
  mrzValidation.textContent = result.mrz.length < 2 ? 'Not detected' : (result.valid ? 'Checksum valid' : 'Checksum mismatch');
  mrzValidation.classList.toggle('invalid', result.mrz.length >= 2 && !result.valid);
  rawText.textContent = text;
}

function setStatus(message, done = false) { statusText.textContent = message; scanStatus.classList.toggle('done', done); scanStatus.classList.remove('error'); }
function showError(message) { results.hidden = false; setStatus(message); scanStatus.classList.add('error'); results.scrollIntoView({ behavior: 'smooth' }); }
function escapeHtml(value) { return value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[character]); }
async function copyResults() { const values = parsePassport(rawText.textContent).data; await navigator.clipboard.writeText(JSON.stringify(values, null, 2)); copyButton.innerHTML = 'Copied <span aria-hidden="true">✓</span>'; setTimeout(() => { copyButton.innerHTML = 'Copy JSON <span aria-hidden="true">□</span>'; }, 1600); }
function resetApp() { fileInput.value = ''; results.hidden = true; resultGrid.innerHTML = ''; rawText.textContent = ''; window.scrollTo({ top: 0, behavior: 'smooth' }); }

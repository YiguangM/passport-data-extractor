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

async function startExtraction(file) {
  if (file.size > 15 * 1024 * 1024) return showError('That file is larger than 15 MB.');
  if (!['application/pdf', 'image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return showError('Please choose a PNG, JPG, WEBP, or PDF file.');
  results.hidden = false;
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  setStatus('Preparing your scan…');
  try {
    const images = file.type === 'application/pdf' ? await renderPdf(file) : [await imageFromFile(file)];
    const text = await recognizeImages(images);
    const extracted = parsePassport(text);
    renderResults(extracted, text);
    setStatus('Scan complete', true);
  } catch (error) {
    console.error(error);
    setStatus(error.message || 'The scan could not be completed.');
    scanStatus.classList.add('error');
  }
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
  if (mrz.length >= 2) parseMrz(mrz, data);
  const visible = text.replace(/\s+/g, ' ');
  data.documentType ||= findValue(visible, /(?:type|document type)\s*[:\-]?\s*([A-Z]{1,3})/i);
  data.issuingCountry ||= findValue(visible, /(?:issuing state|country code|country)\s*[:\-]?\s*([A-Z]{3})/i);
  data.passportNumber ||= findValue(visible, /(?:passport no\.?|passport number|document no\.?)\s*[:\-]?\s*([A-Z0-9]{6,12})/i);
  data.nationality ||= findValue(visible, /nationality\s*[:\-]?\s*([A-Z][A-Z ]{2,20})/i);
  data.dateOfBirth ||= findValue(visible, /date of birth\s*[:\-]?\s*([0-9A-Z./-]{6,12})/i);
  data.dateOfExpiry ||= findValue(visible, /date of expiry\s*[:\-]?\s*([0-9A-Z./-]{6,12})/i);
  return { data, mrz, valid: validateMrz(mrz) };
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
  data.passportNumber = second.slice(0, 9).replace(/</g, '');
  data.nationality = second.slice(10, 13).replace(/</g, '');
  data.dateOfBirth = formatMrzDate(second.slice(13, 19));
  data.sex = ({ M: 'Male', F: 'Female', '<': 'Unspecified' })[second[20]] || second[20] || '';
  data.dateOfExpiry = formatMrzDate(second.slice(21, 27));
  data.personalNumber = second.slice(28, 42).replace(/</g, '');
}

function cleanMrzName(value) { return value.replace(/</g, ' ').replace(/\s+/g, ' ').trim().replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function formatMrzDate(value) { return /^\d{6}$/.test(value) ? `${value.slice(4, 6)}/${value.slice(2, 4)}/${Number(value.slice(0, 2)) > 30 ? '19' : '20'}${value.slice(0, 2)}` : value.replace(/</g, ''); }
function findValue(text, pattern) { const match = text.match(pattern); return match ? match[1].trim() : ''; }
function validateMrz(mrz) { return mrz.length === 2 && mrz.every((line) => line.length === 44) && mrz[0].startsWith('P'); }

function renderResults(result, text) {
  resultGrid.innerHTML = fields.map(([key, label]) => `<dl class="data-field${result.data[key] ? '' : ' missing'}"><dt>${label}</dt><dd>${escapeHtml(result.data[key] || 'Not detected')}</dd></dl>`).join('');
  mrzLines.innerHTML = result.mrz.length ? result.mrz.map((line) => `<div>${escapeHtml(line)}</div>`).join('') : '<span>MRZ was not detected. Try a higher-resolution scan.</span>';
  mrzValidation.textContent = result.valid ? 'Format detected' : 'Needs review';
  mrzValidation.classList.toggle('invalid', !result.valid);
  rawText.textContent = text;
}

function setStatus(message, done = false) { statusText.textContent = message; scanStatus.classList.toggle('done', done); scanStatus.classList.remove('error'); }
function showError(message) { results.hidden = false; setStatus(message); scanStatus.classList.add('error'); results.scrollIntoView({ behavior: 'smooth' }); }
function escapeHtml(value) { return value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[character]); }
async function copyResults() { const values = parsePassport(rawText.textContent).data; await navigator.clipboard.writeText(JSON.stringify(values, null, 2)); copyButton.innerHTML = 'Copied <span aria-hidden="true">✓</span>'; setTimeout(() => { copyButton.innerHTML = 'Copy JSON <span aria-hidden="true">□</span>'; }, 1600); }
function resetApp() { fileInput.value = ''; results.hidden = true; resultGrid.innerHTML = ''; rawText.textContent = ''; window.scrollTo({ top: 0, behavior: 'smooth' }); }

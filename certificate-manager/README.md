# Certificate Manager

Ingest certificates (image / PDF / Word), auto-detect and crop the certificate,
review the crop, and file it into category folders with a clean, consistent name.

The image processing runs in Python; the crop-review step runs in your browser
using Cropper.js, so you get real drag handles to fix a bad auto-crop before you
hit **Save**. Nothing is written to disk until you press Save.

## Categories & naming

Certificates are filed into three folders under your chosen destination:

- `Academic Certificates`
- `Extracurricular Certificates`
- `Mark Sheets`

Filenames are built from these parts, in order, skipping any that are empty:

    Title - Organisation - Addressed to - [Sem <n>] - Date

- Academic / Extra-curricular: `Title - Organisation - Addressed to - Date`
  e.g. `Machine Learning Specialization - Stanford University - Jeet Patel - 2024.png`
- Mark sheets add the semester: `Title - Organisation - Sem <n> - Date`
  e.g. `B.Com - MSU Baroda - Sem IV - 2023.png`

Every field is editable in the UI before saving, and name collisions get a
` (2)`, ` (3)` suffix instead of overwriting.

## Setup (Windows / PowerShell)

```powershell
cd certmgr
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

The badges at the top of the page show which optional features are active
(PDF / OCR / AI).

### Optional: local OCR (auto-fills name / category / year / semester)

1. Install the Tesseract engine (UB Mannheim build is the usual Windows one).
2. If `tesseract.exe` isn't on your PATH, point the app at it:
   ```powershell
   $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
   python app.py
   ```
Without Tesseract the app still works fully — the detail fields just start empty.

### Optional: AI vision extraction (best pre-fill accuracy)

```powershell
$env:USE_AI = "1"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# optional: $env:ANTHROPIC_MODEL = "claude-sonnet-5"
python app.py
```
When enabled, each page is read by a vision model that returns the category,
title, year and (for mark sheets) semester. It falls back to OCR, then to blank
fields, so it never blocks you.

### Optional: text-based Word documents

Scanned certificates pasted into Word are read directly (the embedded image is
extracted). A *text-only* Word certificate needs LibreOffice to render it:
install LibreOffice, and if `soffice` isn't on PATH set:
```powershell
$env:SOFFICE_PATH = "C:\Program Files\LibreOffice\program\soffice.exe"
```

## Using it

1. **Add files** (or drag them onto the window). Images, PDFs and Word docs are
   all accepted; multi-page PDFs become one entry per page.
2. Each document appears with a suggested crop box already drawn around the
   detected certificate. Drag the handles to fix it, or use **Auto crop** /
   **Whole page** / **Rotate**.
3. Pick the **category**, check the auto-filled **name / year** (and **semester**
   for mark sheets), and glance at the *Will be saved as* preview.
4. **Save & next** writes the crop to the right folder and moves on. **Skip**
   discards a page. `Enter` also saves.

## Notes

- Cropper.js loads from a CDN, so the browser needs internet the first time. To
  run fully offline, download `cropper.min.js` + `cropper.min.css` into
  `static/` and change the two `<link>`/`<script>` URLs in
  `templates/index.html` to point at them.
- **Edge detection / de-skew:** on each page the app looks for the document's
  four corners. If it finds them, it perspective-corrects the page to a flat,
  straightened document and pre-crops it tight — that's the **Deskew: on** state.
  Toggle the **Deskew** button to fall back to the raw page if a detection looks
  wrong. Flat scans and screenshots (no visible borders) skip de-skew and use an
  axis-aligned content box instead.
- **Large batches:** drop as many files as you like. Uploads are sent in small
  batches and each page is analysed only when you reach it, so an 80+ file drop
  loads immediately instead of stalling on OCR. The next page is analysed in the
  background while you work on the current one.
- **Best field accuracy:** organisation and addressed-to are hard to get from
  plain OCR — enable AI vision (above) for reliable Title / Organisation /
  Addressed-to / Date extraction.
- Temp files live in your system temp dir and are cleaned up when a batch
  finishes.

"""
Certificate Manager
===================
Local web app: ingest certificates (image / PDF / Word), detect the certificate
edges and crop (with optional perspective de-skew), review, and file into
category folders with a structured name.

Run:   python app.py   ->   http://127.0.0.1:5000

Design notes
------------
* Upload is fast: it only splits files into page images. The heavy work
  (edge detection, de-skew, OCR/AI) happens lazily, one page at a time, when
  you reach that page. This is what makes an 83-file batch load instantly
  instead of hanging on OCR.
* Uploads are batched from the browser, so a large drop never becomes one giant
  request.

Optional smart extraction:
  * Local OCR : install Tesseract + `pip install pytesseract`.
  * AI vision : set USE_AI=1 and ANTHROPIC_API_KEY (best accuracy, and the only
                reliable way to get organisation / addressed-to).
Everything still works with neither installed; you just fill fields in yourself.
"""

import os
import io
import re
import json
import base64
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from uuid import uuid4

import numpy as np
import cv2
from PIL import Image
from flask import Flask, request, jsonify, send_file, render_template, abort

# ------------------------------------------------------------------ optional deps
try:
    import fitz  # PyMuPDF
    HAVE_FITZ = True
except Exception:
    HAVE_FITZ = False

try:
    import pytesseract
    _tcmd = os.environ.get("TESSERACT_CMD")
    if _tcmd:
        pytesseract.pytesseract.tesseract_cmd = _tcmd
    HAVE_TESS = True
except Exception:
    HAVE_TESS = False

# ------------------------------------------------------------------ config
APP_ROOT = Path(__file__).resolve().parent
WORK_ROOT = Path(tempfile.gettempdir()) / "certmgr_sessions"
WORK_ROOT.mkdir(parents=True, exist_ok=True)
DEFAULT_DEST = str(Path.home() / "Certificates")

CATEGORIES = {
    "academic": "Academic Certificates",
    "extracurricular": "Extracurricular Certificates",
    "marksheet": "Mark Sheets",
}

USE_AI = os.environ.get("USE_AI", "0") == "1"
AI_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
AI_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SOFFICE = os.environ.get("SOFFICE_PATH", "soffice")
RENDER_DPI = 200

app = Flask(__name__)
# Allow large multi-file batches.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024   # 2 GB per request
app.config["MAX_FORM_PARTS"] = 20000
try:
    app.config["MAX_FORM_MEMORY_SIZE"] = None
except Exception:
    pass


# ================================================================== ingestion
def _save_pil(img: Image.Image, path: Path):
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(str(path))


def _pdf_to_pages(src: Path, out_dir: Path, stem: str):
    if not HAVE_FITZ:
        raise RuntimeError("PyMuPDF (fitz) is required for PDFs. pip install PyMuPDF")
    pages = []
    doc = fitz.open(str(src))
    mat = fitz.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0)
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(matrix=mat)
        p = out_dir / f"{stem}_p{i:02d}.png"
        pix.save(str(p))
        pages.append(p)
    doc.close()
    return pages


def _docx_to_pages(src: Path, out_dir: Path, stem: str):
    pages = []
    try:
        with zipfile.ZipFile(str(src)) as z:
            media = sorted(
                n for n in z.namelist()
                if n.startswith("word/media/")
                and n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"))
            )
            for idx, name in enumerate(media):
                try:
                    img = Image.open(io.BytesIO(z.read(name)))
                    if img.width < 400 or img.height < 400:
                        continue
                    p = out_dir / f"{stem}_img{idx:02d}.png"
                    _save_pil(img, p)
                    pages.append(p)
                except Exception:
                    continue
    except Exception:
        pass
    if pages:
        return pages
    try:
        subprocess.run(
            [SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
            check=True, timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pdf = out_dir / (src.stem + ".pdf")
        if pdf.exists():
            return _pdf_to_pages(pdf, out_dir, stem)
    except Exception:
        pass
    raise RuntimeError(
        "Could not read this Word file (no embedded certificate image and "
        "LibreOffice not available for text conversion). Save it as PDF/image.")


def ingest_file(src: Path, out_dir: Path, stem: str):
    ext = src.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"):
        p = out_dir / f"{stem}.png"
        _save_pil(Image.open(str(src)), p)
        return [p]
    if ext == ".pdf":
        return _pdf_to_pages(src, out_dir, stem)
    if ext in (".docx", ".doc"):
        return _docx_to_pages(src, out_dir, stem)
    raise RuntimeError(f"Unsupported file type: {ext}")


# ================================================================== edge detection
def _order_quad(pts):
    pts = pts.reshape(4, 2).astype("float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    return np.array([
        pts[np.argmin(s)],   # top-left
        pts[np.argmin(d)],   # top-right
        pts[np.argmax(s)],   # bottom-right
        pts[np.argmax(d)],   # bottom-left
    ], dtype="float32")


def find_document_quad(bgr):
    """Find a convex 4-corner document outline. Return ordered corners or None."""
    h, w = bgr.shape[:2]
    area_img = float(h * w)
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale != 1.0 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    small_area = small.shape[0] * small.shape[1]
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        a = cv2.contourArea(c)
        if a < 0.20 * small_area:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = _order_quad(approx) / scale
        # skip if it's basically the whole frame (already flat) -> no warp needed
        (tl, tr, br, bl) = quad
        near_full = (tl[0] < 0.02 * w and tl[1] < 0.02 * h and
                     br[0] > 0.98 * w and br[1] > 0.98 * h)
        if near_full:
            return None
        if cv2.contourArea(quad.astype("float32")) < 0.20 * area_img:
            continue
        return quad
    return None


def four_point_warp(bgr, quad):
    (tl, tr, br, bl) = quad
    wA = np.linalg.norm(br - bl); wB = np.linalg.norm(tr - tl)
    hA = np.linalg.norm(tr - br); hB = np.linalg.norm(tl - bl)
    W = max(int(wA), int(wB)); H = max(int(hA), int(hB))
    if W < 10 or H < 10:
        return None
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(bgr, M, (W, H))


def suggest_crop_axis(bgr):
    """Axis-aligned box around the certificate/content."""
    h, w = bgr.shape[:2]
    area_img = float(h * w)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(blur, 40, 120), np.ones((5, 5), np.uint8), 2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if a < 0.15 * area_img or a > 0.995 * area_img:
            continue
        if best is None or a > best[0]:
            best = (a,) + cv2.boundingRect(c)
    if best:
        _, x, y, ww, hh = best
    else:
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        ys, xs = np.where(th > 0)
        if len(xs) and (xs.max() - xs.min()) * (ys.max() - ys.min()) > 0.1 * area_img:
            x, y, ww, hh = xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()
        else:
            x, y, ww, hh = 0, 0, w, h
    pad = int(0.01 * max(w, h))
    x = max(0, x - pad); y = max(0, y - pad)
    ww = min(w - x, ww + 2 * pad); hh = min(h - y, hh + 2 * pad)
    return {"x": int(x), "y": int(y), "width": int(ww), "height": int(hh)}


# ================================================================== metadata (OCR)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.I)
SEM_RE = re.compile(r"sem(?:ester)?\s*[-:]?\s*([ivx]+|\d{1,2})", re.I)
ADDR_RE = re.compile(
    r"(?:awarded to|presented to|granted to|this is to certify that|"
    r"certify that|conferred upon|issued to)\s*[:\-]?\s*([A-Z][A-Za-z.'\- ]{2,50})", re.I)
ORG_RE = re.compile(
    r"([A-Z][A-Za-z.&,'\- ]*?\b(?:University|Institute|Institution|College|Board|"
    r"Academy|Council|Society|Foundation|Corporation|Company|Limited|Ltd|Pvt|"
    r"School|Federation|Association|Commission|Authority|Technologies)\b[A-Za-z.&,'\- ]*)")

LABELS = ("author", "publication", "journal", "volume", "issue", "pages",
          "publisher", "description", "abstract", "date", "doi", "citation",
          "scholar", "related", "cited by", "roll no", "seat no", "enrol",
          "registration", "sgpa", "cgpa", "grade", "subject")

KEYWORDS = {
    "marksheet": ["marksheet", "mark sheet", "statement of marks", "grade card",
                  "sgpa", "cgpa", "marks obtained", "grade point", "semester", "result"],
    "extracurricular": ["participation", "participated", "sports", "cultural",
                        "workshop", "hackathon", "volunteer", "ncc", "nss", "winner",
                        "runner", "competition", "event", "seminar", "webinar",
                        "internship", "training", "attended"],
    "academic": ["degree", "bachelor", "master", "diploma", "certificate of completion",
                 "course", "university", "board", "examination", "passed",
                 "has completed", "has successfully", "certification", "thesis",
                 "dissertation"],
}


def classify(text_low):
    scores = {k: sum(text_low.count(w) for w in ws) for k, ws in KEYWORDS.items()}
    if scores["marksheet"] >= 2:
        return "marksheet"
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "academic"


def _looks_like_author_line(s):
    """Heuristic: comma-separated list of 'A Surname' / 'AB Surname' tokens."""
    if s.count(",") >= 2:
        toks = [t.strip() for t in s.split(",") if t.strip()]
        initials = sum(1 for t in toks
                       if re.match(r"^[A-Z]{1,3}\.?\s*[A-Z][a-z]+", t)
                       or re.match(r"^[A-Z][a-z]+\s+[A-Z]{1,3}\.?$", t))
        if initials >= max(2, len(toks) // 2):
            return True
    return False


def guess_title(lines):
    """
    Title = the prominent text block at the top, above the first metadata label,
    excluding author-style lines. Falls back to the longest early line.
    """
    top = []
    for ln in lines[:12]:
        low = ln.lower()
        if any(low.startswith(lb) or low == lb for lb in LABELS):
            break
        if _looks_like_author_line(ln):
            break
        top.append(ln.strip())
    top = [t for t in top if len(t) >= 4]
    if top:
        title = " ".join(top[:3]).strip()
        title = re.sub(r"\s+", " ", title)
        if len(title) >= 6:
            return title[:120]
    # fallback: longest non-author line in the first 15
    cand = [l.strip() for l in lines[:15]
            if 6 <= len(l.strip()) <= 120 and not _looks_like_author_line(l)]
    cand.sort(key=len, reverse=True)
    return cand[0] if cand else ""


def guess_org(text):
    m = ORG_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .,-")[:80]
    return ""


def extract_with_tesseract(img_path):
    if not HAVE_TESS:
        return {}
    try:
        text = pytesseract.image_to_string(Image.open(str(img_path)))
    except Exception:
        return {}
    low = text.lower()
    lines = [l for l in (ln.strip() for ln in text.splitlines()) if l]
    dm = DATE_RE.search(text)
    years = YEAR_RE.findall(text)
    sem = SEM_RE.search(text)
    addr = ADDR_RE.search(text)
    return {
        "category": classify(low),
        "title": guess_title(lines),
        "organisation": guess_org(text),
        "addressed_to": (re.sub(r"\s+", " ", addr.group(1)).strip() if addr else ""),
        "date": (dm.group(1) if dm else (years[-1] if years else "")),
        "semester": (sem.group(1).upper() if sem else ""),
    }


def extract_with_ai(img_path):
    if not (USE_AI and AI_KEY):
        return {}
    try:
        b64 = base64.b64encode(Path(img_path).read_bytes()).decode()
        prompt = (
            "This image is a certificate, mark sheet, or academic document. "
            "Return ONLY a JSON object (no prose, no code fences) with keys:\n"
            "  category      : one of 'academic','extracurricular','marksheet'\n"
            "  title         : the MAIN TITLE / subject of the document (the course, "
            "award, degree, or paper title) - NOT the author or recipient name\n"
            "  organisation  : the issuing body / university / institute / company\n"
            "  addressed_to  : the person the document is issued to / awarded to, if any\n"
            "  date          : the date shown; use the full date if present, else a "
            "4-digit year, else ''\n"
            "  semester      : semester number or roman numeral if this is a mark "
            "sheet, else ''\n"
            "Use '' for anything not present. Be concise.")
        body = json.dumps({
            "model": AI_MODEL, "max_tokens": 400,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                    "media_type": "image/png", "data": b64}},
                {"type": "text", "text": prompt}]}]}).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": AI_KEY,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        txt = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
        obj = json.loads(txt)
        cat = str(obj.get("category", "")).lower()
        return {
            "category": cat if cat in CATEGORIES else "academic",
            "title": str(obj.get("title", "")).strip(),
            "organisation": str(obj.get("organisation", "")).strip(),
            "addressed_to": str(obj.get("addressed_to", "")).strip(),
            "date": str(obj.get("date", "")).strip(),
            "semester": str(obj.get("semester", "")).strip(),
        }
    except Exception:
        return {}


def extract_metadata(img_path):
    meta = extract_with_ai(img_path) or extract_with_tesseract(img_path)
    base = {"category": "academic", "title": "", "organisation": "",
            "addressed_to": "", "date": "", "semester": ""}
    base.update({k: v for k, v in (meta or {}).items() if v})
    return base


# ================================================================== filename
def sanitize(s):
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', " ", str(s))
    return re.sub(r"\s+", " ", s).strip(" .") or ""


def build_filename(category, title, organisation, addressed_to, date, semester, ext):
    parts = [sanitize(title) or "Untitled"]
    for extra in (organisation, addressed_to):
        v = sanitize(extra)
        if v:
            parts.append(v)
    if category == "marksheet" and sanitize(semester):
        parts.append("Sem " + sanitize(semester))
    v = sanitize(date)
    if v:
        parts.append(v)
    return " - ".join(parts) + ext


def unique_path(folder: Path, filename: str):
    p = folder / filename
    if not p.exists():
        return p
    stem, ext = os.path.splitext(filename)
    i = 2
    while (folder / f"{stem} ({i}){ext}").exists():
        i += 1
    return folder / f"{stem} ({i}){ext}"


# ================================================================== variant/rotate io
def _variant_path(session, pid, variant):
    base = WORK_ROOT / session / pid
    if variant == "warp":
        wp = base.with_suffix(base.suffix + ".warp.png")
        return wp if wp.exists() else base
    return base


def load_variant(session, pid, variant, rot):
    p = _variant_path(session, pid, variant)
    if not p.exists():
        return None
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    rot = int(rot) % 360
    if rot == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
    elif rot == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


# ================================================================== routes
@app.route("/")
def index():
    return render_template("index.html", default_dest=DEFAULT_DEST,
                           have_tess=HAVE_TESS, have_ai=bool(USE_AI and AI_KEY),
                           have_fitz=HAVE_FITZ)


@app.route("/upload", methods=["POST"])
def upload():
    """Fast phase: split files into page images only. No OCR/edge work here."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files received."}), 400
    session = request.form.get("session") or uuid4().hex[:12]
    sdir = WORK_ROOT / session
    sdir.mkdir(parents=True, exist_ok=True)

    pages, errors = [], []
    for f in files:
        original = f.filename or "file"
        stem = (sanitize(Path(original).stem)[:32] or "doc") + "_" + uuid4().hex[:6]
        src = sdir / ("src_" + (sanitize(original) or "file"))
        try:
            f.save(str(src))
            for pp in ingest_file(src, sdir, stem):
                pages.append({"id": pp.name, "source": original})
        except Exception as e:
            errors.append(f"{original}: {e}")
        finally:
            try:
                src.unlink()
            except Exception:
                pass
    return jsonify({"session": session, "pages": pages, "errors": errors})


@app.route("/analyze/<session>/<pid>")
def analyze(session, pid):
    """Lazy phase: edge-detect + de-skew + OCR/AI for one page, cached."""
    src = WORK_ROOT / session / pid
    if not src.exists():
        return jsonify({"error": "expired"}), 404
    cache = src.with_suffix(src.suffix + ".json")
    if cache.exists():
        try:
            return jsonify(json.loads(cache.read_text()))
        except Exception:
            pass

    bgr = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = bgr.shape[:2]
    result = {"origW": W, "origH": H, "hasWarp": False}

    quad = find_document_quad(bgr)
    if quad is not None:
        warped = four_point_warp(bgr, quad)
        if warped is not None:
            wp = src.with_suffix(src.suffix + ".warp.png")
            cv2.imencode(".png", warped)[1].tofile(str(wp))
            wh, ww = warped.shape[:2]
            result["hasWarp"] = True
            result["warpW"] = ww
            result["warpH"] = wh
            m = int(0.01 * max(ww, wh))
            result["cropWarp"] = {"x": m, "y": m,
                                  "width": ww - 2 * m, "height": wh - 2 * m}

    result["cropOrig"] = suggest_crop_axis(bgr)
    result["defaultVariant"] = "warp" if result["hasWarp"] else "orig"
    result["meta"] = extract_metadata(src)
    try:
        cache.write_text(json.dumps(result))
    except Exception:
        pass
    return jsonify(result)


@app.route("/page/<session>/<pid>")
def page_img(session, pid):
    variant = request.args.get("variant", "orig")
    rot = int(request.args.get("rot", 0))
    img = load_variant(session, pid, variant, rot)
    if img is None:
        abort(404)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        abort(500)
    return send_file(io.BytesIO(buf.tobytes()), mimetype="image/png")


@app.route("/save", methods=["POST"])
def save():
    d = request.get_json(force=True)
    session, pid = d["session"], d["pageId"]
    variant = d.get("variant", "orig")
    rot = int(d.get("rot", 0))
    crop = d["crop"]
    category = d["category"]
    if category not in CATEGORIES:
        return jsonify({"error": "Bad category."}), 400
    dest_base = d.get("dest") or DEFAULT_DEST
    fmt = (d.get("format") or "png").lower()

    img = load_variant(session, pid, variant, rot)
    if img is None:
        return jsonify({"error": "Page not found (session may have expired)."}), 404
    h, w = img.shape[:2]
    x = max(0, int(round(crop["x"]))); y = max(0, int(round(crop["y"])))
    x2 = min(w, x + int(round(crop["width"])))
    y2 = min(h, y + int(round(crop["height"])))
    if x2 <= x or y2 <= y:
        return jsonify({"error": "Empty crop region."}), 400
    cropped = img[y:y2, x:x2]

    folder = Path(dest_base) / CATEGORIES[category]
    folder.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if fmt in ("jpg", "jpeg") else ".png"
    fname = build_filename(category, d.get("title", ""), d.get("organisation", ""),
                           d.get("addressed_to", ""), d.get("date", ""),
                           d.get("semester", ""), ext)
    out = unique_path(folder, fname)
    im = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
    im.save(str(out), quality=92) if ext == ".jpg" else im.save(str(out))
    return jsonify({"saved": str(out)})


@app.route("/cleanup", methods=["POST"])
def cleanup():
    session = (request.get_json(force=True) or {}).get("session")
    if session:
        shutil.rmtree(WORK_ROOT / session, ignore_errors=True)
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 60)
    print(" Certificate Manager  ->  http://127.0.0.1:5000")
    print(f"  PDF (PyMuPDF): {HAVE_FITZ}   OCR (Tesseract): {HAVE_TESS}   "
          f"AI: {bool(USE_AI and AI_KEY)}")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)

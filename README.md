# Resume Service v1 — Local ATS PDF Optimizer

A fully local Python service that:
1. Accepts a PDF resume upload
2. Extracts text with **PyMuPDF**
3. Parses it with **regex + spaCy NLP**
4. Generates a clean, ATS-friendly PDF with **ReportLab**
5. Returns the file for download — then auto-cleans temp files

**100% local. No external APIs, databases, cloud services, or LLMs.**

---

## Project Structure

```
resume-service/
├── main.py                  # FastAPI app + /process endpoint
├── extractor.py             # PyMuPDF text extraction
├── parser.py                # regex + spaCy section parser
├── generator.py             # ReportLab PDF builder
├── templates/
│   ├── __init__.py
│   └── resume_template.py   # Styles, colours, section renderers
├── static/
│   └── index.html           # Single-page drag-and-drop frontend
├── uploads/                 # Temp input files (auto-cleaned)
├── output/                  # Temp output files (auto-cleaned)
└── requirements.txt
```

---

## Quick Start

### 1. Create & activate a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Download the spaCy English model

```powershell
python -m spacy download en_core_web_sm
```

### 4. Run the server

```powershell
python main.py
```

Or using uvicorn directly:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Open the app

Navigate to: **http://localhost:8000**

---

## How It Works

```
[Browser]
  │  POST /process  (multipart PDF)
  ▼
[FastAPI main.py]
  │
  ├─ extractor.py  — fitz.open() → get_text("blocks", sort=True)
  │                  Preserves reading order block by block
  │
  ├─ parser.py     — re patterns: email, phone, LinkedIn, GitHub
  │                  spaCy en_core_web_sm PERSON NER for name
  │                  Keyword heuristics for section detection
  │                  Per-section content parsers
  │
  ├─ generator.py  — ReportLab BaseDocTemplate + Platypus story
  │                  Single-column ATS-safe layout
  │                  Clean typography (Helvetica family)
  │
  └─ FileResponse  → browser auto-downloads PDF
     + BackgroundTask cleans uploads/ and output/ temp files
```

---

## API Endpoints

| Method | Path       | Description                           |
|--------|------------|---------------------------------------|
| GET    | `/`        | Serve the frontend                    |
| POST   | `/process` | Upload PDF → returns ATS PDF          |
| GET    | `/health`  | Health check                          |
| GET    | `/docs`    | Auto-generated Swagger UI             |

---

## ATS Template Design

The generated PDF follows ATS best practices:

- **Single-column layout** — multi-column layouts confuse most ATS parsers
- **Standard fonts** — Helvetica (PDF-embedded, universally readable)
- **Black text on white** — maximum ATS compatibility
- **No images/graphics** — ATS parsers skip them
- **No text boxes** — content stays in the document flow
- **Clear section headings** — `EXPERIENCE`, `EDUCATION`, `SKILLS`, etc.
- **Proper heading hierarchy** — Name → Title → Sections → Entries

---

## Parsed Sections

| Section        | Detection keywords                                     |
|----------------|-------------------------------------------------------|
| Summary        | summary, profile, objective, about, overview           |
| Experience     | experience, work experience, employment, career history|
| Education      | education, academic, qualification, degree             |
| Skills         | skills, technical skills, competencies, technologies   |
| Projects       | projects, personal projects, key projects              |
| Certifications | certification, licenses, credentials, courses          |
| Awards         | awards, honors, achievements, recognition              |
| Languages      | languages, language proficiency                        |
| Publications   | publications, papers, research                         |
| Volunteer      | volunteer, volunteering, community service             |

---

## Security Notes

- File type validation: `.pdf` only (by extension + content)
- File size limit: 20 MB
- No file persistence: temp files deleted immediately after response
- No eval/exec on uploaded content
- Runs entirely on localhost by default

---

## Requirements

- Python 3.10+
- Windows / macOS / Linux

### Python packages

```
fastapi
uvicorn[standard]
python-multipart
pymupdf
pypdf
reportlab
spacy
en_core_web_sm (spaCy model)
```

---

## Limitations (v1)

- Image-based / scanned PDFs are not supported (no OCR)
- Very complex multi-column source PDFs may parse in unexpected order
- spaCy model is optional; service degrades gracefully to regex-only name detection
- No resume scoring or keyword gap analysis (planned for v2)

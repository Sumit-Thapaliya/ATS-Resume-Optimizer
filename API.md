# ATS Resume Optimizer — API

Turns a **PDF or DOCX** resume into a clean, **ATS-optimised PDF**.

```
POST https://<your-service>.onrender.com/v1/format
Header:  X-API-Key: <your-key>
Body:    multipart/form-data
```

---

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/format` | `X-API-Key` | Upload resume → get ATS PDF |
| `GET`  | `/v1/health` | none | Liveness + config check |
| `GET`  | `/docs` | none | Interactive Swagger UI |
| `GET`  | `/` | none | Browser upload page (for humans) |

---

## Request

`multipart/form-data` fields:

| Field | Type | Default | Notes |
|---|---|---|---|
| `file` | file | **required** | `.pdf` or `.docx`, max 20 MB |
| `max_pages` | int | `2` | Page budget, 1–5. Lower values **prune content**. |
| `watermark` | bool | `false` | Diagonal stamp |
| `watermark_text` | string | `DRAFT` | |
| `watermark_opacity` | float | `0.05` | 0.0 – 1.0 |

> `max_pages=1` will delete experience bullets / projects / education to fit.
> Leave it at `2` unless the caller explicitly needs one page.

## Response

**`200 OK`** → `application/pdf`

```
content-type: application/pdf
content-disposition: attachment; filename="AMIT_SARRAF_ATS_Resume.pdf"
x-job-id: e1dc237ba7d94079a651bcbe664e3b61
x-service-version: 2.0.0
```

Save the body to disk — it is the PDF itself, not JSON.

### Errors

| Status | Meaning |
|---|---|
| `401` | Missing or invalid `X-API-Key` |
| `400` | No file / empty file |
| `413` | Over 20 MB |
| `415` | Not a PDF or DOCX |
| `422` | No extractable text (scanned image PDF) |
| `429` | Rate limit exceeded (default 20/min) |
| `503` | Server has no API keys configured |

Body is always `{"detail": "<human-readable reason>"}`.

---

## Calling it

### curl
```bash
curl -X POST https://ats-resume-api.onrender.com/v1/format \
  -H "X-API-Key: $RESUME_API_KEY" \
  -F "file=@resume.pdf" \
  -F "max_pages=2" \
  -o ats_resume.pdf
```

### JavaScript / TypeScript (Node 18+, or any browser)
```ts
export async function optimizeResume(file: File, apiKey: string): Promise<Blob> {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('max_pages', '2');

  const res = await fetch('https://ats-resume-api.onrender.com/v1/format', {
    method: 'POST',
    headers: { 'X-API-Key': apiKey },   // do NOT expose this key in a browser — proxy it server-side
    body: fd,
  });

  if (!res.ok) {
    const { detail } = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(`Resume API ${res.status}: ${detail}`);
  }
  return res.blob();   // the PDF
}

// Save it (Node)
import { writeFile } from 'node:fs/promises';
const pdf = await optimizeResume(file, process.env.RESUME_API_KEY!);
await writeFile('out.pdf', Buffer.from(await pdf.arrayBuffer()));
```

> **Never ship the API key to a browser.** Call this API from your own
> server (a Next.js route handler, an Express endpoint, etc.) and let the
> browser talk only to your server.

### Python
```python
import httpx

with open("resume.pdf", "rb") as f:
    r = httpx.post(
        "https://ats-resume-api.onrender.com/v1/format",
        headers={"X-API-Key": os.environ["RESUME_API_KEY"]},
        files={"file": ("resume.pdf", f, "application/pdf")},
        data={"max_pages": "2"},
        timeout=120,
    )
r.raise_for_status()
open("out.pdf", "wb").write(r.content)
```

---

## Deploying to Render

1. Push this repo to GitHub.
2. Render → **New +** → **Blueprint** → pick the repo.
   `render.yaml` is picked up automatically and creates the service.
3. Render will prompt for **`API_KEYS`** (it is marked `sync: false` so the
   secret is never committed). Paste a comma-separated list, e.g.
   `abc123-secret-one,def456-secret-two` — one key per calling project, so you
   can revoke one without breaking the others.
4. Deploy. Your URL is `https://ats-resume-api.onrender.com`.
5. Check it: `curl https://ats-resume-api.onrender.com/v1/health`

Generate a strong key with:
```bash
openssl rand -hex 32
```

### Render free-tier caveats
- **Spins down after ~15 min idle.** The first request after that takes
  **10–30 s** while the container boots and loads the spaCy model.
  Fix: use a paid instance, or ping `/v1/health` every 10 minutes.
- **512 MB RAM.** spaCy + PyMuPDF fit, but it is not generous.
- **Ephemeral disk.** Fine here — uploads and outputs are deleted after each
  request anyway.

### Quick tunnel instead (demo only)
```bash
# local
API_KEYS=demo-key uvicorn main:app --host 0.0.0.0 --port 8000
# separate terminal
ngrok http 8000
```
ngrok gives you a URL like `https://abcd-1234.ngrok-free.app`.
**The URL changes every time you restart ngrok** — fine for showing someone,
wrong for a project that depends on it.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `API_KEYS` | *(empty → endpoint returns 503)* | Comma-separated valid keys |
| `RATE_LIMIT_PER_MINUTE` | `20` | Requests per IP per minute |
| `MAX_UPLOAD_MB` | `20` | Upload ceiling |
| `PORT` | `8000` | Set automatically by Render |

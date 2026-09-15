"""
parser.py
---------
Parses extracted resume text into a structured dictionary.
Handles edge cases:
  - Bullet character cleaning & inline bullet splitting (●, •, ▪, etc.)
  - Accurate date range parsing (e.g. July 2025 – Present, Sep 2022 – June 2025)
  - Section isolation and title/contact extraction
  - Clean project & education parsing without chopped parentheses or merged text
"""

from __future__ import annotations

import re
import logging
from typing import Optional

import spacy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# spaCy model — loaded once at module import
# ---------------------------------------------------------------------------
_NLP: Optional[spacy.Language] = None


def _get_nlp() -> spacy.Language:
    global _NLP
    if _NLP is None:
        try:
            _NLP = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' not found. "
                "Run: python -m spacy download en_core_web_sm"
            )
            _NLP = None
    return _NLP


# ---------------------------------------------------------------------------
# Bullet regexes & helpers
# ---------------------------------------------------------------------------
RE_BULLET_PREFIX = re.compile(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]+\s*")
RE_INLINE_BULLET = re.compile(r"\s*[●•▪⚪◦■▫]\s*")

def _clean_bullet(text: str) -> str:
    return RE_BULLET_PREFIX.sub("", text.strip()).strip()

def _split_inline_bullets(text: str) -> list[str]:
    parts = RE_INLINE_BULLET.split(text)
    res = []
    for p in parts:
        c = _clean_bullet(p)
        if c and len(c) > 1:
            res.append(c)
    return res


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------
RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I
)
RE_PHONE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,5}"
)
RE_LINKEDIN = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-/%]+", re.I
)
RE_GITHUB = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[\w\-/%]+", re.I
)
RE_URL = re.compile(
    r"https?://[^\s]+", re.I
)

# Date range matching: e.g. July 2025 – Present, Sep 2022 – June 2025, 2017 – 2021
MONTH_PAT = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_TOKEN = rf"(?:{MONTH_PAT}[\s,]*)?(?:19|20)\d{{2}}"
DATE_END_TOKEN = rf"(?:{DATE_TOKEN}|Present|Current|Now|Ongoing)"

RE_DATE_RANGE = re.compile(
    rf"\b{DATE_TOKEN}\s*[\-\u2010\u2011\u2012\u2013\u2014\u2015\s,]+\s*{DATE_END_TOKEN}\b",
    re.I,
)
RE_SINGLE_DATE = re.compile(rf"\b{DATE_TOKEN}\b", re.I)
RE_GPA = re.compile(r"\b(?:GPA|CGPA)[:\s]*(\d\.\d{1,2})\b", re.I)
RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")
RE_LOCATION_SUFFIX = re.compile(
    r"\b([A-Z][a-zA-Z]{2,15}(?:\s+[A-Z][a-zA-Z]{2,15})?),\s*([A-Z]{2}|[A-Z][a-zA-Z]+)\s*$",
)

# ---------------------------------------------------------------------------
# Section heading keywords
# ---------------------------------------------------------------------------
SECTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("summary",       ["summary", "profile", "objective", "about", "overview", "professional summary"]),
    ("experience",    ["experience", "work experience", "employment", "work history", "professional experience",
                       "career history", "positions held"]),
    ("education",     ["education", "academic", "qualification", "degree", "schooling"]),
    ("skills",        ["skills", "technical skills", "core competencies", "expertise", "technologies",
                       "tools", "competencies", "key skills", "other", "additional information"]),
    ("projects",      ["projects", "personal projects", "key projects", "notable projects"]),
    ("certifications",["certification", "certifications", "licenses", "credentials", "courses"]),
    ("awards",        ["awards", "honors", "achievements", "recognition"]),
    ("languages",     ["foreign languages", "language proficiency"]),
    ("publications",  ["publications", "papers", "research"]),
    ("volunteer",     ["volunteer", "volunteering", "community service"]),
    ("references",    ["references", "referees"]),
]


def _detect_section(line: str) -> Optional[str]:
    """Return section key if the line looks like a section heading."""
    clean = line.strip().lower().rstrip(":").strip()
    if len(clean) > 60:
        return None
    for section_key, keywords in SECTION_KEYWORDS:
        for kw in keywords:
            if clean == kw or clean.startswith(kw):
                return section_key
    return None


def _clean_line(line: str) -> str:
    return line.strip()


def _is_blank(line: str) -> bool:
    return not line.strip()


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def parse_resume(raw_text: str, pdf_metadata: dict | None = None) -> dict:
    text = raw_text.replace("\f", "\n\n").replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = [ln for ln in text.split("\n")]
    non_blank_lines = [ln for ln in lines if not _is_blank(ln)]

    contact = _extract_contact(text)
    name = _extract_name(non_blank_lines, text, contact)
    title = _extract_title(non_blank_lines, name, contact)

    sections = _slice_sections(lines)

    summary       = _parse_summary(sections.get("summary", []))
    experience    = _parse_experience(sections.get("experience", []))
    education     = _parse_education(sections.get("education", []))
    skills        = _parse_skills(sections.get("skills", []))
    projects      = _parse_projects(sections.get("projects", []))
    certifications= _parse_list_section(sections.get("certifications", []))
    awards        = _parse_list_section(sections.get("awards", []))
    languages     = _parse_list_section(sections.get("languages", []))
    publications  = _parse_list_section(sections.get("publications", []))
    volunteer     = _parse_list_section(sections.get("volunteer", []))

    return {
        "name": name,
        "title": title,
        "contact": contact,
        "summary": summary,
        "experience": experience,
        "education": education,
        "skills": skills,
        "projects": projects,
        "certifications": certifications,
        "awards": awards,
        "languages": languages,
        "publications": publications,
        "volunteer": volunteer,
    }


# ---------------------------------------------------------------------------
# Contact extraction
# ---------------------------------------------------------------------------

def _extract_contact(text: str) -> dict:
    email_m = RE_EMAIL.search(text)
    phone_m = RE_PHONE.search(text)
    linkedin_m = RE_LINKEDIN.search(text)
    github_m = RE_GITHUB.search(text)

    location = _extract_location(text)

    website = ""
    for url_m in RE_URL.finditer(text):
        u = url_m.group()
        if "linkedin" not in u.lower() and "github" not in u.lower():
            website = u
            break

    return {
        "email": email_m.group() if email_m else "",
        "phone": phone_m.group().strip() if phone_m else "",
        "linkedin": linkedin_m.group() if linkedin_m else "",
        "github": github_m.group() if github_m else "",
        "location": location,
        "website": website,
    }


def _extract_location(text: str) -> str:
    top = "\n".join(text.split("\n")[:15])
    loc_re = re.compile(
        r"\b([A-Z][a-zA-Z\s]+),\s*([A-Z]{2}|[A-Z][a-zA-Z]+)\b"
    )
    m = loc_re.search(top)
    if m:
        return m.group().strip()
    return ""


# ---------------------------------------------------------------------------
# Name extraction
# ---------------------------------------------------------------------------

def _extract_name(non_blank_lines: list[str], full_text: str, contact: dict) -> str:
    nlp = _get_nlp()
    if nlp is not None:
        snippet = full_text[:400]
        doc = nlp(snippet)
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                candidate = ent.text.strip()
                if "@" not in candidate and len(candidate.split()) >= 2:
                    return candidate

    skip_vals = {contact["email"], contact["phone"], contact["linkedin"]}
    for line in non_blank_lines[:8]:
        stripped = line.strip()
        if not stripped:
            continue
        if any(sv and sv in stripped for sv in skip_vals if sv):
            continue
        if RE_EMAIL.search(stripped) or RE_PHONE.search(stripped) or RE_URL.search(stripped):
            continue
        words = stripped.split()
        if 1 < len(words) <= 5 and _detect_section(stripped) is None:
            if all(w[0].isupper() for w in words if w.isalpha()):
                return stripped

    return non_blank_lines[0].strip() if non_blank_lines else "Unknown Candidate"


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

def _extract_title(non_blank_lines: list[str], name: str, contact: dict) -> str:
    skip_vals = {contact.get("email"), contact.get("phone")}
    found_name = False

    for line in non_blank_lines[:10]:
        stripped = line.strip()
        if stripped == name or name in stripped:
            found_name = True
            continue
        if found_name:
            if any(sv and sv in stripped for sv in skip_vals if sv):
                continue
            if RE_EMAIL.search(stripped) or RE_PHONE.search(stripped):
                continue
            if _detect_section(stripped) is not None:
                break
            if any(kw in stripped.lower() for kw in ["developer", "engineer", "architect", "lead", "manager", "designer", "specialist", "consultant", "joiner", "|"]):
                return stripped
    return ""


# ---------------------------------------------------------------------------
# Section slicer
# ---------------------------------------------------------------------------

def _slice_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current_section = "header"

    for line in lines:
        detected = _detect_section(line)
        if detected:
            current_section = detected
            if detected not in sections:
                sections[detected] = []
        else:
            sections.setdefault(current_section, []).append(line)

    return sections


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_summary(lines: list[str]) -> str:
    text = " ".join(_clean_line(ln) for ln in lines if not _is_blank(ln))
    return text.strip()


def _parse_skills(lines: list[str]) -> list[str]:
    skills: list[str] = []
    for line in lines:
        stripped = _clean_line(line)
        if not stripped:
            continue

        clean = _clean_bullet(stripped)
        if ":" in clean and not clean.lower().startswith("http"):
            skills.append(clean)
            continue

        for sep in [",", "|", "•", "·", "/"]:
            if sep in clean:
                parts = [p.strip() for p in clean.split(sep) if p.strip()]
                skills.extend(parts)
                break
        else:
            if clean:
                skills.append(clean)

    seen = set()
    result = []
    for s in skills:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def _parse_experience(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None

    for line in lines:
        stripped = _clean_line(line)
        if _is_blank(line):
            continue

        date_m = RE_DATE_RANGE.search(stripped)
        is_bullet = bool(RE_BULLET_PREFIX.match(stripped)) or ("●" in stripped or "•" in stripped)

        if date_m and not is_bullet:
            if current:
                entries.append(current)
            dates = date_m.group().strip()
            remainder = stripped[:date_m.start()].strip().rstrip("–—-|,").strip()
            role, company, location = _split_role_company(remainder)
            current = {
                "company": company,
                "role": role,
                "dates": dates,
                "location": location,
                "bullets": [],
            }
        elif current is not None:
            bullets = _split_inline_bullets(stripped)
            if bullets:
                current["bullets"].extend(bullets)
            else:
                loc_m = RE_LOCATION_SUFFIX.search(stripped)
                line_loc = ""
                line_text = stripped
                if loc_m:
                    line_loc = loc_m.group(0).strip()
                    line_text = stripped[:loc_m.start()].strip().rstrip(",|–-")

                if not current["role"]:
                    current["role"] = line_text
                    if line_loc and not current["location"]:
                        current["location"] = line_loc
                elif not current["company"]:
                    current["company"] = line_text
                    if line_loc and not current["location"]:
                        current["location"] = line_loc
                elif stripped:
                    clean_p = _clean_bullet(stripped)
                    if clean_p:
                        current["bullets"].append(clean_p)

    if current:
        entries.append(current)

    return entries


def _split_role_company(text: str) -> tuple[str, str, str]:
    location = ""
    loc_m = RE_LOCATION_SUFFIX.search(text)
    if loc_m:
        location = loc_m.group(0).strip()
        text = text[:loc_m.start()].strip().rstrip(",|–-")

    for sep in [" | ", " – ", " — ", " - ", " @ ", " / "]:
        if sep in text:
            parts = [p.strip() for p in text.split(sep, 1)]
            return parts[0], parts[1] if len(parts) > 1 else "", location

    return "", text, location


def _parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None

    for line in lines:
        stripped = _clean_line(line)
        if _is_blank(line):
            continue

        date_m = RE_DATE_RANGE.search(stripped) or RE_YEAR.search(stripped)
        gpa_m = RE_GPA.search(stripped)
        is_bullet = bool(RE_BULLET_PREFIX.match(stripped))

        if date_m and not is_bullet:
            if current:
                entries.append(current)
            dates_text = date_m.group().strip()
            remainder = stripped[:date_m.start()].strip().rstrip("–—-|,").strip()
            remainder = re.sub(r"[\(\)]\s*$", "", remainder).strip()
            remainder = re.sub(r"^\s*[\(\)]", "", remainder).strip()

            degree, institution, location = _split_degree_institution(remainder)
            current = {
                "institution": institution,
                "degree": degree,
                "dates": dates_text,
                "location": location,
                "gpa": gpa_m.group(1) if gpa_m else "",
                "bullets": [],
            }
        elif current is not None:
            bullets = _split_inline_bullets(stripped)
            if bullets:
                current["bullets"].extend(bullets)
            elif gpa_m and not current["gpa"]:
                current["gpa"] = gpa_m.group(1)
            else:
                loc_m = RE_LOCATION_SUFFIX.search(stripped)
                line_loc = ""
                line_text = stripped
                if loc_m:
                    line_loc = loc_m.group(0).strip()
                    line_text = stripped[:loc_m.start()].strip().rstrip(",|–-")

                if not current["degree"]:
                    current["degree"] = line_text
                    if line_loc and not current["location"]:
                        current["location"] = line_loc
                elif not current["institution"]:
                    current["institution"] = line_text
                    if line_loc and not current["location"]:
                        current["location"] = line_loc
                elif stripped:
                    clean_p = _clean_bullet(stripped)
                    if clean_p:
                        current["bullets"].append(clean_p)
        else:
            if stripped:
                clean_p = _clean_bullet(stripped)
                current = {
                    "institution": clean_p,
                    "degree": "",
                    "dates": "",
                    "location": "",
                    "gpa": gpa_m.group(1) if gpa_m else "",
                    "bullets": [],
                }

    if current:
        entries.append(current)

    return entries


def _split_degree_institution(text: str) -> tuple[str, str, str]:
    location = ""
    loc_m = RE_LOCATION_SUFFIX.search(text)
    if loc_m:
        location = loc_m.group(0).strip()
        text = text[:loc_m.start()].strip().rstrip(",|–-")

    for sep in [" | ", " – ", " — ", " - ", " @ ", " at ", " / "]:
        if sep in text:
            parts = [p.strip() for p in text.split(sep, 1)]
            return parts[0], parts[1] if len(parts) > 1 else "", location
    return "", text, location


def _parse_projects(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None

    for line in lines:
        stripped = _clean_line(line)
        if _is_blank(line):
            if current:
                entries.append(current)
                current = None
            continue

        is_bullet = bool(RE_BULLET_PREFIX.match(stripped)) or ("●" in stripped or "•" in stripped)
        date_m = RE_DATE_RANGE.search(stripped)

        if not is_bullet and (current is None or date_m or re.search(r"\([a-zA-Z0-9\.\-]+\.(?:com|org|io|net|app|dev)\)", stripped)):
            if current:
                entries.append(current)
            dates = date_m.group().strip() if date_m else ""
            name_part = stripped[:date_m.start()].strip() if date_m else stripped
            name_part = _clean_bullet(name_part)
            current = {"name": name_part, "tech": "", "dates": dates, "bullets": []}
        elif current is not None:
            if re.search(r"\btech(?:nolog(?:y|ies))?s?:|stack:|built with:|using:", stripped, re.I):
                current["tech"] = _clean_bullet(stripped)
            else:
                bullets = _split_inline_bullets(stripped)
                if bullets:
                    current["bullets"].extend(bullets)
                else:
                    clean_p = _clean_bullet(stripped)
                    if clean_p:
                        current["bullets"].append(clean_p)

    if current:
        entries.append(current)

    return entries


def _parse_list_section(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = _clean_line(line)
        if not stripped:
            continue
        bullets = _split_inline_bullets(stripped)
        if bullets:
            items.extend(bullets)
        else:
            for sep in [",", "|"]:
                if sep in stripped:
                    items.extend(_clean_bullet(p) for p in stripped.split(sep) if _clean_bullet(p))
                    break
            else:
                clean = _clean_bullet(stripped)
                if clean:
                    items.append(clean)
    return items

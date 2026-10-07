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


# A line that is ONLY bullet glyphs. Happens when the bullet is laid out as its
# own text block in the PDF, so PyMuPDF returns e.g. '\u25cf ' on a line by itself.
RE_BULLET_ONLY = re.compile(r"^[\s\-\u2022\u00b7\*\u25aa\u25b8\u25ba\u2713\u2714\u25cf\u26aa\u25e6\u25a0\u25ab\u2043\u2013\u2014o]+$")

# Sentence-final punctuation - decides whether the next line continues the
# current bullet or starts something new.
RE_TERMINAL = re.compile(r"[.!?;:]\s*$")


def _is_bullet_only(text: str) -> bool:
    """True for lines containing nothing but bullet glyphs."""
    return bool(RE_BULLET_ONLY.match(text))


# A wrapped line is usually long, or ends on a comma / conjunction / preposition.
RE_DANGLING_END = re.compile(
    r"(?:[,;:\-\u2013\u2014/]|"
    r"\b(?:and|or|the|a|an|of|to|in|for|with|on|at|by|from|through|across|into|as|"
    r"that|which|using|via|per|than|but|while|after|before|during|within|without|"
    r"including|such|etc)\s*)$",
    re.I,
)
CONTINUATION_MIN_LEN = 80


# Resume bullets overwhelmingly open with a past-tense action verb. In
# glyph-less lists this is the only signal that a new bullet has started.
RE_ACTION_VERB_START = re.compile(
    r"^(?:[A-Z][a-z]+(?:ed|ted|led|ped|ned|red|sed|ved|wed)\b|"
    r"(?:Built|Led|Drove|Spearheaded|Oversaw|Wrote|Set|Ran|Made|Won|Grew|Cut|Saved|"
    r"Managed|Created|Designed|Developed|Engineered|Architected|Implemented|Deployed|"
    r"Integrated|Optimized|Optimised|Reduced|Improved|Increased|Delivered|Maintained|"
    r"Collaborated|Mentored|Automated|Migrated|Launched|Configured|Performed|"
    r"Orchestrated|Streamlined|Established|Coordinated|Researched|Analyzed|Analysed)\b)"
)


def _looks_like_new_bullet(text: str) -> bool:
    """
    True when a bare line clearly starts a NEW bullet rather than continuing
    the previous one.

    Needed for glyph-less bullet lists (common in LinkedIn-style exports),
    where every bullet is long and unpunctuated, so the wrap heuristic alone
    would merge an entire job's achievements into one giant paragraph.
    """
    t = text.strip()
    return len(t) >= 35 and bool(RE_ACTION_VERB_START.match(t))


def _is_continuation(prev: str, nxt: str = "") -> bool:
    """
    True when `prev` looks like a sentence cut off mid-line by the PDF/DOCX
    text extractor, rather than a finished bullet.

    Requires BOTH that the text is unterminated AND that it either runs long
    (a real wrap) or ends on a dangling comma/conjunction. This stops a short,
    complete bullet in a glyph-less list from absorbing the next bullet.
    """
    if not prev or RE_TERMINAL.search(prev):
        return False
    # A dangling comma/conjunction always wins: the sentence is mid-clause.
    if RE_DANGLING_END.search(prev):
        return True
    # Otherwise a line that opens with an action verb starts a new bullet,
    # even though the previous one ran long and carried no full stop.
    if nxt and _looks_like_new_bullet(nxt):
        return False
    return len(prev) >= CONTINUATION_MIN_LEN


def _looks_like_entry_header(text: str) -> bool:
    """
    True if a bare (non-bulleted) line looks like a 'Role \u2014 Company' header
    rather than a sentence. Stops the NEXT job's header being swallowed into
    the current job's bullet list.
    """
    if RE_TERMINAL.search(text) or len(text) > 80:
        return False
    return bool(re.search(r"\s(?:\u2014|\u2013|\||at)\s", text))


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
    ("summary",       ["summary", "profile", "objective", "about", "about me", "overview",
                       "professional summary", "career objective", "career summary",
                       "executive summary", "personal statement", "professional profile",
                       "career profile", "introduction", "bio"]),
    ("experience",    ["experience", "work experience", "employment", "work history",
                       "professional experience", "career history", "positions held",
                       "employment history", "professional background", "work", "internship",
                       "internships", "internship experience", "industry experience",
                       "relevant experience", "career", "professional career", "engagements"]),
    ("education",     ["education", "academic", "qualification", "qualifications", "degree",
                       "schooling", "academics", "academic background", "academic qualifications",
                       "educational background", "education & training", "education and training",
                       "studies", "academic history"]),
    ("skills",        ["skills", "technical skills", "core competencies", "expertise",
                       "technologies", "tools", "competencies", "key skills",
                       "additional information", "technical proficiencies", "proficiencies",
                       "tech stack", "technology stack", "skill set", "skillset",
                       "areas of expertise", "strengths", "core skills", "professional skills",
                       "tools & technologies", "tools and technologies", "technical expertise",
                       "it skills", "computer skills", "software skills", "specialties",
                       "specialisations", "specializations"]),
    ("projects",      ["projects", "personal projects", "key projects", "notable projects",
                       "academic projects", "selected projects", "project experience",
                       "side projects", "portfolio", "major projects", "project work",
                       "capstone", "capstone project", "open source", "open source contributions"]),
    ("certifications",["certification", "certifications", "licenses", "licences", "credentials",
                       "courses", "coursework", "relevant coursework", "training",
                       "trainings", "professional development", "licenses & certifications",
                       "certificates", "online courses", "workshops"]),
    ("awards",        ["awards", "honors", "honours", "achievements", "recognition",
                       "key achievements", "accomplishments", "awards & honors",
                       "awards and honours", "scholarships", "accolades", "distinctions",
                       "extracurricular", "extracurricular activities", "activities"]),
    ("languages",     ["foreign languages", "language proficiency", "languages known",
                       "spoken languages", "language skills"]),
    ("publications",  ["publications", "papers", "research", "research experience",
                       "research papers", "conference papers", "patents", "presentations"]),
    ("volunteer",     ["volunteer", "volunteering", "community service", "volunteer experience",
                       "social work", "community involvement", "leadership",
                       "positions of responsibility"]),
    ("references",    ["references", "referees", "references available"]),
    # Low-value sections. They are parsed so they stop leaking into the section
    # above them (certifications used to absorb "Hobbies", "Languages", etc.),
    # and the PDF fitter drops them first when squeezing onto one page.
    ("interests",     ["interests", "hobbies", "hobbies & interests", "hobbies and interests",
                       "personal interests", "technical interests", "research interests",
                       "areas of interest", "extra-curricular activities & interests",
                       "extra-curricular activities", "extra curricular activities",
                       "extracurricular activities & interests", "activities & interests",
                       "personal details", "personal information", "declaration"]),
]

# Matched last and only as a whole line, because these words are too generic to
# risk a prefix match against real content (e.g. "Other duties included ...").
_WEAK_SECTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("skills", ["other", "misc", "miscellaneous", "additional"]),
]

# A heading is a short line; prefix matching beyond this length is almost always
# a false positive on a sentence that merely begins with a section word.
_MAX_HEADING_LEN = 60
_MAX_PREFIX_HEADING_LEN = 40


def _detect_section(line: str) -> Optional[str]:
    """
    Return the section key if the line looks like a section heading.

    Matching is deliberately ordered:
      1. exact match on a known synonym            ("Technical Skills")
      2. prefix match on a short line              ("Skills & Tools")
      3. whole-line match on a weak/generic word   ("Other")

    Longest keywords are tried first so that "work experience" is not
    swallowed by the shorter "work".
    """
    raw = line.strip()
    # 'Tech Stack: MERN Stack, Angular, MongoDB' is CONTENT, not a heading:
    # a real heading has nothing of substance after the colon. Without this
    # guard such a line ended the Experience section and swallowed the jobs
    # below it into Skills.
    if ":" in raw:
        after = raw.split(":", 1)[1].strip()
        if len(after.split()) >= 2:
            return None

    clean = line.strip().lower().rstrip(":").rstrip("-").strip()
    # Strip decorative characters some templates wrap headings in.
    clean = clean.strip("_*•|=~ ").strip()
    if not clean or len(clean) > _MAX_HEADING_LEN:
        return None
    # A heading rarely ends in a sentence period or contains sentence commas.
    if clean.endswith("."):
        return None

    # 1. Exact synonym match.
    for section_key, keywords in SECTION_KEYWORDS:
        if clean in keywords:
            return section_key

    # 2. Prefix match, longest keyword first, only on short lines.
    if len(clean) <= _MAX_PREFIX_HEADING_LEN:
        candidates: list[tuple[int, str]] = []
        for section_key, keywords in SECTION_KEYWORDS:
            for kw in keywords:
                if clean.startswith(kw):
                    candidates.append((len(kw), section_key))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1]

    # 3. Generic words, whole line only.
    for section_key, keywords in _WEAK_SECTION_KEYWORDS:
        if clean in keywords:
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
    interests     = _parse_list_section(sections.get("interests", []))

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
        "interests": interests,
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

# Job-title words. A line made only of these is a headline, not a person's name.
RE_TITLE_WORDS = re.compile(
    r"\b(developer|engineer|manager|analyst|designer|architect|consultant|"
    r"administrator|specialist|scientist|technician|officer|director|lead|head|"
    r"intern|trainee|associate|coordinator|executive|researcher|joiner)\b", re.I
)


def _looks_like_person_name(line: str) -> bool:
    """
    Guard the name extractor.

    Without this, a resume whose first line is an availability note
    ('Immediate Joiner') or a headline ('Senior Full Stack Developer')
    gets adopted as the candidate's name - which then becomes the PDF's
    main heading AND the download filename.
    """
    if not line:
        return False
    if RE_AVAILABILITY.fullmatch(line.strip()):
        return False
    if RE_TITLE_WORDS.search(line):
        return False
    # "Birgunj, Nepal" is a location, not a person.
    if RE_LOCATION_SUFFIX.search(line.strip()):
        return False
    return True


def _header_lines(non_blank_lines: list[str], limit: int = 8) -> list[str]:
    """
    The lines above the first section heading. A real name only ever lives
    here; scanning past it is how 'Artificial Intelligence' (a spaCy PERSON
    false positive inside a summary) ended up as a candidate's name.
    """
    header: list[str] = []
    for line in non_blank_lines[:limit]:
        if _detect_section(line.strip()) is not None:
            break
        header.append(line.strip())
    return header


def _extract_name(non_blank_lines: list[str], full_text: str, contact: dict) -> str:
    header = _header_lines(non_blank_lines)

    # spaCy, but ONLY over the header area - never the summary text.
    nlp = _get_nlp()
    if nlp is not None and header:
        doc = nlp("\n".join(header[:4]))
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                candidate = ent.text.strip()
                if ("@" not in candidate and len(candidate.split()) >= 2
                        and _looks_like_person_name(candidate)):
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
        # Stop at the first section heading - the name lives in the header
        # area only. Scanning further adopts a company name instead.
        if _detect_section(stripped) is not None:
            break
        words = stripped.split()
        if 1 < len(words) <= 5:
            if (all(w[0].isupper() for w in words if w.isalpha())
                    and _looks_like_person_name(stripped)):
                return stripped

    # Last resort. Only the header area is eligible, and the same
    # contact / section / availability / title / location filters apply.
    for line in non_blank_lines[:4]:
        cand = line.strip()
        if not cand:
            continue
        if any(sv and sv in cand for sv in skip_vals if sv):
            continue
        if RE_EMAIL.search(cand) or RE_PHONE.search(cand) or RE_URL.search(cand):
            continue
        if _detect_section(cand) is not None:
            break
        if _looks_like_person_name(cand):
            return cand
    return "Unknown Candidate"


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

# Availability / notice-period phrases that people paste into their headline.
# They are not job titles and should not appear in the ATS header line.
RE_AVAILABILITY = re.compile(
    r"""\b(?:
        immediate(?:ly)?\s+(?:joiner|joining|join(?:ing)?\s)? |
        available\s+(?:immediately|from\b|to\s+join\b)|
        notice\s+period\b[^|]*|
        open\s+to\s+work\b |
        seeking\s+(?:new\s+)?opportunit\w+\b |
        willing\s+to\s+relocate\b |
        ready\s+to\s+join\b |
        can\s+join\s+immediately\b
    )""",
    re.I | re.X,
)


def _clean_title(raw: str) -> str:
    """
    Strip availability notes from a headline.

    'Senior Full Stack Developer | Cloud & DevOps Engineer | Immediate Joiner'
      -> 'Senior Full Stack Developer | Cloud & DevOps Engineer'

    Segments are split on the separators resumes actually use, so a phrase is
    only removed when it stands alone - it never eats a real job title.
    """
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"\s*[|·•/]\s*", raw) if p.strip()]
    kept = [p for p in parts if not RE_AVAILABILITY.fullmatch(p)]

    # Nothing survived (headline was ONLY an availability note) - fall back to
    # removing the phrase inline rather than returning an empty title.
    if not kept:
        cleaned = RE_AVAILABILITY.sub("", raw)
        return re.sub(r"\s*[|·•/]\s*$", "", cleaned).strip(" |·•/")

    return " | ".join(kept)


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
                cleaned = _clean_title(stripped)
                if cleaned:
                    return cleaned
                # The line was only an availability note - keep looking.
                continue
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
        if not clean:
            continue

        # A short orphan line with no separators is the tail of a wrapped
        # category, e.g. "... Nginx, Reverse" / "Proxy".
        if (skills
                and ":" not in clean
                and not any(c in clean for c in (",", "|", "/", "\u2022", "\u00b7"))
                and len(clean.split()) <= 3
                and not RE_TERMINAL.search(skills[-1])):
            skills[-1] = f"{skills[-1]} {clean}".strip()
            continue

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


def _looks_like_job_title(text: str) -> bool:
    """
    True when a short bare line reads like a job title rather than prose.

    Used for the date-first layout (LinkedIn exports, many modern templates):

        Jul 2026 - Present
        Associate Software Engineer     <- title
        Aurora Forge                    <- company
        Kathmandu, Nepal - On-site      <- location

    The old parser only looked BACKWARDS for a header, so all three of those
    lines were swallowed as bullets and the entry ended up with no title.
    """
    t = text.strip().rstrip(",;")
    if not t or len(t) > 70 or RE_TERMINAL.search(t):
        return False
    if len(t.split()) > 8:
        return False
    return bool(RE_TITLE_WORDS.search(t))


def _looks_like_org_name(text: str) -> bool:
    """True when a short bare line reads like a company/organisation name."""
    t = text.strip().rstrip(",;")
    if not t or len(t) > 70 or RE_TERMINAL.search(t):
        return False
    words = t.split()
    if len(words) > 7:
        return False
    # Mostly capitalised words, no trailing sentence punctuation.
    caps = [w for w in words if w[:1].isupper() or w.isupper()]
    return len(caps) >= max(1, len(words) - 1)


RE_TECH_LINE = re.compile(r"^\s*(?:tech(?:nolog(?:y|ies))?\s*stack|tech|stack|built\s+with|tools)\s*:",
                          re.I)


def _parse_experience(lines: list[str]) -> list[dict]:
    """
    Parse the experience section.

    Handles the layouts real resumes actually use:
      * 'Role \u2014 Company' on one line with the dates on the NEXT line
      * bullet glyphs sitting on a line of their own ('\u25cf ' alone)
      * one bullet wrapped across several extracted lines
    """
    entries: list[dict] = []
    current: dict | None = None
    pending_headers: list[str] = []   # up to 2 header lines seen before a date
    open_bullet = False          # a bare bullet glyph was just seen
    backfill = 0                 # bare lines still allowed to fill role/company

    def _flush():
        nonlocal current
        if current:
            entries.append(current)
            current = None

    for line in lines:
        stripped = _clean_line(line)
        if _is_blank(line):
            continue

        # A line that is nothing but a bullet glyph introduces the bullet that
        # follows. Skipping it here stops it being parsed as a role/company.
        if _is_bullet_only(stripped):
            open_bullet = True
            continue

        date_m = RE_DATE_RANGE.search(stripped)
        is_bullet = (
            open_bullet
            or bool(RE_BULLET_PREFIX.match(stripped))
            or ("\u25cf" in stripped) or ("\u2022" in stripped)
        )
        open_bullet = False

        # ── New entry: a line carrying a date range ────────────────────────
        if date_m and not is_bullet:
            _flush()
            dates = date_m.group().strip()
            remainder = stripped[:date_m.start()].strip().rstrip("\u2013\u2014-|,").strip()

            # Role/Company frequently sits on the PREVIOUS line(s). Some
            # templates stack them:  Title / Company / Date.
            role = company = location = ""
            if not remainder and pending_headers:
                if len(pending_headers) >= 2 and _looks_like_job_title(pending_headers[-2]):
                    role = _clean_title(pending_headers[-2])
                    company = pending_headers[-1]
                else:
                    remainder = pending_headers[-1]
            pending_headers = []

            if not (role or company):
                role, company, location = _split_role_company(remainder)
            current = {
                "company": company,
                "role": role,
                "dates": dates,
                "location": location,
                "bullets": [],
            }
            # Date-first layout: the role/company lines follow the date line.
            # Allow the next couple of bare lines to backfill them.
            if not (role or company):
                backfill = 3
            elif not location:
                # Role/company were read from the lines above the date, so only
                # the location line (if any) still needs claiming - otherwise it
                # renders as a bullet: '- Kathmandu, Bagmati, Nepal - On-site'.
                backfill = 1
            else:
                backfill = 0
            continue

        clean = _clean_bullet(stripped)
        if not clean:
            continue

        # ── Before the first entry: remember as a possible header ──────────
        if current is None:
            pending_headers.append(clean)
            del pending_headers[:-2]
            continue

        # ── Date-first layout: backfill role / company / location ─────────
        if backfill and not is_bullet:
            if not current["role"] and _looks_like_job_title(clean):
                current["role"] = _clean_title(clean)
                backfill -= 1
                continue
            if current["role"] and not current["company"] and _looks_like_org_name(clean):
                loc_m = RE_LOCATION_SUFFIX.search(clean)
                if loc_m and loc_m.start() > 0:
                    current["company"] = clean[: loc_m.start()].strip().rstrip(",|–-").strip()
                    current["location"] = current["location"] or loc_m.group(0).strip()
                else:
                    current["company"] = clean
                backfill -= 1
                continue
            if not current["location"] and len(clean) < 70 and (
                RE_LOCATION_SUFFIX.search(clean)
                # 'Kathmandu, Bagmati, Nepal - On-site' has no 2-part suffix,
                # but a short comma-separated line here is still a location.
                or ("," in clean and len(clean.split()) <= 8 and not RE_TERMINAL.search(clean))
            ):
                current["location"] = clean.strip()
                backfill -= 1
                continue
            backfill = 0

        # ── Inside an entry ────────────────────────────────────────────────
        if is_bullet:
            current["bullets"].extend(_split_inline_bullets(stripped) or [clean])
            continue

        # Not bulleted: if the previous bullet is still an open sentence,
        # this line is its continuation.
        if (current["bullets"] and not RE_TECH_LINE.match(clean)
                and _is_continuation(current["bullets"][-1], clean)):
            current["bullets"][-1] = f"{current['bullets'][-1]} {clean}".strip()
            continue

        # Previous bullet closed -> either the next job's header or a
        # plain (unglyphed) bullet.
        if _looks_like_entry_header(clean) or _looks_like_job_title(clean):
            pending_headers.append(clean)
            del pending_headers[:-2]
        else:
            current["bullets"].append(clean)

    _flush()
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


def _balance_parens(text: str) -> str:
    """Trim only *unmatched* outer parentheses - never a matching pair."""
    t = text.strip()
    changed = True
    while changed:
        changed = False
        if t.endswith(")") and t.count("(") < t.count(")"):
            t = t[:-1].strip(); changed = True
        elif t.endswith("(") and t.count("(") > t.count(")"):
            t = t[:-1].strip(); changed = True
        elif t.startswith("(") and t.count("(") > t.count(")"):
            t = t[1:].strip(); changed = True
        elif t.startswith(")") and t.count(")") > t.count("("):
            t = t[1:].strip(); changed = True
    return t.rstrip(" ,;|\u2013\u2014-").strip()


def _split_compound_education(line: str) -> list[str]:
    """
    '12th \u2014 School A (2017) | 10th \u2014 School B (2015)' holds two entries on
    one line. Split it only when every pipe-separated part carries its own year.
    """
    if "|" not in line:
        return [line]
    parts = [part.strip() for part in line.split("|") if part.strip()]
    if len(parts) < 2:
        return [line]
    if all(RE_YEAR.search(part) for part in parts):
        return parts
    return [line]


RE_DEGREE_WORDS = re.compile(
    r"\b(?:bachelor|master|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\.?|m\.?\s?e\.?|b\.?\s?sc|m\.?\s?sc|"
    r"b\.?\s?a\.?|m\.?\s?a\.?|b\.?\s?com|mba|bba|bca|mca|ph\.?\s?d|doctorate|diploma|"
    r"higher\s+secondary|secondary\s+education|intermediate|associate\s+degree|"
    r"10th|12th|\+2|slc|see|hsc|ssc)\b",
    re.I,
)


def _looks_like_degree(text: str) -> bool:
    """
    True when a line names a qualification rather than a school.

    Degree-first layouts put the qualification above the institution:
        Bachelor of Engineering (BE) in Computer Engineering
        2023 - Present
        Advance College of Engineering and Management, Kathmandu
    Treating line 1 as the institution left the real college unrecorded and
    produced the 'education entry has no institution name' warning.
    """
    t = text.strip()
    if not t or len(t) > 90:
        return False
    if re.search(r"\b(?:university|college|institute|school|academy|campus)\b", t, re.I):
        # 'Bachelor of Technology, XYZ Institute' names both - treat as degree
        # only when the qualification word comes first.
        deg = RE_DEGREE_WORDS.search(t)
        org = re.search(r"\b(?:university|college|institute|school|academy|campus)\b", t, re.I)
        return bool(deg and org and deg.start() < org.start())
    return bool(RE_DEGREE_WORDS.search(t))


def _parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    edu_backfill = 0             # bare lines still allowed to fill institution

    expanded: list[str] = []
    for raw_line in lines:
        expanded.extend(_split_compound_education(_clean_line(raw_line)))

    for line in expanded:
        stripped = _clean_line(line)
        if _is_blank(line):
            continue

        date_m = RE_DATE_RANGE.search(stripped) or RE_YEAR.search(stripped)
        gpa_m = RE_GPA.search(stripped)
        is_bullet = bool(RE_BULLET_PREFIX.match(stripped))

        if date_m and not is_bullet:
            dates_text = date_m.group().strip()
            remainder = stripped[:date_m.start()].strip().rstrip("–—-|,").strip()
            remainder = _balance_parens(remainder)

            # Date-only line directly under an entry that still has no dates:
            #   Panipat Institute of Engineering and Technology
            #   Bachelor of Technology in Computer Science
            #   Aug 2022 - May 2026          <- belongs to the entry above
            # Starting a new entry here produced a phantom record with no
            # institution name, which is what triggered the parse warning.
            if not remainder and current and not current["dates"]:
                current["dates"] = dates_text
                if gpa_m and not current["gpa"]:
                    current["gpa"] = gpa_m.group(1)
                # Degree-first layout, institution still to come:
                #   Bachelor of Engineering (BE) in Computer Engineering
                #   2023 - Present
                #   Advance College of Engineering and Management, Kathmandu
                edu_backfill = 2 if not current["institution"] else 0
                continue

            if current:
                entries.append(current)

            degree, institution, location = _split_degree_institution(remainder)
            current = {
                "institution": institution,
                "degree": degree,
                "dates": dates_text,
                "location": location,
                "gpa": gpa_m.group(1) if gpa_m else "",
                "bullets": [],
            }
        elif (current is not None and not is_bullet and _looks_like_degree(stripped)
              and current["institution"] and (current["dates"] or current["degree"])):
            # A new qualification line while the current entry is already
            # complete starts the NEXT education entry, instead of being
            # appended as a bullet of the previous one.
            entries.append(current)
            clean_p = _clean_bullet(stripped)
            edu_backfill = 2
            current = {
                "institution": "",
                "degree": clean_p,
                "dates": "",
                "location": "",
                "gpa": gpa_m.group(1) if gpa_m else "",
                "bullets": [],
            }
        elif current is not None and edu_backfill and not is_bullet and not current["institution"]:
            # The line right after a bare date line is the institution.
            edu_backfill -= 1
            loc_m = RE_LOCATION_SUFFIX.search(stripped)
            if loc_m and loc_m.start() > 0:
                current["institution"] = stripped[: loc_m.start()].strip().rstrip(",|-").strip()
                if not current["location"]:
                    current["location"] = loc_m.group(0).strip()
            else:
                current["institution"] = _clean_bullet(stripped)
        elif current is not None:
            parts = _split_inline_bullets(stripped) or []
            # A plain line comes back as a single-item list, which used to be
            # appended as a bullet - that is how the degree and location lines
            # ended up as bullets instead of filling their own fields.
            bullets = parts if (is_bullet or len(parts) > 1) else []
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

                if line_loc and not line_text and not current["location"]:
                    # A bare 'City, Region' line belongs in location, not in
                    # the bullet list under the degree.
                    current["location"] = line_loc
                elif not current["degree"]:
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
                # Degree-first layout: this line is the qualification, and the
                # institution arrives a line or two later.
                as_degree = _looks_like_degree(clean_p)
                if as_degree:
                    edu_backfill = 2
                current = {
                    "institution": "" if as_degree else clean_p,
                    "_degree_first": as_degree,
                    "degree": clean_p if as_degree else "",
                    "dates": "",
                    "location": "",
                    "gpa": gpa_m.group(1) if gpa_m else "",
                    "bullets": [],
                }

    if current:
        entries.append(current)

    for entry in entries:
        entry.pop("_degree_first", None)

    return entries


def _split_degree_institution(text: str) -> tuple[str, str, str]:
    """
    Split 'Degree - Institution, Location' into its three parts.

    The degree/institution separator is handled BEFORE the location suffix.
    Doing it the other way round let the location regex swallow the school:
    '10th Standard - Mascot Academy, Nepal' lost 'Mascot Academy' and kept
    only '10th Standard -' as the institution.
    """
    separators = [" | ", " \u2013 ", " \u2014 ", " - ", " @ ", " at ", " / "]

    degree = ""
    rest = text
    for sep in separators:
        if sep in text:
            head, tail = [p.strip() for p in text.split(sep, 1)]
            degree, rest = head, tail
            break

    location = ""
    loc_m = RE_LOCATION_SUFFIX.search(rest)
    # 'DAV RB Kedia HSS School, Nepal' looks like 'Name, Country' to the
    # location regex. If the match still names an institution it is part of
    # the school name, not the city.
    if loc_m and re.search(r"\b(?:school|college|university|institute|academy|campus|hss)\b",
                           loc_m.group(0), re.I):
        loc_m = None
    if loc_m and loc_m.start() > 0:
        location = loc_m.group(0).strip()
        rest = rest[:loc_m.start()].strip().rstrip(",|\u2013-")
    elif loc_m and not degree:
        # The whole line is just a location.
        location = loc_m.group(0).strip()
        rest = ""

    return degree, rest, location


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

        # Many resumes label each project with a round bullet and use dashes
        # for its achievements:
        #     • MedBridge - Healthcare Resource Exchange Platform
        #     - Built a multi-hospital platform ...
        # Treating every glyph line as an achievement collapsed all projects
        # into one entry, so a round bullet starts a NEW project here.
        is_project_header = bool(re.match(r"^\s*[•●]\s*\S", stripped)) and len(stripped) < 110
        # Unbulleted 'Name - Subtitle | Tech' header lines do the same job.
        if not is_bullet and current is not None and not date_m:
            is_project_header = is_project_header or (
                _looks_like_entry_header(stripped)
                and len(stripped) < 110
                and stripped[:1].isupper()
                and not RE_DANGLING_END.search(stripped)
            )

        if is_project_header or (not is_bullet and (
                current is None or date_m
                or re.search(r"\([a-zA-Z0-9\.\-]+\.(?:com|org|io|net|app|dev)\)", stripped))):
            if current:
                entries.append(current)
            dates = date_m.group().strip() if date_m else ""
            name_part = stripped[:date_m.start()].strip() if date_m else stripped
            name_part = _clean_bullet(name_part)

            # A bare date line under a named project belongs to that project,
            # it does not start a new (nameless) one.
            if date_m and not name_part and current is not None and not current["dates"]:
                current["dates"] = dates
                continue
            current = {"name": name_part, "tech": "", "dates": dates, "bullets": []}
        elif current is not None:
            clean_p = _clean_bullet(stripped)
            if not clean_p:
                continue
            if re.search(r"\btech(?:nolog(?:y|ies))?s?:|stack:|built with:|using:", stripped, re.I):
                current["tech"] = clean_p
            elif (not is_bullet and current["bullets"]
                  and _is_continuation(current["bullets"][-1], clean_p)):
                # continuation of a bullet wrapped across extracted lines
                current["bullets"][-1] = f"{current['bullets'][-1]} {clean_p}".strip()
            else:
                current["bullets"].extend(_split_inline_bullets(stripped) or [clean_p])

    if current:
        entries.append(current)

    entries = [e for e in entries if (e.get("name") or "").strip() or e.get("bullets")]

    # A project whose title is repeated by the layout (title line, then the
    # same title again above its bullets) must not appear twice.
    merged: list[dict] = []
    for entry in entries:
        key = (entry.get("name") or "").strip().lower()
        if merged and key and key == (merged[-1].get("name") or "").strip().lower():
            prev = merged[-1]
            prev["dates"] = prev.get("dates") or entry.get("dates", "")
            prev["tech"] = prev.get("tech") or entry.get("tech", "")
            for b in entry.get("bullets", []):
                if b not in prev["bullets"]:
                    prev["bullets"].append(b)
            continue
        merged.append(entry)
    entries = merged

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

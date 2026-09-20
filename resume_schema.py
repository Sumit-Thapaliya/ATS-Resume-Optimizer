"""
resume_schema.py
----------------
Pydantic models for the structured resume JSON, plus the mapper that converts
the output of parser.parse_resume() into that shape.

Design notes
------------
* Shape follows the JSON Resume convention (basics / work / education / ...)
  so the output is portable to other tools.
* Every value here comes from the deterministic parser. Nothing is generated.
* `warnings` exists so a silent parse failure is never indistinguishable from
  a genuinely empty section - if the parser found no work entries, the JSON
  says so explicitly instead of just returning [].
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Location(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    countryCode: Optional[str] = None
    raw: str = ""


class Profile(BaseModel):
    network: str
    username: str
    url: str


class Basics(BaseModel):
    name: str = ""
    label: str = ""
    email: str = ""
    phone: str = ""
    url: str = ""
    summary: str = ""
    location: Location = Field(default_factory=Location)
    profiles: list[Profile] = Field(default_factory=list)


class WorkEntry(BaseModel):
    name: str = ""                 # company
    position: str = ""             # role / title
    startDate: Optional[str] = None   # ISO "YYYY-MM"
    endDate: Optional[str] = None     # ISO "YYYY-MM" or "Present"
    datesRaw: str = ""             # exactly as printed on the resume
    location: str = ""
    highlights: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str = ""
    area: str = ""                 # degree / field
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    datesRaw: str = ""
    location: str = ""
    score: str = ""                # GPA / percentage
    highlights: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    name: str = ""                 # category label, e.g. "Languages"
    keywords: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str = ""
    tech: str = ""
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    datesRaw: str = ""
    url: str = ""
    highlights: list[str] = Field(default_factory=list)


class ResumeData(BaseModel):
    basics: Basics = Field(default_factory=Basics)
    work: list[WorkEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class ResumeJSON(BaseModel):
    """Top-level document written to disk."""
    success: bool = True
    candidate_name: str = ""
    total_years_experience: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    raw_resume_data: ResumeData = Field(default_factory=ResumeData)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_RE_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s*'?\s*((?:19|20)\d{2}|\d{2})\b")
_RE_NUM_YEAR   = re.compile(r"\b(\d{1,2})\s*/\s*((?:19|20)\d{2})\b")
_RE_YEAR_SLASH = re.compile(r"\b((?:19|20)\d{2})\s*/\s*(\d{1,2})\b")
_RE_BARE_YEAR  = re.compile(r"\b((?:19|20)\d{2})\b")
_RE_END_WORD   = re.compile(r"\b(present|current|now|ongoing|continue|till\s*date)\b", re.I)
# NOTE: "/" is deliberately NOT a range separator - it appears inside dates
# like "03/2018" and "2020/01", and splitting on it shreds them into ["03","2018"].
_RE_SPLIT      = re.compile(r"\s*(?:[-\u2010-\u2015]|\bto\b|\bthrough\b|\bthru\b|\btill\b|\buntil\b)\s*", re.I)


def _to_iso(token: str) -> Optional[str]:
    """'Jul 2026' -> '2026-07'; '03/2018' -> '2018-03'; 'Present' -> 'Present'."""
    if not token:
        return None
    t = token.strip()
    if _RE_END_WORD.search(t):
        return "Present"

    m = _RE_MONTH_YEAR.search(t)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower()) or _MONTHS.get(m.group(1)[:4].lower())
        if mon:
            yr = m.group(2)
            if len(yr) == 2:          # "Jan'20" -> 2020
                yr = "20" + yr
            return f"{int(yr):04d}-{mon:02d}"

    m = _RE_NUM_YEAR.search(t)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"

    m = _RE_YEAR_SLASH.search(t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"

    m = _RE_BARE_YEAR.search(t)
    if m:
        return f"{int(m.group(1)):04d}-01"

    return None


def split_dates(raw: str) -> tuple[Optional[str], Optional[str]]:
    """'Jul 2026 – Present' -> ('2026-07', 'Present')."""
    if not raw:
        return None, None
    parts = [p for p in _RE_SPLIT.split(raw) if p and p.strip()]
    if len(parts) >= 2:
        return _to_iso(parts[0]), _to_iso(parts[-1])
    if len(parts) == 1:
        iso = _to_iso(parts[0])
        return iso, iso
    return None, None


def _iso_to_ym(iso: Optional[str], latest: bool = False) -> Optional[tuple[int, int]]:
    if not iso or iso == "Present":
        return None
    try:
        y, m = iso.split("-")
        return int(y), int(m)
    except (ValueError, AttributeError):
        return None


def compute_years(work: list[WorkEntry], today: Optional[date] = None) -> float:
    """
    Total years of experience as the UNION of employment intervals, so
    overlapping internships are not double-counted and gaps are not counted.
    Returns 0.0 when no usable dates exist.
    """
    today = today or date.today()
    intervals: list[tuple[int, int, int, int]] = []

    for w in work:
        s = _iso_to_ym(w.startDate)
        if not s:
            continue
        e = _iso_to_ym(w.endDate)
        if w.endDate == "Present" or not e:
            e = (today.year, today.month)
        if e < s:
            s, e = e, s
        intervals.append((s[0], s[1], e[0], e[1]))

    if not intervals:
        return 0.0

    def to_days(ym: tuple[int, int]) -> int:
        return ym[0] * 12 + ym[1]

    merged: list[list[int]] = []
    for a in sorted(intervals):
        cur = [to_days((a[0], a[1])), to_days((a[2], a[3]))]
        if merged and cur[0] <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], cur[1])
        else:
            merged.append(cur)

    months = sum(e - s for s, e in merged)
    return round(months / 12.0, 1)


# ---------------------------------------------------------------------------
# Field mappers
# ---------------------------------------------------------------------------

_COUNTRIES = {"nepal": "NP", "india": "IN", "usa": "US", "united states": "US",
              "uk": "GB", "united kingdom": "GB", "australia": "AU", "canada": "CA"}


def _split_location(raw: str) -> Location:
    if not raw:
        return Location()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    city = parts[0] if parts else None
    region = parts[1] if len(parts) > 1 else None
    country_code = None
    for p in parts:
        code = _COUNTRIES.get(p.lower())
        if code:
            country_code = code
    return Location(address=None, city=city, region=region,
                    countryCode=country_code, raw=raw)


def _profiles(contact: dict) -> list[Profile]:
    out: list[Profile] = []
    li = (contact.get("linkedin") or "").strip()
    if li:
        slug = li.rstrip("/").split("/")[-1]
        out.append(Profile(network="LinkedIn", username=slug,
                           url=li if li.startswith("http") else f"https://{li}"))
    gh = (contact.get("github") or "").strip()
    if gh:
        slug = gh.rstrip("/").split("/")[-1]
        out.append(Profile(network="GitHub", username=slug,
                           url=gh if gh.startswith("http") else f"https://{gh}"))
    web = (contact.get("website") or "").strip()
    if web:
        out.append(Profile(network="Portfolio", username=web.split("//")[-1].split("/")[0],
                           url=web if web.startswith("http") else f"https://{web}"))
    return out


_RE_SKILL_LABEL = re.compile(r"^([A-Za-z][A-Za-z &/+]{1,30}?)\s*:\s*(.+)$")


def _skill_groups(skills: list[str]) -> list[SkillGroup]:
    """'Languages: Java, Python' -> SkillGroup(name='Languages', keywords=[...])."""
    groups: list[SkillGroup] = []
    loose: list[str] = []
    for s in skills:
        s = (s or "").strip()
        if not s:
            continue
        m = _RE_SKILL_LABEL.match(s)
        if m:
            kws = [k.strip() for k in re.split(r"[,;|]", m.group(2)) if k.strip()]
            groups.append(SkillGroup(name=m.group(1).strip(), keywords=kws))
        else:
            loose.extend(k.strip() for k in re.split(r"[,;|]", s) if k.strip())
    if loose:
        groups.append(SkillGroup(name="Other", keywords=loose))
    return groups


# ---------------------------------------------------------------------------
# Main mapper
# ---------------------------------------------------------------------------

def _bullets(entry: dict) -> list[str]:
    b = entry.get("bullets") or []
    return [str(x).strip() for x in b if str(x).strip()] if isinstance(b, list) else []


def build_warnings(parsed: dict, data: ResumeData) -> list[str]:
    """
    Surface silent parse failures. A caller reading the JSON can tell the
    difference between 'this resume has no projects' and 'we failed to read them'.
    """
    w: list[str] = []
    if not data.basics.name:
        w.append("No candidate name detected.")
    if not data.basics.email and not data.basics.phone:
        w.append("No contact details detected.")
    if not data.work:
        w.append("No work experience entries detected - the section may use an "
                 "unrecognised date format or layout.")
    elif any(not e.position for e in data.work):
        n = sum(1 for e in data.work if not e.position)
        w.append(f"{n} of {len(data.work)} work entries have no job title.")
    if not data.education:
        w.append("No education entries detected.")
    elif any(not e.institution for e in data.education):
        w.append("At least one education entry has no institution name.")
    if not data.skills:
        w.append("No skills detected.")
    if data.work and compute_years(data.work) == 0.0:
        w.append("Work entries found but no parseable dates - "
                 "total_years_experience is 0.")
    return w


def prune_empty(obj):
    """
    Recursively drop fields that carry no information, so the JSON reflects
    only what was actually found on THIS resume - never a fixed template
    padded with nulls, empty strings and empty arrays.

    Kept even when falsy: booleans and numbers (0 and False are real values).
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = prune_empty(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(obj, list):
        return [prune_empty(x) for x in obj
                if not (x is None or x == "" or x == [] or x == {})]
    return obj


def to_resume_json(parsed: dict) -> ResumeJSON:
    """Convert parser.parse_resume() output into the structured JSON document."""
    contact = parsed.get("contact") or {}

    basics = Basics(
        name=(parsed.get("name") or "").strip(),
        label=(parsed.get("title") or "").strip(),
        email=(contact.get("email") or "").strip(),
        phone=(contact.get("phone") or "").strip(),
        url=(contact.get("website") or "").strip(),
        summary=(parsed.get("summary") or "").strip(),
        location=_split_location(contact.get("location") or ""),
        profiles=_profiles(contact),
    )

    work: list[WorkEntry] = []
    for e in parsed.get("experience") or []:
        raw = (e.get("dates") or "").strip()
        s, en = split_dates(raw)
        work.append(WorkEntry(
            name=(e.get("company") or "").strip(),
            position=(e.get("role") or "").strip(),
            startDate=s, endDate=en, datesRaw=raw,
            location=(e.get("location") or "").strip(),
            highlights=_bullets(e),
        ))

    education: list[EducationEntry] = []
    for e in parsed.get("education") or []:
        raw = (e.get("dates") or "").strip()
        s, en = split_dates(raw)
        education.append(EducationEntry(
            institution=(e.get("institution") or "").strip(),
            area=(e.get("degree") or "").strip(),
            startDate=s, endDate=en, datesRaw=raw,
            location=(e.get("location") or "").strip(),
            score=(e.get("gpa") or "").strip(),
            highlights=_bullets(e),
        ))

    projects: list[ProjectEntry] = []
    for p in parsed.get("projects") or []:
        raw = (p.get("dates") or "").strip()
        s, en = split_dates(raw)
        projects.append(ProjectEntry(
            name=(p.get("name") or "").strip(),
            tech=(p.get("tech") or "").strip(),
            startDate=s, endDate=en, datesRaw=raw,
            highlights=_bullets(p),
        ))

    data = ResumeData(
        basics=basics,
        work=work,
        education=education,
        skills=_skill_groups(parsed.get("skills") or []),
        projects=projects,
        certifications=[str(c).strip() for c in (parsed.get("certifications") or []) if str(c).strip()],
        awards=[str(a).strip() for a in (parsed.get("awards") or []) if str(a).strip()],
        languages=[str(l).strip() for l in (parsed.get("languages") or []) if str(l).strip()],
    )

    return ResumeJSON(
        success=True,
        candidate_name=basics.name,
        total_years_experience=compute_years(data.work),
        warnings=build_warnings(parsed, data),
        raw_resume_data=data,
    )


def to_json_string(parsed: dict, *, indent: int = 2) -> str:
    """Parse -> validated model -> JSON string containing only present fields."""
    doc = to_resume_json(parsed)
    return json.dumps(prune_empty(doc.model_dump()), indent=indent, ensure_ascii=False)

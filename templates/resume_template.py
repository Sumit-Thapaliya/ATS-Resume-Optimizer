"""
templates/resume_template.py
----------------------------
ReportLab style definitions and section-rendering utilities.
Implements the Classic Resume Worded (Wall Street Oasis / Harvard ATS) format:
  • Times-Roman typography family
  • 100% full-width two-column alignment for Company/Dates & Title/Location
  • Uppercase bold section headers with solid horizontal divider lines
  • Clean bullet points with support for sub-bullets
  • ATS-optimised single-column flowable structure

Fixes applied:
  1. Date column is now fluid (min(2.0in, 32% of width)) and month names are
     abbreviated, so ranges like "September 2022 – December 2024" stay on ONE
     line and remain parseable by ATS date regexes.
  2. _esc_bold_prefix() only bolds whitelisted labels — no more "<b>Shift 9:</b>".
  3. Sub-bullet detection is now a single real regex instead of dead code.
  4. skills_block() decides category-vs-plain PER ITEM instead of globally.
  5. KeepTogether() wraps only the entry header, never the whole bullet list,
     so long entries can no longer overflow the frame.
"""

from __future__ import annotations

import re
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Paragraph,
    Spacer,
    HRFlowable,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------
PAGE_SIZE = LETTER
MARGIN_TOP = 0.50 * inch
MARGIN_BOTTOM = 0.50 * inch
MARGIN_LEFT = 0.55 * inch
MARGIN_RIGHT = 0.55 * inch
USABLE_WIDTH = PAGE_SIZE[0] - MARGIN_LEFT - MARGIN_RIGHT

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
C_BLACK = colors.HexColor("#000000")
C_MUTED = colors.HexColor("#333333")
C_RULE  = colors.HexColor("#000000")

# ---------------------------------------------------------------------------
# Font configuration (Times-Roman is built-in to ReportLab)
# ---------------------------------------------------------------------------
FONT_REGULAR = "Times-Roman"
FONT_BOLD    = "Times-Bold"
FONT_ITALIC  = "Times-Italic"

# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------

def _make_style(**kwargs) -> ParagraphStyle:
    defaults = dict(
        fontName=FONT_REGULAR,
        fontSize=10,
        leading=13,
        textColor=C_BLACK,
        alignment=TA_LEFT,
        spaceAfter=0,
        spaceBefore=0,
    )
    defaults.update(kwargs)
    return ParagraphStyle("custom", **defaults)


# Name at top center of resume
STYLE_NAME = _make_style(
    fontName=FONT_BOLD,
    fontSize=22,
    leading=25,
    textColor=C_BLACK,
    alignment=TA_CENTER,
    spaceAfter=3,
)

# Professional title (if provided)
STYLE_TITLE = _make_style(
    fontName=FONT_ITALIC,
    fontSize=10.5,
    leading=13,
    textColor=C_MUTED,
    alignment=TA_CENTER,
    spaceAfter=4,
)

# Contact line under name
STYLE_CONTACT = _make_style(
    fontName=FONT_REGULAR,
    fontSize=9.5,
    leading=13,
    textColor=C_BLACK,
    alignment=TA_CENTER,
    spaceAfter=8,
)

# Section heading (EXPERIENCE, EDUCATION, OTHER, etc.)
STYLE_SECTION_HEADING = _make_style(
    fontName=FONT_BOLD,
    fontSize=11,
    leading=14,
    textColor=C_BLACK,
    spaceBefore=8,
    spaceAfter=2,
    alignment=TA_LEFT,
)

# Entry left bold (Company, Institution, Project)
STYLE_ORG = _make_style(
    fontName=FONT_BOLD,
    fontSize=10.5,
    leading=13,
    textColor=C_BLACK,
)

# Entry right bold (Dates)
STYLE_META_BOLD = _make_style(
    fontName=FONT_BOLD,
    fontSize=10.5,
    leading=13,
    textColor=C_BLACK,
    alignment=TA_RIGHT,
)

# Entry left italic (Role, Degree)
STYLE_ROLE = _make_style(
    fontName=FONT_ITALIC,
    fontSize=9.5,
    leading=12.5,
    textColor=C_MUTED,
)

# Entry right regular/italic (Location)
STYLE_META = _make_style(
    fontName=FONT_REGULAR,
    fontSize=9.5,
    leading=12.5,
    textColor=C_MUTED,
    alignment=TA_RIGHT,
)

# Body copy (Summary)
STYLE_BODY = _make_style(
    fontName=FONT_REGULAR,
    fontSize=9.5,
    leading=13,
    textColor=C_BLACK,
    spaceAfter=4,
)

# Bullet point text (Main level)
STYLE_BULLET = _make_style(
    fontName=FONT_REGULAR,
    fontSize=9.5,
    leading=12.5,
    textColor=C_BLACK,
    leftIndent=12,
    firstLineIndent=-10,
    spaceAfter=2,
)

# Sub-bullet point text (Nested level)
STYLE_SUB_BULLET = _make_style(
    fontName=FONT_REGULAR,
    fontSize=9.0,
    leading=12.0,
    textColor=C_MUTED,
    leftIndent=24,
    firstLineIndent=-10,
    spaceAfter=2,
)

BULLET_PREFIX = "<font size=6>&bull;</font>  "
SUB_BULLET_PREFIX = "<font size=5.5>o</font>  "

# Consistent glyph size for the compact skills grid (leader and separators match)
SKILL_LEADER = "<font size=5.5>&bull;</font>  "
SKILL_SEP    = "   <font size=5.5>&bull;</font>   "


# ---------------------------------------------------------------------------
# Date normalisation (FIX #1)
# ---------------------------------------------------------------------------
_MONTH_ABBR = {
    "January": "Jan", "February": "Feb", "March": "Mar", "April": "Apr",
    "May": "May", "June": "Jun", "July": "Jul", "August": "Aug",
    "September": "Sep", "October": "Oct", "November": "Nov", "December": "Dec",
}


def _short_dates(text: str) -> str:
    """
    Abbreviate full month names so a date range fits the right-hand column
    on a single line. ATS parsers read ranges far more reliably unbroken.

        "September 2022 – December 2024"  ->  "Sep 2022 – Dec 2024"
    """
    if not text:
        return text
    for full, abbr in _MONTH_ABBR.items():
        # word-boundary replace so "Septembrine" style accidents can't happen
        text = re.sub(rf"\b{full}\b", abbr, text)
    return text


# ---------------------------------------------------------------------------
# Layout Helpers
# ---------------------------------------------------------------------------

def _date_col_width(usable_width: float = USABLE_WIDTH) -> float:
    """
    Fluid right-column width (FIX #1).

    Wide enough for "Sep 2022 – Dec 2024" at 10.5pt Times-Bold (~110pt),
    but never so wide that it starves the left column on narrow layouts.
    """
    return min(2.0 * inch, usable_width * 0.32)


def make_two_col_row(
    left_html: str,
    right_html: str,
    left_style: ParagraphStyle,
    right_style: ParagraphStyle,
    usable_width: float = USABLE_WIDTH,
) -> Table:
    """
    Build a borderless 2-column table spanning usable_width.
    Left cell is left-aligned, right cell is right-aligned to exact margin.
    """
    p_left = Paragraph(left_html, left_style)
    p_right = Paragraph(right_html, right_style)
    w_right = _date_col_width(usable_width)
    w_left = usable_width - w_right
    tbl = Table([[p_left, p_right]], colWidths=[w_left, w_right])
    tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    return tbl


def divider(width: float = USABLE_WIDTH, thickness: float = 1.0) -> HRFlowable:
    """A solid horizontal rule spanning usable width."""
    return HRFlowable(
        width=width,
        thickness=thickness,
        color=C_RULE,
        spaceBefore=1,
        spaceAfter=5,
    )


def section_heading(title: str) -> list:
    """Return [heading paragraph, divider line]."""
    return [
        Paragraph(title.upper(), STYLE_SECTION_HEADING),
        divider(),
    ]


def contact_line(contact: dict) -> Paragraph:
    """Build a single centred contact string separated by pipes."""
    parts = []
    if contact.get("email"):
        parts.append(contact["email"])
    if contact.get("phone"):
        parts.append(contact["phone"])
    if contact.get("location"):
        parts.append(contact["location"])
    if contact.get("linkedin"):
        lk = contact["linkedin"]
        lk_display = lk.replace("https://", "").replace("http://", "").replace("www.", "")
        parts.append(lk_display)
    if contact.get("github"):
        gh = contact["github"]
        gh_display = gh.replace("https://", "").replace("http://", "").replace("www.", "")
        parts.append(gh_display)
    if contact.get("website"):
        parts.append(contact["website"])

    text = "  |  ".join(parts) if parts else ""
    return Paragraph(text, STYLE_CONTACT)


# ---------------------------------------------------------------------------
# Bullet helpers (FIX #3)
# ---------------------------------------------------------------------------

# Sub-bullet markers only. '-' and '*' are deliberately excluded: parser.py
# already strips them via RE_BULLET_PREFIX, so a surviving '-' is main-level.
RE_SUB_BULLET = re.compile(r"^(?:o|v|->|>)\s+")

# Main-level bullet noise to strip before rendering.
RE_BULLET_NOISE = re.compile(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]+")


def _is_sub_bullet(text: str) -> bool:
    """True only for genuine nested markers ('o ', 'v ', '> ', '-> ')."""
    return bool(RE_SUB_BULLET.match(text))


def _strip_bullet(text: str) -> str:
    return RE_BULLET_NOISE.sub("", text.strip()).strip()


def _bullet_flowable(text: str) -> Paragraph:
    """Render one bullet string at the correct level."""
    if _is_sub_bullet(text):
        clean = re.sub(r"^(?:o|v|->|>)\s*", "", text.strip())
        return Paragraph(f"{SUB_BULLET_PREFIX}{_esc_bold_prefix(clean)}", STYLE_SUB_BULLET)
    return Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(_strip_bullet(text))}", STYLE_BULLET)


# ---------------------------------------------------------------------------
# Section Flowable Builders
# ---------------------------------------------------------------------------

def experience_block(entry: dict) -> list:
    """Build flowables for a single work experience entry."""
    items = []

    company = entry.get("company", "").strip()
    role    = entry.get("role", "").strip()
    dates   = entry.get("dates", "").strip()
    loc     = entry.get("location", "").strip()
    bullets = entry.get("bullets", [])

    header = []
    body = []

    # Header Row: Company (Bold, left) | Dates (Bold, right)
    if company or dates:
        header.append(make_two_col_row(
            f"<b>{_esc(company)}</b>" if company else "",
            f"<b>{_esc(_short_dates(dates))}</b>" if dates else "",
            STYLE_ORG,
            STYLE_META_BOLD,
        ))

    # Sub-header Row: Role (Italic, left) | Location (Regular, right)
    if role or loc:
        header.append(make_two_col_row(
            f"<i>{_esc(role)}</i>" if role else "",
            _esc(loc) if loc else "",
            STYLE_ROLE,
            STYLE_META,
        ))

    # Bullets & sub-bullets
    for bullet in bullets:
        b_str = bullet.strip()
        if b_str:
            body.append(_bullet_flowable(b_str))

    # FIX #5: keep ONLY the header together — never the bullet list.
    if header:
        items.append(KeepTogether(header))
        if body:
            items.append(Spacer(1, 2))
    items.extend(body)

    return items


def education_block(entry: dict) -> list:
    """Build flowables for a single education entry."""
    items = []

    institution = entry.get("institution", "").strip()
    degree      = entry.get("degree", "").strip()
    dates       = entry.get("dates", "").strip()
    gpa         = entry.get("gpa", "").strip()
    location    = entry.get("location", "").strip()
    bullets     = entry.get("bullets", [])

    header = []
    body = []

    # Header Row: Institution (Bold, left) | Date (Bold, right)
    if institution or dates:
        header.append(make_two_col_row(
            f"<b>{_esc(institution)}</b>" if institution else "",
            f"<b>{_esc(_short_dates(dates))}</b>" if dates else "",
            STYLE_ORG,
            STYLE_META_BOLD,
        ))

    # Sub-header Row: Degree/Major (Italic, left) | Location/GPA (Right)
    right_meta_parts = []
    if location:
        right_meta_parts.append(location)
    if gpa and "gpa" not in degree.lower():
        right_meta_parts.append(f"GPA: {gpa}")
    right_meta = ", ".join(right_meta_parts)

    if degree or right_meta:
        header.append(make_two_col_row(
            f"<i>{_esc(degree)}</i>" if degree else "",
            _esc(right_meta) if right_meta else "",
            STYLE_ROLE,
            STYLE_META,
        ))

    for bullet in bullets:
        b_str = bullet.strip()
        if b_str:
            body.append(Paragraph(
                f"{BULLET_PREFIX}{_esc_bold_prefix(_strip_bullet(b_str))}", STYLE_BULLET))

    # FIX #5
    if header:
        items.append(KeepTogether(header))
        if body:
            items.append(Spacer(1, 2))
    items.extend(body)

    return items


# ---------------------------------------------------------------------------
# Skills (FIX #4)
# ---------------------------------------------------------------------------

_SKILLS_PER_LINE = 6


def _flush_plain_skills(plain: list[str], items: list) -> None:
    """Emit accumulated plain skills as compact 6-per-line rows."""
    for i in range(0, len(plain), _SKILLS_PER_LINE):
        chunk = plain[i:i + _SKILLS_PER_LINE]
        line_text = SKILL_SEP.join(_esc(s) for s in chunk)
        items.append(Paragraph(f"{SKILL_LEADER}{line_text}", STYLE_BULLET))


def skills_block(skills: list[str]) -> list:
    """
    Render the skills section.

    FIX #4: the category-vs-plain decision is made PER ITEM. A single
    "Languages: Nepali, English" entry no longer forces every other skill
    onto its own bullet line.
    """
    if not skills:
        return []

    items: list = []
    plain: list[str] = []

    for skill_item in skills:
        clean_item = re.sub(r"^[\-•·\*▪▸►✓✔]\s*", "", skill_item.strip())
        if not clean_item:
            continue

        if _is_labelled_category(clean_item):
            # flush any pending plain skills first so order is preserved
            _flush_plain_skills(plain, items)
            plain = []
            items.append(Paragraph(
                f"{BULLET_PREFIX}{_esc_bold_prefix(clean_item)}", STYLE_BULLET))
        else:
            plain.append(clean_item)

    _flush_plain_skills(plain, items)
    return items


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def projects_block(entry: dict) -> list:
    """Build flowables for a single project entry."""
    items = []

    name    = entry.get("name", "").strip()
    desc    = entry.get("description", "").strip()
    tech    = entry.get("tech", "").strip()
    dates   = entry.get("dates", "").strip()
    bullets = entry.get("bullets", [])

    header = []
    body = []

    if name or dates:
        header.append(make_two_col_row(
            f"<b>{_esc(name)}</b>" if name else "",
            f"<b>{_esc(_short_dates(dates))}</b>" if dates else "",
            STYLE_ORG,
            STYLE_META_BOLD,
        ))

    if tech:
        clean_t = _strip_bullet(tech)
        if clean_t:
            body.append(Paragraph(
                f"{BULLET_PREFIX}<b>Tech Stack:</b> {_esc(clean_t)}", STYLE_BULLET))

    if bullets:
        for b in bullets:
            clean_b = _strip_bullet(b)
            if clean_b:
                body.append(Paragraph(
                    f"{BULLET_PREFIX}{_esc_bold_prefix(clean_b)}", STYLE_BULLET))
    elif desc:
        clean_d = _strip_bullet(desc)
        if clean_d:
            body.append(Paragraph(
                f"{BULLET_PREFIX}{_esc_bold_prefix(clean_d)}", STYLE_BULLET))

    # FIX #5
    if header:
        items.append(KeepTogether(header))
        if body:
            items.append(Spacer(1, 2))
    items.extend(body)

    return items


def list_section_block(items_list: list[str]) -> list:
    """Generic bullet list renderer for certifications, awards, languages, etc."""
    flowables = []
    for item in items_list:
        clean_item = re.sub(r"^[\-•·\*▪▸►✓✔]\s*", "", item.strip())
        if clean_item:
            flowables.append(Paragraph(
                f"{BULLET_PREFIX}{_esc_bold_prefix(clean_item)}", STYLE_BULLET))
    return flowables


# ---------------------------------------------------------------------------
# Text Escaping & Formatting Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape XML-special characters for ReportLab Paragraph."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
    )


# FIX #2 (revised): recognise a "Label: values" line structurally instead of
# by a fixed word list, so real categories like "Frontend:", "DevOps & Cloud:",
# "Languages & Other:" and "Marketing Technology:" are bolded, while prose such
# as "Shift 9:00 AM - 5:00 PM" or "Note: promoted twice" is left alone.
LABEL_STOPWORDS = {
    "note", "nb", "eg", "e.g", "ie", "i.e", "etc", "warning", "important",
    "ps", "p.s", "total", "approx", "approximately", "re", "ref", "step",
    "day", "time", "shift", "hours", "duration", "age", "size",
}


def _is_labelled_category(text: str) -> bool:
    """
    True when the text is a category line of the form "Label: v1, v2, v3".

    A prefix qualifies only if it:
      * is 1-4 words
      * contains no digits          -> rejects "Shift 9:", "Q3 2024:"
      * is not a prose stopword     -> rejects "Note:"
      * is followed by something that reads like a list of values
    """
    if ":" not in text or text.lower().startswith(("http", "www")):
        return False

    prefix, rest = (part.strip() for part in text.split(":", 1))
    words = prefix.split()

    if not 1 <= len(words) <= 4:
        return False
    if any(ch.isdigit() for ch in prefix):
        return False
    if prefix.lower().rstrip(".,") in LABEL_STOPWORDS:
        return False
    if not rest:
        return False

    # remainder must look like a value list: separators, or at least two words
    return bool(re.search(r"[,;/|&]", rest)) or len(rest.split()) >= 2


def _esc_bold_prefix(text: str) -> str:
    """
    Bold the leading label of a "Label: values" line, but only for genuine
    resume categories (see _is_labelled_category).
    """
    if _is_labelled_category(text):
        prefix, rest = (part.strip() for part in text.split(":", 1))
        return f"<b>{_esc(prefix)}:</b> {_esc(rest)}"

    return _esc(text)

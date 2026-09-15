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
"""

from __future__ import annotations

import re
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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
C_DARK  = colors.HexColor("#111111")
C_MUTED = colors.HexColor("#333333")
C_RULE  = colors.HexColor("#000000")

# ---------------------------------------------------------------------------
# Font configuration (Times-Roman is built-in to ReportLab)
# ---------------------------------------------------------------------------
FONT_REGULAR = "Times-Roman"
FONT_BOLD    = "Times-Bold"
FONT_ITALIC  = "Times-Italic"
FONT_BOLDITA = "Times-BoldItalic"

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

BULLET_PREFIX = "<font size=6>&bull;</font>&nbsp;&nbsp;"
SUB_BULLET_PREFIX = "<font size=5.5>o</font>&nbsp;&nbsp;"



# ---------------------------------------------------------------------------
# Layout Helpers
# ---------------------------------------------------------------------------

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
    w_right = 1.4 * inch
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
    """Build a single left-aligned contact string separated by pipes."""
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

    text = " &nbsp;|&nbsp; ".join(parts) if parts else ""
    return Paragraph(text, STYLE_CONTACT)


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

    inner = []

    # Header Row: Company (Bold, left) | Dates (Bold, right)
    if company or dates:
        inner.append(make_two_col_row(
            f"<b>{_esc(company)}</b>" if company else "",
            f"<b>{_esc(dates)}</b>" if dates else "",
            STYLE_ORG,
            STYLE_META_BOLD,
        ))

    # Sub-header Row: Role (Italic, left) | Location (Regular, right)
    if role or loc:
        inner.append(make_two_col_row(
            f"<i>{_esc(role)}</i>" if role else "",
            _esc(loc) if loc else "",
            STYLE_ROLE,
            STYLE_META,
        ))

    if inner:
        inner.append(Spacer(1, 2))

    # Bullets & sub-bullets
    for bullet in bullets:
        b_str = bullet.strip()
        if not b_str:
            continue

        # Check for sub-bullet markers (e.g. starts with 'o ', '->', '  -')
        is_sub = bool(re.match(r"^(?:o|v|\-|\>|\*)\s+", b_str)) and len(b_str) > 2 and b_str.startswith("o ")
        if is_sub or b_str.startswith("o "):
            clean_b = re.sub(r"^(?:o|\-|\*|●|•)\s*", "", b_str)
            inner.append(Paragraph(f"{SUB_BULLET_PREFIX}{_esc_bold_prefix(clean_b)}", STYLE_SUB_BULLET))
        else:
            clean_b = re.sub(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]*", "", b_str)
            inner.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_b)}", STYLE_BULLET))

    if inner:
        items.append(KeepTogether(inner[:3]))
        items.extend(inner[3:])

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

    inner = []

    # Header Row: Institution (Bold, left) | Date (Bold, right)
    if institution or dates:
        inner.append(make_two_col_row(
            f"<b>{_esc(institution)}</b>" if institution else "",
            f"<b>{_esc(dates)}</b>" if dates else "",
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
        inner.append(make_two_col_row(
            f"<i>{_esc(degree)}</i>" if degree else "",
            _esc(right_meta) if right_meta else "",
            STYLE_ROLE,
            STYLE_META,
        ))

    if inner:
        inner.append(Spacer(1, 2))

    for bullet in bullets:
        b_str = bullet.strip()
        if not b_str:
            continue
        clean_b = re.sub(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]*", "", b_str)
        inner.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_b)}", STYLE_BULLET))

    if inner:
        items.append(KeepTogether(inner))

    return items


def skills_block(skills: list[str]) -> list:
    """Render skills section formatted with category prefixes or clean bullet lines."""
    if not skills:
        return []

    items = []

    # Check if skills contain colon-separated categories (e.g. "Technical Skills: Python, SQL...")
    has_categories = any(":" in s for s in skills)

    if has_categories:
        for skill_item in skills:
            clean_item = re.sub(r"^[\-•·\*▪▸►✓✔]\s*", "", skill_item.strip())
            items.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_item)}", STYLE_BULLET))
    else:
        # Group into lines of ~6 items
        chunk_size = 6
        chunks = [skills[i:i+chunk_size] for i in range(0, len(skills), chunk_size)]
        for chunk in chunks:
            line_text = " &nbsp;&nbsp;<font size=5.5>&bull;</font>&nbsp;&nbsp; ".join(_esc(s) for s in chunk)
            items.append(Paragraph(f"{BULLET_PREFIX}{line_text}", STYLE_BULLET))

    return items


def projects_block(entry: dict) -> list:
    """Build flowables for a single project entry."""
    items = []

    name    = entry.get("name", "").strip()
    desc    = entry.get("description", "").strip()
    tech    = entry.get("tech", "").strip()
    dates   = entry.get("dates", "").strip()
    bullets = entry.get("bullets", [])

    inner = []
    if name or dates:
        inner.append(make_two_col_row(
            f"<b>{_esc(name)}</b>" if name else "",
            f"<b>{_esc(dates)}</b>" if dates else "",
            STYLE_ORG,
            STYLE_META_BOLD,
        ))

    if tech:
        clean_t = re.sub(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]*", "", tech)
        inner.append(Paragraph(f"{BULLET_PREFIX}<b>Tech Stack:</b> {_esc(clean_t)}", STYLE_BULLET))

    if bullets:
        for b in bullets:
            clean_b = re.sub(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]*", "", b.strip())
            if clean_b:
                inner.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_b)}", STYLE_BULLET))
    elif desc:
        clean_d = re.sub(r"^[\-•·\*▪▸►✓✔●⚪◦■▫⁃–—\s]*", "", desc)
        if clean_d:
            inner.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_d)}", STYLE_BULLET))

    if inner:
        items.append(KeepTogether(inner))

    return items


def list_section_block(items_list: list[str]) -> list:
    """Generic bullet list renderer for certifications, awards, languages, etc."""
    flowables = []
    for item in items_list:
        clean_item = re.sub(r"^[\-•·\*▪▸►✓✔]\s*", "", item.strip())
        if clean_item:
            flowables.append(Paragraph(f"{BULLET_PREFIX}{_esc_bold_prefix(clean_item)}", STYLE_BULLET))
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


def _esc_bold_prefix(text: str) -> str:
    """
    If line has a label before colon (e.g. "Technical Skills: R, Python" or "Awards: Fellow"),
    make the prefix bold: "<b>Technical Skills:</b> R, Python".
    """
    if ":" in text and not text.lower().startswith("http"):
        parts = text.split(":", 1)
        prefix = parts[0].strip()
        rest = parts[1].strip()
        # Only make bold if prefix is short label (1-5 words)
        if 1 <= len(prefix.split()) <= 5:
            return f"<b>{_esc(prefix)}:</b> {_esc(rest)}"

    return _esc(text)

"""
generator.py
------------
Generates an ATS-friendly PDF resume from structured parsed data.
Uses ReportLab's Platypus document model for clean, reflow-able layout.

Guarantees 1-page output by dynamically optimizing content and spacing.
Supports an optional diagonal watermark via templates/watermark.py.
"""

from __future__ import annotations

import copy
import io
import logging
import os
from functools import partial
from pathlib import Path

from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    KeepTogether,
)
from reportlab.lib.units import inch

from templates.resume_template import (
    PAGE_SIZE,
    MARGIN_TOP,
    MARGIN_BOTTOM,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    STYLE_NAME,
    STYLE_BODY,
    STYLE_TITLE,
    contact_line,
    divider,
    section_heading,
    experience_block,
    education_block,
    skills_block,
    projects_block,
    list_section_block,
    _esc,
)
from templates.watermark import draw_watermark

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------

def generate_resume_pdf(
    parsed: dict,
    output_path: str,
    *,
    watermark: bool = True,
    watermark_text: str = "DRAFT",
    watermark_opacity: float = 0.05,
    max_pages: int = 2,
) -> str:
    """
    Generate an ATS-optimised PDF from a parsed resume dict.

    Fits the resume into `max_pages` (default 2) before pruning anything.
    A hard 1-page cap silently DELETED real content - 3 experience bullets,
    2 projects and 1 education entry on a typical 5-year resume - so the
    page budget is now a parameter and pruning is the last resort.

    Args:
        parsed:            Output of parser.parse_resume()
        output_path:       Destination file path for the PDF.
        watermark:         If True, stamp a diagonal watermark on every page (default: True).
        watermark_text:    Text to render as watermark (default: "DRAFT").
        watermark_opacity: Watermark alpha 0.0–1.0 (default: 0.05).
        max_pages:         Page budget before any content is pruned (default: 2).

    Returns:
        The output_path string (same as input, for convenience).
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    max_pages = max(1, min(int(max_pages), 5))

    def _render(data: dict, compact: bool) -> tuple[bytes, int]:
        """Render a story in memory; return (pdf_bytes, page_count)."""
        buf = io.BytesIO()
        doc = _build_doc(
            buf,
            watermark=watermark,
            watermark_text=watermark_text,
            watermark_opacity=watermark_opacity,
            compact=compact,
        )
        doc.build(_build_story(data, compact=compact))
        return buf.getvalue(), doc.page

    pdf_bytes: bytes | None = None
    pages = 0
    for level in range(5):
        opt_data = _optimize_parsed_data(parsed, level)
        for is_compact in (False, True):
            candidate, n_pages = _render(opt_data, is_compact)
            if n_pages <= max_pages:
                pdf_bytes, pages = candidate, n_pages
                logger.info(
                    "Fit achieved: %d page(s), pruning level %d (compact=%s)",
                    n_pages, level, is_compact,
                )
                break
        if pdf_bytes is not None:
            break

    if pdf_bytes is None:
        pdf_bytes, pages = _render(_optimize_parsed_data(parsed, 4), True)

    logger.info("Generating PDF → %s", output_path)
    Path(output_path).write_bytes(pdf_bytes)
    logger.info("PDF generated successfully (%d bytes, pages=%d)", os.path.getsize(output_path), pages)
    if pages > max_pages:
        logger.warning("Content still exceeds %d page(s) after maximum pruning.", max_pages)
    return output_path


# ---------------------------------------------------------------------------
# Content Optimization Helper
# ---------------------------------------------------------------------------

def _optimize_parsed_data(parsed: dict, level: int) -> dict:
    """
    Prune and compress parsed resume content based on optimization level.
    Ensures that main/core content (Header, Experience, Education, Skills) is preserved.
    """
    if level == 0:
        return parsed

    data = copy.deepcopy(parsed)

    # 1. Summary
    summary = data.get("summary", "")
    if summary:
        max_sum_chars = {1: 450, 2: 300, 3: 200, 4: 120}.get(level, 450)
        if len(summary) > max_sum_chars:
            cut = summary[:max_sum_chars]
            last_dot = cut.rfind(".")
            if last_dot > 40:
                summary = cut[:last_dot + 1]
            else:
                last_space = cut.rfind(" ")
                summary = cut[:last_space] + "..." if last_space > 0 else cut + "..."
            data["summary"] = summary

    # 2. Experience
    exp = data.get("experience", [])
    if exp:
        max_exp_entries = {1: 6, 2: 4, 3: 3, 4: 3}.get(level, 6)
        max_bullets = {1: 4, 2: 3, 3: 2, 4: 2}.get(level, 4)
        max_b_chars = {1: 400, 2: 280, 3: 200, 4: 140}.get(level, 400)

        exp = exp[:max_exp_entries]
        for entry in exp:
            bullets = entry.get("bullets", [])
            new_bullets = []
            for b in bullets[:max_bullets]:
                if len(b) > max_b_chars:
                    cut = b[:max_b_chars]
                    last_space = cut.rfind(" ")
                    b = cut[:last_space] + "..." if last_space > 0 else cut + "..."
                new_bullets.append(b)
            entry["bullets"] = new_bullets
        data["experience"] = exp

    # 3. Education
    edu = data.get("education", [])
    if edu:
        max_edu_entries = {1: 3, 2: 2, 3: 2, 4: 1}.get(level, 3)
        max_edu_bullets = {1: 2, 2: 1, 3: 0, 4: 0}.get(level, 2)
        edu = edu[:max_edu_entries]
        for entry in edu:
            if max_edu_bullets == 0:
                entry["bullets"] = []
            else:
                entry["bullets"] = entry.get("bullets", [])[:max_edu_bullets]
        data["education"] = edu

    # 4. Skills
    skills = data.get("skills", [])
    if skills:
        max_skills = {1: 25, 2: 15, 3: 12, 4: 10}.get(level, 25)
        data["skills"] = skills[:max_skills]

    # 5. Projects
    proj = data.get("projects", [])
    if proj:
        max_proj_entries = {1: 3, 2: 2, 3: 1, 4: 1}.get(level, 3)
        proj = proj[:max_proj_entries]
        data["projects"] = proj

    # 6. Secondary sections
    if level >= 2:
        data["publications"] = []
        data["volunteer"] = []
    if level >= 3:
        data["languages"] = data.get("languages", [])[:2]
        data["certifications"] = data.get("certifications", [])[:2]
        data["awards"] = data.get("awards", [])[:1]
    if level >= 4:
        data["languages"] = []
        data["certifications"] = []
        data["awards"] = []

    return data


# ---------------------------------------------------------------------------
# Document template
# ---------------------------------------------------------------------------

def _build_doc(
    path_or_buf,
    *,
    watermark: bool = True,
    watermark_text: str = "DRAFT",
    watermark_opacity: float = 0.05,
    compact: bool = False,
) -> BaseDocTemplate:

    top_m   = 0.35 * inch if compact else MARGIN_TOP
    bot_m   = 0.35 * inch if compact else MARGIN_BOTTOM
    left_m  = 0.45 * inch if compact else MARGIN_LEFT
    right_m = 0.45 * inch if compact else MARGIN_RIGHT

    doc = BaseDocTemplate(
        path_or_buf,
        pagesize=PAGE_SIZE,
        topMargin=top_m,
        bottomMargin=bot_m,
        leftMargin=left_m,
        rightMargin=right_m,
        title="Resume",
        author="Resume Service",
    )

    frame = Frame(
        left_m,
        bot_m,
        PAGE_SIZE[0] - left_m - right_m,
        PAGE_SIZE[1] - top_m - bot_m,
        id="main",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )

    # Build optional watermark callback
    if watermark:
        wm_cb = partial(
            draw_watermark,
            text=watermark_text,
            opacity=watermark_opacity,
        )
        page_tpl = PageTemplate(
            id="main",
            frames=[frame],
            onPage=wm_cb,
        )
    else:
        page_tpl = PageTemplate(id="main", frames=[frame])

    doc.addPageTemplates([page_tpl])
    return doc


# ---------------------------------------------------------------------------
# Story builder
# ---------------------------------------------------------------------------

def _build_story(parsed: dict, compact: bool = False) -> list:
    story = []
    sp_h = 2 if compact else 4
    sp_exp = 3 if compact else 5

    # ── Header ──────────────────────────────────────────────────────────────
    name    = parsed.get("name", "").strip()
    title   = parsed.get("title", "").strip()
    contact = parsed.get("contact", {})

    if name:
        story.append(Paragraph(_esc(name), STYLE_NAME))
    if title:
        story.append(Paragraph(_esc(title), STYLE_TITLE))

    contact_p = contact_line(contact)
    if contact_p:
        story.append(contact_p)

    # ── Summary ─────────────────────────────────────────────────────────────
    summary = parsed.get("summary", "").strip()
    if summary:
        story.extend(section_heading("Summary"))
        story.append(Paragraph(_esc(summary), STYLE_BODY))
        story.append(Spacer(1, sp_h))

    # ── Experience ──────────────────────────────────────────────────────────
    experience = parsed.get("experience", [])
    if experience:
        story.extend(section_heading("Experience"))
        for entry in experience:
            story.extend(experience_block(entry))
            story.append(Spacer(1, sp_exp))

    # ── Education ───────────────────────────────────────────────────────────
    education = parsed.get("education", [])
    if education:
        story.extend(section_heading("Education"))
        for entry in education:
            story.extend(education_block(entry))
            story.append(Spacer(1, sp_h))

    # ── Skills ──────────────────────────────────────────────────────────────
    skills = parsed.get("skills", [])
    if skills:
        story.extend(section_heading("Skills"))
        story.extend(skills_block(skills))
        story.append(Spacer(1, sp_h))

    # ── Projects ────────────────────────────────────────────────────────────
    projects = parsed.get("projects", [])
    if projects:
        story.extend(section_heading("Projects"))
        for entry in projects:
            story.extend(projects_block(entry))
            story.append(Spacer(1, sp_h))

    # ── Certifications ──────────────────────────────────────────────────────
    certifications = parsed.get("certifications", [])
    if certifications:
        story.extend(section_heading("Certifications"))
        story.extend(list_section_block(certifications))
        story.append(Spacer(1, sp_h))

    # ── Awards ──────────────────────────────────────────────────────────────
    awards = parsed.get("awards", [])
    if awards:
        story.extend(section_heading("Awards & Honors"))
        story.extend(list_section_block(awards))
        story.append(Spacer(1, sp_h))

    # ── Languages ───────────────────────────────────────────────────────────
    languages = parsed.get("languages", [])
    if languages:
        story.extend(section_heading("Languages"))
        story.extend(list_section_block(languages))
        story.append(Spacer(1, sp_h))

    # ── Publications ────────────────────────────────────────────────────────
    publications = parsed.get("publications", [])
    if publications:
        story.extend(section_heading("Publications"))
        story.extend(list_section_block(publications))
        story.append(Spacer(1, sp_h))

    # ── Volunteer ───────────────────────────────────────────────────────────
    volunteer = parsed.get("volunteer", [])
    if volunteer:
        story.extend(section_heading("Volunteer"))
        story.extend(list_section_block(volunteer))
        story.append(Spacer(1, sp_h))

    return story

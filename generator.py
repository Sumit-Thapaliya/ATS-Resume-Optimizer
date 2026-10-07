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
import re
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

    def _render(data: dict, compact: bool, spacing: float = 1.0) -> tuple[bytes, int]:
        """Render a story in memory; return (pdf_bytes, page_count)."""
        buf = io.BytesIO()
        doc = _build_doc(
            buf,
            watermark=watermark,
            watermark_text=watermark_text,
            watermark_opacity=watermark_opacity,
            compact=compact,
            spacing=spacing,
        )
        doc.build(_build_story(data, compact=compact, spacing=spacing))
        return buf.getvalue(), doc.page

    # Try optimization levels 0 -> 4; level 0 prunes nothing at all.
    # The first render that fits IS the final PDF, so its bytes are kept
    # rather than discarded and rebuilt from scratch.
    pdf_bytes: bytes | None = None
    pages = 0
    fit_level = -1
    fit_compact = False
    fill_data: dict = parsed
    for level in range(len(PRUNE_LEVELS)):
        opt_data = _optimize_parsed_data(parsed, level)  # depends only on level
        for is_compact in (False, True):
            candidate, n_pages = _render(opt_data, is_compact)
            if n_pages <= max_pages:
                pdf_bytes, pages = candidate, n_pages
                fit_level, fit_compact, fill_data = level, is_compact, opt_data
                logger.info(
                    "Fit achieved: %d page(s), pruning level %d (compact=%s)",
                    n_pages, level, is_compact,
                )
                break
        if pdf_bytes is not None:
            break

    # ── Page-fill pass ───────────────────────────────────────────────────────
    # The level ladder moves in steps, so the first level that fits can leave
    # the page noticeably empty - content was cut that would still have fitted.
    # Walk back UP the ladder, adding content field by field and keeping every
    # variant that still fits the page budget. A resume that already fitted at
    # level 0 skips this entirely: nothing was cut, so there is nothing to add.
    if pdf_bytes is not None and fit_level > 0:
        cfg = dict(PRUNE_LEVELS[fit_level])
        improved = False
        # Each trial is a full render, so cap the work. Render's free tier is
        # slow and an upload must not spend seconds chasing the last few words.
        trials_left = PAGE_FILL_MAX_TRIALS
        for target_level in range(fit_level - 1, -1, -1):
            richer = PRUNE_LEVELS[target_level]
            gained_here = False
            for field in ("bchars", "bullets", "exp", "proj", "skills",
                          "summary", "edu", "edu_bullets", "drop"):
                target = richer.get(field, cfg[field])
                # 'drop' counts DOWN as the resume gets richer.
                descending = field == "drop"
                while (cfg[field] > target) if descending else (cfg[field] < target):
                    if trials_left <= 0:
                        break
                    trials_left -= 1
                    trial = dict(cfg)
                    if descending:
                        trial[field] = cfg[field] - 1
                    elif field in ("bchars", "summary", "skills"):
                        trial[field] = min(target, cfg[field] + max(10, (target - cfg[field]) // 2))
                    else:
                        trial[field] = cfg[field] + 1
                    candidate, n_pages = _render(
                        _optimize_parsed_data(parsed, fit_level, overrides=trial), fit_compact
                    )
                    if n_pages > max_pages:
                        break
                    cfg = trial
                    pdf_bytes, pages = candidate, n_pages
                    fill_data = _optimize_parsed_data(parsed, fit_level, overrides=trial)
                    improved = gained_here = True
            if not gained_here or trials_left <= 0:
                break
        if improved:
            logger.info(
                "Page-fill: recovered content (bullets=%s, bchars=%s, exp=%s, "
                "proj=%s, skills=%s, drop=%s)",
                cfg["bullets"], cfg["bchars"], cfg["exp"], cfg["proj"],
                cfg["skills"], cfg["drop"],
            )

    # ── Vertical fill ────────────────────────────────────────────────────────
    # Content add-back has run out of things to add, but a short resume can
    # still leave the lower third of the page blank. Open the layout up -
    # larger margins and section spacing - and keep the roomiest version that
    # is still exactly one page. Nothing is added or removed here; the same
    # content simply occupies the page properly.
    if pdf_bytes is not None and max_pages == 1:
        best_spacing = 1.0
        for spacing in (1.15, 1.35, 1.6, 1.9, 2.3):
            candidate, n_pages = _render(fill_data, fit_compact, spacing)
            if n_pages != 1:
                break
            pdf_bytes, pages = candidate, n_pages
            best_spacing = spacing
        if best_spacing > 1.0:
            logger.info("Vertical fill: spacing x%.2f to fill the page", best_spacing)

    if pdf_bytes is None:
        # Nothing fit even at maximum pruning - emit the most compact version.
        pdf_bytes, pages = _render(_optimize_parsed_data(parsed, len(PRUNE_LEVELS) - 1), True)

    logger.info("Generating PDF → %s", output_path)
    Path(output_path).write_bytes(pdf_bytes)
    logger.info("PDF generated successfully (%d bytes, pages=%d)", os.path.getsize(output_path), pages)
    if pages > max_pages:
        logger.warning("Content still exceeds %d page(s) after maximum pruning.", max_pages)
    return output_path


# ---------------------------------------------------------------------------
# Content Optimization Helper
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Words a phrase must never end on - cutting here reads as an unfinished thought.
_TRAILING_JUNK = re.compile(
    r"\s+(?:and|or|the|a|an|of|to|in|for|with|on|at|by|from|through|across|into|as|"
    r"that|which|using|via|per|than|but|while|after|before|during|within|without|"
    r"including|such|etc)\s*$",
    re.I,
)


def _smart_truncate(text: str, limit: int) -> str:
    """
    Shorten `text` so it still reads as a complete thought.

    Resumes previously ended mid-phrase with an ellipsis:

        "...single-handedly adopted across marketing…"
        "...built serverless backend workflows…"

    which is worse than saying less. The rule now is: keep WHOLE sentences,
    and never emit an ellipsis.

      1. Keep as many complete sentences as fit the budget.
      2. If even the first sentence is over budget, keep it in full when the
         overshoot is modest - a complete sentence is worth a slightly longer
         line.
      3. Only when it is far too long, cut back to the last clause boundary
         and close the phrase cleanly, with no trailing conjunction.
    """
    text = (text or "").strip()
    if not text or len(text) <= limit:
        return text

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    # 1. Whole sentences that fit.
    kept: list[str] = []
    used = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if kept else 0)
        if used + extra > limit:
            break
        kept.append(sentence)
        used += extra
    if kept:
        return " ".join(kept)

    first = sentences[0] if sentences else text

    # 2. One long but complete sentence - allow a modest overshoot.
    if len(first) <= limit * 1.8:
        return first

    # 3. Too long even for that: fall back to the last clean clause boundary.
    window = first[:limit]
    clause = max(
        window.rfind("; "), window.rfind(": "),
        window.rfind(" - "), window.rfind(" \u2013 "), window.rfind(" \u2014 "),
        window.rfind(", "),
    )
    if clause >= int(limit * 0.5):
        phrase = window[:clause]
    else:
        space = window.rfind(" ")
        phrase = window[:space] if space > 0 else window

    phrase = _TRAILING_JUNK.sub("", phrase).rstrip(" ,;:-\u2013\u2014")
    return phrase


def _is_meaningful_entry(entry: dict, key_fields: tuple[str, ...]) -> bool:
    """
    True when an entry still carries real information.

    Pruning can empty an entry out completely (bullets dropped, title missing),
    which previously rendered as a section heading with blank space under it.
    """
    if not isinstance(entry, dict):
        return bool(str(entry).strip())
    for field in key_fields:
        if str(entry.get(field) or "").strip():
            return True
    return bool([b for b in entry.get("bullets", []) or [] if str(b).strip()])


def _drop_empty_sections(data: dict) -> dict:
    """
    Remove entries and sections that have no renderable content.

    Without this a heading such as "Education" could survive on its own after
    pruning stripped everything beneath it.
    """
    data["experience"] = [
        e for e in data.get("experience", []) or []
        if _is_meaningful_entry(e, ("title", "position", "company", "organisation", "organization"))
    ]
    data["education"] = [
        e for e in data.get("education", []) or []
        if _is_meaningful_entry(e, ("institution", "school", "degree", "field", "university"))
    ]
    data["projects"] = [
        e for e in data.get("projects", []) or []
        if _is_meaningful_entry(e, ("name", "title", "description"))
    ]

    for key in ("skills", "certifications", "awards", "languages", "publications",
                "volunteer", "interests"):
        data[key] = [s for s in data.get(key, []) or [] if str(s).strip()]

    if not str(data.get("summary") or "").strip():
        data["summary"] = ""

    return data


# Pruning is PRIORITY-ORDERED, not uniform. Low-value sections (interests,
# hobbies, volunteering, publications) are shed first so that the page budget
# is spent on what recruiters actually read: experience, projects and skills.
#
# Level 0 touches nothing. A resume that already fits on one page is therefore
# rendered exactly as parsed - it is never trimmed just because a higher level
# exists, and never shrinks to less than it needs.
# Upper bound on the extra renders the page-fill pass may perform.
PAGE_FILL_MAX_TRIALS = 12

PRUNE_LEVELS: list[dict] = [
    # lvl  summary  exp bul bchars edu ebul skills proj  drop (low-value first)
    dict(summary=10_000, exp=99, bullets=99, bchars=10_000, edu=99, edu_bullets=99,
         skills=99, proj=99, drop=0),
    # 1 — shed interests/hobbies only; real content untouched
    dict(summary=700, exp=9, bullets=7, bchars=520, edu=5, edu_bullets=3,
         skills=45, proj=6, drop=1),
    # 2 — shed volunteering and publications too
    dict(summary=550, exp=8, bullets=6, bchars=440, edu=4, edu_bullets=2,
         skills=38, proj=5, drop=2),
    # 3 — trim awards/languages, start trimming the longest bullets
    dict(summary=450, exp=7, bullets=5, bchars=380, edu=4, edu_bullets=2,
         skills=32, proj=5, drop=3),
    # 4 — mild trimming of core content
    dict(summary=360, exp=6, bullets=4, bchars=320, edu=3, edu_bullets=1,
         skills=26, proj=4, drop=3),
    # 5 — moderate
    dict(summary=300, exp=5, bullets=4, bchars=270, edu=3, edu_bullets=1,
         skills=22, proj=4, drop=4),
    # 6 — aggressive
    dict(summary=240, exp=5, bullets=3, bchars=230, edu=2, edu_bullets=0,
         skills=18, proj=3, drop=4),
    # 7 — strong
    dict(summary=180, exp=4, bullets=3, bchars=190, edu=2, edu_bullets=0,
         skills=15, proj=3, drop=5),
    # 8 — very strong
    dict(summary=140, exp=4, bullets=2, bchars=160, edu=2, edu_bullets=0,
         skills=12, proj=2, drop=5),
    # 9 — last resort: experience, education and skills only
    dict(summary=110, exp=3, bullets=2, bchars=140, edu=1, edu_bullets=0,
         skills=10, proj=2, drop=6),
]

# What each `drop` tier removes, cheapest sacrifice first.
DROP_ORDER: list[tuple[str, ...]] = [
    (),                                             # 0 - nothing
    ("interests",),                                 # 1
    ("interests", "volunteer"),                     # 2
    ("interests", "volunteer", "publications"),     # 3
    ("interests", "volunteer", "publications", "awards"),            # 4
    ("interests", "volunteer", "publications", "awards", "languages"),  # 5
    ("interests", "volunteer", "publications", "awards", "languages",
     "certifications"),                             # 6
]


def _optimize_parsed_data(parsed: dict, level: int, overrides: dict | None = None) -> dict:
    """
    Prune and compress parsed resume content for a given optimisation level.

    Levels are deliberately fine-grained (0 = untouched, 8 = last resort) so the
    fitter can stop at the gentlest level that fits instead of jumping straight
    to a heavily stripped version. Core sections (header, experience, education,
    skills) always survive; only their depth is reduced.

    Every shortening goes through _smart_truncate, so text ends on a sentence or
    clause boundary rather than mid-word. Entries emptied by pruning are dropped
    entirely, so no section heading is ever rendered above blank space.
    """
    level = max(0, min(int(level), len(PRUNE_LEVELS) - 1))
    cfg = dict(PRUNE_LEVELS[level])
    if overrides:
        cfg.update(overrides)

    data = copy.deepcopy(parsed)

    # 1. Summary
    summary = str(data.get("summary") or "").strip()
    if summary:
        data["summary"] = _smart_truncate(summary, cfg["summary"])

    # 2. Experience — keep the most recent roles, trim bullets, never a bare title
    exp = data.get("experience", []) or []
    if exp:
        exp = exp[: cfg["exp"]]
        for entry in exp:
            bullets = [b for b in (entry.get("bullets") or []) if str(b).strip()]
            entry["bullets"] = [
                _smart_truncate(str(b), cfg["bchars"]) for b in bullets[: cfg["bullets"]]
            ]
        data["experience"] = exp

    # 3. Education
    edu = data.get("education", []) or []
    if edu:
        edu = edu[: cfg["edu"]]
        for entry in edu:
            kept = [b for b in (entry.get("bullets") or []) if str(b).strip()]
            entry["bullets"] = kept[: cfg["edu_bullets"]] if cfg["edu_bullets"] else []
        data["education"] = edu

    # 4. Skills
    skills = data.get("skills", []) or []
    if skills:
        data["skills"] = skills[: cfg["skills"]]

    # 5. Projects
    proj = data.get("projects", []) or []
    if proj:
        proj = proj[: cfg["proj"]]
        for entry in proj:
            if isinstance(entry, dict):
                bullets = [b for b in (entry.get("bullets") or []) if str(b).strip()]
                entry["bullets"] = [
                    _smart_truncate(str(b), cfg["bchars"]) for b in bullets[: cfg["bullets"]]
                ]
        data["projects"] = proj

    # 6. Secondary sections: drop whole low-value sections, cheapest first.
    for key in DROP_ORDER[min(cfg["drop"], len(DROP_ORDER) - 1)]:
        data[key] = []

    # Never leave a heading stranded above nothing.
    return _drop_empty_sections(data)


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
    spacing: float = 1.0,
) -> BaseDocTemplate:

    # `spacing` > 1 opens the layout up so a short resume fills the page
    # instead of leaving a block of white space at the bottom.
    top_m   = (0.35 * inch if compact else MARGIN_TOP) * spacing
    bot_m   = (0.35 * inch if compact else MARGIN_BOTTOM) * spacing
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

def _build_story(parsed: dict, compact: bool = False, spacing: float = 1.0) -> list:
    story = []
    sp_h = (2 if compact else 4) * spacing
    sp_exp = (3 if compact else 5) * spacing

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

    # ── Interests ───────────────────────────────────────────────────────────
    interests = parsed.get("interests", [])
    if interests:
        story.extend(section_heading("Interests"))
        story.extend(list_section_block(interests))
        story.append(Spacer(1, sp_h))

    # ── Volunteer ───────────────────────────────────────────────────────────
    volunteer = parsed.get("volunteer", [])
    if volunteer:
        story.extend(section_heading("Volunteer"))
        story.extend(list_section_block(volunteer))
        story.append(Spacer(1, sp_h))

    return story

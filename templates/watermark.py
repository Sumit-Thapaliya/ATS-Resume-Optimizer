"""
templates/watermark.py
----------------------
Diagonal centre watermark renderer for ReportLab PDFs.

Usage
-----
Pass `draw_watermark` (or a partial with custom args) as the
`onPage` / `onLaterPages` callback on a ReportLab `PageTemplate`:

    from functools import partial
    from templates.watermark import draw_watermark

    cb = partial(draw_watermark, text="CONFIDENTIAL", opacity=0.07)
    PageTemplate(id="main", frames=[frame], onPage=cb, onLaterPages=cb)
"""

from __future__ import annotations

import math
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER


# ---------------------------------------------------------------------------
# Default watermark settings
# ---------------------------------------------------------------------------
DEFAULT_TEXT      = "DRAFT"
DEFAULT_FONT      = "Helvetica-Bold"
DEFAULT_FONT_SIZE = 64
DEFAULT_COLOR     = colors.HexColor("#D0D0D0")
DEFAULT_OPACITY   = 0.05          # 0.0 = invisible, 1.0 = fully opaque
DEFAULT_ANGLE     = 45            # degrees counter-clockwise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def draw_watermark(
    canvas,
    doc,
    *,
    text: str       = DEFAULT_TEXT,
    font: str       = DEFAULT_FONT,
    font_size: int  = DEFAULT_FONT_SIZE,
    color           = DEFAULT_COLOR,
    opacity: float  = DEFAULT_OPACITY,
    angle: float    = DEFAULT_ANGLE,
) -> None:
    """
    Draw a diagonal semi-transparent watermark centred on the page.
    """
    page_w, page_h = doc.pagesize

    canvas.saveState()

    # --- transparency --------------------------------------------------------
    if hasattr(canvas, "setFillAlpha"):
        canvas.setFillAlpha(opacity)
    if hasattr(canvas, "setStrokeAlpha"):
        canvas.setStrokeAlpha(opacity)

    canvas.setFillColor(color)
    canvas.setFont(font, font_size)

    # --- centre of page ------------------------------------------------------
    cx = page_w / 2.0
    cy = page_h / 2.0

    # Move origin to page centre, rotate, then draw centred text
    canvas.translate(cx, cy)
    canvas.rotate(angle)

    # Measure text width so we can centre it horizontally after rotation
    text_width = canvas.stringWidth(text, font, font_size)

    canvas.drawString(-text_width / 2.0, -font_size / 4.0, text)

    canvas.restoreState()


def draw_watermark_tiled(
    canvas,
    doc,
    *,
    text: str       = DEFAULT_TEXT,
    font: str       = DEFAULT_FONT,
    font_size: int  = 36,
    color           = DEFAULT_COLOR,
    opacity: float  = 0.05,
    angle: float    = DEFAULT_ANGLE,
    cols: int       = 3,
    rows: int       = 5,
) -> None:
    """
    Draw a tiled watermark pattern across the entire page.

    Similar to ``draw_watermark`` but repeats the text in a grid pattern.
    Useful when a single large watermark isn't visible enough on a long page.

    Args:
        canvas:    ReportLab canvas object.
        doc:       ReportLab document object.
        text:      Watermark string.
        font:      ReportLab font name.
        font_size: Font size in points.
        color:     ReportLab color object.
        opacity:   Alpha value (0.0–1.0).
        angle:     Rotation angle in degrees.
        cols:      Number of watermark columns across the page.
        rows:      Number of watermark rows down the page.
    """
    page_w, page_h = doc.pagesize

    canvas.saveState()
    canvas.setFillColor(color, alpha=opacity)
    canvas.setFont(font, font_size)

    col_step = page_w / cols
    row_step = page_h / rows
    text_width = canvas.stringWidth(text, font, font_size)

    for c in range(cols):
        for r in range(rows):
            cx = col_step * c + col_step / 2
            cy = row_step * r + row_step / 2

            canvas.saveState()
            canvas.translate(cx, cy)
            canvas.rotate(angle)
            canvas.drawString(-text_width / 2.0, -font_size / 4.0, text)
            canvas.restoreState()

    canvas.restoreState()

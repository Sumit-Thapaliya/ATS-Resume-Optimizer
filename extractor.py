"""
extractor.py
------------
Extracts raw text from a PDF file using PyMuPDF (fitz).
Preserves reading order block-by-block, page-by-page.
"""

import fitz  # PyMuPDF


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract all text from a PDF file in reading order.

    Args:
        pdf_path: Absolute path to the PDF file.

    Returns:
        A single string containing all extracted text,
        with pages separated by a form-feed character.

    Raises:
        ValueError: If the PDF cannot be opened or is encrypted.
        RuntimeError: If text extraction fails.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"Cannot open PDF '{pdf_path}': {exc}") from exc

    if doc.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported.")

    pages_text: list[str] = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)

        # Use "blocks" to preserve spatial reading order (top → bottom, left → right)
        blocks = page.get_text("blocks", sort=True)  # sort=True → reading order

        page_lines: list[str] = []
        for block in blocks:
            # block = (x0, y0, x1, y1, text, block_no, block_type)
            # block_type 0 = text, 1 = image
            if block[6] == 0:  # text block only
                raw = block[4].strip()
                if raw:
                    page_lines.append(raw)

        pages_text.append("\n".join(page_lines))

    doc.close()

    # Join pages; use form-feed so the parser can split on pages if needed
    full_text = "\f".join(pages_text)
    return full_text


def extract_metadata(pdf_path: str) -> dict:
    """
    Extract PDF metadata (title, author, etc.) as a supplemental hint.

    Returns:
        dict with keys: title, author, subject, creator, producer
    """
    try:
        doc = fitz.open(pdf_path)
        meta = doc.metadata or {}
        doc.close()
        return {
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
            "creator": meta.get("creator", ""),
            "producer": meta.get("producer", ""),
        }
    except Exception:
        return {}

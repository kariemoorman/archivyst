"""
Media processing for images, PDFs, and attachments.

Handles VLM descriptions for images and PDF text extraction/summarization.
"""

import base64
import io
from pathlib import Path

from archivyst.core.models import AttachmentSummary, ImageDescription
from archivyst.llm.client import LLMClient

try:
    import fitz  # type: ignore[import-untyped]  # pymupdf

    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

try:
    import pdfplumber

    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import pytesseract  # type: ignore[import-untyped]
    from PIL import Image

    HAS_OCR = True
except ImportError:
    HAS_OCR = False


IMAGE_DESCRIPTION_PROMPT = """\
Describe this image in detail. Include:
- What the image shows (objects, people, scenes, diagrams)
- Any text visible in the image
- The purpose or context if apparent
- Key visual elements and their relationships

Be specific and factual. If it's a diagram or chart, explain its structure and content."""


PDF_SUMMARY_PROMPT = """\
Summarize the following document content. Provide:
1. Main topic/purpose (1 sentence)
2. Key points (3-5 bullet points)
3. Document type (report, manual, article, etc.)

Content:
{content}

Summary:"""


def load_image_as_base64(
    path: Path,
    max_image_size: int = 50_000_000,
) -> tuple[str, str]:
    """
    Load an image file and convert to base64.

    Returns:
        Tuple of (base64_data, media_type)

    Raises:
        ValueError: If image file exceeds size limit
    """
    file_size = path.stat().st_size
    if file_size > max_image_size:
        raise ValueError(
            f"Image too large ({file_size:,} bytes, max {max_image_size:,}): {path}"
        )

    suffix = path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    media_type = media_types.get(suffix, "image/png")

    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    return data, media_type


async def describe_image(
    client: LLMClient,
    image_path: Path,
) -> ImageDescription:
    """
    Generate a VLM description of an image.

    Args:
        client: LLM client instance (must support vision)
        image_path: Path to the image file
    """
    base64_data, media_type = load_image_as_base64(image_path)

    description = await client.complete_vision(
        prompt=IMAGE_DESCRIPTION_PROMPT,
        image_base64=base64_data,
        media_type=media_type,
    )

    return ImageDescription(
        path=str(image_path),
        description=description.strip(),
        model=client.config.models.vision,
    )


def extract_pdf_text(path: Path, max_pages: int | None = None) -> str:
    """
    Extract text content from a PDF file.

    Uses pymupdf for primary extraction, falls back to pdfplumber for tables.

    Args:
        path: Path to PDF file
        max_pages: Maximum number of pages to extract (None = all)
    """
    if not HAS_FITZ:
        raise ImportError(
            "pymupdf (fitz) is required for PDF extraction: pip install pymupdf"
        )

    doc = fitz.open(path)
    texts: list[str] = []

    pages_to_process = doc.page_count
    if max_pages:
        pages_to_process = min(pages_to_process, max_pages)

    for page_num in range(pages_to_process):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            texts.append(f"--- Page {page_num + 1} ---\n{text}")

    doc.close()
    return "\n\n".join(texts)


def extract_pdf_with_tables(path: Path, max_pages: int | None = None) -> str:
    """
    Extract text and tables from a PDF using pdfplumber.

    Better for documents with tabular data.
    """
    if not HAS_PDFPLUMBER:
        raise ImportError(
            "pdfplumber is required for table extraction: pip install pdfplumber"
        )

    texts: list[str] = []

    with pdfplumber.open(path) as pdf:
        pages_to_process = len(pdf.pages)
        if max_pages:
            pages_to_process = min(pages_to_process, max_pages)

        for page_num in range(pages_to_process):
            page = pdf.pages[page_num]

            # Extract tables
            tables = page.extract_tables()
            table_texts = []
            for table in tables:
                if table:
                    rows = [
                        " | ".join(str(cell or "") for cell in row) for row in table
                    ]
                    table_texts.append("\n".join(rows))

            # Extract regular text
            text = page.extract_text() or ""

            page_content = f"--- Page {page_num + 1} ---\n{text}"
            if table_texts:
                page_content += "\n\nTables:\n" + "\n\n".join(table_texts)

            texts.append(page_content)

    return "\n\n".join(texts)


def extract_pdf_text_ocr(path: Path, max_pages: int | None = None) -> str:
    """
    Extract text from a scanned PDF using OCR.

    Uses pymupdf to render pages as images, then pytesseract for OCR.
    """
    if not HAS_FITZ or not HAS_OCR:
        raise ImportError(
            "pymupdf, pytesseract, and Pillow are required for OCR:"
            " pip install pymupdf pytesseract Pillow"
        )

    doc = fitz.open(path)
    texts: list[str] = []

    pages_to_process = doc.page_count
    if max_pages:
        pages_to_process = min(pages_to_process, max_pages)

    for page_num in range(pages_to_process):
        page = doc[page_num]

        # Render page as image
        mat = fitz.Matrix(2, 2)  # 2x zoom for better OCR
        pix = page.get_pixmap(matrix=mat)

        # Convert to PIL Image
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # OCR
        text = pytesseract.image_to_string(img)
        if text.strip():
            texts.append(f"--- Page {page_num + 1} (OCR) ---\n{text}")

    doc.close()
    return "\n\n".join(texts)


async def summarize_pdf(
    client: LLMClient,
    path: Path,
    use_ocr: bool = False,
    max_pages: int = 20,
) -> AttachmentSummary:
    """
    Extract and summarize a PDF document.

    Args:
        client: LLM client instance
        path: Path to PDF file
        use_ocr: Whether to use OCR for text extraction
        max_pages: Maximum pages to process
    """
    # Extract text
    if use_ocr:
        content = extract_pdf_text_ocr(path, max_pages)
    else:
        content = extract_pdf_text(path, max_pages)

        # Fall back to OCR if no text extracted
        if not content.strip():
            content = extract_pdf_text_ocr(path, max_pages)

    # Truncate for LLM
    max_content = 8000
    truncated = content[:max_content]
    if len(content) > max_content:
        truncated += "\n[...truncated...]"

    # Generate summary
    prompt = PDF_SUMMARY_PROMPT.format(content=truncated)
    summary = await client.complete(
        prompt=prompt,
        task="summarization",
        max_tokens=400,
    )

    # Try to extract structure (headings from PDF)
    structure = extract_pdf_structure(path)

    return AttachmentSummary(
        source_path=str(path),
        summary=summary.strip(),
        structure=structure,
        model=client.config.models.summarization,
    )


def extract_pdf_structure(path: Path) -> list[str] | None:
    """
    Extract outline/bookmarks from a PDF if available.

    Returns list of heading strings or None if no structure found.
    """
    if not HAS_FITZ:
        raise ImportError("pymupdf (fitz) is required: pip install pymupdf")

    doc = fitz.open(path)
    toc = doc.get_toc()
    doc.close()

    if not toc:
        return None

    # Format as indented list
    structure: list[str] = []
    for level, title, page in toc:
        indent = "  " * (level - 1)
        structure.append(f"{indent}{title} (p.{page})")

    return structure


async def describe_pdf_page_visual(
    client: LLMClient,
    path: Path,
    page_num: int = 0,
) -> str:
    """
    Use VLM to describe a PDF page visually.

    Useful for pages with diagrams, charts, or complex layouts.
    """
    if not HAS_FITZ:
        raise ImportError("pymupdf (fitz) is required: pip install pymupdf")

    doc = fitz.open(path)
    page = doc[page_num]

    # Render as image
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(matrix=mat)
    img_data = pix.tobytes("png")
    doc.close()

    # Convert to base64
    base64_data = base64.b64encode(img_data).decode("utf-8")

    description = await client.complete_vision(
        prompt=(
            "Describe this document page."
            " Include any diagrams, charts, tables, and their content."
        ),
        image_base64=base64_data,
        media_type="image/png",
    )

    return description.strip()


def find_vault_images(vault_path: Path, formats: list[str]) -> list[Path]:
    """
    Find all images in a vault.

    Args:
        vault_path: Root path of the vault
        formats: List of image extensions to include (without dots)
    """
    images: list[Path] = []

    for fmt in formats:
        images.extend(vault_path.rglob(f"*.{fmt}"))
        images.extend(vault_path.rglob(f"*.{fmt.upper()}"))

    return images


def find_vault_pdfs(vault_path: Path) -> list[Path]:
    """Find all PDFs in a vault."""
    pdfs = list(vault_path.rglob("*.pdf"))
    pdfs.extend(vault_path.rglob("*.PDF"))
    return pdfs

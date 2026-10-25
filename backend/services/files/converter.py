import io
from pathlib import Path
from typing import Optional, Tuple
import fitz  # PyMuPDF
import PIL.Image
import PIL.ImageOps
from backend.core.exceptions import AppException, ErrorCode
from backend.core.logging import logger

# ReportLab imports for building standardized printable PDFs
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def convert_image_to_pdf(file_bytes: bytes) -> bytes:
    """Converts image bytes (JPG, PNG, BMP, WEBP, TIFF) into a standard printable PDF."""
    try:
        img = PIL.Image.open(io.BytesIO(file_bytes))
        # Correct mobile phone camera orientation via EXIF
        try:
            img = PIL.ImageOps.exif_transpose(img)
        except Exception:
            pass

        # Convert to RGB (flatten RGBA transparency onto pure white)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = PIL.Image.new("RGB", img.size, (255, 255, 255))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        pdf_buf = io.BytesIO()
        img.save(pdf_buf, format="PDF", resolution=150.0)
        return pdf_buf.getvalue()
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Failed to process image: {str(e)}",
        )


def convert_docx_to_pdf(file_bytes: bytes) -> bytes:
    """Converts a Word document (.docx) into a clean, formatted printable PDF."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Failed to parse Word document: {str(e)}",
        )

    out_buf = io.BytesIO()
    pdf = SimpleDocTemplate(
        out_buf,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    story = []

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            story.append(Spacer(1, 6))
            continue

        p_style_name = p.style.name.lower() if p.style and p.style.name else ""
        if "heading 1" in p_style_name or "title" in p_style_name:
            style = styles["Heading1"]
        elif "heading 2" in p_style_name:
            style = styles["Heading2"]
        elif "heading 3" in p_style_name:
            style = styles["Heading3"]
        else:
            style = styles["Normal"]

        story.append(Paragraph(text, style))
        story.append(Spacer(1, 4))

    for tbl in doc.tables:
        table_data = []
        for row in tbl.rows:
            table_data.append([c.text.strip() for c in row.cells])
        if table_data:
            t_obj = Table(table_data)
            t_obj.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t_obj)
            story.append(Spacer(1, 10))

    if not story:
        story.append(Paragraph("Empty Document", styles["Normal"]))

    try:
        pdf.build(story)
        return out_buf.getvalue()
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Could not render Word document to PDF: {str(e)}",
        )


def convert_pptx_to_pdf(file_bytes: bytes) -> bytes:
    """Converts a PowerPoint presentation (.pptx) into a landscape printable PDF (1 slide = 1 page)."""
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(file_bytes))
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Failed to parse PowerPoint presentation: {str(e)}",
        )

    out_buf = io.BytesIO()
    # A4 Landscape (841.89 x 595.27 points)
    pdf = SimpleDocTemplate(
        out_buf,
        pagesize=landscape(A4),
        leftMargin=40,
        rightMargin=40,
        topMargin=35,
        bottomMargin=35,
    )
    styles = getSampleStyleSheet()
    story = []

    slide_count = len(prs.slides)
    for idx, slide in enumerate(prs.slides, 1):
        story.append(Paragraph(f"<b>Slide {idx} of {slide_count}</b>", styles["Heading3"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey, spaceAfter=12))

        has_content = False
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text:
                for p in shape.text_frame.paragraphs:
                    txt = p.text.strip()
                    if txt:
                        story.append(Paragraph(txt, styles["Normal"]))
                        story.append(Spacer(1, 4))
                        has_content = True

        if not has_content:
            story.append(Paragraph("<i>(Slide contains graphic or empty content)</i>", styles["Italic"]))

        # Break page after each slide except the last one
        if idx < slide_count:
            story.append(PageBreak())

    if not story:
        story.append(Paragraph("Empty Presentation", styles["Normal"]))

    try:
        pdf.build(story)
        return out_buf.getvalue()
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Could not render PowerPoint to PDF: {str(e)}",
        )


def convert_xlsx_to_pdf(file_bytes: bytes) -> bytes:
    """Converts an Excel workbook (.xlsx) into a formatted tabular printable PDF."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Failed to parse Excel workbook: {str(e)}",
        )

    out_buf = io.BytesIO()
    pdf = SimpleDocTemplate(
        out_buf,
        pagesize=landscape(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30,
    )
    styles = getSampleStyleSheet()
    story = []

    sheet_count = len(wb.sheetnames)
    for idx, sheet_name in enumerate(wb.sheetnames):
        ws = wb[sheet_name]
        story.append(Paragraph(f"<b>Sheet: {sheet_name}</b>", styles["Heading2"]))
        story.append(Spacer(1, 8))

        table_data = []
        for row in ws.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                table_data.append([str(c) if c is not None else "" for c in row])

        if table_data:
            # Cap columns at 10 per table view for clean fitting
            max_cols = min(12, max(len(r) for r in table_data))
            fitted_data = [r[:max_cols] for r in table_data[:100]]  # limit first 100 rows per sheet
            t_obj = Table(fitted_data)
            t_obj.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(t_obj)
            story.append(Spacer(1, 15))
        else:
            story.append(Paragraph("<i>(Empty Sheet)</i>", styles["Italic"]))
            story.append(Spacer(1, 10))

        if idx < sheet_count - 1:
            story.append(PageBreak())

    if not story:
        story.append(Paragraph("Empty Spreadsheet", styles["Normal"]))

    try:
        pdf.build(story)
        return out_buf.getvalue()
    except Exception as e:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message=f"Could not render Excel spreadsheet to PDF: {str(e)}",
        )


def convert_text_to_pdf(file_bytes: bytes, filename: str) -> bytes:
    """Converts plain text or CSV into a standardized printable PDF."""
    try:
        text_content = file_bytes.decode("utf-8", errors="replace")
    except Exception:
        text_content = file_bytes.decode("latin-1", errors="replace")

    out_buf = io.BytesIO()
    pdf = SimpleDocTemplate(out_buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{filename}</b>", styles["Heading3"]),
        HRFlowable(width="100%", thickness=1, color=colors.lightgrey, spaceAfter=8),
    ]

    for line in text_content.splitlines():
        clean_line = line.strip()
        if not clean_line:
            story.append(Spacer(1, 4))
        else:
            story.append(Paragraph(clean_line, styles["Normal"]))

    pdf.build(story)
    return out_buf.getvalue()


def convert_and_validate_document(
    file_bytes: bytes,
    original_filename: str,
    mime_type: Optional[str] = None,
) -> Tuple[bytes, int, str]:
    """
    Universal document validator and converter.
    Accepts:
      - PDF (.pdf)
      - Images (.jpg, .jpeg, .png, .bmp, .webp, .tiff)
      - Word (.docx, .doc)
      - PowerPoint (.pptx, .ppt)
      - Excel (.xlsx, .xls)
      - Text (.txt, .csv)

    Returns:
      (pdf_bytes, page_count, detected_format)
    Raises:
      AppException if file is empty, password-protected, corrupted, or unsupported.
    """
    if not file_bytes or len(file_bytes) == 0:
        raise AppException(
            code=ErrorCode.CORRUPT_FILE,
            message="Uploaded document is empty (0 bytes).",
        )

    ext = Path(original_filename).suffix.lower()
    clean_mime = (mime_type or "").lower()

    # 1. Native PDF
    if file_bytes.startswith(b"%PDF-") or ext == ".pdf":
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            if doc.is_encrypted:
                doc.close()
                raise AppException(
                    code=ErrorCode.PASSWORD_PROTECTED_FILE,
                    message="PDF is password protected. Please upload an unlocked PDF.",
                )
            page_count = doc.page_count
            doc.close()
            if page_count <= 0:
                raise AppException(
                    code=ErrorCode.ZERO_PAGE_FILE,
                    message="PDF document contains zero pages.",
                )
            return file_bytes, page_count, "pdf"
        except AppException:
            raise
        except Exception as e:
            raise AppException(
                code=ErrorCode.CORRUPT_FILE,
                message=f"Failed to parse PDF document: {str(e)}",
            )

    # 2. Image formats (JPEG, PNG, WEBP, BMP, TIFF)
    is_image = (
        ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif"]
        or clean_mime.startswith("image/")
        or file_bytes.startswith(b"\xff\xd8\xff")  # JPEG
        or file_bytes.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or file_bytes.startswith(b"BM")  # BMP
        or (len(file_bytes) > 12 and file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP")
    )
    if is_image:
        pdf_bytes = convert_image_to_pdf(file_bytes)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return pdf_bytes, page_count, "image"

    # 3. Microsoft Word (.docx, .doc)
    if ext in [".docx", ".doc"] or "wordprocessingml" in clean_mime or "msword" in clean_mime:
        pdf_bytes = convert_docx_to_pdf(file_bytes)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return pdf_bytes, page_count, "docx"

    # 4. Microsoft PowerPoint (.pptx, .ppt)
    if ext in [".pptx", ".ppt"] or "presentationml" in clean_mime or "powerpoint" in clean_mime:
        pdf_bytes = convert_pptx_to_pdf(file_bytes)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return pdf_bytes, page_count, "pptx"

    # 5. Microsoft Excel (.xlsx, .xls)
    if ext in [".xlsx", ".xls"] or "spreadsheetml" in clean_mime or "excel" in clean_mime:
        pdf_bytes = convert_xlsx_to_pdf(file_bytes)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return pdf_bytes, page_count, "xlsx"

    # 6. Plain Text / CSV (.txt, .csv)
    if ext in [".txt", ".csv"] or clean_mime.startswith("text/"):
        pdf_bytes = convert_text_to_pdf(file_bytes, original_filename)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        doc.close()
        return pdf_bytes, page_count, "text"

    # Unsupported format
    raise AppException(
        code=ErrorCode.UNSUPPORTED_FILE,
        message=(
            f"Unsupported file format '{ext or original_filename}'. "
            f"Supported formats include: PDF (.pdf), Word (.docx), PowerPoint (.pptx), "
            f"Excel (.xlsx), Images (.jpg, .png, .jpeg, .webp), and Text (.txt)."
        ),
    )

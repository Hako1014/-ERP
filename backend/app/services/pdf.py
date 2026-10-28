from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_NAME = "STSong-Light"
_registered = False


def _ensure_font() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))
        _registered = True


@dataclass
class PdfTable:
    headers: list[str]
    rows: list[list[Any]]


@dataclass
class PdfDocument:
    title: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    tables: list[PdfTable] = field(default_factory=list)
    footers: list[tuple[str, str]] = field(default_factory=list)
    page_size_mm: tuple[float, float] | None = None


def _cell(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph("" if value is None else str(value), style)


def _resolve_page_size(
    page_size_mm: tuple[float, float] | None,
) -> tuple[float, float]:
    if not page_size_mm:
        return A4
    width, height = page_size_mm
    if width <= 0 or height <= 0:
        raise ValueError("纸张尺寸必须大于 0")
    return float(width) * mm, float(height) * mm


def _build_styles(
    page_width: float,
    page_height: float,
) -> tuple[ParagraphStyle, ParagraphStyle]:
    compact = page_width < 140 * mm or page_height < 160 * mm
    title_size = 12 if compact else 16
    body_size = 7 if compact else 9

    title_style = ParagraphStyle(
        "TitleCN",
        parent=getSampleStyleSheet()["Title"],
        fontName=FONT_NAME,
        fontSize=title_size,
        leading=title_size + 5,
        alignment=1,
        spaceAfter=6 if compact else 12,
    )
    normal_style = ParagraphStyle(
        "NormalCN",
        parent=getSampleStyleSheet()["Normal"],
        fontName=FONT_NAME,
        fontSize=body_size,
        leading=body_size + 5,
    )
    return title_style, normal_style


def _document_story(
    doc: PdfDocument,
    *,
    title_style: ParagraphStyle,
    normal_style: ParagraphStyle,
    available: float,
) -> list[Any]:
    story: list[Any] = [Paragraph(doc.title, title_style)]

    if doc.fields:
        field_rows = [
            [_cell(f"{k}：", normal_style), _cell(v, normal_style)]
            for k, v in doc.fields
        ]
        label_width = min(35 * mm, available * 0.32)
        field_table = Table(
            field_rows,
            colWidths=[label_width, available - label_width],
        )
        field_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(field_table)
        story.append(Spacer(1, 8))

    for table in doc.tables:
        ncols = len(table.headers)
        col_width = available / ncols if ncols else available
        data = [[_cell(h, normal_style) for h in table.headers]]
        data += [[_cell(c, normal_style) for c in row] for row in table.rows]
        t = Table(data, colWidths=[col_width] * ncols, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 8))

    for k, v in doc.footers:
        story.append(Paragraph(f"{k}：{v}", normal_style))
        story.append(Spacer(1, 3))

    return story


def render_pdf_many(
    docs: list[PdfDocument],
    *,
    page_size_mm: tuple[float, float] | None = None,
) -> bytes:
    """按单据顺序渲染为多页 PDF，每张单据至少占一页。"""
    if not docs:
        raise ValueError("至少需要一张单据")

    _ensure_font()
    effective_page_size = page_size_mm or docs[0].page_size_mm
    pagesize = _resolve_page_size(effective_page_size)
    page_width, page_height = pagesize
    compact = page_width < 140 * mm or page_height < 160 * mm
    horizontal_margin = 5 * mm if compact else 15 * mm
    top_margin = 8 * mm if compact else 20 * mm
    bottom_margin = 8 * mm if compact else 20 * mm

    buf = BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=pagesize,
        leftMargin=horizontal_margin,
        rightMargin=horizontal_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
        title=docs[0].title,
    )
    available = page_width - horizontal_margin * 2
    title_style, normal_style = _build_styles(page_width, page_height)

    story: list[Any] = []
    for index, doc in enumerate(docs):
        if index:
            story.append(PageBreak())
        story.extend(
            _document_story(
                doc,
                title_style=title_style,
                normal_style=normal_style,
                available=available,
            )
        )

    pdf.build(story)
    return buf.getvalue()


def render_pdf(doc: PdfDocument) -> bytes:
    """将单据结构渲染为 PDF，支持中文与按模板输出的头信息/明细表/合计。"""
    return render_pdf_many([doc])

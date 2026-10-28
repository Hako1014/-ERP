"""受控文件解析、来源定位与模型结构化提取；只读附件，不创建业务单据。"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pypdf import PdfReader

from app.core.errors import BizException, ErrorCode
from app.models.assistant_attachment import AssistantAttachment
from app.services.assistant_attachment_files import PrivateAttachmentStorage, policy

MAX_TEXT_CHARS = 4_000
MAX_SOURCE_LINES = 101
MAX_SHEET_COLUMNS = 20
MAX_RENDERED_BYTES = 15 * 1024 * 1024
MAX_RENDERED_PIXELS = 40_000_000


def _bad(message: str) -> None:
    raise BizException(ErrorCode.BAD_REQUEST, message)


def _safe_bytes(row: AssistantAttachment) -> bytes:
    data = PrivateAttachmentStorage().read(row.storage_key)
    import hashlib

    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise BizException(ErrorCode.CONFLICT, "附件内容校验失败，请重新上传")
    return data


def _suffix(row: AssistantAttachment) -> str:
    return Path(row.display_name).suffix.lower()


def _pdf_info(data: bytes) -> tuple[list[str], list[int]]:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        texts = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception:
        raise BizException(ErrorCode.VALIDATION_ERROR, "PDF 无法安全读取，请重新导出后上传") from None
    return texts, [index + 1 for index, value in enumerate(texts) if len(value) < 24]


def options(row: AssistantAttachment) -> dict[str, Any]:
    """仅返回本地选择项和扫描页信息，不调用模型、不返回正文。"""
    data = _safe_bytes(row)
    ext = _suffix(row)
    if row.media_type.startswith("image/"):
        return {"kind": "image", "page_count": 1, "scan_pages": [], "sheets": []}
    if ext == ".pdf":
        text_pages, scan_pages = _pdf_info(data)
        return {"kind": "pdf", "page_count": len(text_pages), "scan_pages": scan_pages, "sheets": []}
    if ext == ".xlsx":
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            return {"kind": "xlsx", "page_count": None, "scan_pages": [],
                    "sheets": [sheet.title for sheet in workbook.worksheets]}
        finally:
            workbook.close()
    return {"kind": "text", "page_count": None, "scan_pages": [], "sheets": []}


def _lines(rows: list[list[Any]], *, ref_prefix: str, source_label: str, source_kind: str) -> tuple[str, list[dict[str, Any]]]:
    if len(rows) > MAX_SOURCE_LINES:
        _bad(f"内容超过 {MAX_SOURCE_LINES} 行，请先拆分文件；系统未截断原文")
    text_lines, sources = [], []
    for index, row in enumerate(rows, start=1):
        values = ["" if value is None else str(value).strip() for value in row]
        if not any(values):
            continue
        if len(values) > MAX_SHEET_COLUMNS:
            _bad(f"{source_label}超过 {MAX_SHEET_COLUMNS} 列，请先拆分；系统未截断内容")
        ref = f"{ref_prefix}{index:03d}"
        text_value = " | ".join(values)
        text_lines.append(f"[REF:{ref}] {text_value}")
        sources.append({"ref": ref, "location": source_label.replace("{row}", str(index)), "text": text_value})
    text = "\n".join(text_lines)
    if len(text) > MAX_TEXT_CHARS:
        _bad(f"可识别文本超过 {MAX_TEXT_CHARS} 字，请拆分或选择较少页面；系统未静默截断")
    if not text:
        _bad("文件中未找到可识别的文字或单元格")
    return text, sources


def _csv_rows(data: bytes, media_type: str) -> list[list[str]]:
    from app.services.assistant_attachment_files import decode_text

    content = decode_text(data)
    try:
        sample = content[:2048]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|") if sample else csv.excel
        rows = list(csv.reader(io.StringIO(content), dialect))
    except csv.Error:
        rows = list(csv.reader(io.StringIO(content)))
    if len(rows) > MAX_SOURCE_LINES:
        _bad(f"表格超过 {MAX_SOURCE_LINES} 行，请先拆分文件；系统未截断原文")
    return rows


def _pdf_text(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    text_pages, _ = _pdf_info(data)
    lines, sources = [], []
    total_lines = 0
    for page_no, page_text in enumerate(text_pages, start=1):
        for row_no, value in enumerate((line.strip() for line in page_text.splitlines()), start=1):
            if not value:
                continue
            total_lines += 1
            if total_lines > MAX_SOURCE_LINES:
                _bad(f"PDF 可提取文字超过 {MAX_SOURCE_LINES} 行，请选择较少页面；系统未截断原文")
            ref = f"P{page_no:03d}L{row_no:03d}"
            lines.append(f"[REF:{ref}] {value}")
            sources.append({"ref": ref, "location": f"第 {page_no} 页 · 第 {row_no} 行", "text": value})
    text = "\n".join(lines)
    if len(text) > MAX_TEXT_CHARS:
        _bad(f"PDF 可提取文字超过 {MAX_TEXT_CHARS} 字，请选择较少扫描页或拆分文件；系统未静默截断")
    return text, sources


def _xlsx_text(data: bytes, sheet_name: str | None) -> tuple[str, list[dict[str, Any]]]:
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        names = [sheet.title for sheet in workbook.worksheets]
        if len(names) > 1 and not sheet_name:
            _bad("工作簿包含多个工作表，请明确选择一个工作表")
        if sheet_name and sheet_name not in names:
            _bad("所选工作表不存在，请重新选择")
        sheet = workbook[sheet_name or names[0]]
        rows = []
        for index, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            if index > MAX_SOURCE_LINES:
                _bad(f"工作表超过 {MAX_SOURCE_LINES} 行，请先拆分；系统未截断原文")
            rows.append(list(values))
        return _lines(rows, ref_prefix="R", source_label=f"工作表 {sheet.title} · 第 {{row}} 行", source_kind="xlsx")
    finally:
        workbook.close()


def _render_pdf_pages(data: bytes, pages: list[int]) -> list[dict[str, Any]]:
    import pypdfium2 as pdfium

    rendered: list[dict[str, Any]] = []
    total_bytes = 0
    total_pixels = 0
    try:
        document = pdfium.PdfDocument(data)
        try:
            for page_no in pages:
                page = document[page_no - 1]
                width, height = page.get_size()
                pixels = width * height
                max_pixels = policy()["max_image_pixels"]
                scale = min(1.5, math.sqrt(max_pixels / max(pixels, 1)))
                if scale < 0.5:
                    _bad(f"PDF 第 {page_no} 页尺寸异常，无法安全渲染；请先拆分或降低页面尺寸")
                total_pixels += int(width * scale) * int(height * scale)
                if total_pixels > MAX_RENDERED_PIXELS:
                    _bad("所选 PDF 页渲染总像素超过 4000 万，请减少选择页数；未发送部分页面")
                bitmap = page.render(scale=scale, grayscale=False)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=88, optimize=True)
                    data_jpeg = output.getvalue()
                finally:
                    bitmap.close()
                    page.close()
                total_bytes += len(data_jpeg)
                if total_bytes > MAX_RENDERED_BYTES:
                    _bad("所选页面渲染后超过 15 MiB，请减少选择页数；未发送部分页面")
                rendered.append({"ref": f"P{page_no:03d}", "data": data_jpeg, "media_type": "image/jpeg"})
        finally:
            document.close()
    except BizException:
        raise
    except Exception:
        raise BizException(ErrorCode.VALIDATION_ERROR, "扫描 PDF 页面无法安全渲染，请重新导出后上传") from None
    return rendered


def prepare_input(row: AssistantAttachment, *, pages: list[int], sheet_name: str | None) -> dict[str, Any]:
    data = _safe_bytes(row)
    ext = _suffix(row)
    images: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    text = ""
    if row.media_type.startswith("image/"):
        images = [{"ref": "IMG001", "data": data, "media_type": row.media_type}]
        text = "[REF:IMG001] 用户提供的单据图片；请仅提取图片中可读的信息。"
        sources = [{"ref": "IMG001", "location": "图片原件", "text": "图像内容由模型识别；请逐项人工核对"}]
        mode = "image"
    elif ext == ".pdf":
        text, sources = _pdf_text(data)
        _, scan_pages = _pdf_info(data)
        if scan_pages and not pages:
            _bad("检测到扫描页，请明确选择要发送识别的页码")
        if any(page not in scan_pages for page in pages):
            _bad("所选页中包含非扫描页或不存在的页码，请刷新选择")
        if len(pages) > policy()["max_pdf_pages"]:
            _bad(f"单次最多选择 {policy()['max_pdf_pages']} 个扫描页")
        images = _render_pdf_pages(data, pages) if pages else []
        sources.extend({"ref": image["ref"], "location": f"PDF 第 {int(image['ref'][1:])} 页扫描图", "text": "页面图像由模型识别；请人工核对"} for image in images)
        if not text and not images:
            _bad("所选 PDF 页面没有可提取文字或扫描图")
        mode = "pdf"
    elif ext == ".xlsx":
        text, sources = _xlsx_text(data, sheet_name)
        mode = "xlsx"
    elif ext == ".csv":
        rows = _csv_rows(data, row.media_type)
        text, sources = _lines(rows, ref_prefix="L", source_label="第 {row} 行", source_kind="csv")
        mode = "csv"
    else:
        from app.services.assistant_attachment_files import decode_text

        content = decode_text(data)
        rows = [[value] for value in content.splitlines()]
        text, sources = _lines(rows, ref_prefix="L", source_label="第 {row} 行", source_kind="text")
        mode = "text"
    if len(text) > MAX_TEXT_CHARS:
        _bad(f"提取内容超过 {MAX_TEXT_CHARS} 字，请拆分文件或选择更少页面；系统未静默截断")
    return {"mode": mode, "text": text, "sources": sources, "images": images,
            "selections": {"pages": pages, "sheet_name": sheet_name}}


def messages_for_input(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services.assistant_drafts import extraction_messages

    source_text = prepared["text"] or "图片已按用户选择的范围附在下方。"
    messages = extraction_messages(source_text, include_source_refs=True)
    user_content: list[dict[str, Any]] = [{"type": "text", "text": source_text}]
    for image in prepared["images"]:
        from app.integrations.assistant_provider import inline_image_content

        user_content.append({"type": "text", "text": f"[REF:{image['ref']}] 用户选择的图像来源。"})
        user_content.append(inline_image_content(image["data"], image["media_type"]))
    messages[1]["content"] = user_content
    return messages


def result_contract(extracted, *, source_text: str, sources: list[dict[str, Any]], usage: dict[str, Any]) -> dict[str, Any]:
    known_refs = {item["ref"] for item in sources}

    def verified_ref(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if cleaned.startswith("[REF:") and cleaned.endswith("]"):
            cleaned = cleaned[5:-1]
        return cleaned if cleaned in known_refs else None

    def field(value: str | None, source_ref: str | None) -> dict[str, Any]:
        ref = verified_ref(source_ref)
        return {"value": value, "source_ref": ref, "source_confirmed": bool(value and ref)}

    items = []
    for item in extracted.items:
        values = item.model_dump()
        source_ref = values.pop("source_ref", None)
        item_sources = {key: field(value, source_ref) for key, value in values.items()}
        items.append({"fields": item_sources, "source_ref": verified_ref(source_ref)})
    fields = {
        "supplier_name": field(extracted.supplier_name, extracted.supplier_source_ref),
        "supplier_code": field(extracted.supplier_code, extracted.supplier_source_ref),
        "plan_date": field(extracted.plan_date, extracted.plan_date_source_ref),
        "remark": field(extracted.remark, extracted.remark_source_ref),
    }
    missing = [name for name, value in fields.items() if name != "remark" and not value["value"]]
    if not items:
        missing.append("商品明细")
    for index, item in enumerate(items, start=1):
        for name in ("product_name", "product_code", "quantity"):
            if not item["fields"][name]["value"]:
                missing.append(f"明细{index}.{name}")
    return {
        "document_type": extracted.document_type,
        "document_type_supported": extracted.document_type == "purchase_plan",
        "supported_for_draft": extracted.document_type == "purchase_plan" and bool(items),
        "fields": fields,
        "items": items,
        "extracted_text": extracted.extracted_text,
        "missing_fields": missing,
        "source_text": source_text,
        "sources": sources,
        "confirmation_required": True,
        "created_business_document": False,
        "notice": "识别结果仅供核对，不会自动创建或审批单据；未标明来源的值需人工确认。",
        "usage": usage,
    }

from app.services.pdf import PdfDocument, PdfTable, render_pdf


def test_render_pdf_valid():
    doc = PdfDocument(
        title="采购订单",
        fields=[("订单号", "CGDD20260923-0001"), ("供应商", "示例供应商")],
        tables=[PdfTable(["商品", "数量", "金额"], [["苹果", 3, "1.50"]])],
        footers=[("合计", "¥1.50")],
    )
    data = render_pdf(doc)

    assert data.startswith(b"%PDF")
    assert data.rstrip().endswith(b"%%EOF")
    assert len(data) > 500
    assert b"STSong" in data  # 中文字体已嵌入


def test_render_pdf_minimal():
    data = render_pdf(PdfDocument(title="测试单据"))
    assert data.startswith(b"%PDF")
    assert data.rstrip().endswith(b"%%EOF")

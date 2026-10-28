import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.mcp_credentials import router as mcp_credentials_router
from app.api.account_subject import router as account_subject_router
from app.api.after_sale import router as after_sale_router
from app.api.approval import router as approval_router
from app.api.approval_manage import router as approval_manage_router
from app.api.assistant import router as assistant_router
from app.api.assistant_conversations import router as assistant_conversations_router
from app.api.assistant_attachments import router as assistant_attachments_router
from app.api.assistant_extractions import router as assistant_extractions_router
from app.api.assistant_document_drafts import router as assistant_document_drafts_router
from app.api.quick_entry import router as quick_entry_router
from app.api.company import router as company_router
from app.api.common import router as common_router
from app.api.dashboard import router as dashboard_router
from app.api.finance_doc import router as finance_doc_router
from app.api.invoice import router as invoice_router
from app.api.logistics import router as logistics_router
from app.api.notification import router as notification_router
from app.api.organization import router as organization_router
from app.api.order import router as order_router
from app.api.party import router as party_router
from app.api.pay_receipt import router as pay_receipt_router
from app.api.prepay import router as prepay_router
from app.api.print import router as print_router
from app.api.product import router as product_router
from app.api.purchase_invoice import router as purchase_invoice_router
from app.api.purchase_order import router as purchase_order_router
from app.api.purchase_plan import router as purchase_plan_router
from app.api.purchase_return import router as purchase_return_router
from app.api.report import router as report_router
from app.api.shop import router as shop_router
from app.api.ship import router as ship_router
from app.api.stock_check import router as stock_check_router
from app.api.stock_in import router as stock_in_router
from app.api.stock_out import router as stock_out_router
from app.api.stock_query import router as stock_query_router
from app.api.stock_settlement import router as stock_settlement_router
from app.api.transfer import router as transfer_router
from app.api.workbench import router as workbench_router
from app.api.upload import router as upload_router
from app.api.voucher import router as voucher_router
from app.api.warehouse import router as warehouse_router
from app.api.wave import router as wave_router
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import setup_logging
from app.core.ip_access import IpAccessMiddleware
from app.core.middleware import RequestIdMiddleware
from app.core.response import Result

settings = get_settings()
setup_logging(settings.log_level)

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description="电商 ERP 系统后端 API",
    version=settings.app_version,
    debug=settings.debug,
)

app.add_middleware(IpAccessMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()],
    allow_credentials=False,  # 认证只接受 Authorization: Bearer，不使用跨站 Cookie。
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)
app.mount(
    "/uploads",
    StaticFiles(directory=settings.upload_dir, check_dir=False),
    name="uploads",
)
app.include_router(upload_router)
app.include_router(common_router)
app.include_router(auth_router)
app.include_router(mcp_credentials_router)
app.include_router(dashboard_router)
app.include_router(report_router)
app.include_router(account_subject_router)
app.include_router(voucher_router)
app.include_router(finance_doc_router)
app.include_router(invoice_router)
app.include_router(approval_router)
app.include_router(approval_manage_router)
app.include_router(assistant_router)
app.include_router(assistant_conversations_router)
app.include_router(assistant_attachments_router)
app.include_router(assistant_extractions_router)
app.include_router(assistant_document_drafts_router)
app.include_router(quick_entry_router)
app.include_router(company_router)
app.include_router(shop_router)
app.include_router(logistics_router)
app.include_router(notification_router)
app.include_router(organization_router)
app.include_router(workbench_router)
app.include_router(purchase_plan_router)
app.include_router(purchase_order_router)
app.include_router(prepay_router)
app.include_router(purchase_return_router)
app.include_router(purchase_invoice_router)
app.include_router(product_router)
app.include_router(party_router)
app.include_router(pay_receipt_router)
app.include_router(warehouse_router)
app.include_router(stock_in_router)
app.include_router(stock_out_router)
app.include_router(stock_query_router)
app.include_router(stock_check_router)
app.include_router(stock_settlement_router)
app.include_router(transfer_router)
app.include_router(order_router)
app.include_router(wave_router)
app.include_router(print_router)
app.include_router(ship_router)
app.include_router(after_sale_router)


@app.get("/health", tags=["系统"])
def health() -> dict:
    """健康检查，返回裸 JSON 供探针使用（不走 Result 包装）。"""
    return {"status": "ok", "service": "erp-api", "version": app.version}


@app.get("/", tags=["系统"])
def index() -> Result[dict]:
    """首页占位，展示统一响应结构。"""
    return Result.ok({"name": "电商 ERP API", "docs": "/docs"})

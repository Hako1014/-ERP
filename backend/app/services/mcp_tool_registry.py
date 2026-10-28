"""MCP 外发工具目录：只登记输入固定、结果受限且分页有界的只读查询。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from app.core.money import round_money
from app.models.audit_log import AuditLog
from app.models.assistant_governance import AssistantConfig, AssistantUsage
from app.core.query_access import CAPABILITIES, authorize_query
from app.models.basic import (
    Brand, Category, SpecType, SpecValue, Unit, UnitGroup, UnitGroupItem,
)
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.finance import AccountSubject, Invoice, ProjectCategory, Voucher, VoucherItem
from app.models.org import Company, Department, Position, Staff
from app.models.order import (
    ExchangeOrder, ExchangeOrderItem, LoadNo, Order, OrderItem, OrderShipment,
    OrderShipmentItem, PrintTemplate, ReturnOrderItem, ReturnRegister,
    Wave, WaveOrder, WaveStrategy,
)
from app.models.party import Account, Supplier, SupplierPrice
from app.models.platform import AreaRule, FreightTemplate, Logistics, Platform, Shop, ShopApi
from app.models.product import ComboProduct, ComboProductItem, Product, ProductSku
from app.models.rbac import IpRule
from app.models.purchase import (
    PurchaseInvoice, PurchaseOrder, PurchaseOrderItem, PurchasePlanCreationRequest,
    PurchasePlanItem, PurchaseReturn, PurchaseReturnItem,
)
from app.models.stock import (
    LocationAdjust, LocationAdjustItem, Putaway, StockCheck, StockCheckItem,
    StockCostRewriteLog, StockSettlement,
    StockIn, StockInItem, StockOut, StockOutItem, StockWarning, Transfer,
    TransferIn, TransferInItem, TransferItem, TransferOut, TransferOutItem,
    ShopStock,
)
from app.models.warehouse import Location, StockLocation, Warehouse, Zone
from app.services import (
    approval_manage_service,
    approval_service,
    assistant_governance,
    assistant_service,
    after_sale_service,
    account_subject_service,
    finance_doc_service,
    invoice_service,
    logistics_service,
    metrics_service,
    order_service,
    party_service,
    pay_receipt_service,
    prepay_service,
    product_service,
    purchase_invoice_service,
    purchase_order_service,
    purchase_plan_service,
    purchase_return_service,
    query_projection,
    shared_queries,
    stock_check_service,
    stock_in_service,
    stock_out_service,
    stock_query_service,
    transfer_service,
    voucher_service,
    wave_service,
    warehouse_service,
)
from app.services.mcp_credentials import ACCESS_TOOL, McpIdentity
from app.services import notification_query_service


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PageArguments(StrictArguments):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PurchasePlanSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    supplier_id: int | None = Field(default=None, ge=1)
    status: Literal["IN_PROGRESS", "ABORTED", "FINISHED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None


class PurchaseNeedsArguments(PageArguments):
    supplier_id: int = Field(ge=1)
    keyword: str | None = Field(default=None, max_length=64)


class PurchasePlanDetailArguments(StrictArguments):
    plan_id: int = Field(ge=1)


class PurchasePrepayDetailArguments(StrictArguments):
    prepay_id: int = Field(ge=1)


class PurchasePlanItemArguments(PageArguments):
    plan_id: int = Field(ge=1)


class PurchasePlanCreationRequestArguments(StrictArguments):
    request_id: str = Field(min_length=36, max_length=36)


class PurchaseOrderSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    supplier_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "RECEIVING", "RECEIVED", "CLOSED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    plan_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)


class PurchaseOrderDetailArguments(StrictArguments):
    order_id: int = Field(ge=1)


class PurchaseOrderItemArguments(PageArguments):
    order_id: int = Field(ge=1)


class PurchaseOrderReportArguments(PageArguments):
    start_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    end_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    supplier_id: int | None = Field(default=None, ge=1)


class PurchaseInboundReportArguments(PageArguments):
    start_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    end_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    supplier_id: int | None = Field(default=None, ge=1)
    purchaser_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)


class PurchaseInvoiceReportArguments(PageArguments):
    start_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    end_time: str | None = Field(
        default=None, max_length=40,
        description="ISO 8601日期时间；含时区时转UTC，与REST筛选口径一致",
    )
    supplier_id: int | None = Field(default=None, ge=1)
    write_off_status: Literal["NONE", "PARTIAL", "FULL"] | None = None


class InventoryTransitReportArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    supplier_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)


class SalesOrderSearchArguments(PageArguments):
    shop_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)
    status: Literal["NEW", "PENDING", "SHIPPED", "DONE", "CANCELLED", "DELETED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    pay_status: str | None = Field(default=None, max_length=16)
    flag: Literal[
        "DISTRIBUTION", "MERGED", "WEISHANG", "REFUND", "RETURN", "DELETED",
        "CANCELLING", "RESEND_BEFORE", "RESEND_AFTER", "SPLIT_MAIN", "SPLIT_CHILD",
    ] | None = None
    keyword: str | None = Field(default=None, max_length=64)
    start_time: str | None = Field(default=None, max_length=40)
    end_time: str | None = Field(default=None, max_length=40)


class SalesOrderDetailArguments(StrictArguments):
    order_id: int = Field(ge=1)


class SalesOrderItemsArguments(PageArguments):
    order_id: int = Field(ge=1)


class SalesOrderLogsArguments(PageArguments):
    order_id: int = Field(ge=1)


class ShipmentSearchArguments(PageArguments):
    order_id: int | None = Field(default=None, ge=1)
    status: Literal["SCANNING", "SHIPPED", "CANCELLED"] | None = None
    tracking_no: str | None = Field(default=None, min_length=1, max_length=64)
    platform_sync_status: Literal["PENDING", "SYNCED", "FAILED", "SKIPPED"] | None = None
    start_time: str | None = Field(default=None, max_length=40)
    end_time: str | None = Field(default=None, max_length=40)


class OrderShipmentsArguments(ShipmentSearchArguments):
    order_id: int = Field(ge=1)


class ShipmentDetailArguments(StrictArguments):
    shipment_id: int = Field(ge=1)


class ShipmentItemsArguments(PageArguments):
    shipment_id: int = Field(ge=1)


class PlatformSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)


class PlatformDetailArguments(StrictArguments):
    platform_id: int = Field(ge=1)


class ShopSearchArguments(PageArguments):
    platform_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)
    is_locked: bool | None = None
    auto_sync_order: bool | None = None
    auto_sync_stock: bool | None = None


class ShopDetailArguments(StrictArguments):
    shop_id: int = Field(ge=1)


class StockCheckSearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class StockCheckDetailArguments(StrictArguments):
    check_id: int = Field(ge=1)


class StockCheckItemsArguments(PageArguments):
    check_id: int = Field(ge=1)


class PendingPutawayArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)


class PutawaySearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    location_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    stock_in_id: int | None = Field(default=None, ge=1)


class StockInSearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    purchase_order_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class StockInDetailArguments(StrictArguments):
    stock_in_id: int = Field(ge=1)


class StockInItemsArguments(PageArguments):
    stock_in_id: int = Field(ge=1)


class StockOutSearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    purchase_return_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    off_shelf_status: Literal["NONE", "DONE"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class StockOutDetailArguments(StrictArguments):
    stock_out_id: int = Field(ge=1)


class StockOutItemsArguments(PageArguments):
    stock_out_id: int = Field(ge=1)


class StockSummaryArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)
    below_warning: bool | None = None


class StockLocationsArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    location_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)


class StockWarningsArguments(PageArguments):
    only_below: bool = False


class StockCostsArguments(PageArguments):
    pass


class LocationAdjustSearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class LocationAdjustDetailArguments(StrictArguments):
    adjust_id: int = Field(ge=1)


class LocationAdjustItemsArguments(PageArguments):
    adjust_id: int = Field(ge=1)


class ShopStockSearchArguments(PageArguments):
    shop_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    is_enabled: bool | None = None
    keyword: str | None = Field(default=None, max_length=64)


class ShopStockDetailArguments(StrictArguments):
    shop_stock_id: int = Field(ge=1)


class StockSettlementSearchArguments(PageArguments):
    settlement_date: str | None = Field(default=None, min_length=10, max_length=10)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)


class StockCostRewriteLogArguments(PageArguments):
    batch_no: str | None = Field(default=None, max_length=32)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    target_type: Literal["STOCK_OUT_ITEM", "STOCK_FLOW"] | None = None
    start_time: str | None = Field(default=None, max_length=64)
    end_time: str | None = Field(default=None, max_length=64)
    keyword: str | None = Field(default=None, max_length=64)


class TransferSearchArguments(PageArguments):
    from_warehouse_id: int | None = Field(default=None, ge=1)
    to_warehouse_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED", "OUTBOUND", "COMPLETED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class TransferDetailArguments(StrictArguments):
    transfer_id: int = Field(ge=1)


class TransferItemsArguments(PageArguments):
    transfer_id: int = Field(ge=1)


class TransferChildSearchArguments(PageArguments):
    transfer_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)
    status: Literal["PENDING", "APPROVED", "REJECTED", "COMPLETED", "TERMINATED"] | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None


class TransferOutDetailArguments(StrictArguments):
    transfer_out_id: int = Field(ge=1)


class TransferOutItemsArguments(PageArguments):
    transfer_out_id: int = Field(ge=1)


class TransferInDetailArguments(StrictArguments):
    transfer_in_id: int = Field(ge=1)


class TransferInItemsArguments(PageArguments):
    transfer_in_id: int = Field(ge=1)


PurchaseReturnStatus = Literal[
    "PENDING", "FINANCE_APPROVED", "APPROVED", "REJECTED", "TERMINATED",
]


class PurchaseReturnSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    supplier_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)
    status: PurchaseReturnStatus | None = None
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None


class PurchaseReturnDetailArguments(StrictArguments):
    return_id: int = Field(ge=1)


class PurchaseReturnItemArguments(PageArguments):
    return_id: int = Field(ge=1)


class ProductSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    category_id: int | None = Field(default=None, ge=1)
    brand_id: int | None = Field(default=None, ge=1)
    listed: bool | None = None
    is_disabled: bool | None = None


class ProductDetailArguments(StrictArguments):
    product_id: int = Field(ge=1)


class ProductReferenceItemsArguments(PageArguments):
    unit_group_id: int = Field(ge=1)


class ProductSpecValuesArguments(PageArguments):
    spec_type_id: int = Field(ge=1)


class ProductSkuSearchArguments(PageArguments):
    product_id: int = Field(ge=1)


class ComboSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    is_enabled: bool | None = None


class ComboDetailArguments(StrictArguments):
    combo_id: int = Field(ge=1)


class ComboItemSearchArguments(PageArguments):
    combo_id: int = Field(ge=1)


class ComboExpandArguments(PageArguments):
    combo_id: int = Field(ge=1)
    qty: float = Field(default=1.0, gt=0, le=999999999999999.999)


class CompanyDetailArguments(StrictArguments):
    pass


class DepartmentDetailArguments(StrictArguments):
    department_id: int = Field(ge=1)


class PositionDetailArguments(StrictArguments):
    position_id: int = Field(ge=1)


class StaffSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    department_id: int | None = Field(default=None, ge=1)
    position_id: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class StaffDetailArguments(StrictArguments):
    staff_id: int = Field(ge=1)


class AssistantCapabilitiesArguments(StrictArguments):
    pass


class SupplierSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class SupplierDetailArguments(StrictArguments):
    supplier_id: int = Field(ge=1)


class SupplierAccountArguments(PageArguments):
    supplier_id: int = Field(ge=1)


class CustomerAccountArguments(PageArguments):
    customer_id: int = Field(ge=1)


class SupplierPriceArguments(PageArguments):
    supplier_id: int = Field(ge=1)
    product_id: int | None = Field(default=None, ge=1)


class CustomerSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class CustomerDetailArguments(StrictArguments):
    customer_id: int = Field(ge=1)


AfterSaleStatus = Literal["PENDING", "SUBMITTED", "CONFIRMED", "CANCELLED"]


class AfterSaleReturnSearchArguments(PageArguments):
    order_id: int | None = Field(default=None, ge=1)
    shop_id: int | None = Field(default=None, ge=1)
    warehouse_id: int | None = Field(default=None, ge=1)
    status: AfterSaleStatus | None = None
    keyword: str | None = Field(default=None, max_length=64)
    start_time: str | None = Field(default=None, max_length=40)
    end_time: str | None = Field(default=None, max_length=40)


class AfterSaleExchangeSearchArguments(PageArguments):
    order_id: int | None = Field(default=None, ge=1)
    shop_id: int | None = Field(default=None, ge=1)
    status: AfterSaleStatus | None = None
    keyword: str | None = Field(default=None, max_length=64)


class AfterSaleDetailArguments(StrictArguments):
    return_id: int = Field(ge=1)


class AfterSaleExchangeDetailArguments(StrictArguments):
    exchange_id: int = Field(ge=1)


class AfterSaleReturnItemsArguments(PageArguments):
    return_id: int = Field(ge=1)


class AfterSaleExchangeItemsArguments(PageArguments):
    exchange_id: int = Field(ge=1)


class ReturnRegisterSearchArguments(PageArguments):
    bound: bool | None = None
    return_order_id: int | None = Field(default=None, ge=1)
    exchange_order_id: int | None = Field(default=None, ge=1)


class WarehouseSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    warehouse_type: Literal["FINISHED", "DEFECTIVE"] | None = None
    is_enabled: bool | None = None


class WarehouseDetailArguments(StrictArguments):
    warehouse_id: int = Field(ge=1)


class WarehouseZoneSearchArguments(PageArguments):
    warehouse_id: int = Field(ge=1)
    keyword: str | None = Field(default=None, max_length=64)


class WarehouseZoneDetailArguments(StrictArguments):
    zone_id: int = Field(ge=1)


class LocationSearchArguments(PageArguments):
    warehouse_id: int = Field(ge=1)
    zone_id: int | None = Field(default=None, ge=1)
    keyword: str | None = Field(default=None, max_length=64)
    is_enabled: bool | None = None


class LocationDetailArguments(StrictArguments):
    location_id: int = Field(ge=1)


class LocationCapacityArguments(StrictArguments):
    location_id: int = Field(ge=1)


class WaveStrategySearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    is_enabled: bool | None = None


class WaveStrategyDetailArguments(StrictArguments):
    strategy_id: int = Field(ge=1)


class WavePendingCountArguments(StrictArguments):
    strategy_id: int = Field(ge=1)
    order_ids: list[int] | None = Field(default=None, max_length=100)


class WaveSearchArguments(PageArguments):
    strategy_id: int | None = Field(default=None, ge=1)
    express_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)
    start_time: str | None = Field(default=None, max_length=64)
    end_time: str | None = Field(default=None, max_length=64)


class WaveDetailArguments(StrictArguments):
    wave_id: int = Field(ge=1)


class WaveOrdersArguments(PageArguments):
    wave_id: int = Field(ge=1)


class WaveLoadNumbersArguments(PageArguments):
    wave_id: int = Field(ge=1)


class StockFlowSearchArguments(PageArguments):
    warehouse_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)
    sku_id: int | None = Field(default=None, ge=1)
    location_id: int | None = Field(default=None, ge=1)
    direction: Literal["IN", "OUT"] | None = None
    biz_type: str | None = Field(default=None, max_length=32)
    source_no: str | None = Field(default=None, max_length=64)
    order_no: str | None = Field(default=None, max_length=64)
    start_time: str | None = Field(default=None, max_length=40, description="ISO 8601日期时间；含时区时按Asia/Shanghai归一")
    end_time: str | None = Field(default=None, max_length=40, description="ISO 8601日期时间；含时区时按Asia/Shanghai归一")
    keyword: str | None = Field(default=None, max_length=64)


Status = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED", "TERMINATED"]
AuditStatus = Literal["PENDING", "APPROVED", "REJECTED"]


class PurchasePrepaySearchArguments(PageArguments):
    supplier_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, ge=1)
    status: Status | None = None
    audit_status: AuditStatus | None = None
    keyword: str | None = Field(default=None, max_length=64)


class PurchaseInvoiceSearchArguments(PageArguments):
    supplier_id: int | None = Field(default=None, ge=1)
    invoice_type: str | None = Field(default=None, max_length=32)
    status: Status | None = None
    audit_status: AuditStatus | None = None
    write_off_status: str | None = Field(default=None, max_length=16)
    business_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)


class PurchaseInvoiceDetailArguments(StrictArguments):
    invoice_id: int = Field(ge=1)


class InvoiceSearchArguments(PageArguments):
    invoice_type: Literal[
        "NORMAL", "SPECIAL", "ELECTRONIC", "RED_SPECIAL", "RED_NORMAL",
    ] | None = None
    status: Literal["PENDING", "ISSUED", "RECEIVED", "CANCELLED"] | None = None
    is_received: bool | None = None
    keyword: str | None = Field(default=None, max_length=64)


class InvoiceDetailArguments(StrictArguments):
    invoice_id: int = Field(ge=1)


class PaymentDetailArguments(StrictArguments):
    payment_id: int = Field(ge=1)


class ReceiptDetailArguments(StrictArguments):
    receipt_id: int = Field(ge=1)


class SupplierAccountBalanceSearchArguments(PageArguments):
    supplier_id: int | None = Field(default=None, ge=1)
    sync_status: Literal["NOT_SYNCED", "SYNCED", "FAILED"] | None = None
    keyword: str | None = Field(default=None, max_length=64)


class SupplierAccountBalanceDetailArguments(StrictArguments):
    account_id: int = Field(ge=1)


class LogisticsSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    is_enabled: bool | None = None


class LogisticsDetailArguments(StrictArguments):
    logistics_id: int = Field(ge=1)


class FreightTemplateSearchArguments(PageArguments):
    logistics_id: int | None = Field(default=None, ge=1)


class FreightTemplateDetailArguments(StrictArguments):
    template_id: int = Field(ge=1)


class FreightCalculationArguments(StrictArguments):
    template_id: int = Field(ge=1)
    count: int | None = Field(default=None, ge=1, le=10_000_000)
    weight: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)


class AreaRuleMatchArguments(StrictArguments):
    logistics_id: int = Field(ge=1)
    province: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=32)
    weight: float | None = Field(default=None, ge=0)


class AreaRuleSearchArguments(PageArguments):
    logistics_id: int | None = Field(default=None, ge=1)


class AreaRuleDetailArguments(StrictArguments):
    rule_id: int = Field(ge=1)


class PrintTemplateSearchArguments(PageArguments):
    logistics_id: int | None = Field(default=None, ge=1)
    template_type: str | None = Field(default=None, max_length=32)


class PrintTemplateDetailArguments(StrictArguments):
    template_id: int = Field(ge=1)


class PrintBatchArguments(StrictArguments):
    batch_no: str = Field(min_length=1, max_length=64)


SubjectCategory = Literal["ASSET", "LIABILITY", "EQUITY", "COST", "PROFIT_LOSS"]
BalanceDirection = Literal["DEBIT", "CREDIT"]


class AccountSubjectSearchArguments(PageArguments):
    keyword: str | None = Field(default=None, max_length=64)
    category: SubjectCategory | None = None
    balance_direction: BalanceDirection | None = None
    is_enabled: bool | None = None
    is_auxiliary: bool | None = None
    is_qty_amount: bool | None = None
    project_category_id: int | None = Field(default=None, ge=1)


class AccountSubjectDetailArguments(StrictArguments):
    subject_id: int = Field(ge=1)


class ProjectCategorySearchArguments(PageArguments):
    pass


class ProjectCategoryDetailArguments(StrictArguments):
    category_id: int = Field(ge=1)


class ExpenseSearchArguments(PageArguments):
    customer_id: int | None = Field(default=None, ge=1)
    status: Status | None = None
    expense_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)


class ExpenseDetailArguments(StrictArguments):
    expense_id: int = Field(ge=1)


class IncomeSearchArguments(PageArguments):
    supplier_id: int | None = Field(default=None, ge=1)
    status: Status | None = None
    income_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)


class IncomeDetailArguments(StrictArguments):
    income_id: int = Field(ge=1)


class PaymentSearchArguments(PageArguments):
    supplier_id: int | None = Field(default=None, ge=1)
    status: Status | None = None
    audit_status: AuditStatus | None = None
    payment_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)


class ReceiptSearchArguments(PageArguments):
    customer_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, ge=1)
    status: Status | None = None
    audit_status: AuditStatus | None = None
    sale_type: str | None = Field(default=None, max_length=32)
    keyword: str | None = Field(default=None, max_length=64)


class SettlementSearchArguments(PageArguments):
    shop_id: int | None = Field(default=None, ge=1)
    status: Literal["IMPORTED", "CHECKED", "CONFIRMED", "CANCELLED"] | None = None
    settlement_date_from: str | None = Field(default=None, max_length=10)
    settlement_date_to: str | None = Field(default=None, max_length=10)
    keyword: str | None = Field(default=None, max_length=64)


class SettlementDetailArguments(StrictArguments):
    settlement_id: int = Field(ge=1)


class VoucherSearchArguments(PageArguments):
    status: Literal["DRAFT", "APPROVED", "INVALID"] | None = None
    keyword: str | None = Field(default=None, max_length=64)
    business_date_from: str | None = Field(default=None, max_length=64)
    business_date_to: str | None = Field(default=None, max_length=64)


class VoucherDetailArguments(StrictArguments):
    voucher_id: int = Field(ge=1)


class VoucherItemsArguments(PageArguments):
    voucher_id: int = Field(ge=1)


class PurchaseInvoiceCostDetailArguments(PageArguments):
    invoice_id: int = Field(ge=1)


class IpRuleDetailArguments(StrictArguments):
    rule_id: int = Field(ge=1)


class DashboardOverviewArguments(StrictArguments):
    trend_period: Literal["day", "week", "month"] = "day"


class AssistantUsageArguments(PageArguments):
    days: int = Field(default=30, ge=1, le=90)


class MyNotificationsArguments(StrictArguments):
    cursor: str | None = Field(default=None, max_length=256)
    category: Literal["all", "approval", "purchase"] = "all"
    read_state: Literal["UNREAD", "READ"] | None = None
    page_size: int = Field(default=20, ge=1, le=100)


class MyNotificationDetailArguments(StrictArguments):
    inbox_id: int = Field(ge=1)


class EmptyQueryArguments(StrictArguments):
    pass


ApprovalDocType = Literal[
    "purchase_plan", "purchase_order", "purchase_prepay", "purchase_return",
    "purchase_invoice", "stock_in", "stock_out", "transfer", "transfer_out",
    "transfer_in", "stock_check", "location_adjust", "order", "expense",
]


class ApprovalFlowArguments(PageArguments):
    doc_type: ApprovalDocType | None = None


class ApprovalRecordArguments(PageArguments):
    doc_type: ApprovalDocType
    doc_id: int = Field(ge=1)


ApprovalManageDocType = Literal[
    "purchase_plan", "purchase_order", "purchase_prepay", "purchase_return",
    "purchase_invoice", "stock_in", "stock_out", "transfer", "transfer_out",
    "transfer_in", "stock_check", "location_adjust", "order",
]


class MyApprovalSearchArguments(PageArguments):
    scope: Literal["pending", "submitted", "processed"] = "pending"
    doc_type: ApprovalManageDocType | None = None
    keyword: str | None = Field(default=None, max_length=64)


class MyApprovalDetailArguments(StrictArguments):
    doc_type: ApprovalManageDocType
    doc_id: int = Field(ge=1)


class ApprovalFlowConfigArguments(PageArguments):
    pass


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    request_id: str
    queried_at: str
    business_timezone: Literal["Asia/Shanghai"]
    effective_filters: dict[str, Any]
    scope_summary: dict[str, str]
    page: dict[str, Any] | None = None
    data: Any
    warnings: list[str]


@dataclass(frozen=True)
class ToolOutput:
    data: Any
    effective_filters: dict[str, Any]
    rows: int
    page: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    description: str
    arguments: type[BaseModel]
    handler: Any


def _page(result: dict) -> dict[str, Any]:
    page, page_size, total = result["page"], result["page_size"], result["total"]
    return {"page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def _search_purchase_plans(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_plans")
    result = purchase_plan_service.list_plans(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.purchase_plan(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _preview_purchase_needs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_needs")
    filters = args.model_dump(exclude_none=True)
    result = purchase_plan_service.preview_purchase_needs_rows(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.purchase_need(row, access) for row in result["items"]]
    effective_filters = {
        **filters,
        "sales_window_days": result["sales_window_days"],
        "candidate_products_total": result["candidate_products_total"],
    }
    return ToolOutput(data, effective_filters, len(data), _page(result))


def _get_purchase_plan(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "purchase_plans")
    company_id = access.policy.company_id
    plan = purchase_plan_service.get_plan_or_404(
        db, args.plan_id, company_id=company_id,
    )
    data = query_projection.purchase_plan(
        purchase_plan_service.plan_dict(
            db, plan, with_items=False, with_approval=False,
            company_id=company_id,
        ),
        access,
    )
    return ToolOutput(data, {"plan_id": args.plan_id}, 1)


def _list_purchase_plan_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_plans")
    company_id = access.policy.company_id
    # 子表没有制单人字段，必须先按父单据的owner/审批参与范围校验，再读取和计数。
    plan = purchase_plan_service.get_plan_or_404(
        db, args.plan_id, company_id=company_id,
    )
    filters = args.model_dump(exclude_none=True)
    query = select(PurchasePlanItem).where(
        PurchasePlanItem.plan_id == plan.id,
        PurchasePlanItem.company_id == company_id,
    )
    total = db.scalar(
        select(func.count(PurchasePlanItem.id)).where(
            PurchasePlanItem.plan_id == plan.id,
            PurchasePlanItem.company_id == company_id,
        )
    ) or 0
    rows = db.scalars(
        query.order_by(PurchasePlanItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [
        query_projection.purchase_plan_item(
            purchase_plan_service.plan_item_dict(db, item), access,
        )
        for item in rows
    ]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_purchase_plan_creation_request(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from uuid import UUID
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "purchase_creation_requests")
    try:
        request_id = str(UUID(args.request_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "请求标识必须是UUID") from exc
    row = db.execute(
        select(PurchasePlanCreationRequest.status, PurchasePlanCreationRequest.plan_id)
        .where(
            PurchasePlanCreationRequest.company_id == access.staff.company_id,
            PurchasePlanCreationRequest.staff_id == access.staff.id,
            PurchasePlanCreationRequest.request_id == request_id,
        )
    ).one_or_none()
    if row is None:
        data = {"status": "NOT_FOUND", "plan_id": None}
    else:
        if row.status not in {"PENDING", "SUCCEEDED", "FAILED"}:
            raise BizException(ErrorCode.INTERNAL_ERROR, "请求状态无效")
        data = {
            "status": row.status,
            "plan_id": row.plan_id if row.status == "SUCCEEDED" else None,
        }
    return ToolOutput(data, {}, 1)


def _search_purchase_orders(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_orders")
    result = purchase_order_service.list_orders(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.purchase_order(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_purchase_order(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_orders")
    company_id = access.policy.company_id
    order = purchase_order_service.get_order_or_404(
        db, args.order_id, company_id=company_id,
    )
    detail = purchase_order_service.order_dict(
        db, order, with_items=False, with_approval=False,
        company_id=company_id,
    )
    data = query_projection.purchase_order(detail, access)
    return ToolOutput(data, {"order_id": args.order_id}, 1)


def _list_purchase_order_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_orders")
    company_id = access.policy.company_id
    order = purchase_order_service.get_order_or_404(
        db, args.order_id, company_id=company_id,
    )
    conditions = [
        PurchaseOrderItem.order_id == order.id,
        PurchaseOrderItem.company_id == company_id,
    ]
    total = int(db.scalar(
        select(func.count(PurchaseOrderItem.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        select(PurchaseOrderItem, Product.code, Product.name, ProductSku.spec_name,
               ProductSku.spec_code)
        .join(Product, and_(
            Product.id == PurchaseOrderItem.product_id,
            Product.company_id == company_id,
        ))
        .outerjoin(ProductSku, and_(
            ProductSku.id == PurchaseOrderItem.sku_id,
            ProductSku.product_id == PurchaseOrderItem.product_id,
            ProductSku.company_id == company_id,
        ))
        .where(*conditions).order_by(PurchaseOrderItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    items = [{
        "id": item.id, "product_id": item.product_id,
        "product_name": product_name, "product_code": product_code,
        "sku_id": item.sku_id, "spec_name": spec_name,
        "spec_code": spec_code, "qty": item.qty,
        "arrived_qty": item.arrived_qty,
        "difference_qty": item.qty - item.arrived_qty,
        # 与 REST 订单明细保持一致；历史行的冗余在途列可能未重算。
        "in_transit_qty": max(item.qty - item.arrived_qty, Decimal("0")),
        "purchase_price": item.purchase_price, "amount": item.amount,
    } for item, product_code, product_name, spec_name, spec_code in rows]
    data = query_projection.purchase_order({"id": order.id, "items": items}, access)["items"]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _purchase_order_report(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "purchase_report")
    start_time = _parse_report_datetime(args.start_time)
    end_time = _parse_report_datetime(args.end_time)
    if start_time and end_time and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    conditions = list(scope_conditions(db, PurchaseOrder.created_by))
    if start_time:
        conditions.append(PurchaseOrder.created_at >= start_time)
    if end_time:
        conditions.append(PurchaseOrder.created_at <= end_time)
    if args.supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == args.supplier_id)

    grouped = (
        select(
            PurchaseOrder.supplier_id.label("supplier_id"),
            Supplier.name.label("supplier_name"),
            func.count(PurchaseOrder.id).label("order_count"),
            func.coalesce(func.sum(PurchaseOrder.total_qty), 0).label("total_qty"),
            func.coalesce(func.sum(PurchaseOrder.total_amount), 0).label("total_amount"),
            func.coalesce(func.sum(PurchaseOrder.arrived_qty), 0).label("arrived_qty"),
            func.coalesce(func.sum(
                PurchaseOrder.total_qty - PurchaseOrder.arrived_qty
            ), 0).label("in_transit_qty"),
        )
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(*conditions)
        .group_by(PurchaseOrder.supplier_id, Supplier.name)
        .subquery("mcp_purchase_order_report")
    )
    total = int(db.scalar(select(func.count(grouped.c.supplier_id))) or 0)
    summary_row = db.execute(select(
        func.coalesce(func.sum(grouped.c.order_count), 0),
        func.coalesce(func.sum(grouped.c.total_qty), 0),
        func.coalesce(func.sum(grouped.c.total_amount), 0),
        func.coalesce(func.sum(grouped.c.arrived_qty), 0),
        func.coalesce(func.sum(grouped.c.in_transit_qty), 0),
    )).one()
    rows = db.execute(
        select(grouped).order_by(grouped.c.supplier_id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).mappings().all()
    can_see_amount = bool(access.roles & {"ADMIN", "PURCHASER", "FINANCE"}) and (
        "*" in access.permissions or "module:purchase" in access.permissions
    )
    items = []
    for row in rows:
        item = {
            "supplier_id": row["supplier_id"],
            "supplier_name": row["supplier_name"],
            "order_count": int(row["order_count"] or 0),
            "total_qty": Decimal(row["total_qty"] or 0).quantize(Decimal("0.001")),
            "arrived_qty": Decimal(row["arrived_qty"] or 0).quantize(Decimal("0.001")),
            "in_transit_qty": Decimal(row["in_transit_qty"] or 0).quantize(Decimal("0.001")),
        }
        if can_see_amount:
            item["total_amount"] = round_money(row["total_amount"] or 0)
        items.append(item)
    summary = {
        "supplier_count": total,
        "order_count": int(summary_row[0] or 0),
        "total_qty": Decimal(summary_row[1] or 0).quantize(Decimal("0.001")),
        "arrived_qty": Decimal(summary_row[3] or 0).quantize(Decimal("0.001")),
        "in_transit_qty": Decimal(summary_row[4] or 0).quantize(Decimal("0.001")),
    }
    if can_see_amount:
        summary["total_amount"] = round_money(summary_row[2] or 0)
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput({"items": items, "summary": summary}, filters, len(items), page)


def _purchase_inbound_report(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "purchase_report")
    start_time = _parse_report_datetime(args.start_time)
    end_time = _parse_report_datetime(args.end_time)
    if start_time and end_time and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    conditions = [
        *scope_conditions(db, StockIn.created_by),
        *scope_conditions(db, PurchaseOrder.created_by),
        StockIn.stock_in_type == "PURCHASE",
        StockIn.audit_status == "APPROVED",
    ]
    if start_time:
        conditions.append(StockIn.created_at >= start_time)
    if end_time:
        conditions.append(StockIn.created_at <= end_time)
    if args.supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == args.supplier_id)
    if args.purchaser_id is not None:
        conditions.append(PurchaseOrder.purchaser_id == args.purchaser_id)
    if args.product_id is not None:
        conditions.append(StockInItem.product_id == args.product_id)
    amount_expression = func.coalesce(
        StockInItem.amount,
        StockInItem.qty * func.coalesce(StockInItem.cost_price, 0),
    )
    grouped = (
        select(
            PurchaseOrder.supplier_id.label("supplier_id"),
            Supplier.name.label("supplier_name"),
            PurchaseOrder.purchaser_id.label("purchaser_id"),
            Staff.name.label("purchaser_name"),
            StockInItem.product_id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            StockInItem.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            func.count(func.distinct(StockIn.id)).label("stock_in_count"),
            func.coalesce(func.sum(StockInItem.qty), 0).label("qty"),
            func.coalesce(func.sum(amount_expression), 0).label("amount"),
        )
        .select_from(StockInItem)
        .join(StockIn, StockIn.id == StockInItem.stock_in_id)
        .join(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .outerjoin(Staff, Staff.id == PurchaseOrder.purchaser_id)
        .join(Product, Product.id == StockInItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == StockInItem.sku_id,
            ProductSku.product_id == StockInItem.product_id,
        ))
        .where(*conditions)
        .group_by(
            PurchaseOrder.supplier_id, Supplier.name,
            PurchaseOrder.purchaser_id, Staff.name,
            StockInItem.product_id, Product.code, Product.name,
            StockInItem.sku_id, ProductSku.spec_code, ProductSku.spec_name,
        )
        .subquery("mcp_purchase_inbound_report")
    )
    total = int(db.scalar(select(func.count()).select_from(grouped)) or 0)
    summary_values = db.execute(select(
        func.coalesce(func.sum(grouped.c.qty), 0),
        func.coalesce(func.sum(grouped.c.amount), 0),
    )).one()
    stock_in_count = int(db.scalar(
        select(func.count(func.distinct(StockIn.id)))
        .select_from(StockInItem)
        .join(StockIn, StockIn.id == StockInItem.stock_in_id)
        .join(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
        .where(*conditions)
    ) or 0)
    rows = db.execute(
        select(grouped).order_by(
            grouped.c.supplier_id, grouped.c.purchaser_id,
            grouped.c.product_id, grouped.c.sku_id,
        ).offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).mappings().all()
    can_see_amount = "*" in access.permissions or "module:finance" in access.permissions
    items = []
    for row in rows:
        item = {
            "supplier_id": row["supplier_id"],
            "supplier_name": row["supplier_name"],
            "purchaser_id": row["purchaser_id"],
            "purchaser_name": row["purchaser_name"],
            "product_id": row["product_id"],
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "sku_id": row["sku_id"],
            "spec_code": row["spec_code"],
            "spec_name": row["spec_name"],
            "stock_in_count": int(row["stock_in_count"] or 0),
            "qty": Decimal(row["qty"] or 0).quantize(Decimal("0.001")),
        }
        if can_see_amount:
            item["amount"] = round_money(row["amount"] or 0)
        items.append(item)
    summary = {
        "stock_in_count": stock_in_count,
        "qty": Decimal(summary_values[0] or 0).quantize(Decimal("0.001")),
    }
    if can_see_amount:
        summary["amount"] = round_money(summary_values[1] or 0)
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput({"items": items, "summary": summary}, filters, len(items), page)


def _purchase_invoice_report(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "purchase_invoice_report")
    start_time = _parse_report_datetime(args.start_time)
    end_time = _parse_report_datetime(args.end_time)
    if start_time and end_time and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    conditions = list(scope_conditions(db, PurchaseInvoice.created_by))
    if start_time:
        conditions.append(PurchaseInvoice.created_at >= start_time)
    if end_time:
        conditions.append(PurchaseInvoice.created_at <= end_time)
    if args.supplier_id is not None:
        conditions.append(PurchaseInvoice.supplier_id == args.supplier_id)
    if args.write_off_status is not None:
        conditions.append(PurchaseInvoice.write_off_status == args.write_off_status)
    net_amount = PurchaseInvoice.amount - func.coalesce(PurchaseInvoice.tax_amount, 0)
    grouped = (
        select(
            PurchaseInvoice.supplier_id.label("supplier_id"),
            Supplier.name.label("supplier_name"),
            PurchaseInvoice.write_off_status.label("write_off_status"),
            func.count(PurchaseInvoice.id).label("invoice_count"),
            func.coalesce(func.sum(PurchaseInvoice.amount), 0).label("amount"),
            func.coalesce(func.sum(PurchaseInvoice.tax_amount), 0).label("tax_amount"),
            func.coalesce(func.sum(net_amount), 0).label("net_amount"),
            func.coalesce(func.sum(PurchaseInvoice.write_off_amount), 0).label("write_off_amount"),
        )
        .join(Supplier, Supplier.id == PurchaseInvoice.supplier_id)
        .where(*conditions)
        .group_by(
            PurchaseInvoice.supplier_id, Supplier.name,
            PurchaseInvoice.write_off_status,
        )
        .subquery("mcp_purchase_invoice_report")
    )
    total = int(db.scalar(select(func.count(grouped.c.supplier_id))) or 0)
    summary_row = db.execute(select(
        func.coalesce(func.sum(grouped.c.invoice_count), 0),
        func.coalesce(func.sum(grouped.c.amount), 0),
        func.coalesce(func.sum(grouped.c.tax_amount), 0),
        func.coalesce(func.sum(grouped.c.net_amount), 0),
        func.coalesce(func.sum(grouped.c.write_off_amount), 0),
    )).one()
    rows = db.execute(
        select(grouped).order_by(
            grouped.c.supplier_id, grouped.c.write_off_status,
        ).offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).mappings().all()
    can_see_amount = "*" in access.permissions or "module:finance" in access.permissions
    items = []
    for row in rows:
        item = {
            "supplier_id": row["supplier_id"],
            "supplier_name": row["supplier_name"],
            "write_off_status": row["write_off_status"],
            "invoice_count": int(row["invoice_count"] or 0),
        }
        if can_see_amount:
            item.update({
                key: round_money(row[key] or 0)
                for key in ("amount", "tax_amount", "net_amount", "write_off_amount")
            })
        items.append(item)
    summary = {"invoice_count": int(summary_row[0] or 0)}
    if can_see_amount:
        summary.update({
            key: round_money(summary_row[index] or 0)
            for index, key in enumerate(
                ("amount", "tax_amount", "net_amount", "write_off_amount"),
                start=1,
            )
        })
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput({"items": items, "summary": summary}, filters, len(items), page)


def _inventory_in_transit_report(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "inventory_report")
    stock_conditions = [
        StockLocation.status == "NORMAL",
        Warehouse.is_virtual.is_(False),
        Warehouse.is_enabled.is_(True),
    ]
    if args.warehouse_id is not None:
        stock_conditions.append(Location.warehouse_id == args.warehouse_id)
    if args.product_id is not None:
        stock_conditions.append(ProductSku.product_id == args.product_id)
    keyword = (args.keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        stock_conditions.append(or_(
            Product.code.like(like), Product.name.like(like),
            ProductSku.spec_code.like(like), ProductSku.spec_name.like(like),
        ))
    stock = (
        select(
            ProductSku.product_id.label("product_id"),
            ProductSku.id.label("sku_id"),
            func.coalesce(func.sum(StockLocation.qty), 0).label("qty"),
        )
        .select_from(StockLocation)
        .join(Location, Location.id == StockLocation.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
        .join(Product, Product.id == ProductSku.product_id)
        .where(*stock_conditions)
        .group_by(ProductSku.product_id, ProductSku.id)
        .subquery("mcp_stock_by_sku")
    )

    transit_conditions = [
        *scope_conditions(db, PurchaseOrder.created_by),
        PurchaseOrder.audit_status == "APPROVED",
        PurchaseOrder.status.in_(("APPROVED", "RECEIVING")),
        PurchaseOrderItem.qty > PurchaseOrderItem.arrived_qty,
    ]
    if args.warehouse_id is not None:
        transit_conditions.append(PurchaseOrder.warehouse_id == args.warehouse_id)
    if args.supplier_id is not None:
        transit_conditions.append(PurchaseOrder.supplier_id == args.supplier_id)
    if args.product_id is not None:
        transit_conditions.append(PurchaseOrderItem.product_id == args.product_id)
    if keyword:
        like = f"%{keyword}%"
        transit_conditions.append(or_(
            Product.code.like(like), Product.name.like(like),
            ProductSku.spec_code.like(like), ProductSku.spec_name.like(like),
        ))
    transit = (
        select(
            PurchaseOrderItem.product_id.label("product_id"),
            PurchaseOrderItem.sku_id.label("sku_id"),
            func.coalesce(func.sum(
                PurchaseOrderItem.qty - PurchaseOrderItem.arrived_qty
            ), 0).label("qty"),
        )
        .select_from(PurchaseOrderItem)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .join(Product, Product.id == PurchaseOrderItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == PurchaseOrderItem.sku_id,
            ProductSku.product_id == PurchaseOrderItem.product_id,
        ))
        .where(*transit_conditions)
        .group_by(PurchaseOrderItem.product_id, PurchaseOrderItem.sku_id)
        .subquery("mcp_purchase_transit_by_sku")
    )
    all_keys = union_all(
        select(stock.c.product_id, stock.c.sku_id),
        select(transit.c.product_id, transit.c.sku_id),
    ).subquery("mcp_stock_transit_keys")
    keys = select(
        all_keys.c.product_id, all_keys.c.sku_id,
    ).group_by(all_keys.c.product_id, all_keys.c.sku_id).subquery("mcp_unique_stock_transit_keys")
    stock_join = and_(
        stock.c.product_id == keys.c.product_id,
        or_(
            stock.c.sku_id == keys.c.sku_id,
            and_(stock.c.sku_id.is_(None), keys.c.sku_id.is_(None)),
        ),
    )
    transit_join = and_(
        transit.c.product_id == keys.c.product_id,
        or_(
            transit.c.sku_id == keys.c.sku_id,
            and_(transit.c.sku_id.is_(None), keys.c.sku_id.is_(None)),
        ),
    )
    report_rows = (
        select(
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            keys.c.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            func.coalesce(stock.c.qty, 0).label("current_qty"),
            func.coalesce(transit.c.qty, 0).label("in_transit_qty"),
            (func.coalesce(stock.c.qty, 0) + func.coalesce(transit.c.qty, 0)).label("total_qty"),
        )
        .select_from(keys)
        .join(Product, Product.id == keys.c.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == keys.c.sku_id,
            ProductSku.product_id == keys.c.product_id,
        ))
        .outerjoin(stock, stock_join)
        .outerjoin(transit, transit_join)
        .subquery("mcp_inventory_transit_report")
    )
    total = int(db.scalar(select(func.count()).select_from(report_rows)) or 0)
    summary_row = db.execute(select(
        func.count(report_rows.c.product_id),
        func.coalesce(func.sum(report_rows.c.current_qty), 0),
        func.coalesce(func.sum(report_rows.c.in_transit_qty), 0),
        func.coalesce(func.sum(report_rows.c.total_qty), 0),
    )).one()
    rows = db.execute(
        select(report_rows).order_by(
            report_rows.c.product_id, report_rows.c.sku_id,
        ).offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).mappings().all()
    items = [{
        "product_id": row["product_id"],
        "product_code": row["product_code"],
        "product_name": row["product_name"],
        "sku_id": row["sku_id"],
        "spec_code": row["spec_code"],
        "spec_name": row["spec_name"],
        "current_qty": Decimal(row["current_qty"] or 0).quantize(Decimal("0.001")),
        "in_transit_qty": Decimal(row["in_transit_qty"] or 0).quantize(Decimal("0.001")),
        "total_qty": Decimal(row["total_qty"] or 0).quantize(Decimal("0.001")),
    } for row in rows]
    summary = {
        "sku_count": int(summary_row[0] or 0),
        "current_qty": Decimal(summary_row[1] or 0).quantize(Decimal("0.001")),
        "in_transit_qty": Decimal(summary_row[2] or 0).quantize(Decimal("0.001")),
        "total_qty": Decimal(summary_row[3] or 0).quantize(Decimal("0.001")),
    }
    filters = args.model_dump(exclude_none=True)
    if keyword:
        filters["keyword"] = keyword
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput({"items": items, "summary": summary}, filters, len(items), page)


def _search_sales_orders(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    start_time = _parse_business_datetime(args.start_time)
    end_time = _parse_business_datetime(args.end_time)
    if start_time and end_time and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    conditions = [Order.company_id == company_id]
    if args.shop_id is not None:
        conditions.append(Order.shop_id == args.shop_id)
    if args.warehouse_id is not None:
        conditions.append(Order.warehouse_id == args.warehouse_id)
    if args.status:
        conditions.append(Order.status == args.status)
    if args.audit_status:
        conditions.append(Order.audit_status == args.audit_status)
    if args.pay_status:
        conditions.append(Order.pay_status == args.pay_status.strip().upper())
    if args.flag:
        conditions.append(Order.flags.like(f"%{args.flag}%"))
    if args.keyword and args.keyword.strip():
        conditions.append(Order.order_no.like(f"%{args.keyword.strip()}%"))
    if start_time:
        conditions.append(Order.order_time >= start_time)
    if end_time:
        conditions.append(Order.order_time <= end_time)

    total = int(db.scalar(select(func.count(Order.id)).where(*conditions)) or 0)
    quantity = (
        select(func.coalesce(func.sum(OrderItem.qty), 0))
        .where(
            OrderItem.order_id == Order.id,
            OrderItem.company_id == company_id,
        ).correlate(Order).scalar_subquery()
    )
    rows = db.execute(
        select(
            Order, Shop.name, Platform.name, Warehouse.name, Logistics.name, quantity,
        )
        .outerjoin(Shop, and_(Shop.id == Order.shop_id, Shop.company_id == company_id))
        .outerjoin(Platform, and_(Platform.id == Shop.platform_id, Platform.company_id == company_id))
        .outerjoin(Warehouse, and_(Warehouse.id == Order.warehouse_id, Warehouse.company_id == company_id))
        .outerjoin(Logistics, and_(Logistics.id == Order.logistics_id, Logistics.company_id == company_id))
        .where(*conditions).order_by(Order.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = []
    for order, shop_name, platform_name, warehouse_name, logistics_name, total_qty in rows:
        flags = [
            value.strip() for value in (order.flags or "").split(",")
            if value.strip() in order_service.ORDER_FLAGS
        ]
        data.append(query_projection.sales_order({
            "id": order.id, "order_no": order.order_no,
            "shop_id": order.shop_id, "shop_name": shop_name,
            "platform_name": platform_name,
            "warehouse_id": order.warehouse_id, "warehouse_name": warehouse_name,
            "logistics_id": order.logistics_id, "logistics_name": logistics_name,
            "status": order.status,
            "status_label": order_service.STATUS_LABELS.get(order.status, order.status),
            "audit_status": order.audit_status, "pay_status": order.pay_status,
            "flags": flags,
            "flags_label": [order_service.ORDER_FLAGS[code] for code in flags],
            "weight": order.weight, "total_qty": total_qty,
            "order_time": order.order_time, "pay_time": order.pay_time,
            "ship_time": order.ship_time,
            "order_amount": order.order_amount, "paid_amount": order.paid_amount,
            "freight": order.freight, "platform_discount": order.platform_discount,
            "seller_discount": order.seller_discount,
        }, access))
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_sales_order(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    order = order_service.get_order_or_404(db, args.order_id, company_id=company_id)
    data = order_service.order_dict(
        db, order, with_items=False, with_approval=False,
    )
    data["flags"] = [
        code for code in data.get("flags", []) if code in order_service.ORDER_FLAGS
    ]
    data["flags_label"] = [
        order_service.ORDER_FLAGS[code] for code in data["flags"]
    ]
    data["total_qty"] = db.scalar(
        select(func.coalesce(func.sum(OrderItem.qty), 0))
        .where(OrderItem.order_id == order.id, OrderItem.company_id == company_id)
    )
    projected = query_projection.sales_order(data, access)
    return ToolOutput(projected, {"order_id": args.order_id}, 1)


def _list_sales_order_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    order_service.get_order_or_404(db, args.order_id, company_id=company_id)
    conditions = [
        OrderItem.order_id == args.order_id,
        OrderItem.company_id == company_id,
        Product.company_id == company_id,
    ]
    total = int(db.scalar(
        select(func.count(OrderItem.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        select(OrderItem, Product.code, Product.name, ProductSku.spec_name)
        .join(Product, Product.id == OrderItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == OrderItem.sku_id,
            ProductSku.product_id == OrderItem.product_id,
            ProductSku.company_id == company_id,
        ))
        .where(*conditions).order_by(OrderItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.sales_order_item({
        "id": item.id, "order_id": item.order_id,
        "product_id": item.product_id, "product_code": product_code,
        "product_name": product_name, "sku_id": item.sku_id,
        "sku_name": item.sku_name or sku_name, "qty": item.qty,
        "is_gift": item.is_gift, "price": item.price, "amount": item.amount,
    }, access) for item, product_code, product_name, sku_name in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _list_sales_order_logs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    order_service.get_order_or_404(db, args.order_id, company_id=company_id)
    audit_events = select(
        AuditLog.id.label("event_id"),
        literal("AUDIT", type_=ApprovalRecord.doc_type.type).label("source"),
        AuditLog.action.label("action"),
        literal(None, type_=ApprovalRecord.step.type).label("step"),
        literal(None, type_=ApprovalRecord.status.type).label("status"),
        AuditLog.created_at.label("created_at"),
    ).where(
        AuditLog.target_type == order_service.DOC_TYPE,
        AuditLog.target_id == str(args.order_id),
        AuditLog.company_id == company_id,
    )
    approval_events = select(
        ApprovalRecord.id.label("event_id"),
        literal("APPROVAL", type_=ApprovalRecord.doc_type.type).label("source"),
        ApprovalRecord.action.label("action"),
        ApprovalRecord.step.label("step"),
        ApprovalRecord.status.label("status"),
        func.coalesce(
            ApprovalRecord.audit_time, ApprovalRecord.created_at,
        ).label("created_at"),
    ).where(
        ApprovalRecord.doc_type == order_service.DOC_TYPE,
        ApprovalRecord.doc_id == args.order_id,
        ApprovalRecord.company_id == company_id,
    )
    events = union_all(audit_events, approval_events).subquery()
    total = int(db.scalar(select(func.count()).select_from(events)) or 0)
    rows = db.execute(
        select(events).order_by(
            events.c.created_at.desc(), events.c.source.asc(),
            events.c.event_id.desc(),
        ).offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.sales_order_log(dict(row._mapping), access) for row in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _shipment_summary(shipment: OrderShipment, order_no: str) -> dict[str, Any]:
    tracking = (shipment.tracking_no or "").strip()
    masked_tracking = f"****{tracking[-4:]}" if len(tracking) > 4 else (
        "已脱敏" if tracking else None
    )
    return {
        "id": shipment.id,
        "shipment_no": shipment.shipment_no,
        "order_id": shipment.order_id,
        "order_no": order_no,
        "warehouse_id": shipment.warehouse_id,
        "package_no": shipment.package_no,
        "tracking_no_masked": masked_tracking,
        "shipping_type": shipment.shipping_type,
        "source": shipment.source,
        "status": shipment.status,
        "expected_weight_g": shipment.expected_weight_g,
        "actual_weight_g": shipment.actual_weight_g,
        "weight_out_of_range": shipment.weight_out_of_range,
        "weight_overridden": shipment.weight_overridden,
        "platform_sync_status": shipment.platform_sync_status,
        "shipped_at": shipment.shipped_at,
        "created_at": shipment.created_at,
        "updated_at": shipment.updated_at,
    }


def _list_shipments(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    conditions = []
    if args.order_id is not None:
        if db.scalar(select(Order.id).where(
            Order.id == args.order_id, Order.company_id == company_id,
        )) is None:
            raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
        conditions.append(OrderShipment.order_id == args.order_id)
    if args.status is not None:
        conditions.append(OrderShipment.status == args.status)
    if args.tracking_no:
        conditions.append(OrderShipment.tracking_no.like(f"%{args.tracking_no.strip()}%"))
    if args.platform_sync_status is not None:
        conditions.append(OrderShipment.platform_sync_status == args.platform_sync_status)
    start_time = _parse_business_datetime(args.start_time)
    end_time = _parse_business_datetime(args.end_time)
    if start_time and end_time and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    if start_time:
        conditions.append(OrderShipment.created_at >= start_time)
    if end_time:
        conditions.append(OrderShipment.created_at <= end_time)
    total = int(db.scalar(
        select(func.count(OrderShipment.id))
        .join(Order, and_(Order.id == OrderShipment.order_id, Order.company_id == company_id))
        .where(OrderShipment.company_id == company_id, *conditions)
    ) or 0)
    rows = db.execute(
        select(OrderShipment, Order.order_no)
        .join(Order, and_(Order.id == OrderShipment.order_id, Order.company_id == company_id))
        .where(OrderShipment.company_id == company_id, *conditions)
        .order_by(OrderShipment.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    items = [_shipment_summary(shipment, order_no) for shipment, order_no in rows]
    filters = args.model_dump(exclude_none=True)
    if args.tracking_no:
        filters["tracking_no"] = "[用于检索，已脱敏]"
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput(items, filters, len(items), page)


def _get_shipment(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    row = db.execute(
        select(OrderShipment, Order.order_no)
        .join(Order, Order.id == OrderShipment.order_id)
        .where(
            OrderShipment.id == args.shipment_id,
            OrderShipment.company_id == company_id,
            Order.company_id == company_id,
        )
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.FORBIDDEN, "无权查询该发货记录")
    shipment, order_no = row
    return ToolOutput(
        _shipment_summary(shipment, order_no), {"shipment_id": args.shipment_id}, 1,
    )


def _list_shipment_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "sales_orders")
    company_id = access.policy.company_id
    parent_exists = db.scalar(
        select(OrderShipment.id)
        .join(Order, Order.id == OrderShipment.order_id)
        .where(
            OrderShipment.id == args.shipment_id,
            OrderShipment.company_id == company_id,
            Order.company_id == company_id,
        )
    )
    if parent_exists is None:
        raise BizException(ErrorCode.FORBIDDEN, "无权查询该发货记录")
    conditions = [
        OrderShipmentItem.shipment_id == args.shipment_id,
        OrderShipmentItem.company_id == company_id,
        Product.company_id == company_id,
    ]
    total = int(db.scalar(
        select(func.count(OrderShipmentItem.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        select(
            OrderShipmentItem.id, OrderShipmentItem.order_item_id,
            OrderShipmentItem.product_id, Product.code, Product.name,
            OrderShipmentItem.sku_id, ProductSku.spec_code, ProductSku.spec_name,
            OrderShipmentItem.qty,
        )
        .join(Product, Product.id == OrderShipmentItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == OrderShipmentItem.sku_id,
            ProductSku.product_id == OrderShipmentItem.product_id,
            ProductSku.company_id == company_id,
        ))
        .where(*conditions).order_by(OrderShipmentItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    items = [{
        "id": row[0], "order_item_id": row[1], "product_id": row[2],
        "product_code": row[3], "product_name": row[4],
        "sku_id": row[5], "spec_code": row[6], "spec_name": row[7],
        "qty": row[8],
    } for row in rows]
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput(items, filters, len(items), page)


def _list_platforms(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "shop_catalog")
    company_id = access.policy.company_id
    conditions = [Platform.company_id == company_id]
    keyword = (args.keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        conditions.append(or_(Platform.name.like(like), Platform.code.like(like)))
    total = int(db.scalar(select(func.count(Platform.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(Platform.id, Platform.name, Platform.code)
        .where(*conditions).order_by(Platform.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    items = [{"id": row.id, "name": row.name, "code": row.code} for row in rows]
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput(items, filters, len(items), page)


def _get_platform(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "shop_catalog")
    row = db.execute(
        select(Platform.id, Platform.name, Platform.code)
        .where(Platform.id == args.platform_id, Platform.company_id == access.policy.company_id)
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "平台不存在")
    return ToolOutput(
        {"id": row.id, "name": row.name, "code": row.code},
        {"platform_id": args.platform_id}, 1,
    )


def _shop_projection_row(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform_id": row.platform_id,
        "platform_name": row.platform_name,
        "name": row.name,
        "supplier_id": row.supplier_id,
        "supplier_name": row.supplier_name,
        "is_locked": row.is_locked,
        "auto_sync_order": row.auto_sync_order,
        "auto_sync_stock": row.auto_sync_stock,
        "download_mode": row.download_mode,
        "enable_logistics_params": row.enable_logistics_params,
        "api_configured": bool(row.api_configured),
    }


def _shop_select(company_id: int):
    api_configured = select(ShopApi.id).where(
        ShopApi.shop_id == Shop.id,
        ShopApi.company_id == company_id,
    ).exists()
    return (
        select(
            Shop.id.label("id"), Shop.platform_id.label("platform_id"),
            Platform.name.label("platform_name"), Shop.name.label("name"),
            Shop.supplier_id.label("supplier_id"), Supplier.name.label("supplier_name"),
            Shop.is_locked.label("is_locked"),
            Shop.auto_sync_order.label("auto_sync_order"),
            Shop.auto_sync_stock.label("auto_sync_stock"),
            Shop.download_mode.label("download_mode"),
            Shop.enable_logistics_params.label("enable_logistics_params"),
            api_configured.label("api_configured"),
        )
        .select_from(Shop)
        .join(Platform, and_(Platform.id == Shop.platform_id, Platform.company_id == company_id))
        .outerjoin(Supplier, and_(Supplier.id == Shop.supplier_id, Supplier.company_id == company_id))
    )


def _list_shops(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "shop_catalog")
    company_id = access.policy.company_id
    conditions = [Shop.company_id == company_id]
    if args.platform_id is not None:
        conditions.append(Shop.platform_id == args.platform_id)
    keyword = (args.keyword or "").strip()
    if keyword:
        # 不在账号、tag、URL、地址或备注等敏感/自由文本列中搜索。
        conditions.append(Shop.name.like(f"%{keyword}%"))
    for field in ("is_locked", "auto_sync_order", "auto_sync_stock"):
        value = getattr(args, field)
        if value is not None:
            conditions.append(getattr(Shop, field).is_(value))
    total = int(db.scalar(select(func.count(Shop.id)).where(*conditions)) or 0)
    rows = db.execute(
        _shop_select(company_id).where(*conditions).order_by(Shop.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    items = [_shop_projection_row(row) for row in rows]
    filters = args.model_dump(exclude_none=True)
    page = _page({"page": args.page, "page_size": args.page_size, "total": total})
    return ToolOutput(items, filters, len(items), page)


def _get_shop(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "shop_catalog")
    row = db.execute(
        _shop_select(access.policy.company_id).where(
            Shop.id == args.shop_id,
            Shop.company_id == access.policy.company_id,
        )
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "店铺不存在")
    return ToolOutput(_shop_projection_row(row), {"shop_id": args.shop_id}, 1)


def _get_shop_api_status(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "shop_catalog")
    company_id = access.policy.company_id
    if db.scalar(select(Shop.id).where(
        Shop.id == args.shop_id, Shop.company_id == company_id,
    )) is None:
        raise BizException(ErrorCode.NOT_FOUND, "店铺不存在")
    configured = db.scalar(
        select(ShopApi.id).where(
            ShopApi.shop_id == args.shop_id, ShopApi.company_id == company_id,
        ).limit(1)
    ) is not None
    return ToolOutput({"shop_id": args.shop_id, "api_configured": configured}, {
        "shop_id": args.shop_id,
    }, 1)


def _search_purchase_returns(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_returns")
    result = purchase_return_service.list_returns(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.purchase_return(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _search_after_sale_returns(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "after_sale")
    start_time = _parse_business_datetime(filters.get("start_time"))
    end_time = _parse_business_datetime(filters.get("end_time"))
    if start_time and end_time and start_time > end_time:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    result = after_sale_service.list_returns(
        db, **{**filters, "start_time": start_time, "end_time": end_time,
               "with_items": False, "with_register": False},
    )
    data = [query_projection.after_sale_return(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_after_sale_return(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "after_sale")
    record = after_sale_service.get_return_or_404(db, args.return_id)
    data = after_sale_service.return_dict(
        db, record, with_items=False, with_register=False,
    )
    return ToolOutput(
        query_projection.after_sale_return(data, access),
        {"return_id": args.return_id}, 1,
    )


def _list_after_sale_return_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "after_sale")
    after_sale_service.get_return_or_404(db, args.return_id)
    conditions = [ReturnOrderItem.return_order_id == args.return_id]
    total = db.scalar(select(func.count(ReturnOrderItem.id)).where(*conditions)) or 0
    rows = db.execute(
        select(ReturnOrderItem, Product.name, Product.code,
               ProductSku.spec_code, ProductSku.spec_name)
        .join(Product, Product.id == ReturnOrderItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == ReturnOrderItem.sku_id,
            ProductSku.product_id == ReturnOrderItem.product_id,
        ))
        .where(*conditions).order_by(ReturnOrderItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.after_sale_return_item({
        "id": row.id, "product_id": row.product_id,
        "product_code": product_code, "product_name": product_name,
        "sku_id": row.sku_id, "spec_code": spec_code, "spec_name": spec_name,
        "qty": row.qty, "refund_amount": row.refund_amount,
    }, access) for row, product_name, product_code, spec_code, spec_name in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _search_return_registers(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "after_sale")
    if args.return_order_id is not None and args.exchange_order_id is not None:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "退货单和换货单筛选条件不能同时指定")
    conditions = []
    if args.bound is True:
        conditions.append(or_(
            ReturnRegister.return_order_id.is_not(None),
            ReturnRegister.exchange_order_id.is_not(None),
        ))
    elif args.bound is False:
        conditions.extend((
            ReturnRegister.return_order_id.is_(None),
            ReturnRegister.exchange_order_id.is_(None),
        ))
    if args.return_order_id is not None:
        after_sale_service.get_return_or_404(db, args.return_order_id)
        conditions.append(ReturnRegister.return_order_id == args.return_order_id)
    if args.exchange_order_id is not None:
        after_sale_service.get_exchange_or_404(db, args.exchange_order_id)
        conditions.append(ReturnRegister.exchange_order_id == args.exchange_order_id)
    total = db.scalar(select(func.count(ReturnRegister.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(ReturnRegister).where(*conditions).order_by(ReturnRegister.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.return_register(
        after_sale_service.register_dict(db, row), access,
    ) for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _search_after_sale_exchanges(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "after_sale")
    result = after_sale_service.list_exchanges(
        db, **{**filters, "with_items": False, "with_register": False},
    )
    data = [query_projection.after_sale_exchange(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_after_sale_exchange(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "after_sale")
    record = after_sale_service.get_exchange_or_404(db, args.exchange_id)
    data = after_sale_service.exchange_dict(
        db, record, with_items=False, with_register=False,
    )
    return ToolOutput(
        query_projection.after_sale_exchange(data, access),
        {"exchange_id": args.exchange_id}, 1,
    )


def _list_after_sale_exchange_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "after_sale")
    after_sale_service.get_exchange_or_404(db, args.exchange_id)
    conditions = [ExchangeOrderItem.exchange_order_id == args.exchange_id]
    total = db.scalar(select(func.count(ExchangeOrderItem.id)).where(*conditions)) or 0
    rows = db.execute(
        select(ExchangeOrderItem, Product.name, Product.code,
               ProductSku.spec_code, ProductSku.spec_name)
        .join(Product, Product.id == ExchangeOrderItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == ExchangeOrderItem.sku_id,
            ProductSku.product_id == ExchangeOrderItem.product_id,
        ))
        .where(*conditions).order_by(ExchangeOrderItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.after_sale_exchange_item({
        "id": row.id, "product_id": row.product_id,
        "product_code": product_code, "product_name": product_name,
        "sku_id": row.sku_id, "spec_code": spec_code, "spec_name": spec_name,
        "qty": row.qty, "exchange_qty": row.exchange_qty,
    }, access) for row, product_name, product_code, spec_code, spec_name in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_purchase_return(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "purchase_returns")
    company_id = access.policy.company_id
    record = purchase_return_service.get_return_or_404(
        db, args.return_id, company_id=company_id,
    )
    data = purchase_return_service.return_dict(
        db, record, with_items=False, with_approval=False, company_id=company_id,
    )
    return ToolOutput(
        query_projection.purchase_return(data, access),
        {"return_id": args.return_id}, 1,
    )


def _list_purchase_return_items(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_returns")
    company_id = access.policy.company_id
    purchase_return_service.get_return_or_404(
        db, args.return_id, company_id=company_id,
    )
    conditions = [
        PurchaseReturnItem.return_id == args.return_id,
        PurchaseReturnItem.company_id == company_id,
        Product.company_id == company_id,
    ]
    total = db.scalar(select(func.count(PurchaseReturnItem.id)).where(*conditions)) or 0
    rows = db.execute(
        select(
            PurchaseReturnItem, Product.name, Product.code, Product.art_no,
            ProductSku.spec_name, ProductSku.spec_code, Unit.name,
        )
        .join(Product, Product.id == PurchaseReturnItem.product_id)
        .outerjoin(ProductSku, and_(
            ProductSku.id == PurchaseReturnItem.sku_id,
            ProductSku.product_id == PurchaseReturnItem.product_id,
            ProductSku.company_id == company_id,
        ))
        .outerjoin(Unit, Unit.id == PurchaseReturnItem.unit_id)
        .where(*conditions)
        .order_by(PurchaseReturnItem.id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.purchase_return_item({
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product_name,
        "product_code": product_code,
        "art_no": art_no,
        "sku_id": item.sku_id,
        "spec_name": spec_name,
        "spec_code": spec_code,
        "unit_id": item.unit_id,
        "unit_name": unit_name,
        "unit_price": item.unit_price,
        "return_qty": item.return_qty,
        "amount": item.amount,
    }, access) for item, product_name, product_code, art_no, spec_name,
        spec_code, unit_name in rows]
    result = {"items": data, "page": args.page,
              "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_purchase_return_stock_out(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_returns")
    data = purchase_return_service.get_return_stock_out(
        db, args.return_id, company_id=access.policy.company_id,
    )
    projected = query_projection.purchase_return_stock_out(data, access)
    return ToolOutput(projected, {"return_id": args.return_id}, 1 if data else 0)


def _get_purchase_return_refund(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_returns")
    data = purchase_return_service.get_return_refund(
        db, args.return_id, company_id=access.policy.company_id,
    )
    projected = query_projection.purchase_return_refund(data, access)
    return ToolOutput(projected, {"return_id": args.return_id}, 1 if data else 0)


def _search_products(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "product_catalog")
    result = product_service.list_products(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.product(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _list_product_categories(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    conditions = [Category.company_id == access.policy.company_id]
    total = db.scalar(select(func.count(Category.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Category).where(*conditions).order_by(Category.sort_order, Category.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{
        "id": row.id, "name": row.name, "parent_id": row.parent_id,
        "sort_order": row.sort_order,
    } for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(), len(data), _page(result))


def _list_product_brands(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    conditions = [Brand.company_id == access.policy.company_id]
    total = db.scalar(select(func.count(Brand.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Brand).where(*conditions).order_by(Brand.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{"id": row.id, "name": row.name} for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(), len(data), _page(result))


def _list_product_units(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    conditions = [Unit.company_id == access.policy.company_id]
    total = db.scalar(select(func.count(Unit.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Unit).where(*conditions).order_by(Unit.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{"id": row.id, "name": row.name, "code": row.code} for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(), len(data), _page(result))


def _list_product_unit_groups(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    company_id = access.policy.company_id
    group_conditions = [UnitGroup.company_id == company_id]
    total = db.scalar(
        select(func.count(UnitGroup.id)).where(*group_conditions)
    ) or 0
    rows = db.execute(
        select(
            UnitGroup.id, UnitGroup.name, UnitGroup.main_unit_id,
            Unit.name.label("main_unit_name"),
            func.count(UnitGroupItem.id).label("item_count"),
        )
        .join(Unit, and_(
            Unit.id == UnitGroup.main_unit_id, Unit.company_id == company_id,
        ))
        .outerjoin(UnitGroupItem, and_(
            UnitGroupItem.unit_group_id == UnitGroup.id,
            UnitGroupItem.company_id == company_id,
        ))
        .where(*group_conditions)
        .group_by(UnitGroup.id, UnitGroup.name, UnitGroup.main_unit_id, Unit.name)
        .order_by(UnitGroup.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{
        "id": row.id, "name": row.name, "main_unit_id": row.main_unit_id,
        "main_unit_name": row.main_unit_name, "item_count": int(row.item_count),
    } for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(), len(data), _page(result))


def _list_product_unit_group_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    company_id = access.policy.company_id
    group_id = db.scalar(select(UnitGroup.id).where(
        UnitGroup.id == args.unit_group_id,
        UnitGroup.company_id == company_id,
    ))
    if group_id is None:
        return ToolOutput([], args.model_dump(), 0, _page({
            "page": args.page, "page_size": args.page_size, "total": 0,
        }))
    total = db.scalar(
        select(func.count(UnitGroupItem.id)).where(
            UnitGroupItem.unit_group_id == args.unit_group_id,
            UnitGroupItem.company_id == company_id,
        )
    ) or 0
    rows = db.execute(
        select(
            UnitGroupItem.id, UnitGroupItem.unit_group_id, UnitGroupItem.unit_id,
            Unit.name.label("unit_name"), UnitGroupItem.conversion_rate,
        )
        .join(Unit, and_(
            Unit.id == UnitGroupItem.unit_id, Unit.company_id == company_id,
        ))
        .where(
            UnitGroupItem.unit_group_id == args.unit_group_id,
            UnitGroupItem.company_id == company_id,
        )
        .order_by(UnitGroupItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{
        "id": row.id, "unit_group_id": row.unit_group_id,
        "unit_id": row.unit_id, "unit_name": row.unit_name,
        "conversion_rate": row.conversion_rate,
    } for row in rows]
    filters = args.model_dump()
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _list_product_spec_types(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    company_id = access.policy.company_id
    conditions = [SpecType.company_id == company_id]
    total = db.scalar(select(func.count(SpecType.id)).where(*conditions)) or 0
    rows = db.execute(
        select(
            SpecType.id, SpecType.name, SpecType.category_id,
            Category.name.label("category_name"),
        )
        .outerjoin(Category, and_(
            Category.id == SpecType.category_id,
            Category.company_id == company_id,
        ))
        .where(*conditions)
        .order_by(SpecType.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{
        "id": row.id, "name": row.name, "category_id": row.category_id,
        "category_name": row.category_name,
    } for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, args.model_dump(), len(data), _page(result))


def _list_product_spec_values(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "product_settings")
    company_id = access.policy.company_id
    type_id = db.scalar(select(SpecType.id).where(
        SpecType.id == args.spec_type_id,
        SpecType.company_id == company_id,
    ))
    if type_id is None:
        return ToolOutput([], args.model_dump(), 0, _page({
            "page": args.page, "page_size": args.page_size, "total": 0,
        }))
    conditions = [
        SpecValue.spec_type_id == args.spec_type_id,
        SpecValue.company_id == company_id,
    ]
    total = db.scalar(
        select(func.count(SpecValue.id)).where(*conditions)
    ) or 0
    rows = db.scalars(
        select(SpecValue)
        .where(*conditions)
        .order_by(SpecValue.sort_order, SpecValue.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{
        "id": row.id, "spec_type_id": row.spec_type_id,
        "name": row.name, "code": row.code, "sort_order": row.sort_order,
    } for row in rows]
    filters = args.model_dump()
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_product(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "product_catalog")
    company_id = access.policy.company_id
    product = product_service.get_product_or_404(
        db, args.product_id, company_id=company_id,
    )
    data = query_projection.product(
        product_service.product_dict(db, product, company_id=company_id), access,
    )
    return ToolOutput(data, {"product_id": args.product_id}, 1)


def _list_product_skus(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "product_catalog")
    company_id = access.policy.company_id
    product_service.get_product_or_404(
        db, args.product_id, company_id=company_id,
    )
    conditions = [
        ProductSku.product_id == args.product_id,
        ProductSku.company_id == company_id,
    ]
    total = db.scalar(select(func.count(ProductSku.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(ProductSku).where(*conditions).order_by(ProductSku.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.product_sku({
        "id": row.id,
        "product_id": row.product_id,
        "spec_name": row.spec_name,
        "spec_code": row.spec_code,
        "barcode": row.barcode,
        "is_active": row.is_active,
    }, access) for row in rows]
    result = {"items": data, "page": args.page, "page_size": args.page_size,
              "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _search_combos(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "product_catalog")
    result = product_service.list_combos(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.combo(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_combo(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "product_catalog")
    company_id = access.policy.company_id
    combo = product_service.get_combo_or_404(
        db, args.combo_id, company_id=company_id,
    )
    data = query_projection.combo(
        product_service.combo_dict(
            db, combo, with_items=False, company_id=company_id,
        ), access,
    )
    return ToolOutput(data, {"combo_id": args.combo_id}, 1)


def _list_combo_items(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "product_catalog")
    company_id = access.policy.company_id
    product_service.get_combo_or_404(
        db, args.combo_id, company_id=company_id,
    )
    conditions = [
        ComboProductItem.combo_id == args.combo_id,
        ComboProductItem.company_id == company_id,
    ]
    total = db.scalar(
        select(func.count(ComboProductItem.id)).where(*conditions)
    ) or 0
    rows = db.execute(
        select(ComboProductItem, Product.name, ProductSku.spec_code)
        .join(Product, and_(
            Product.id == ComboProductItem.product_id,
            Product.company_id == company_id,
        ))
        .outerjoin(ProductSku, and_(
            ProductSku.id == ComboProductItem.sku_id,
            ProductSku.product_id == ComboProductItem.product_id,
            ProductSku.company_id == company_id,
        ))
        .where(*conditions)
        .order_by(ComboProductItem.sort_order, ComboProductItem.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.combo_item({
        "id": item.id,
        "combo_id": item.combo_id,
        "product_id": item.product_id,
        "product_name": product_name,
        "sku_id": item.sku_id,
        "sku_spec_code": sku_spec_code,
        "qty": item.qty,
        "sort_order": item.sort_order,
    }, access) for item, product_name, sku_spec_code in rows]
    result = {"items": data, "page": args.page, "page_size": args.page_size,
              "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _expand_combo(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "product_catalog")
    quantity = Decimal(str(args.qty))
    if quantity != quantity.quantize(Decimal("0.001")):
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "数量最多允许三位小数")
    result = product_service.expand_combo_page(
        db, combo_id=args.combo_id, qty=quantity,
        page=args.page, page_size=args.page_size,
        company_id=access.policy.company_id,
    )
    data = [query_projection.combo_expansion_item(row, access)
            for row in result["items"]]
    return ToolOutput(
        data, args.model_dump(exclude_none=True), len(data), _page(result),
    )


def _get_company(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "company_directory")
    company_row = db.get(Company, access.staff.company_id)
    if company_row is None:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.NOT_FOUND, "公司不存在")
    data = query_projection.company({
        "id": company_row.id,
        "full_name": company_row.full_name,
        "short_name": company_row.short_name,
        "legal_person": company_row.legal_person,
        "phone": company_row.phone,
        "tax_no": company_row.tax_no,
    }, access)
    return ToolOutput(data, {}, 1)


def _list_departments(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "company_directory")
    conditions = [Department.company_id == access.staff.company_id]
    total = int(db.scalar(select(func.count(Department.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(Department).where(*conditions)
        .order_by(Department.sort_order, Department.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.department({
        "id": row.id, "name": row.name,
        "parent_id": row.parent_id, "sort_order": row.sort_order,
    }, access) for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_department(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "company_directory")
    row = db.scalar(select(Department).where(
        Department.id == args.department_id,
        Department.company_id == access.staff.company_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "部门不存在")
    data = query_projection.department({
        "id": row.id, "name": row.name,
        "parent_id": row.parent_id, "sort_order": row.sort_order,
    }, access)
    return ToolOutput(data, {"department_id": args.department_id}, 1)


def _list_positions(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "company_directory")
    conditions = [Position.company_id == access.staff.company_id]
    total = int(db.scalar(select(func.count(Position.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(Position).where(*conditions).order_by(Position.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.position({"id": row.id, "name": row.name}, access)
            for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_position(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "company_directory")
    row = db.scalar(select(Position).where(
        Position.id == args.position_id,
        Position.company_id == access.staff.company_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "职务不存在")
    data = query_projection.position({"id": row.id, "name": row.name}, access)
    return ToolOutput(data, {"position_id": args.position_id}, 1)


def _list_company_staff(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "company_directory")
    conditions = [Staff.company_id == access.staff.company_id]
    if args.keyword:
        like = f"%{args.keyword.strip()}%"
        conditions.append(or_(Staff.name.like(like), Staff.staff_no.like(like)))
    if args.department_id is not None:
        conditions.append(Staff.department_id == args.department_id)
    if args.position_id is not None:
        conditions.append(Staff.position_id == args.position_id)
    if args.is_active is not None:
        conditions.append(Staff.is_active.is_(args.is_active))

    total = int(db.scalar(select(func.count(Staff.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(Staff, Department.name, Position.name)
        .outerjoin(Department, and_(
            Department.id == Staff.department_id,
            Department.company_id == Staff.company_id,
        ))
        .outerjoin(Position, and_(
            Position.id == Staff.position_id,
            Position.company_id == Staff.company_id,
        ))
        .where(*conditions).order_by(Staff.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.staff_directory_item({
        "id": row.id, "name": row.name, "staff_no": row.staff_no,
        "department_id": row.department_id, "department_name": department_name,
        "position_id": row.position_id, "position_name": position_name,
        "is_active": row.is_active,
    }, access) for row, department_name, position_name in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_company_staff(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "company_directory")
    row = db.execute(
        select(Staff, Department.name, Position.name)
        .outerjoin(Department, and_(
            Department.id == Staff.department_id,
            Department.company_id == Staff.company_id,
        ))
        .outerjoin(Position, and_(
            Position.id == Staff.position_id,
            Position.company_id == Staff.company_id,
        ))
        .where(
            Staff.id == args.staff_id,
            Staff.company_id == access.staff.company_id,
        )
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "员工不存在")
    employee, department_name, position_name = row
    data = query_projection.staff_directory_item({
        "id": employee.id, "name": employee.name,
        "staff_no": employee.staff_no,
        "department_id": employee.department_id,
        "department_name": department_name,
        "position_id": employee.position_id,
        "position_name": position_name,
        "is_active": employee.is_active,
    }, access)
    return ToolOutput(data, {"staff_id": args.staff_id}, 1)


def _get_company_staff_data_scope(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.services import company_service

    access = authorize_query(db, "company_directory")
    result = company_service.get_staff_data_scope(
        db, staff_id=args.staff_id, company_id=access.staff.company_id,
    )
    # Match the REST response's two-field contract; never expose department IDs,
    # role assignments, or other staff/security metadata here.
    data = {
        "staff_id": result["staff_id"],
        "scope_type": result["scope_type"],
    }
    return ToolOutput(data, {"staff_id": args.staff_id}, 1)


def _get_assistant_capabilities(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "assistant_capabilities")
    result = assistant_service.build_capabilities(db, access.staff)
    data = query_projection.assistant_capabilities(result, access)
    return ToolOutput(data, {}, 1)


def _search_suppliers(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "supplier_catalog")
    result = party_service.list_suppliers(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.supplier(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_supplier(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "supplier_catalog")
    supplier = party_service.get_supplier(
        db, args.supplier_id, company_id=access.policy.company_id,
    )
    if supplier is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商不存在")
    data = query_projection.supplier(party_service.supplier_dict(db, supplier), access)
    return ToolOutput(data, {"supplier_id": args.supplier_id}, 1)


def _mask_bank_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(character for character in value if character.isalnum())
    return f"****{normalized[-4:]}" if len(normalized) > 4 else "已脱敏"


def _party_account_projection(
    row: Account, *, supplier_id: int | None, customer_id: int | None, access,
) -> dict:
    result = {
        "id": row.id,
        "supplier_id": supplier_id,
        "customer_id": customer_id,
        "bank": row.bank,
        "branch": row.branch,
        "bank_no": _mask_bank_number(row.bank_no),
        "account_no": _mask_bank_number(row.account_no),
        "is_default": row.is_default,
    }
    if "*" in access.permissions or "module:finance" in access.permissions:
        result["balance"] = row.balance
    return result


def _list_supplier_accounts(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "party_accounts")
    supplier = party_service.get_supplier_or_404(
        db, args.supplier_id, company_id=access.policy.company_id,
    )
    condition = and_(
        Account.supplier_id == supplier.id,
        Account.company_id == access.policy.company_id,
    )
    total = db.scalar(select(func.count(Account.id)).where(condition)) or 0
    rows = db.scalars(
        select(Account).where(condition)
        .order_by(Account.is_default.desc(), Account.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [
        _party_account_projection(
            row, supplier_id=supplier.id, customer_id=None, access=access,
        )
        for row in rows
    ]
    filters = args.model_dump()
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _list_customer_accounts(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "party_accounts")
    customer = party_service.get_customer_or_404(
        db, args.customer_id, company_id=access.policy.company_id,
    )
    condition = and_(
        Account.customer_id == customer.id,
        Account.company_id == access.policy.company_id,
    )
    total = db.scalar(select(func.count(Account.id)).where(condition)) or 0
    rows = db.scalars(
        select(Account).where(condition)
        .order_by(Account.is_default.desc(), Account.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [
        _party_account_projection(
            row, supplier_id=None, customer_id=customer.id, access=access,
        )
        for row in rows
    ]
    filters = args.model_dump()
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _list_supplier_prices(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "supplier_prices")
    filters = args.model_dump(exclude_none=True)
    result = party_service.list_supplier_prices(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.supplier_price(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _search_customers(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "customer_catalog")
    result = party_service.list_customers(
        db, **filters, company_id=access.policy.company_id,
    )
    data = [query_projection.customer(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_customer(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "customer_catalog")
    customer = party_service.get_customer(
        db, args.customer_id, company_id=access.policy.company_id,
    )
    if customer is None:
        raise BizException(ErrorCode.NOT_FOUND, "客户不存在")
    data = query_projection.customer(party_service.customer_dict(db, customer), access)
    return ToolOutput(data, {"customer_id": args.customer_id}, 1)


def _search_warehouses(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "warehouse_catalog")
    company_id = access.policy.company_id
    conditions = [Warehouse.company_id == company_id]
    if args.keyword:
        like = f"%{args.keyword.strip()}%"
        conditions.append((Warehouse.code.like(like)) | (Warehouse.name.like(like)))
    if args.warehouse_type:
        conditions.append(Warehouse.warehouse_type == args.warehouse_type)
    if args.is_enabled is not None:
        conditions.append(Warehouse.is_enabled.is_(args.is_enabled))
    total = db.scalar(select(func.count(Warehouse.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Warehouse).where(*conditions).order_by(Warehouse.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.warehouse({
        "id": row.id, "code": row.code, "name": row.name,
        "region": row.region, "address": row.address,
        "warehouse_type": row.warehouse_type, "is_enabled": row.is_enabled,
        "is_default": row.is_default,
    }, access) for row in rows]
    result = {"items": data, "page": args.page, "page_size": args.page_size,
              "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_warehouse(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "warehouse_catalog")
    row = warehouse_service.get_warehouse_or_404(
        db, args.warehouse_id, company_id=access.policy.company_id,
    )
    data = query_projection.warehouse(warehouse_service.warehouse_dict(row), access)
    return ToolOutput(data, {"warehouse_id": args.warehouse_id}, 1)


def _list_warehouse_zones(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "warehouse_catalog")
    company_id = access.policy.company_id
    warehouse_service.get_warehouse_or_404(db, args.warehouse_id, company_id=company_id)
    conditions = [
        Zone.warehouse_id == args.warehouse_id,
        Zone.company_id == company_id,
    ]
    if args.keyword:
        like = f"%{args.keyword.strip()}%"
        conditions.append((Zone.code.like(like)) | (Zone.name.like(like)))
    total = db.scalar(select(func.count(Zone.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Zone).where(*conditions).order_by(Zone.sort_order, Zone.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [{"id": row.id, "warehouse_id": row.warehouse_id,
             "code": row.code, "name": row.name, "sort_order": row.sort_order}
            for row in rows]
    result = {"items": data, "page": args.page, "page_size": args.page_size,
              "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_warehouse_zone(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "warehouse_catalog")
    company_id = access.policy.company_id
    row = warehouse_service.get_zone_or_404(db, args.zone_id, company_id=company_id)
    warehouse_service.get_warehouse_or_404(db, row.warehouse_id, company_id=company_id)
    data = {
        "id": row.id,
        "warehouse_id": row.warehouse_id,
        "code": row.code,
        "name": row.name,
        "sort_order": row.sort_order,
    }
    return ToolOutput(data, {"zone_id": args.zone_id}, 1)


def _search_locations(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "warehouse_catalog")
    company_id = access.policy.company_id
    warehouse_service.get_warehouse_or_404(db, args.warehouse_id, company_id=company_id)
    conditions = [
        Location.warehouse_id == args.warehouse_id,
        Location.company_id == company_id,
    ]
    if args.zone_id is not None:
        conditions.append(Location.zone_id == args.zone_id)
    if args.keyword:
        conditions.append(Location.code.like(f"%{args.keyword.strip()}%"))
    if args.is_enabled is not None:
        conditions.append(Location.is_enabled.is_(args.is_enabled))
    total = db.scalar(select(func.count(Location.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Location).where(*conditions).order_by(Location.priority.desc(),
                                                      Location.sort_order, Location.code)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.location({
        "id": row.id, "warehouse_id": row.warehouse_id, "zone_id": row.zone_id,
        "code": row.code, "is_virtual": row.is_virtual, "is_enabled": row.is_enabled,
    }, access) for row in rows]
    result = {"items": data, "page": args.page, "page_size": args.page_size,
              "total": int(total)}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_location(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "warehouse_catalog")
    company_id = access.policy.company_id
    row = warehouse_service.get_location_or_404(db, args.location_id, company_id=company_id)
    warehouse_service.get_warehouse_or_404(db, row.warehouse_id, company_id=company_id)
    data = query_projection.location({
        "id": row.id,
        "warehouse_id": row.warehouse_id,
        "zone_id": row.zone_id,
        "code": row.code,
        "is_virtual": row.is_virtual,
        "is_enabled": row.is_enabled,
    }, access)
    return ToolOutput(data, {"location_id": args.location_id}, 1)


def _get_location_capacity(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "location_capacity")
    try:
        state = warehouse_service.location_capacity_state(
            db, args.location_id, company_id=access.policy.company_id,
        )
    except BizException as exc:
        raise BizException(ErrorCode.FORBIDDEN, "库位不存在或不可见") from exc
    data = query_projection.location_capacity(state, access)
    return ToolOutput(data, {"location_id": args.location_id}, 1)


def _wave_strategy_select():
    return (
        select(WaveStrategy, Warehouse.name, Logistics.name)
        .outerjoin(Warehouse, Warehouse.id == WaveStrategy.warehouse_id)
        .outerjoin(Logistics, Logistics.id == WaveStrategy.logistics_id)
    )


def _wave_strategy_data(row, warehouse_name, logistics_name, access) -> dict:
    return query_projection.wave_strategy({
        "id": row.id,
        "name": row.name,
        "logistics_id": row.logistics_id,
        "logistics_name": logistics_name,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "wave_order_count": row.wave_order_count,
        "load_no_type": row.load_no_type,
        "sort_type": row.sort_type,
        "is_enabled": row.is_enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)


def _search_wave_strategies(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "wave_queries")
    conditions = []
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(WaveStrategy.name.like(f"%{keyword}%", escape="\\"))
    if args.is_enabled is not None:
        conditions.append(WaveStrategy.is_enabled.is_(args.is_enabled))
    total = int(db.scalar(
        select(func.count(WaveStrategy.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        _wave_strategy_select()
        .where(*conditions)
        .order_by(WaveStrategy.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [
        _wave_strategy_data(row, warehouse_name, logistics_name, access)
        for row, warehouse_name, logistics_name in rows
    ]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_wave_strategy(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "wave_queries")
    row = db.execute(
        _wave_strategy_select().where(WaveStrategy.id == args.strategy_id)
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.FORBIDDEN, "波次策略不存在或不可见")
    strategy, warehouse_name, logistics_name = row
    data = _wave_strategy_data(strategy, warehouse_name, logistics_name, access)
    return ToolOutput(data, {"strategy_id": args.strategy_id}, 1)


def _get_wave_strategy_pending_count(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    authorize_query(db, "wave_queries")
    strategy = db.get(WaveStrategy, args.strategy_id)
    if strategy is None:
        raise BizException(ErrorCode.FORBIDDEN, "波次策略不存在或不可见")
    if args.order_ids is not None and any(order_id < 1 for order_id in args.order_ids):
        raise BizException(ErrorCode.BAD_REQUEST, "订单ID必须大于0")
    conditions = [
        Order.status == "PENDING",
        Order.audit_status == "APPROVED",
        Order.id.not_in(select(WaveOrder.order_id)),
    ]
    if strategy.warehouse_id is not None:
        conditions.append(Order.warehouse_id == strategy.warehouse_id)
    if strategy.logistics_id is not None:
        conditions.append(Order.logistics_id == strategy.logistics_id)
    if strategy.shop_ids:
        try:
            shop_ids = [int(item.strip()) for item in strategy.shop_ids.split(",") if item.strip()]
        except ValueError as exc:
            raise BizException(ErrorCode.INTERNAL_ERROR, "波次策略店铺配置异常") from exc
        if shop_ids:
            conditions.append(Order.shop_id.in_(shop_ids))
    if args.order_ids is not None:
        conditions.append(Order.id.in_(args.order_ids))
    count = int(db.scalar(select(func.count(Order.id)).where(*conditions)) or 0)
    data = {"strategy_id": args.strategy_id, "pending_count": count}
    return ToolOutput(data, args.model_dump(exclude_none=True), 1)


def _wave_count_subquery(model, foreign_key, label: str):
    return (
        select(func.count(model.id))
        .where(foreign_key == Wave.id)
        .correlate(Wave)
        .scalar_subquery()
        .label(label)
    )


def _wave_document_data(row, order_count, load_count, access) -> dict:
    return query_projection.wave_document({
        "id": row.id,
        "wave_no": row.wave_no,
        "strategy_id": row.strategy_id,
        "name": row.name,
        "express_type": row.express_type,
        "total_order_count": row.total_order_count,
        "order_count": int(order_count or 0),
        "load_number_count": int(load_count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)


def _search_waves(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "wave_queries")
    start = _parse_business_datetime(args.start_time)
    end = _parse_business_datetime(args.end_time)
    if start and end and start > end:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "波次开始时间不能晚于结束时间")
    conditions = []
    if args.strategy_id is not None:
        conditions.append(Wave.strategy_id == args.strategy_id)
    if args.express_type:
        conditions.append(Wave.express_type == args.express_type.strip())
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{keyword}%"
        conditions.append(or_(
            Wave.wave_no.like(pattern, escape="\\"),
            Wave.name.like(pattern, escape="\\"),
        ))
    if start is not None:
        conditions.append(Wave.created_at >= start)
    if end is not None:
        conditions.append(Wave.created_at <= end)
    total = int(db.scalar(select(func.count(Wave.id)).where(*conditions)) or 0)
    order_count = _wave_count_subquery(WaveOrder, WaveOrder.wave_id, "order_count")
    load_count = _wave_count_subquery(LoadNo, LoadNo.wave_id, "load_count")
    rows = db.execute(
        select(Wave, order_count, load_count)
        .where(*conditions)
        .order_by(Wave.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [
        _wave_document_data(row, order_count_value, load_count_value, access)
        for row, order_count_value, load_count_value in rows
    ]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_wave(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "wave_queries")
    order_count = _wave_count_subquery(WaveOrder, WaveOrder.wave_id, "order_count")
    load_count = _wave_count_subquery(LoadNo, LoadNo.wave_id, "load_count")
    row = db.execute(
        select(Wave, order_count, load_count).where(Wave.id == args.wave_id)
    ).one_or_none()
    if row is None:
        raise BizException(ErrorCode.FORBIDDEN, "波次不存在或不可见")
    wave, order_total, load_total = row
    data = _wave_document_data(wave, order_total, load_total, access)
    return ToolOutput(data, {"wave_id": args.wave_id}, 1)


def _list_wave_orders(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "wave_queries")
    if db.get(Wave, args.wave_id) is None:
        raise BizException(ErrorCode.FORBIDDEN, "波次不存在或不可见")
    base = (
        select(
            WaveOrder.id.label("id"), Order.order_no.label("order_no"),
            Order.warehouse_id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            Order.logistics_id.label("logistics_id"),
            Logistics.name.label("logistics_name"),
            Order.status.label("status"), Order.audit_status.label("audit_status"),
            Order.order_time.label("order_time"), WaveOrder.load_no.label("load_no"),
            WaveOrder.sequence.label("sequence"),
        )
        .join(Order, Order.id == WaveOrder.order_id)
        .outerjoin(Warehouse, Warehouse.id == Order.warehouse_id)
        .outerjoin(Logistics, Logistics.id == Order.logistics_id)
        .where(WaveOrder.wave_id == args.wave_id)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base).order_by(base.c.sequence, base.c.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.wave_order(dict(row._mapping), access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(), len(data), _page(page))


def _list_wave_load_numbers(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "wave_queries")
    if db.get(Wave, args.wave_id) is None:
        raise BizException(ErrorCode.FORBIDDEN, "波次不存在或不可见")
    total = int(db.scalar(
        select(func.count(LoadNo.id)).where(LoadNo.wave_id == args.wave_id)
    ) or 0)
    rows = db.scalars(
        select(LoadNo).where(LoadNo.wave_id == args.wave_id)
        .order_by(LoadNo.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.wave_load_number({
        "id": row.id, "wave_id": row.wave_id,
        "load_no": row.load_no, "express_type": row.express_type,
    }, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(), len(data), _page(page))


def _stock_document_data(
    row: StockIn | StockOut,
    *,
    warehouse_name: str | None,
    item_count: int,
    putaway_qty: Decimal | None = None,
) -> dict[str, Any]:
    if isinstance(row, StockIn):
        applied = Decimal(putaway_qty or 0)
        return {
            "id": row.id,
            "stock_in_no": row.stock_in_no,
            "warehouse_id": row.warehouse_id,
            "warehouse_name": warehouse_name,
            "stock_in_type": row.stock_in_type,
            "status": row.status,
            "audit_status": row.audit_status,
            "total_qty": row.total_qty,
            "putaway_qty": applied,
            "pending_putaway_qty": max(Decimal(row.total_qty) - applied, Decimal("0.000")),
            "item_count": item_count,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    return {
        "id": row.id,
        "stock_out_no": row.stock_out_no,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "stock_out_type": row.stock_out_type,
        "status": row.status,
        "audit_status": row.audit_status,
        "off_shelf_status": row.off_shelf_status,
        "total_qty": row.total_qty,
        "item_count": item_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _pending_putaways(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "stock_documents")
    putaway_totals = (
        select(
            Putaway.source_id.label("source_id"),
            Putaway.product_id.label("product_id"),
            Putaway.sku_id.label("sku_id"),
            func.sum(Putaway.qty).label("putaway_qty"),
        )
        .where(Putaway.source_type == "STOCK_IN")
        .group_by(Putaway.source_id, Putaway.product_id, Putaway.sku_id)
        .subquery()
    )
    receipt_conditions = [
        StockIn.status == "APPROVED",
        *scope_conditions(db, StockIn.created_by),
    ]
    if args.warehouse_id is not None:
        receipt_conditions.append(StockIn.warehouse_id == args.warehouse_id)
    received = (
        select(
            StockIn.id.label("source_id"),
            StockIn.warehouse_id.label("warehouse_id"),
            StockInItem.product_id.label("product_id"),
            StockInItem.sku_id.label("sku_id"),
            func.sum(StockInItem.qty).label("received_qty"),
        )
        .join(StockInItem, StockInItem.stock_in_id == StockIn.id)
        .where(*receipt_conditions)
        .group_by(
            StockIn.id, StockIn.warehouse_id,
            StockInItem.product_id, StockInItem.sku_id,
        )
        .subquery()
    )
    criteria = [
        received.c.source_id == putaway_totals.c.source_id,
        received.c.product_id == putaway_totals.c.product_id,
        received.c.sku_id.is_not_distinct_from(putaway_totals.c.sku_id),
    ]
    grouped = (
        select(
            received.c.warehouse_id,
            Warehouse.name.label("warehouse_name"),
            received.c.product_id,
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            received.c.sku_id,
            ProductSku.spec_code,
            ProductSku.spec_name,
            func.sum(received.c.received_qty).label("received_qty"),
            func.sum(func.coalesce(putaway_totals.c.putaway_qty, 0)).label("putaway_qty"),
        )
        .select_from(received)
        .join(Warehouse, Warehouse.id == received.c.warehouse_id)
        .join(Product, Product.id == received.c.product_id)
        .outerjoin(ProductSku, ProductSku.id == received.c.sku_id)
        .outerjoin(putaway_totals, and_(*criteria))
    )
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{keyword}%"
        grouped = grouped.where(or_(
            Product.name.like(like, escape="\\"),
            Product.code.like(like, escape="\\"),
            ProductSku.spec_code.like(like, escape="\\"),
        ))
    grouped = grouped.group_by(
        received.c.warehouse_id, Warehouse.name,
        received.c.product_id, Product.code, Product.name,
        received.c.sku_id, ProductSku.spec_code, ProductSku.spec_name,
    ).having(
        func.sum(received.c.received_qty)
        > func.sum(func.coalesce(putaway_totals.c.putaway_qty, 0))
    ).subquery()
    total = int(db.scalar(select(func.count()).select_from(grouped)) or 0)
    rows = db.execute(
        select(grouped)
        .order_by(grouped.c.warehouse_id, grouped.c.product_id, grouped.c.sku_id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.pending_putaway({
        "warehouse_id": row.warehouse_id,
        "warehouse_name": row.warehouse_name,
        "product_id": row.product_id,
        "product_name": row.product_name,
        "product_code": row.product_code,
        "sku_id": row.sku_id,
        "spec_name": row.spec_name,
        "spec_code": row.spec_code,
        "received_qty": row.received_qty,
        "putaway_qty": row.putaway_qty,
        "pending_putaway_qty": Decimal(row.received_qty) - Decimal(row.putaway_qty or 0),
    }, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_putaways(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "stock_documents")
    stock_in_ids = select(StockIn.id).where(
        *scope_conditions(db, StockIn.created_by)
    ).correlate(None)
    transfer_in_ids = (
        select(TransferIn.id)
        .join(Transfer, Transfer.id == TransferIn.transfer_id)
        .where(*scope_conditions(db, Transfer.created_by))
        .correlate(None)
    )
    conditions = [or_(
        and_(Putaway.source_type == "STOCK_IN", Putaway.source_id.in_(stock_in_ids)),
        and_(Putaway.source_type == "TRANSFER_IN", Putaway.source_id.in_(transfer_in_ids)),
    )]
    if args.warehouse_id is not None:
        conditions.append(Putaway.warehouse_id == args.warehouse_id)
    if args.location_id is not None:
        conditions.append(Putaway.location_id == args.location_id)
    if args.product_id is not None:
        conditions.append(Putaway.product_id == args.product_id)
    if args.sku_id is not None:
        conditions.append(Putaway.sku_id == args.sku_id)
    if args.stock_in_id is not None:
        conditions.extend([
            Putaway.source_type == "STOCK_IN",
            Putaway.source_id == args.stock_in_id,
        ])
    total = int(db.scalar(select(func.count(Putaway.id)).where(*conditions)) or 0)
    source_no = case(
        (Putaway.source_type == "STOCK_IN", StockIn.stock_in_no),
        (Putaway.source_type == "TRANSFER_IN", TransferIn.transfer_in_no),
        else_=None,
    )
    rows = db.execute(
        select(
            Putaway,
            source_no.label("source_no"),
            Warehouse.name.label("warehouse_name"),
            Location.code.label("location_code"),
            Product.name.label("product_name"),
            Product.code.label("product_code"),
            ProductSku.spec_name,
            ProductSku.spec_code,
        )
        .join(Warehouse, Warehouse.id == Putaway.warehouse_id)
        .join(Location, Location.id == Putaway.location_id)
        .join(Product, Product.id == Putaway.product_id)
        .outerjoin(ProductSku, ProductSku.id == Putaway.sku_id)
        .outerjoin(StockIn, and_(
            Putaway.source_type == "STOCK_IN", StockIn.id == Putaway.source_id,
        ))
        .outerjoin(TransferIn, and_(
            Putaway.source_type == "TRANSFER_IN", TransferIn.id == Putaway.source_id,
        ))
        .where(*conditions)
        .order_by(Putaway.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.putaway({
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_no": source_no_value,
        "warehouse_id": item.warehouse_id,
        "warehouse_name": warehouse_name,
        "location_id": item.location_id,
        "location_code": location_code,
        "product_id": item.product_id,
        "product_name": product_name,
        "product_code": product_code,
        "sku_id": item.sku_id,
        "spec_name": spec_name,
        "spec_code": spec_code,
        "qty": item.qty,
        "created_at": item.created_at,
    }, access) for (
        item, source_no_value, warehouse_name, location_code,
        product_name, product_code, spec_name, spec_code,
    ) in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _stock_in_metrics(db: Session, stock_in_ids: list[int]) -> dict[int, tuple[int, Decimal]]:
    if not stock_in_ids:
        return {}
    item_counts = db.execute(
        select(StockInItem.stock_in_id, func.count(StockInItem.id))
        .where(StockInItem.stock_in_id.in_(stock_in_ids))
        .group_by(StockInItem.stock_in_id)
    ).all()
    putaway_totals = db.execute(
        select(Putaway.source_id, func.sum(Putaway.qty))
        .where(
            Putaway.source_type == "STOCK_IN",
            Putaway.source_id.in_(stock_in_ids),
        )
        .group_by(Putaway.source_id)
    ).all()
    result = {
        int(stock_in_id): (int(count), Decimal("0.000"))
        for stock_in_id, count in item_counts
    }
    for source_id, qty in putaway_totals:
        count, _ = result.get(int(source_id), (0, Decimal("0.000")))
        result[int(source_id)] = (count, Decimal(qty or 0))
    for stock_in_id in stock_in_ids:
        result.setdefault(stock_in_id, (0, Decimal("0.000")))
    return result


def _search_stock_ins(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "stock_documents")
    conditions = scope_conditions(db, StockIn.created_by)
    if args.warehouse_id is not None:
        conditions.append(StockIn.warehouse_id == args.warehouse_id)
    if args.purchase_order_id is not None:
        conditions.append(StockIn.purchase_order_id == args.purchase_order_id)
    if args.status is not None:
        conditions.append(StockIn.status == args.status)
    if args.audit_status is not None:
        conditions.append(StockIn.audit_status == args.audit_status)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(StockIn.stock_in_no.like(f"%{keyword}%", escape="\\"))
    total = int(db.scalar(select(func.count(StockIn.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(StockIn, Warehouse.name)
        .join(Warehouse, Warehouse.id == StockIn.warehouse_id)
        .where(*conditions)
        .order_by(StockIn.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    metrics = _stock_in_metrics(db, [row.id for row, _ in rows])
    data = [query_projection.stock_document(_stock_document_data(
        row, warehouse_name=warehouse_name, item_count=metrics[row.id][0],
        putaway_qty=metrics[row.id][1],
    ), access) for row, warehouse_name in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_stock_in(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_documents")
    row = stock_in_service.get_stock_in_or_404(db, args.stock_in_id)
    warehouse_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.warehouse_id))
    metrics = _stock_in_metrics(db, [row.id])[row.id]
    data = query_projection.stock_document(_stock_document_data(
        row, warehouse_name=warehouse_name, item_count=metrics[0], putaway_qty=metrics[1],
    ), access)
    return ToolOutput(data, {"stock_in_id": args.stock_in_id}, 1)


def _list_stock_in_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_documents")
    stock_in_service.get_stock_in_or_404(db, args.stock_in_id)
    conditions = [StockInItem.stock_in_id == args.stock_in_id]
    total = int(db.scalar(select(func.count(StockInItem.id)).where(*conditions)) or 0)
    putaway_qty = (
        select(func.coalesce(func.sum(Putaway.qty), 0))
        .where(
            Putaway.source_type == "STOCK_IN",
            Putaway.source_id == StockInItem.stock_in_id,
            Putaway.product_id == StockInItem.product_id,
            Putaway.sku_id.is_not_distinct_from(StockInItem.sku_id),
        )
        .correlate(StockInItem)
        .scalar_subquery()
    )
    rows = db.execute(
        select(
            StockInItem,
            Product.code,
            Product.name,
            ProductSku.spec_code,
            ProductSku.spec_name,
            putaway_qty.label("putaway_qty"),
        )
        .join(Product, Product.id == StockInItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockInItem.sku_id)
        .where(*conditions)
        .order_by(StockInItem.id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_document_item({
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product_code,
        "product_name": product_name,
        "sku_id": item.sku_id,
        "spec_code": spec_code,
        "spec_name": spec_name,
        "qty": item.qty,
        "putaway_qty": Decimal(applied or 0),
        "pending_putaway_qty": max(Decimal(item.qty) - Decimal(applied or 0), Decimal("0.000")),
        "cost_price": item.cost_price,
        "amount": item.amount,
    }, access) for item, product_code, product_name, spec_code, spec_name, applied in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_stock_outs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "stock_documents")
    conditions = scope_conditions(db, StockOut.created_by)
    if args.warehouse_id is not None:
        conditions.append(StockOut.warehouse_id == args.warehouse_id)
    if args.purchase_return_id is not None:
        conditions.append(StockOut.purchase_return_id == args.purchase_return_id)
    if args.status is not None:
        conditions.append(StockOut.status == args.status)
    if args.audit_status is not None:
        conditions.append(StockOut.audit_status == args.audit_status)
    if args.off_shelf_status is not None:
        conditions.append(StockOut.off_shelf_status == args.off_shelf_status)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(StockOut.stock_out_no.like(f"%{keyword}%", escape="\\"))
    total = int(db.scalar(select(func.count(StockOut.id)).where(*conditions)) or 0)
    item_count = (
        select(func.count(StockOutItem.id))
        .where(StockOutItem.stock_out_id == StockOut.id)
        .correlate(StockOut)
        .scalar_subquery()
    )
    rows = db.execute(
        select(StockOut, Warehouse.name, item_count.label("item_count"))
        .join(Warehouse, Warehouse.id == StockOut.warehouse_id)
        .where(*conditions)
        .order_by(StockOut.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_document(_stock_document_data(
        row, warehouse_name=warehouse_name, item_count=int(count or 0),
    ), access) for row, warehouse_name, count in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_stock_out(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_documents")
    row = stock_out_service.get_stock_out_or_404(db, args.stock_out_id)
    warehouse_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.warehouse_id))
    item_count = int(db.scalar(
        select(func.count(StockOutItem.id)).where(StockOutItem.stock_out_id == row.id)
    ) or 0)
    data = query_projection.stock_document(_stock_document_data(
        row, warehouse_name=warehouse_name, item_count=item_count,
    ), access)
    return ToolOutput(data, {"stock_out_id": args.stock_out_id}, 1)


def _list_stock_out_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_documents")
    stock_out_service.get_stock_out_or_404(db, args.stock_out_id)
    conditions = [StockOutItem.stock_out_id == args.stock_out_id]
    total = int(db.scalar(select(func.count(StockOutItem.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(
            StockOutItem.id,
            StockOutItem.product_id,
            Product.code,
            Product.name,
            StockOutItem.sku_id,
            ProductSku.spec_code,
            ProductSku.spec_name,
            StockOutItem.location_id,
            Location.code,
            StockOutItem.qty,
        )
        .join(Product, Product.id == StockOutItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockOutItem.sku_id)
        .outerjoin(Location, Location.id == StockOutItem.location_id)
        .where(*conditions)
        .order_by(StockOutItem.id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_document_item({
        "id": row[0],
        "product_id": row[1],
        "product_code": row[2],
        "product_name": row[3],
        "sku_id": row[4],
        "spec_code": row[5],
        "spec_name": row[6],
        "location_id": row[7],
        "location_code": row[8],
        "qty": row[9],
    }, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_stock_summary(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    stock_conditions = []
    if args.warehouse_id is not None:
        stock_conditions.append(Location.warehouse_id == args.warehouse_id)
    if args.product_id is not None:
        stock_conditions.append(Product.id == args.product_id)
    if args.sku_id is not None:
        stock_conditions.append(ProductSku.id == args.sku_id)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{keyword}%"
        stock_conditions.append(or_(
            Product.code.like(like, escape="\\"),
            Product.name.like(like, escape="\\"),
            ProductSku.spec_code.like(like, escape="\\"),
            ProductSku.spec_name.like(like, escape="\\"),
        ))
    stock_groups = (
        select(
            Location.warehouse_id.label("warehouse_id"),
            Product.id.label("product_id"),
            ProductSku.id.label("sku_id"),
            func.sum(StockLocation.qty).label("total_qty"),
            func.sum(case((StockLocation.status == "NORMAL", StockLocation.qty), else_=0)).label("normal_qty"),
            func.sum(case((StockLocation.status == "DEFECTIVE", StockLocation.qty), else_=0)).label("defective_qty"),
        )
        .join(Location, Location.id == StockLocation.location_id)
        .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
        .join(Product, Product.id == ProductSku.product_id)
        .where(*stock_conditions)
        .group_by(Location.warehouse_id, Product.id, ProductSku.id)
        .subquery()
    )
    stock_out_totals = (
        select(
            StockOut.warehouse_id.label("warehouse_id"),
            StockOutItem.product_id.label("product_id"),
            StockOutItem.sku_id.label("sku_id"),
            func.sum(StockOutItem.qty).label("qty"),
        )
        .join(StockOut, StockOut.id == StockOutItem.stock_out_id)
        .where(
            StockOut.status.in_(["PENDING", "APPROVED"]),
            StockOut.off_shelf_status != "DONE",
        )
        .group_by(StockOut.warehouse_id, StockOutItem.product_id, StockOutItem.sku_id)
        .subquery()
    )
    transfer_out_totals = (
        select(
            TransferOut.warehouse_id.label("warehouse_id"),
            TransferOutItem.product_id.label("product_id"),
            TransferOutItem.sku_id.label("sku_id"),
            func.sum(TransferOutItem.qty).label("qty"),
        )
        .join(TransferOut, TransferOut.id == TransferOutItem.transfer_out_id)
        .where(TransferOut.status.in_(["PENDING", "APPROVED"]))
        .group_by(TransferOut.warehouse_id, TransferOutItem.product_id, TransferOutItem.sku_id)
        .subquery()
    )
    warning_totals = (
        select(
            StockWarning.product_id.label("product_id"),
            StockWarning.sku_id.label("sku_id"),
            func.max(StockWarning.warn_qty).label("warn_qty"),
        )
        .group_by(StockWarning.product_id, StockWarning.sku_id)
        .subquery()
    )
    total_qty = stock_groups.c.total_qty
    occupied = (
        func.coalesce(stock_out_totals.c.qty, 0)
        + func.coalesce(transfer_out_totals.c.qty, 0)
    )
    warn_qty = func.coalesce(warning_totals.c.warn_qty, Product.warn_stock, 0)
    is_below_warning = total_qty < warn_qty
    cost_price = (
        case(
            (ProductSku.cost_price.is_not(None), ProductSku.cost_price),
            else_=Product.cost_price,
        )
        if "*" in access.permissions or "module:finance" in access.permissions
        else literal(None)
    )
    query = (
        select(
            stock_groups.c.warehouse_id,
            Warehouse.name.label("warehouse_name"),
            stock_groups.c.product_id,
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            stock_groups.c.sku_id,
            ProductSku.spec_code,
            ProductSku.spec_name,
            stock_groups.c.normal_qty,
            stock_groups.c.defective_qty,
            total_qty,
            occupied.label("occupied_qty"),
            (total_qty - occupied).label("available_qty"),
            case((occupied > total_qty, occupied - total_qty), else_=0).label("shortage_qty"),
            warn_qty.label("warn_qty"),
            is_below_warning.label("is_below_warning"),
            cost_price.label("cost_price"),
        )
        .select_from(stock_groups)
        .join(Warehouse, Warehouse.id == stock_groups.c.warehouse_id)
        .join(Product, Product.id == stock_groups.c.product_id)
        .join(ProductSku, ProductSku.id == stock_groups.c.sku_id)
        .outerjoin(warning_totals, and_(
            warning_totals.c.product_id == stock_groups.c.product_id,
            warning_totals.c.sku_id == stock_groups.c.sku_id,
        ))
        .outerjoin(stock_out_totals, and_(
            stock_out_totals.c.warehouse_id == stock_groups.c.warehouse_id,
            stock_out_totals.c.product_id == stock_groups.c.product_id,
            stock_out_totals.c.sku_id == stock_groups.c.sku_id,
        ))
        .outerjoin(transfer_out_totals, and_(
            transfer_out_totals.c.warehouse_id == stock_groups.c.warehouse_id,
            transfer_out_totals.c.product_id == stock_groups.c.product_id,
            transfer_out_totals.c.sku_id == stock_groups.c.sku_id,
        ))
    )
    if args.below_warning is not None:
        query = query.where(is_below_warning.is_(args.below_warning))
    base = query.subquery()
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(base.c.warehouse_id, base.c.product_id, base.c.sku_id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = []
    for row in rows:
        projection = {
            "warehouse_id": row.warehouse_id,
            "warehouse_name": row.warehouse_name,
            "product_id": row.product_id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "sku_id": row.sku_id,
            "spec_code": row.spec_code,
            "spec_name": row.spec_name,
            "normal_qty": row.normal_qty,
            "defective_qty": row.defective_qty,
            "total_qty": row.total_qty,
            "occupied_qty": row.occupied_qty,
            "available_qty": row.available_qty,
            "shortage_qty": row.shortage_qty,
            "warn_qty": row.warn_qty,
            "is_below_warning": row.is_below_warning,
        }
        if row.cost_price is not None:
            projection["cost_price"] = row.cost_price
            projection["stock_cost"] = round_money(Decimal(row.total_qty) * Decimal(row.cost_price))
        data.append(query_projection.stock_summary(projection, access))
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_stock_locations(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    conditions = []
    if args.warehouse_id is not None:
        conditions.append(Location.warehouse_id == args.warehouse_id)
    if args.location_id is not None:
        conditions.append(Location.id == args.location_id)
    if args.product_id is not None:
        conditions.append(Product.id == args.product_id)
    if args.sku_id is not None:
        conditions.append(ProductSku.id == args.sku_id)
    base = (
        select(
            StockLocation.id.label("stock_location_id"),
            Location.warehouse_id,
            Warehouse.name.label("warehouse_name"),
            Location.id.label("location_id"),
            Location.code.label("location_code"),
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            ProductSku.id.label("sku_id"),
            ProductSku.spec_code,
            ProductSku.spec_name,
            StockLocation.status,
            StockLocation.qty,
        )
        .join(Location, Location.id == StockLocation.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
        .join(Product, Product.id == ProductSku.product_id)
        .where(*conditions)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(
            base.c.warehouse_id, base.c.product_id, base.c.sku_id,
            base.c.location_id,
        )
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_location(dict(row._mapping), access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_stock_warnings(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    current_qty = func.coalesce(func.sum(StockLocation.qty), 0)
    warn_qty = func.coalesce(Product.warn_stock, 0)
    base_query = (
        select(
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            ProductSku.id.label("sku_id"),
            ProductSku.spec_code,
            ProductSku.spec_name,
            current_qty.label("current_qty"),
            warn_qty.label("warn_qty"),
            (current_qty < warn_qty).label("is_below_warning"),
        )
        .join(ProductSku, ProductSku.product_id == Product.id)
        .outerjoin(StockLocation, StockLocation.product_sku_id == ProductSku.id)
        .group_by(
            Product.id, Product.code, Product.name, Product.warn_stock,
            ProductSku.id, ProductSku.spec_code, ProductSku.spec_name,
        )
    )
    if args.only_below:
        base_query = base_query.having(current_qty < warn_qty)
    base = base_query.subquery()
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(base.c.product_id, base.c.sku_id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_warning(dict(row._mapping), access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_stock_costs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    can_see_money = "*" in access.permissions or "module:finance" in access.permissions
    raw_cost = (
        case((ProductSku.cost_price.is_not(None), ProductSku.cost_price), else_=Product.cost_price)
        if can_see_money else literal(None)
    )
    qty = func.coalesce(func.sum(StockLocation.qty), 0)
    base = (
        select(
            Product.id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            ProductSku.id.label("sku_id"),
            ProductSku.spec_code,
            ProductSku.spec_name,
            qty.label("qty"),
            raw_cost.label("cost_price"),
        )
        .join(ProductSku, ProductSku.product_id == Product.id)
        .outerjoin(StockLocation, StockLocation.product_sku_id == ProductSku.id)
        .group_by(
            Product.id, Product.code, Product.name, Product.cost_price,
            ProductSku.id, ProductSku.spec_code, ProductSku.spec_name,
            ProductSku.cost_price,
        )
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(base.c.product_id, base.c.sku_id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = []
    for row in rows:
        projection = dict(row._mapping)
        if row.cost_price is not None:
            projection["total_cost"] = round_money(Decimal(row.qty or 0) * Decimal(row.cost_price))
        data.append(query_projection.stock_cost(projection, access))
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _search_location_adjusts(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    conditions = []
    if args.warehouse_id is not None:
        conditions.append(LocationAdjust.warehouse_id == args.warehouse_id)
    if args.status is not None:
        conditions.append(LocationAdjust.status == args.status)
    if args.audit_status is not None:
        conditions.append(LocationAdjust.audit_status == args.audit_status)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(LocationAdjust.adjust_no.like(f"%{keyword}%", escape="\\"))
    total = int(db.scalar(select(func.count(LocationAdjust.id)).where(*conditions)) or 0)
    item_count = (
        select(func.count(LocationAdjustItem.id))
        .where(LocationAdjustItem.adjust_id == LocationAdjust.id)
        .correlate(LocationAdjust)
        .scalar_subquery()
    )
    rows = db.execute(
        select(LocationAdjust, Warehouse.name, item_count.label("item_count"))
        .join(Warehouse, Warehouse.id == LocationAdjust.warehouse_id)
        .where(*conditions)
        .order_by(LocationAdjust.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.location_adjust({
        "id": row.id,
        "adjust_no": row.adjust_no,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "status": row.status,
        "audit_status": row.audit_status,
        "item_count": int(count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access) for row, warehouse_name, count in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_location_adjust(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    row = stock_query_service.get_location_adjust_or_404(db, args.adjust_id)
    warehouse_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.warehouse_id))
    item_count = int(db.scalar(
        select(func.count(LocationAdjustItem.id)).where(LocationAdjustItem.adjust_id == row.id)
    ) or 0)
    data = query_projection.location_adjust({
        "id": row.id,
        "adjust_no": row.adjust_no,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "status": row.status,
        "audit_status": row.audit_status,
        "item_count": item_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)
    return ToolOutput(data, {"adjust_id": args.adjust_id}, 1)


def _list_location_adjust_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_inventory")
    stock_query_service.get_location_adjust_or_404(db, args.adjust_id)
    from sqlalchemy.orm import aliased
    from_location = aliased(Location)
    to_location = aliased(Location)
    conditions = [LocationAdjustItem.adjust_id == args.adjust_id]
    total = int(db.scalar(select(func.count(LocationAdjustItem.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(
            LocationAdjustItem.id,
            LocationAdjustItem.product_id,
            Product.code,
            Product.name,
            LocationAdjustItem.sku_id,
            ProductSku.spec_code,
            ProductSku.spec_name,
            LocationAdjustItem.from_location_id,
            from_location.code,
            LocationAdjustItem.to_location_id,
            to_location.code,
            LocationAdjustItem.qty,
        )
        .join(Product, Product.id == LocationAdjustItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == LocationAdjustItem.sku_id)
        .join(from_location, from_location.id == LocationAdjustItem.from_location_id)
        .join(to_location, to_location.id == LocationAdjustItem.to_location_id)
        .where(*conditions)
        .order_by(LocationAdjustItem.id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.location_adjust_item({
        "id": row[0],
        "product_id": row[1],
        "product_code": row[2],
        "product_name": row[3],
        "sku_id": row[4],
        "spec_code": row[5],
        "spec_name": row[6],
        "from_location_id": row[7],
        "from_location_code": row[8],
        "to_location_id": row[9],
        "to_location_code": row[10],
        "qty": row[11],
    }, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _stock_check_summaries(db: Session, check_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not check_ids:
        return {}
    actual = StockCheckItem.actual_qty
    rows = db.execute(
        select(
            StockCheckItem.check_id,
            func.count(StockCheckItem.id),
            func.coalesce(func.sum(case((actual.is_not(None), 1), else_=0)), 0),
            func.coalesce(func.sum(StockCheckItem.current_qty), 0),
            func.coalesce(func.sum(actual), 0),
            func.coalesce(func.sum(case((and_(actual.is_not(None), actual > StockCheckItem.current_qty), actual - StockCheckItem.current_qty), else_=0)), 0),
            func.coalesce(func.sum(case((and_(actual.is_not(None), actual < StockCheckItem.current_qty), StockCheckItem.current_qty - actual), else_=0)), 0),
        )
        .where(StockCheckItem.check_id.in_(check_ids))
        .group_by(StockCheckItem.check_id)
    ).all()
    summaries = {
        int(row[0]): {
            "item_count": int(row[1]),
            "result_entered_count": int(row[2]),
            "current_total_qty": row[3],
            "actual_total_qty": row[4],
            "gain_total_qty": row[5],
            "loss_total_qty": row[6],
        }
        for row in rows
    }
    target_ids = [str(check_id) for check_id in check_ids]
    imported_ids = db.scalars(
        select(AuditLog.target_id)
        .where(
            AuditLog.target_type == "stock_check",
            AuditLog.action == "IMPORT_RESULT",
            AuditLog.target_id.in_(target_ids),
        )
        .distinct()
    ).all()
    imported = {int(target_id) for target_id in imported_ids if target_id and target_id.isdigit()}
    for check_id in check_ids:
        summaries.setdefault(check_id, {
            "item_count": 0,
            "result_entered_count": 0,
            "current_total_qty": Decimal("0.000"),
            "actual_total_qty": Decimal("0.000"),
            "gain_total_qty": Decimal("0.000"),
            "loss_total_qty": Decimal("0.000"),
        })["has_imported_result"] = check_id in imported
    return summaries


def _stock_check_summary_data(
    check: StockCheck, warehouse_name: str | None, summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": check.id,
        "check_no": check.check_no,
        "warehouse_id": check.warehouse_id,
        "warehouse_name": warehouse_name,
        "status": check.status,
        "audit_status": check.audit_status,
        **summary,
        "created_at": check.created_at,
        "updated_at": check.updated_at,
    }


def _search_stock_checks(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "stock_checks")
    conditions = scope_conditions(db, StockCheck.created_by)
    if args.warehouse_id is not None:
        conditions.append(StockCheck.warehouse_id == args.warehouse_id)
    if args.status is not None:
        conditions.append(StockCheck.status == args.status)
    if args.audit_status is not None:
        conditions.append(StockCheck.audit_status == args.audit_status)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(StockCheck.check_no.like(f"%{keyword}%", escape="\\"))

    total = int(db.scalar(select(func.count(StockCheck.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(StockCheck, Warehouse.name)
        .join(Warehouse, Warehouse.id == StockCheck.warehouse_id)
        .where(*conditions)
        .order_by(StockCheck.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    summaries = _stock_check_summaries(db, [check.id for check, _ in rows])
    data = [
        query_projection.stock_check(
            _stock_check_summary_data(check, warehouse_name, summaries[check.id]),
            access,
        )
        for check, warehouse_name in rows
    ]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_stock_check(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_checks")
    check = stock_check_service.get_stock_check_or_404(db, args.check_id)
    warehouse_name = db.scalar(
        select(Warehouse.name).where(Warehouse.id == check.warehouse_id)
    )
    summary = _stock_check_summaries(db, [check.id])[check.id]
    data = query_projection.stock_check(
        _stock_check_summary_data(check, warehouse_name, summary), access,
    )
    return ToolOutput(data, {"check_id": args.check_id}, 1)


def _list_stock_check_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_checks")
    stock_check_service.get_stock_check_or_404(db, args.check_id)
    conditions = [StockCheckItem.check_id == args.check_id]
    total = int(db.scalar(
        select(func.count(StockCheckItem.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        select(
            StockCheckItem.id,
            StockCheckItem.product_id,
            Product.code,
            Product.name,
            StockCheckItem.sku_id,
            ProductSku.spec_code,
            ProductSku.spec_name,
            StockCheckItem.location_id,
            Location.code,
            StockCheckItem.current_qty,
            StockCheckItem.actual_qty,
        )
        .join(Product, Product.id == StockCheckItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockCheckItem.sku_id)
        .outerjoin(Location, Location.id == StockCheckItem.location_id)
        .where(*conditions)
        .order_by(
            StockCheckItem.location_id,
            StockCheckItem.product_id,
            StockCheckItem.sku_id,
            StockCheckItem.id,
        )
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [
        query_projection.stock_check_item({
            "id": row[0],
            "product_id": row[1],
            "product_code": row[2],
            "product_name": row[3],
            "sku_id": row[4],
            "spec_code": row[5],
            "spec_name": row[6],
            "location_id": row[7],
            "location_code": row[8],
            "current_qty": row[9],
            "actual_qty": row[10],
            "difference_qty": (
                row[10] - row[9] if row[10] is not None else None
            ),
        }, access)
        for row in rows
    ]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _parse_business_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_iso_datetime(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return parsed


def _parse_report_datetime(value: str | None) -> datetime | None:
    """Mirror metrics_service REST report filters, which compare UTC-naive timestamps."""
    if value is None:
        return None
    parsed = _parse_iso_datetime(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_iso_datetime(value: str) -> datetime:
    from app.core.errors import BizException, ErrorCode
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "日期时间须为ISO 8601格式") from exc
    return parsed


def _search_stock_flows(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "stock_flows")
    start_time = _parse_business_datetime(filters.get("start_time"))
    end_time = _parse_business_datetime(filters.get("end_time"))
    if start_time and end_time and start_time > end_time:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    query_filters = {**filters, "start_time": start_time, "end_time": end_time}
    result = stock_query_service.list_stock_flows(db, **query_filters)
    data = [query_projection.stock_flow(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _search_purchase_prepays(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_prepays")
    result = prepay_service.list_prepays(db, **filters)
    data = [query_projection.purchase_prepay(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_purchase_prepay(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_prepays")
    prepay = prepay_service.get_prepay_or_404(db, args.prepay_id)
    detail = prepay_service.prepay_dict(
        db, prepay, with_approval=False, with_voucher=False,
    )
    data = query_projection.purchase_prepay(detail, access)
    return ToolOutput(data, {"prepay_id": args.prepay_id}, 1)


def _search_purchase_invoices(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "purchase_invoices")
    result = purchase_invoice_service.list_invoices(db, **filters)
    data = [query_projection.purchase_invoice(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_purchase_invoice(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_invoices")
    invoice = purchase_invoice_service.get_invoice_or_404(db, args.invoice_id)
    supplier_name = db.scalar(
        select(Supplier.name).where(Supplier.id == invoice.supplier_id)
    )
    tax_amount = invoice.tax_amount or Decimal("0.00")
    type_config = purchase_invoice_service.INVOICE_TYPES.get(
        invoice.invoice_type, {},
    )
    detail = {
        "id": invoice.id, "invoice_no": invoice.invoice_no,
        "supplier_id": invoice.supplier_id, "supplier_name": supplier_name,
        "invoice_type": invoice.invoice_type,
        "invoice_type_name": type_config.get("name"),
        "tax_rate": invoice.tax_rate, "amount": invoice.amount,
        "tax_amount": tax_amount,
        "net_amount": round_money(invoice.amount - tax_amount),
        "write_off_amount": invoice.write_off_amount,
        "write_off_status": invoice.write_off_status,
        "business_type": invoice.business_type, "status": invoice.status,
        "audit_status": invoice.audit_status,
        "cost_accounted_at": invoice.cost_accounted_at,
        "created_at": invoice.created_at, "updated_at": invoice.updated_at,
    }
    data = query_projection.purchase_invoice(detail, access)
    return ToolOutput(data, {"invoice_id": args.invoice_id}, 1)


def _get_purchase_invoice_cost_detail(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "purchase_invoices")
    # Service 会再次校验父发票本人/合法审批可见范围。
    rows, total = purchase_invoice_service.get_stock_in_cost_detail_page(
        db, args.invoice_id, page=args.page, page_size=args.page_size,
    )
    data = [
        query_projection.purchase_invoice_cost(row, access)
        for row in rows
    ]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(
        data,
        {"invoice_id": args.invoice_id, "page": args.page, "page_size": args.page_size},
        len(data),
        _page(page),
    )


def _list_invoices(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "invoice_management")
    conditions = [
        Invoice.company_id == access.policy.company_id,
        Invoice.document_type == invoice_service.DOCUMENT_INVOICE,
    ]
    if args.invoice_type:
        conditions.append(Invoice.invoice_type == args.invoice_type)
    if args.status:
        conditions.append(Invoice.status == args.status)
    if args.is_received is not None:
        conditions.append(Invoice.is_received.is_(args.is_received))
    if args.keyword:
        like = f"%{args.keyword.strip()}%"
        conditions.append(or_(
            Invoice.invoice_no.like(like),
            Invoice.invoice_title.like(like),
            Invoice.company.like(like),
        ))
    total = int(db.scalar(select(func.count(Invoice.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(Invoice).where(*conditions).order_by(Invoice.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.invoice_management(
        invoice_service.invoice_dict(row), access,
    ) for row in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_invoice(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "invoice_management")
    row = db.scalar(select(Invoice).where(
        Invoice.id == args.invoice_id,
        Invoice.company_id == access.policy.company_id,
        Invoice.document_type == invoice_service.DOCUMENT_INVOICE,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "发票不存在")
    data = query_projection.invoice_management(invoice_service.invoice_dict(row), access)
    return ToolOutput(data, {"invoice_id": args.invoice_id}, 1)


def _list_supplier_account_balances(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "supplier_balances")
    company_id = access.policy.company_id
    conditions = [
        Account.company_id == company_id,
        Supplier.company_id == company_id,
        Account.supplier_id.is_not(None),
    ]
    if args.supplier_id is not None:
        conditions.append(Account.supplier_id == args.supplier_id)
    if args.sync_status:
        conditions.append(Account.kingdee_sync_status == args.sync_status)
    if args.keyword and args.keyword.strip():
        like = f"%{args.keyword.strip()}%"
        conditions.append(or_(Supplier.name.like(like), Supplier.code.like(like)))

    total = int(db.scalar(
        select(func.count(Account.id)).join(Supplier, Supplier.id == Account.supplier_id)
        .where(*conditions)
    ) or 0)
    rows = db.execute(
        select(Account, Supplier).join(Supplier, Supplier.id == Account.supplier_id)
        .where(*conditions).order_by(Account.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.supplier_balance({
        "id": account.id,
        "supplier_id": supplier.id,
        "supplier_code": supplier.code,
        "supplier_name": supplier.name,
        "bank": account.bank,
        "branch": account.branch,
        "bank_no": account.bank_no,
        "account_no": account.account_no,
        "is_default": account.is_default,
        "balance": account.balance,
        "kingdee_sync_status": account.kingdee_sync_status,
        "balance_synced_at": account.balance_synced_at,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }, access) for account, supplier in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_supplier_account_balance(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "supplier_balances")
    account = db.scalar(select(Account).where(
        Account.id == args.account_id,
        Account.company_id == access.policy.company_id,
    ))
    if account is None or account.supplier_id is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商账户不存在")
    row = invoice_service.supplier_account_balance_dict(db, account)
    data = query_projection.supplier_balance(row, access)
    return ToolOutput(data, {"account_id": args.account_id}, 1)


def _search_logistics(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "logistics_catalog")
    company_id = access.policy.company_id
    conditions = [Logistics.company_id == company_id]
    if args.keyword and args.keyword.strip():
        like = f"%{args.keyword.strip()}%"
        conditions.append(or_(Logistics.name.like(like), Logistics.company.like(like)))
    if args.is_enabled is not None:
        conditions.append(Logistics.is_enabled.is_(args.is_enabled))
    total = int(db.scalar(select(func.count(Logistics.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(Logistics).where(*conditions)
        .order_by(Logistics.is_default.desc(), Logistics.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.logistics_carrier({
        "id": row.id, "name": row.name, "company": row.company,
        "contact": row.contact, "phone": row.phone, "address": row.address,
        "is_default": row.is_default, "is_enabled": row.is_enabled,
    }, access) for row in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_logistics(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "logistics_catalog")
    row = db.scalar(select(Logistics).where(
        Logistics.id == args.logistics_id,
        Logistics.company_id == access.policy.company_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "物流方式不存在")
    data = query_projection.logistics_carrier({
        "id": row.id, "name": row.name, "company": row.company,
        "contact": row.contact, "phone": row.phone, "address": row.address,
        "is_default": row.is_default, "is_enabled": row.is_enabled,
    }, access)
    return ToolOutput(data, {"logistics_id": args.logistics_id}, 1)


def _list_freight_templates(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "freight_catalog")
    company_id = access.policy.company_id
    conditions = [FreightTemplate.company_id == company_id]
    if args.logistics_id is not None:
        conditions.append(FreightTemplate.logistics_id == args.logistics_id)
    base_query = select(FreightTemplate, Logistics.name).join(
        Logistics, and_(
            Logistics.id == FreightTemplate.logistics_id,
            Logistics.company_id == company_id,
        ),
    )
    total = int(db.scalar(
        select(func.count(FreightTemplate.id)).join(
            Logistics, and_(
                Logistics.id == FreightTemplate.logistics_id,
                Logistics.company_id == company_id,
            ),
        ).where(*conditions)
    ) or 0)
    rows = db.execute(
        base_query.where(*conditions).order_by(FreightTemplate.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.freight_template({
        "id": row.id, "name": row.name, "logistics_id": row.logistics_id,
        "logistics_name": logistics_name, "charge_type": row.charge_type,
        "first_count": row.first_count, "continue_count": row.continue_count,
        "first_weight": row.first_weight, "continue_weight": row.continue_weight,
        "base_fee": row.base_fee, "continue_fee": row.continue_fee,
        "deduct_weight": row.deduct_weight, "operation_fee": row.operation_fee,
        "consumable_fee": row.consumable_fee, "ewaybill_fee": row.ewaybill_fee,
        "tax_fee": row.tax_fee, "tax_point": row.tax_point,
    }, access) for row, logistics_name in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_freight_template(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "freight_catalog")
    company_id = access.policy.company_id
    row = db.scalar(select(FreightTemplate).where(
        FreightTemplate.id == args.template_id,
        FreightTemplate.company_id == company_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "运费模板不存在")
    logistics_name = db.scalar(
        select(Logistics.name).where(
            Logistics.id == row.logistics_id,
            Logistics.company_id == company_id,
        )
    )
    data = query_projection.freight_template({
        "id": row.id, "name": row.name, "logistics_id": row.logistics_id,
        "logistics_name": logistics_name, "charge_type": row.charge_type,
        "first_count": row.first_count, "continue_count": row.continue_count,
        "first_weight": row.first_weight, "continue_weight": row.continue_weight,
        "base_fee": row.base_fee, "continue_fee": row.continue_fee,
        "deduct_weight": row.deduct_weight, "operation_fee": row.operation_fee,
        "consumable_fee": row.consumable_fee, "ewaybill_fee": row.ewaybill_fee,
        "tax_fee": row.tax_fee, "tax_point": row.tax_point,
    }, access)
    return ToolOutput(data, {"template_id": args.template_id}, 1)


def _calculate_freight(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "freight_catalog")
    result = logistics_service.calculate_freight(
        db, template_id=args.template_id, count=args.count,
        weight=Decimal(str(args.weight)) if args.weight is not None else None,
        company_id=access.policy.company_id,
    )
    data = query_projection.freight_calculation(result, access)
    return ToolOutput(data, args.model_dump(exclude_none=True), 1)


def _match_area_rule(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "area_rule_catalog")
    result = logistics_service.match_area_rule(
        db, logistics_id=args.logistics_id, province=args.province,
        city=args.city,
        weight=Decimal(str(args.weight)) if args.weight is not None else None,
        company_id=access.policy.company_id,
    )
    data = query_projection.area_rule_match(result, access)
    return ToolOutput(data, args.model_dump(exclude_none=True), 1)


def _list_area_rules(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "area_rule_catalog")
    company_id = access.policy.company_id
    conditions = [AreaRule.company_id == company_id]
    if args.logistics_id is not None:
        logistics_service.get_logistics_or_404(
            db, args.logistics_id, company_id=company_id,
        )
        conditions.append(AreaRule.logistics_id == args.logistics_id)
    total = int(db.scalar(select(func.count(AreaRule.id)).where(*conditions)) or 0)
    rows = db.execute(
        select(AreaRule, Logistics.name, FreightTemplate.name)
        .join(Logistics, and_(
            Logistics.id == AreaRule.logistics_id,
            Logistics.company_id == company_id,
        ))
        .outerjoin(FreightTemplate, and_(
            FreightTemplate.id == AreaRule.freight_template_id,
            FreightTemplate.company_id == company_id,
        ))
        .where(*conditions).order_by(AreaRule.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.area_rule({
        "id": rule.id, "logistics_id": rule.logistics_id,
        "logistics_name": logistics_name, "province": rule.province,
        "city": rule.city, "match_weight_min": rule.match_weight_min,
        "match_weight_max": rule.match_weight_max,
        "freight_template_id": rule.freight_template_id,
        "freight_template_name": template_name,
    }, access) for rule, logistics_name, template_name in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_area_rule(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "area_rule_catalog")
    rule = logistics_service.get_area_rule_or_404(
        db, args.rule_id, company_id=access.policy.company_id,
    )
    data = query_projection.area_rule(
        logistics_service.area_rule_dict(
            db, rule, company_id=access.policy.company_id,
        ), access,
    )
    return ToolOutput(data, {"rule_id": args.rule_id}, 1)


def _list_print_templates(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "print_template_catalog")
    company_id = access.policy.company_id
    conditions = [PrintTemplate.company_id == company_id]
    if args.logistics_id is not None:
        logistics_service.get_logistics_or_404(db, args.logistics_id, company_id=company_id)
        conditions.append(PrintTemplate.logistics_id == args.logistics_id)
    if args.template_type:
        conditions.append(PrintTemplate.template_type == args.template_type)
    total = int(db.scalar(
        select(func.count(PrintTemplate.id)).where(*conditions)
    ) or 0)
    rows = db.execute(
        select(PrintTemplate, Logistics.name)
        .outerjoin(Logistics, and_(
            Logistics.id == PrintTemplate.logistics_id,
            Logistics.company_id == company_id,
        ))
        .where(*conditions).order_by(PrintTemplate.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.print_template({
        "id": row.id, "name": row.name, "template_type": row.template_type,
        "logistics_id": row.logistics_id, "logistics_name": logistics_name,
        "content": row.content, "paper_width": row.paper_width,
        "paper_height": row.paper_height, "is_default": row.is_default,
    }, access) for row, logistics_name in rows]
    filters = args.model_dump(exclude_none=True)
    result = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_print_template(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "print_template_catalog")
    company_id = access.policy.company_id
    row = db.scalar(select(PrintTemplate).where(
        PrintTemplate.id == args.template_id,
        PrintTemplate.company_id == company_id,
    ))
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "打印模板不存在")
    logistics_name = (
        db.scalar(select(Logistics.name).where(
            Logistics.id == row.logistics_id,
            Logistics.company_id == company_id,
        ))
        if row.logistics_id is not None else None
    )
    data = query_projection.print_template({
        "id": row.id, "name": row.name, "template_type": row.template_type,
        "logistics_id": row.logistics_id, "logistics_name": logistics_name,
        "content": row.content, "paper_width": row.paper_width,
        "paper_height": row.paper_height, "is_default": row.is_default,
    }, access)
    return ToolOutput(data, {"template_id": args.template_id}, 1)


def _get_print_batch(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "print_batch")
    batch = shared_queries.print_batch(db, batch_no=args.batch_no)
    data = query_projection.print_batch(batch, access)
    return ToolOutput(
        data, {"batch_no": args.batch_no},
        len(data.get("orders", [])),
    )


def _get_account_subject_meta(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    args.model_dump()
    access = authorize_query(db, "finance_accounting")
    data = query_projection.account_subject_meta(
        account_subject_service.list_meta(), access,
    )
    return ToolOutput(data, {}, 1)


def _search_account_subjects(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "finance_accounting")
    result = account_subject_service.list_subjects(db, **filters)
    data = [query_projection.account_subject(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_account_subject(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "finance_accounting")
    subject = account_subject_service.get_subject_or_404(db, args.subject_id)
    data = query_projection.account_subject(
        account_subject_service.subject_dict(db, subject), access,
    )
    return ToolOutput(data, {"subject_id": args.subject_id}, 1)


def _search_project_categories(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump()
    access = authorize_query(db, "finance_accounting")
    total = db.scalar(select(func.count(ProjectCategory.id))) or 0
    rows = db.scalars(
        select(ProjectCategory).order_by(ProjectCategory.code, ProjectCategory.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = [query_projection.project_category({
        "id": row.id, "code": row.code, "name": row.name,
        "remark": row.remark, "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access) for row in rows]
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _get_project_category(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "finance_accounting")
    category = account_subject_service.get_project_category_or_404(
        db, args.category_id,
    )
    data = query_projection.project_category(
        account_subject_service.project_category_dict(category), access,
    )
    return ToolOutput(data, {"category_id": args.category_id}, 1)


def _search_vouchers(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "finance_vouchers")
    start = _parse_business_datetime(args.business_date_from)
    end = _parse_business_datetime(args.business_date_to)
    if start and end and start > end:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "凭证开始日期不能晚于结束日期")
    conditions = list(scope_conditions(db, Voucher.created_by))
    if args.status is not None:
        conditions.append(Voucher.status == args.status)
    if start is not None:
        conditions.append(Voucher.business_date >= start)
    if end is not None:
        conditions.append(Voucher.business_date <= end)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{keyword}%"
        conditions.append(or_(
            Voucher.voucher_no.like(pattern, escape="\\"),
            Voucher.voucher_word.like(pattern, escape="\\"),
        ))
    total = int(db.scalar(select(func.count(Voucher.id)).where(*conditions)) or 0)
    item_count = (
        select(func.count(VoucherItem.id))
        .where(VoucherItem.voucher_id == Voucher.id)
        .correlate(Voucher)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Voucher, item_count)
        .where(*conditions)
        .order_by(Voucher.business_date.desc(), Voucher.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.voucher_document({
        "id": row.id,
        "voucher_word": row.voucher_word,
        "voucher_no": row.voucher_no,
        "business_date": row.business_date,
        "record_date": row.record_date,
        "total_debit": row.total_debit,
        "total_credit": row.total_credit,
        "status": row.status,
        "item_count": int(count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access) for row, count in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    filters = args.model_dump(exclude_none=True)
    return ToolOutput(data, filters, len(data), _page(page))


def _get_voucher(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "finance_vouchers")
    try:
        row = voucher_service.get_voucher_or_404(db, args.voucher_id)
    except BizException as exc:
        raise BizException(ErrorCode.FORBIDDEN, "凭证不存在或不可见") from exc
    count = int(db.scalar(
        select(func.count(VoucherItem.id)).where(VoucherItem.voucher_id == row.id)
    ) or 0)
    data = query_projection.voucher_document({
        "id": row.id,
        "voucher_word": row.voucher_word,
        "voucher_no": row.voucher_no,
        "business_date": row.business_date,
        "record_date": row.record_date,
        "total_debit": row.total_debit,
        "total_credit": row.total_credit,
        "status": row.status,
        "item_count": count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)
    return ToolOutput(data, {"voucher_id": args.voucher_id}, 1)


def _list_voucher_items(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "finance_vouchers")
    try:
        voucher_service.get_voucher_or_404(db, args.voucher_id)
    except BizException as exc:
        raise BizException(ErrorCode.FORBIDDEN, "凭证不存在或不可见") from exc
    base = (
        select(
            VoucherItem.id.label("id"),
            VoucherItem.subject_id.label("subject_id"),
            AccountSubject.code.label("subject_code"),
            AccountSubject.name.label("subject_name"),
            VoucherItem.debit.label("debit"),
            VoucherItem.credit.label("credit"),
        )
        .join(AccountSubject, AccountSubject.id == VoucherItem.subject_id)
        .where(VoucherItem.voucher_id == args.voucher_id)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base).order_by(base.c.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.voucher_item(dict(row._mapping), access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(), len(data), _page(page))


def _list_order_flags(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    args.model_dump()
    authorize_query(db, "order_flag_dictionary")
    data = order_service.list_order_flags()
    return ToolOutput(data, {}, len(data))


def _finance_list(db: Session, args: BaseModel, capability: str,
                  service_fn: Any, projector: Any) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, capability)
    result = service_fn(db, **filters)
    data = [projector(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _search_expenses(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _finance_list(db, args, "finance_expenses", finance_doc_service.list_expenses,
                         query_projection.finance_expense)


def _get_expense(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_expenses")
    row = finance_doc_service.get_expense_or_404(db, args.expense_id)
    detail = finance_doc_service.expense_dict(
        db, row, with_approval=False, with_voucher=False,
    )
    data = query_projection.finance_expense(detail, access)
    return ToolOutput(data, {"expense_id": args.expense_id}, 1)


def _search_incomes(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _finance_list(db, args, "finance_incomes", finance_doc_service.list_incomes,
                         query_projection.finance_income)


def _get_income(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_incomes")
    row = finance_doc_service.get_income_or_404(db, args.income_id)
    detail = finance_doc_service.income_dict(
        db, row, with_approval=False, with_voucher=False,
    )
    data = query_projection.finance_income(detail, access)
    return ToolOutput(data, {"income_id": args.income_id}, 1)


def _search_payments(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _finance_list(db, args, "finance_payments", pay_receipt_service.list_payments,
                         query_projection.finance_payment)


def _get_payment(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_payments")
    payment = pay_receipt_service.get_payment_or_404(db, args.payment_id)
    detail = pay_receipt_service.payment_dict(
        db, payment, with_approval=False, with_voucher=False,
    )
    data = query_projection.finance_payment(detail, access)
    return ToolOutput(data, {"payment_id": args.payment_id}, 1)


def _search_receipts(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _finance_list(db, args, "finance_receipts", pay_receipt_service.list_receipts,
                         query_projection.finance_receipt)


def _get_receipt(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_receipts")
    receipt = pay_receipt_service.get_receipt_or_404(db, args.receipt_id)
    detail = pay_receipt_service.receipt_dict(
        db, receipt, with_approval=False, with_voucher=False,
    )
    data = query_projection.finance_receipt(detail, access)
    return ToolOutput(data, {"receipt_id": args.receipt_id}, 1)


def _search_settlements(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "finance_settlements")
    for key in ("settlement_date_from", "settlement_date_to"):
        if key in filters:
            try:
                filters[key] = date.fromisoformat(filters[key])
            except ValueError as exc:
                from app.core.errors import BizException, ErrorCode
                raise BizException(ErrorCode.BAD_REQUEST, "结算日期须为YYYY-MM-DD") from exc
    result = finance_doc_service.list_settlements(db, **filters)
    data = [query_projection.finance_settlement(row, access) for row in result["items"]]
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(result))


def _get_settlement(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "finance_settlements")
    row = finance_doc_service.get_settlement_or_404(db, args.settlement_id)
    detail = finance_doc_service.settlement_dict(db, row, with_voucher=False)
    data = query_projection.finance_settlement(detail, access)
    return ToolOutput(data, {"settlement_id": args.settlement_id}, 1)


def _linked_voucher_output(
    db: Session,
    access: Any,
    *,
    reference: str,
    filters: dict[str, Any],
) -> ToolOutput:
    voucher = voucher_service.get_active_voucher_by_reference(db, reference)
    if voucher is None:
        return ToolOutput(None, filters, 0)
    item_count = int(
        db.scalar(select(func.count(VoucherItem.id)).where(
            VoucherItem.voucher_id == voucher.id,
        )) or 0
    )
    data = query_projection.linked_voucher({
        "id": voucher.id,
        "voucher_word": voucher.voucher_word,
        "voucher_no": voucher.voucher_no,
        "business_date": voucher.business_date,
        "record_date": voucher.record_date,
        "total_debit": voucher.total_debit,
        "total_credit": voucher.total_credit,
        "status": voucher.status,
        "item_count": item_count,
        "created_at": voucher.created_at,
        "updated_at": voucher.updated_at,
    }, access)
    return ToolOutput(data, filters, 1)


def _get_expense_voucher(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_expenses")
    row = finance_doc_service.get_expense_or_404(db, args.expense_id)
    return _linked_voucher_output(
        db, access, reference=f"费用单:{row.expense_no}",
        filters={"expense_id": args.expense_id},
    )


def _get_income_voucher(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_incomes")
    row = finance_doc_service.get_income_or_404(db, args.income_id)
    return _linked_voucher_output(
        db, access, reference=f"收入单:{row.income_no}",
        filters={"income_id": args.income_id},
    )


def _get_payment_voucher(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_payments")
    row = pay_receipt_service.get_payment_or_404(db, args.payment_id)
    return _linked_voucher_output(
        db, access, reference=f"付款单:{row.payment_no}",
        filters={"payment_id": args.payment_id},
    )


def _get_receipt_voucher(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "finance_receipts")
    row = pay_receipt_service.get_receipt_or_404(db, args.receipt_id)
    return _linked_voucher_output(
        db, access, reference=f"收款单:{row.receipt_no}",
        filters={"receipt_id": args.receipt_id},
    )


def _get_settlement_voucher(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "finance_settlements")
    row = finance_doc_service.get_settlement_or_404(db, args.settlement_id)
    return _linked_voucher_output(
        db, access, reference=f"平台结算单:{row.settlement_no}",
        filters={"settlement_id": args.settlement_id},
    )


def _get_prepay_voucher(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "purchase_prepays")
    row = prepay_service.get_prepay_or_404(db, args.prepay_id)
    return _linked_voucher_output(
        db, access, reference=f"采购预付款申请:{row.prepay_no}",
        filters={"prepay_id": args.prepay_id},
    )


def _list_ip_rules(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "company_ip_rules")
    conditions = [IpRule.company_id == access.staff.company_id]
    total = int(db.scalar(select(func.count(IpRule.id)).where(*conditions)) or 0)
    rows = db.scalars(
        select(IpRule).where(*conditions).order_by(IpRule.id.desc())
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.ip_rule({
        "id": row.id, "rule_type": row.rule_type,
        "ip_address": row.ip_address,
        "remark": row.remark, "company_id": row.company_id,
    }, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(), len(data), _page(page))


def _get_ip_rule(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "company_ip_rules")
    row = db.scalar(select(IpRule).where(
        IpRule.id == args.rule_id,
        IpRule.company_id == access.staff.company_id,
    ))
    if row is None:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.NOT_FOUND, "IP规则不存在")
    data = query_projection.ip_rule({
        "id": row.id, "rule_type": row.rule_type,
        "ip_address": row.ip_address,
        "remark": row.remark, "company_id": row.company_id,
    }, access)
    return ToolOutput(data, {"rule_id": args.rule_id}, 1)


def _get_dashboard_overview(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "dashboard")
    today = metrics_service._business_today()
    yesterday = today - timedelta(days=1)
    today_orders = metrics_service._order_period_stats(
        db, start_date=today, end_date=today,
    )
    yesterday_orders = metrics_service._order_period_stats(
        db, start_date=yesterday, end_date=yesterday,
    )
    customers_today = metrics_service._customer_period_count(
        db, start_date=today, end_date=today,
    )
    customers_yesterday = metrics_service._customer_period_count(
        db, start_date=yesterday, end_date=yesterday,
    )
    warning_count = metrics_service.inventory_warning_count(db)
    data = {
        "as_of": today,
        "metrics": [
            metrics_service._metric(
                key="today_order_amount", label="今日订单金额",
                value=today_orders["amount"],
                previous_value=yesterday_orders["amount"], unit="CNY",
            ),
            metrics_service._metric(
                key="today_order_count", label="订单数量",
                value=today_orders["count"],
                previous_value=yesterday_orders["count"], unit="ORDER",
            ),
            metrics_service._metric(
                key="stock_warning_count", label="库存预警",
                value=warning_count, previous_value=None, unit="SKU",
            ),
            metrics_service._metric(
                key="customer_growth", label="客户增长",
                value=customers_today, previous_value=customers_yesterday,
                unit="CUSTOMER",
            ),
        ],
        "sales_trend": metrics_service.sales_trend(
            db, period=args.trend_period, as_of=today,
        ),
    }
    projected = query_projection.dashboard_snapshot(data, access)
    return ToolOutput(projected, {"trend_period": args.trend_period}, 1)


def _assistant_config_data(db: Session, company_id: int) -> dict[str, Any]:
    row = db.scalar(select(AssistantConfig).where(
        AssistantConfig.company_id == company_id,
    ))
    settings = assistant_governance.get_settings()
    if row is None:
        return {
            "enabled": settings.assistant_enabled,
            "api_key_configured": bool(settings.assistant_api_key.strip()),
            "requests_per_minute": 10,
            "concurrent_requests_per_user": 1,
            "daily_requests_per_user": None,
            "monthly_budget": None,
        }
    configured_key = (
        bool(row.api_key_ciphertext)
        if row.api_key_overrides_environment
        else bool(settings.assistant_api_key.strip())
    )
    return {
        "enabled": row.enabled,
        "api_key_configured": configured_key,
        "requests_per_minute": row.requests_per_minute,
        "concurrent_requests_per_user": row.concurrent_requests_per_user,
        "daily_requests_per_user": row.daily_requests_per_user,
        "monthly_budget": str(row.monthly_budget) if row.monthly_budget is not None else None,
    }


def _usage_since(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _usage_summary(db: Session, conditions: list[Any]) -> dict[str, Any]:
    row = db.execute(select(
        func.count(AssistantUsage.id),
        func.coalesce(func.sum(AssistantUsage.prompt_tokens), 0),
        func.coalesce(func.sum(AssistantUsage.completion_tokens), 0),
        func.sum(case((AssistantUsage.total_tokens.is_(None), 1), else_=0)),
        func.coalesce(func.sum(case(
            (AssistantUsage.cost_status == "known", AssistantUsage.estimated_cost),
            else_=0,
        )), 0),
        func.sum(case((AssistantUsage.cost_status == "unknown", 1), else_=0)),
    ).where(*conditions)).one()
    return {
        "request_count": int(row[0] or 0),
        "known_prompt_tokens": int(row[1] or 0),
        "known_completion_tokens": int(row[2] or 0),
        "unknown_token_count": int(row[3] or 0),
        "known_cost": str(Decimal(str(row[4] or 0))),
        "unknown_cost_count": int(row[5] or 0),
    }


def _usage_row_data(row: AssistantUsage, *, staff_name: str | None = None) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "staff_id": row.staff_id,
        "staff_name": staff_name,
        "status": row.status,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "latency_ms": row.latency_ms,
        "cost_status": row.cost_status,
        "estimated_cost": (
            str(row.estimated_cost)
            if row.cost_status == "known" and row.estimated_cost is not None
            else None
        ),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _assistant_usage_query(
    db: Session,
    args: BaseModel,
    *,
    admin: bool,
) -> ToolOutput:
    access = authorize_query(db, "assistant_admin" if admin else "assistant_usage")
    conditions = [
        AssistantUsage.company_id == access.staff.company_id,
        AssistantUsage.started_at >= _usage_since(args.days),
    ]
    if not admin:
        conditions.append(AssistantUsage.staff_id == access.staff.id)
    total = int(db.scalar(select(func.count(AssistantUsage.id)).where(*conditions)) or 0)
    query = select(AssistantUsage, Staff.name).outerjoin(
        Staff, and_(
            Staff.id == AssistantUsage.staff_id,
            Staff.company_id == access.staff.company_id,
        ),
    ).where(*conditions).order_by(
        AssistantUsage.started_at.desc(), AssistantUsage.id.desc(),
    ).offset((args.page - 1) * args.page_size).limit(args.page_size)
    rows = db.execute(query).all()
    items = [
        query_projection.assistant_usage(
            _usage_row_data(row[0], staff_name=row[1] if admin else None), access,
        )
        for row in rows
    ]
    summary = _usage_summary(db, conditions)
    data: dict[str, Any] = {"summary": summary, "items": items}
    if admin:
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None,
        )
        monthly_conditions = [
            AssistantUsage.company_id == access.staff.company_id,
            AssistantUsage.started_at >= month_start,
        ]
        committed = db.scalar(select(func.coalesce(func.sum(case(
            (AssistantUsage.status == "started", AssistantUsage.reserved_cost),
            (AssistantUsage.cost_status == "unknown", AssistantUsage.reserved_cost),
            else_=AssistantUsage.estimated_cost,
        )), 0)).where(*monthly_conditions))
        config = db.scalar(select(AssistantConfig).where(
            AssistantConfig.company_id == access.staff.company_id,
        ))
        summary["monthly_budget"] = (
            str(config.monthly_budget)
            if config and config.monthly_budget is not None else None
        )
        summary["monthly_budget_committed"] = str(Decimal(str(committed or 0)))
    else:
        config = db.scalar(select(AssistantConfig).where(
            AssistantConfig.company_id == access.staff.company_id,
        ))
        data["limits"] = {
            "requests_per_minute": config.requests_per_minute if config else 10,
            "daily_requests_per_user": config.daily_requests_per_user if config else None,
            "concurrent_requests_per_user": config.concurrent_requests_per_user if config else 1,
        }
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(
        data, {"days": args.days, "page": args.page, "page_size": args.page_size},
        len(items), _page(page),
    )


def _get_admin_assistant_config(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "assistant_admin")
    data = query_projection.assistant_admin_config(
        _assistant_config_data(db, access.staff.company_id), access,
    )
    return ToolOutput(data, {}, 1)


def _get_admin_assistant_usage(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    return _assistant_usage_query(db, args, admin=True)


def _get_my_assistant_usage(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    return _assistant_usage_query(db, args, admin=False)


def _list_my_notifications(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "my_notifications")
    try:
        cursor_id = notification_query_service.decode_cursor(
            args.cursor, staff_id=access.staff.id, company_id=access.staff.company_id,
        )
        rows, next_id, has_more = notification_query_service.query_notifications_page(
            db, access.staff, cursor_id=cursor_id, category=args.category,
            read_state=args.read_state, page_size=args.page_size,
        )
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "通知查询游标或筛选条件无效") from exc
    next_cursor = (
        notification_query_service.encode_cursor(
            staff_id=access.staff.id, company_id=access.staff.company_id,
            inbox_id=next_id,
        )
        if next_id is not None else None
    )
    data = {
        "items": [
            notification_query_service.serialize_notification(
                inbox, staff_id=access.staff.id, company_id=access.staff.company_id,
            )
            for inbox, _event in rows
        ],
    }
    page = {"page_size": args.page_size, "has_more": has_more,
            "next_cursor": next_cursor}
    return ToolOutput(data, filters, len(rows), page)


def _count_my_unread_notifications(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    args.model_dump()
    access = authorize_query(db, "my_notifications")
    count = sum(1 for _ in notification_query_service.iter_visible_notifications(
        db, access.staff, direction="desc", read_state="UNREAD",
    ))
    return ToolOutput({"count": count}, {}, 1)


def _get_my_notification(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "my_notifications")
    inbox = notification_query_service.get_visible_notification(
        db, access.staff, args.inbox_id,
    )
    if inbox is None:
        raise BizException(ErrorCode.FORBIDDEN, "消息不存在或已无权查看")
    data = notification_query_service.serialize_notification(
        inbox, staff_id=access.staff.id, company_id=access.staff.company_id,
    )
    return ToolOutput(data, {"inbox_id": args.inbox_id}, 1)


def _get_stock_settings(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    args.model_dump()
    access = authorize_query(db, "stock_settings")
    data = query_projection.stock_settings(shared_queries.stock_settings(db), access)
    return ToolOutput(data, {}, 1)


def _shop_stock_query(args: BaseModel):
    stock_totals = (
        select(
            Location.warehouse_id.label("warehouse_id"),
            StockLocation.product_sku_id.label("sku_id"),
            func.sum(StockLocation.qty).label("stock_qty"),
        )
        .join(Location, Location.id == StockLocation.location_id)
        .where(StockLocation.status == "NORMAL")
        .group_by(Location.warehouse_id, StockLocation.product_sku_id)
        .subquery()
    )
    outbound_totals = (
        select(
            StockOut.warehouse_id.label("warehouse_id"),
            StockOutItem.product_id.label("product_id"),
            StockOutItem.sku_id.label("sku_id"),
            func.sum(StockOutItem.qty).label("qty"),
        )
        .join(StockOut, StockOut.id == StockOutItem.stock_out_id)
        .where(
            StockOut.status.in_(["PENDING", "APPROVED"]),
            StockOut.off_shelf_status != "DONE",
        )
        .group_by(StockOut.warehouse_id, StockOutItem.product_id, StockOutItem.sku_id)
        .subquery()
    )
    transfer_totals = (
        select(
            TransferOut.warehouse_id.label("warehouse_id"),
            TransferOutItem.product_id.label("product_id"),
            TransferOutItem.sku_id.label("sku_id"),
            func.sum(TransferOutItem.qty).label("qty"),
        )
        .join(TransferOut, TransferOut.id == TransferOutItem.transfer_out_id)
        .where(TransferOut.status.in_(["PENDING", "APPROVED"]))
        .group_by(TransferOut.warehouse_id, TransferOutItem.product_id, TransferOutItem.sku_id)
        .subquery()
    )

    filters = []
    if isinstance(args, ShopStockSearchArguments):
        if args.shop_id is not None:
            filters.append(ShopStock.shop_id == args.shop_id)
        if args.warehouse_id is not None:
            filters.append(ShopStock.warehouse_id == args.warehouse_id)
        if args.product_id is not None:
            filters.append(ShopStock.product_id == args.product_id)
        if args.sku_id is not None:
            filters.append(ShopStock.sku_id == args.sku_id)
        if args.is_enabled is not None:
            filters.append(ShopStock.is_enabled.is_(args.is_enabled))
        if args.keyword and args.keyword.strip():
            keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{keyword}%"
            filters.append(or_(
                Shop.name.like(like, escape="\\"),
                Product.code.like(like, escape="\\"),
                Product.name.like(like, escape="\\"),
                ProductSku.spec_code.like(like, escape="\\"),
                ProductSku.spec_name.like(like, escape="\\"),
            ))
    else:
        filters.append(ShopStock.id == args.shop_stock_id)

    matches = (
        select(ShopStock.id.label("shop_stock_id"))
        .join(Shop, Shop.id == ShopStock.shop_id)
        .join(Product, Product.id == ShopStock.product_id)
        .join(ProductSku, ProductSku.id == ShopStock.sku_id)
        .where(*filters)
        .subquery()
    )
    stock_qty = func.coalesce(stock_totals.c.stock_qty, 0)
    occupied_qty = (
        func.coalesce(outbound_totals.c.qty, 0)
        + func.coalesce(transfer_totals.c.qty, 0)
    )
    available_qty = stock_qty - occupied_qty
    shop_available_qty = case(
        (
            and_(ShopStock.exclusive_qty > 0, available_qty > 0),
            case(
                (ShopStock.exclusive_qty <= available_qty, ShopStock.exclusive_qty),
                else_=available_qty,
            ),
        ),
        else_=0,
    )
    return (
        select(
            ShopStock.id.label("id"),
            ShopStock.shop_id.label("shop_id"),
            Shop.name.label("shop_name"),
            Platform.id.label("platform_id"),
            Platform.name.label("platform_name"),
            ShopStock.warehouse_id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            ShopStock.product_id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            ShopStock.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            ShopStock.exclusive_qty.label("exclusive_qty"),
            stock_qty.label("stock_qty"),
            occupied_qty.label("occupied_qty"),
            available_qty.label("available_qty"),
            shop_available_qty.label("shop_available_qty"),
            ShopStock.is_sync.label("is_sync"),
            ShopStock.is_enabled.label("is_enabled"),
            ShopStock.effective_start.label("effective_start"),
            ShopStock.effective_end.label("effective_end"),
        )
        .join(matches, matches.c.shop_stock_id == ShopStock.id)
        .join(Shop, Shop.id == ShopStock.shop_id)
        .join(Platform, Platform.id == Shop.platform_id)
        .join(Warehouse, Warehouse.id == ShopStock.warehouse_id)
        .join(Product, Product.id == ShopStock.product_id)
        .join(ProductSku, ProductSku.id == ShopStock.sku_id)
        .outerjoin(stock_totals, and_(
            stock_totals.c.warehouse_id == ShopStock.warehouse_id,
            stock_totals.c.sku_id == ShopStock.sku_id,
        ))
        .outerjoin(outbound_totals, and_(
            outbound_totals.c.warehouse_id == ShopStock.warehouse_id,
            outbound_totals.c.product_id == ShopStock.product_id,
            outbound_totals.c.sku_id == ShopStock.sku_id,
        ))
        .outerjoin(transfer_totals, and_(
            transfer_totals.c.warehouse_id == ShopStock.warehouse_id,
            transfer_totals.c.product_id == ShopStock.product_id,
            transfer_totals.c.sku_id == ShopStock.sku_id,
        ))
        .order_by(ShopStock.shop_id, ShopStock.warehouse_id, ShopStock.id)
    )


def _shop_stock_projection(row, access) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    is_effective = bool(row.is_enabled)
    if row.effective_start is not None and now < row.effective_start:
        is_effective = False
    if row.effective_end is not None and now > row.effective_end:
        is_effective = False
    return query_projection.shop_stock({
        "id": row.id,
        "shop_id": row.shop_id,
        "shop_name": row.shop_name,
        "platform_id": row.platform_id,
        "platform_name": row.platform_name,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": row.warehouse_name,
        "product_id": row.product_id,
        "product_code": row.product_code,
        "product_name": row.product_name,
        "sku_id": row.sku_id,
        "spec_code": row.spec_code,
        "spec_name": row.spec_name,
        "exclusive_qty": row.exclusive_qty,
        "stock_qty": row.stock_qty,
        "occupied_qty": row.occupied_qty,
        "available_qty": row.available_qty,
        "shop_available_qty": row.shop_available_qty,
        "is_sync": row.is_sync,
        "is_enabled": row.is_enabled,
        "is_effective": is_effective,
        "effective_start": row.effective_start,
        "effective_end": row.effective_end,
    }, access)


def _list_shop_stocks(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "shop_stocks")
    query = _shop_stock_query(args)
    total = int(db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    rows = db.execute(
        query.offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [_shop_stock_projection(row, access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_shop_stock(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "shop_stocks")
    row = db.execute(_shop_stock_query(args)).first()
    if row is None:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.FORBIDDEN, "店铺库存不存在或不可见")
    return ToolOutput(
        _shop_stock_projection(row, access),
        {"shop_stock_id": args.shop_stock_id},
        1,
    )


def _list_stock_settlement_periods(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_settlements")
    total_cost = (
        func.coalesce(
            func.sum(StockSettlement.qty * StockSettlement.cost_price), 0,
        )
        if "*" in access.permissions or "module:finance" in access.permissions
        else literal(None)
    )
    grouped = (
        select(
            StockSettlement.settlement_date.label("settlement_date"),
            func.count(StockSettlement.id).label("item_count"),
            func.coalesce(func.sum(StockSettlement.qty), 0).label("total_qty"),
            total_cost.label("total_cost"),
        )
        .group_by(StockSettlement.settlement_date)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(grouped)) or 0)
    rows = db.execute(
        select(grouped)
        .order_by(grouped.c.settlement_date.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = []
    for row in rows:
        settlement_date = row.settlement_date
        if isinstance(settlement_date, datetime):
            settlement_date = settlement_date.date()
        data.append(query_projection.stock_settlement_period({
            "settlement_date": settlement_date,
            "item_count": int(row.item_count or 0),
            "total_qty": Decimal(row.total_qty or 0),
            "total_cost": round_money(row.total_cost or 0),
        }, access))
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, {}, len(data), _page(page))


def _search_stock_settlements(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_settlements")
    conditions = []
    settlement_day = None
    if args.settlement_date:
        from app.core.errors import BizException, ErrorCode
        try:
            settlement_day = date.fromisoformat(args.settlement_date)
        except ValueError as exc:
            raise BizException(ErrorCode.BAD_REQUEST, "结存日期必须为YYYY-MM-DD") from exc
        conditions.append(
            StockSettlement.settlement_date == datetime.combine(settlement_day, datetime.min.time())
        )
    if args.product_id is not None:
        conditions.append(StockSettlement.product_id == args.product_id)
    if args.sku_id is not None:
        conditions.append(StockSettlement.sku_id == args.sku_id)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{keyword}%"
        conditions.append(or_(
            Product.code.like(like, escape="\\"),
            Product.name.like(like, escape="\\"),
            ProductSku.spec_code.like(like, escape="\\"),
            ProductSku.spec_name.like(like, escape="\\"),
        ))
    cost_price = (
        StockSettlement.cost_price
        if "*" in access.permissions or "module:finance" in access.permissions
        else literal(None)
    )
    base = (
        select(
            StockSettlement.id.label("id"),
            StockSettlement.settlement_date.label("settlement_date"),
            StockSettlement.product_id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            StockSettlement.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            StockSettlement.qty.label("qty"),
            cost_price.label("cost_price"),
        )
        .join(Product, Product.id == StockSettlement.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockSettlement.sku_id)
        .where(*conditions)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(base.c.settlement_date.desc(), base.c.product_id, base.c.sku_id)
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = []
    for row in rows:
        settlement_date = row.settlement_date
        if isinstance(settlement_date, datetime):
            settlement_date = settlement_date.date()
        payload = dict(row._mapping)
        payload["settlement_date"] = settlement_date
        if row.cost_price is not None:
            payload["cost_price"] = round_money(row.cost_price)
            payload["total_cost"] = round_money(Decimal(row.qty or 0) * Decimal(row.cost_price))
        data.append(query_projection.stock_settlement(payload, access))
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _list_stock_cost_rewrite_logs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "stock_settlements")
    start_time = _parse_business_datetime(args.start_time)
    end_time = _parse_business_datetime(args.end_time)
    if start_time is not None and end_time is not None and start_time > end_time:
        from app.core.errors import BizException, ErrorCode
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    conditions = []
    if args.batch_no and args.batch_no.strip():
        conditions.append(StockCostRewriteLog.batch_no == args.batch_no.strip())
    if args.product_id is not None:
        conditions.append(StockCostRewriteLog.product_id == args.product_id)
    if args.sku_id is not None:
        conditions.append(StockCostRewriteLog.sku_id == args.sku_id)
    if args.target_type is not None:
        conditions.append(StockCostRewriteLog.target_type == args.target_type)
    if start_time is not None:
        conditions.append(StockCostRewriteLog.created_at >= start_time)
    if end_time is not None:
        conditions.append(StockCostRewriteLog.created_at <= end_time)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{keyword}%"
        conditions.append(or_(
            StockCostRewriteLog.batch_no.like(like, escape="\\"),
            Product.code.like(like, escape="\\"),
            Product.name.like(like, escape="\\"),
            ProductSku.spec_code.like(like, escape="\\"),
            ProductSku.spec_name.like(like, escape="\\"),
        ))
    can_see_money = "*" in access.permissions or "module:finance" in access.permissions
    money_fields = (
        (
            StockCostRewriteLog.old_cost_price.label("old_cost_price"),
            StockCostRewriteLog.new_cost_price.label("new_cost_price"),
            StockCostRewriteLog.old_total_cost.label("old_total_cost"),
            StockCostRewriteLog.new_total_cost.label("new_total_cost"),
        )
        if can_see_money else (
            literal(None).label("old_cost_price"), literal(None).label("new_cost_price"),
            literal(None).label("old_total_cost"), literal(None).label("new_total_cost"),
        )
    )
    base = (
        select(
            StockCostRewriteLog.id.label("id"),
            StockCostRewriteLog.batch_no.label("batch_no"),
            StockCostRewriteLog.period_start.label("period_start"),
            StockCostRewriteLog.period_end.label("period_end"),
            StockCostRewriteLog.target_type.label("target_type"),
            StockCostRewriteLog.target_id.label("target_id"),
            StockCostRewriteLog.warehouse_id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            StockCostRewriteLog.product_id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            StockCostRewriteLog.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            StockCostRewriteLog.qty.label("qty"),
            StockCostRewriteLog.cost_source.label("cost_source"),
            *money_fields,
            StockCostRewriteLog.created_at.label("created_at"),
        )
        .join(Product, Product.id == StockCostRewriteLog.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockCostRewriteLog.sku_id)
        .outerjoin(Warehouse, Warehouse.id == StockCostRewriteLog.warehouse_id)
        .where(*conditions)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base)
        .order_by(base.c.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.stock_cost_rewrite_log(dict(row._mapping), access) for row in rows]
    filters = args.model_dump(exclude_none=True)
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(page))


def _search_transfers(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "transfer_queries")
    conditions = list(scope_conditions(db, Transfer.created_by))
    if args.from_warehouse_id is not None:
        conditions.append(Transfer.from_warehouse_id == args.from_warehouse_id)
    if args.to_warehouse_id is not None:
        conditions.append(Transfer.to_warehouse_id == args.to_warehouse_id)
    if args.status is not None:
        conditions.append(Transfer.status == args.status)
    if args.audit_status is not None:
        conditions.append(Transfer.audit_status == args.audit_status)
    if args.keyword and args.keyword.strip():
        keyword = args.keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(Transfer.transfer_no.like(f"%{keyword}%", escape="\\"))
    total = int(db.scalar(select(func.count(Transfer.id)).where(*conditions)) or 0)
    from_warehouse = aliased(Warehouse)
    to_warehouse = aliased(Warehouse)
    item_count = (
        select(func.count(TransferItem.id))
        .where(TransferItem.transfer_id == Transfer.id)
        .correlate(Transfer)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Transfer, from_warehouse.name, to_warehouse.name, item_count)
        .join(from_warehouse, from_warehouse.id == Transfer.from_warehouse_id)
        .join(to_warehouse, to_warehouse.id == Transfer.to_warehouse_id)
        .where(*conditions)
        .order_by(Transfer.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.transfer_document({
        "id": row.id,
        "transfer_no": row.transfer_no,
        "from_warehouse_id": row.from_warehouse_id,
        "from_warehouse_name": from_name,
        "to_warehouse_id": row.to_warehouse_id,
        "to_warehouse_name": to_name,
        "sync_stock": row.sync_stock,
        "status": row.status,
        "audit_status": row.audit_status,
        "total_qty": row.total_qty,
        "item_count": int(count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access) for row, from_name, to_name, count in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _get_transfer(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    access = authorize_query(db, "transfer_queries")
    row = transfer_service.get_transfer_or_404(db, args.transfer_id)
    from_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.from_warehouse_id))
    to_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.to_warehouse_id))
    item_count = int(db.scalar(
        select(func.count(TransferItem.id)).where(TransferItem.transfer_id == row.id)
    ) or 0)
    data = query_projection.transfer_document({
        "id": row.id,
        "transfer_no": row.transfer_no,
        "from_warehouse_id": row.from_warehouse_id,
        "from_warehouse_name": from_name,
        "to_warehouse_id": row.to_warehouse_id,
        "to_warehouse_name": to_name,
        "sync_stock": row.sync_stock,
        "status": row.status,
        "audit_status": row.audit_status,
        "total_qty": row.total_qty,
        "item_count": item_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)
    return ToolOutput(data, {"transfer_id": args.transfer_id}, 1)


def _list_transfer_items(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "transfer_queries")
    try:
        transfer_service.get_transfer_or_404(db, args.transfer_id)
    except BizException as exc:
        raise BizException(ErrorCode.FORBIDDEN, "调拨单不存在或不可见") from exc
    base = (
        select(
            TransferItem.id.label("id"),
            TransferItem.product_id.label("product_id"),
            Product.code.label("product_code"),
            Product.name.label("product_name"),
            TransferItem.sku_id.label("sku_id"),
            ProductSku.spec_code.label("spec_code"),
            ProductSku.spec_name.label("spec_name"),
            TransferItem.qty.label("qty"),
        )
        .join(Product, Product.id == TransferItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == TransferItem.sku_id)
        .where(TransferItem.transfer_id == args.transfer_id)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base).order_by(base.c.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.transfer_item(dict(row._mapping), access) for row in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(), len(data), _page(page))


def _search_transfer_children(
    db: Session, args: BaseModel, identity: McpIdentity, *, inbound: bool,
) -> ToolOutput:
    from app.core.data_scope import scope_conditions

    access = authorize_query(db, "transfer_queries")
    model = TransferIn if inbound else TransferOut
    item_model = TransferInItem if inbound else TransferOutItem
    item_parent_column = TransferInItem.transfer_in_id if inbound else TransferOutItem.transfer_out_id
    id_name = "transfer_in_no" if inbound else "transfer_out_no"
    conditions = list(scope_conditions(db, model.created_by))
    if args.transfer_id is not None:
        conditions.append(model.transfer_id == args.transfer_id)
    if args.warehouse_id is not None:
        conditions.append(model.warehouse_id == args.warehouse_id)
    if args.status is not None:
        conditions.append(model.status == args.status)
    if args.audit_status is not None:
        conditions.append(model.audit_status == args.audit_status)
    total = int(db.scalar(select(func.count(model.id)).where(*conditions)) or 0)
    aggregates = (
        select(
            item_parent_column.label("parent_id"),
            func.count(item_model.id).label("item_count"),
            func.coalesce(func.sum(item_model.qty), 0).label("total_qty"),
        )
        .group_by(item_parent_column)
        .subquery()
    )
    rows = db.execute(
        select(model, Warehouse.name, aggregates.c.item_count, aggregates.c.total_qty)
        .join(Warehouse, Warehouse.id == model.warehouse_id)
        .outerjoin(aggregates, aggregates.c.parent_id == model.id)
        .where(*conditions)
        .order_by(model.id.desc())
        .offset((args.page - 1) * args.page_size)
        .limit(args.page_size)
    ).all()
    data = [query_projection.transfer_document({
        "id": row.id,
        id_name: row.transfer_in_no if inbound else row.transfer_out_no,
        "transfer_id": row.transfer_id,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "status": row.status,
        "audit_status": row.audit_status,
        "total_qty": Decimal(total_qty or 0),
        "item_count": int(item_count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access) for row, warehouse_name, item_count, total_qty in rows]
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, args.model_dump(exclude_none=True), len(data), _page(page))


def _list_transfer_outs(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _search_transfer_children(db, args, identity, inbound=False)


def _list_transfer_ins(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _search_transfer_children(db, args, identity, inbound=True)


def _get_transfer_child(
    db: Session, args: BaseModel, identity: McpIdentity, *, inbound: bool,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "transfer_queries")
    model = TransferIn if inbound else TransferOut
    item_model = TransferInItem if inbound else TransferOutItem
    id_value = args.transfer_in_id if inbound else args.transfer_out_id
    try:
        row = (
            transfer_service.get_transfer_in_or_404(db, id_value)
            if inbound else transfer_service.get_transfer_out_or_404(db, id_value)
        )
    except BizException as exc:
        label = "调拨入库单" if inbound else "调拨出库单"
        raise BizException(ErrorCode.FORBIDDEN, f"{label}不存在或不可见") from exc
    item_fk = "transfer_in_id" if inbound else "transfer_out_id"
    item_count, total_qty = db.execute(
        select(func.count(item_model.id), func.coalesce(func.sum(item_model.qty), 0))
        .where(getattr(item_model, item_fk) == row.id)
    ).one()
    warehouse_name = db.scalar(select(Warehouse.name).where(Warehouse.id == row.warehouse_id))
    doc_no = row.transfer_in_no if inbound else row.transfer_out_no
    data = query_projection.transfer_document({
        "id": row.id,
        "transfer_in_no" if inbound else "transfer_out_no": doc_no,
        "transfer_id": row.transfer_id,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse_name,
        "status": row.status,
        "audit_status": row.audit_status,
        "total_qty": Decimal(total_qty or 0),
        "item_count": int(item_count or 0),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }, access)
    id_key = "transfer_in_id" if inbound else "transfer_out_id"
    return ToolOutput(data, {id_key: id_value}, 1)


def _get_transfer_out(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _get_transfer_child(db, args, identity, inbound=False)


def _get_transfer_in(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _get_transfer_child(db, args, identity, inbound=True)


def _list_transfer_child_items(
    db: Session, args: BaseModel, identity: McpIdentity, *, inbound: bool,
) -> ToolOutput:
    from app.core.errors import BizException, ErrorCode

    access = authorize_query(db, "transfer_queries")
    parent_id = args.transfer_in_id if inbound else args.transfer_out_id
    try:
        (
            transfer_service.get_transfer_in_or_404(db, parent_id)
            if inbound else transfer_service.get_transfer_out_or_404(db, parent_id)
        )
    except BizException as exc:
        label = "调拨入库单" if inbound else "调拨出库单"
        raise BizException(ErrorCode.FORBIDDEN, f"{label}不存在或不可见") from exc
    item_model = TransferInItem if inbound else TransferOutItem
    parent_field = "transfer_in_id" if inbound else "transfer_out_id"
    conditions = [getattr(item_model, parent_field) == parent_id]
    columns = [
        item_model.id.label("id"), item_model.product_id.label("product_id"),
        Product.code.label("product_code"), Product.name.label("product_name"),
        item_model.sku_id.label("sku_id"), ProductSku.spec_code.label("spec_code"),
        ProductSku.spec_name.label("spec_name"),
        item_model.location_id.label("location_id"), Location.code.label("location_code"),
        item_model.qty.label("qty"),
    ]
    base = (
        select(*columns)
        .join(Product, Product.id == item_model.product_id)
        .outerjoin(ProductSku, ProductSku.id == item_model.sku_id)
        .outerjoin(Location, Location.id == item_model.location_id)
        .where(*conditions)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(base)) or 0)
    rows = db.execute(
        select(base).order_by(base.c.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    ).all()
    data = [query_projection.transfer_item(dict(row._mapping), access) for row in rows]
    filters = args.model_dump(exclude_none=True)
    page = {"page": args.page, "page_size": args.page_size, "total": total}
    return ToolOutput(data, filters, len(data), _page(page))


def _list_transfer_out_items(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _list_transfer_child_items(db, args, identity, inbound=False)


def _list_transfer_in_items(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    return _list_transfer_child_items(db, args, identity, inbound=True)


_ROLE_DOC_TYPES = {
    "PURCHASER": {"purchase_plan", "purchase_order", "purchase_prepay", "purchase_return"},
    "VP": {"purchase_prepay"},
    "FINANCE": {"purchase_order", "purchase_prepay", "purchase_return", "purchase_invoice", "order"},
    "QUALITY": {"purchase_return"},
    "WAREHOUSE": {"stock_in", "stock_out", "transfer", "transfer_out", "transfer_in", "stock_check", "location_adjust", "purchase_return"},
}


def _list_approval_flows(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "approval_business")
    allowed_types = set().union(*(_ROLE_DOC_TYPES.get(role, set()) for role in access.roles))
    conditions = []
    if "ADMIN" not in access.roles:
        conditions.append(ApprovalFlow.doc_type.in_(allowed_types) if allowed_types else False)
    if filters.get("doc_type"):
        if "ADMIN" not in access.roles and filters["doc_type"] not in allowed_types:
            from app.core.errors import BizException, ErrorCode
            raise BizException(ErrorCode.FORBIDDEN, "无此类审批流程查询权限")
        conditions.append(ApprovalFlow.doc_type == filters["doc_type"])
    total = db.scalar(select(func.count(ApprovalFlow.id)).where(*conditions)) or 0
    rows = db.scalars(select(ApprovalFlow).where(*conditions).order_by(ApprovalFlow.id)
                      .offset((filters["page"] - 1) * filters["page_size"])
                      .limit(filters["page_size"]))
    data = [{"id": row.id, "name": row.name, "doc_type": row.doc_type} for row in rows]
    page = {"page": filters["page"], "page_size": filters["page_size"],
            "total": total, "has_more": filters["page"] * filters["page_size"] < total}
    return ToolOutput(data, filters, len(data), page)


def _get_approval_records(db: Session, args: BaseModel, identity: McpIdentity) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "approval_business")
    # 审批记录不能作为访问单据的凭据；先复用共享层逐单据类型的可见性复核。
    approval_manage_service.check_document_access(
        db, filters["doc_type"], filters["doc_id"],
    )
    conditions = [
        ApprovalRecord.doc_type == filters["doc_type"],
        ApprovalRecord.doc_id == filters["doc_id"],
    ]
    total = db.scalar(select(func.count(ApprovalRecord.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(ApprovalRecord).where(*conditions)
        .order_by(ApprovalRecord.step, ApprovalRecord.id)
        .offset((filters["page"] - 1) * filters["page_size"])
        .limit(filters["page_size"])
    )
    data = query_projection.approval_records({
        "doc_type": filters["doc_type"],
        "doc_id": filters["doc_id"],
        "records": [{
            "id": row.id,
            "step": row.step,
            "action": row.action,
            "status": row.status,
            "approver_id": row.approver_id,
            "comment": row.comment,
            "reason": row.reason,
            "audit_time": row.audit_time,
            "created_at": row.created_at,
        } for row in rows],
    }, access)
    page = {
        "page": filters["page"], "page_size": filters["page_size"],
        "total": int(total),
        "has_more": filters["page"] * filters["page_size"] < total,
    }
    return ToolOutput(data, filters, len(data["records"]), page)


def _list_approval_doc_types(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    args.model_dump()
    access = authorize_query(db, "approval_business")
    rows = approval_manage_service.list_doc_types()
    allowed_types = set().union(*(
        _ROLE_DOC_TYPES.get(role, set()) for role in access.roles
    ))
    if "ADMIN" not in access.roles:
        rows = [row for row in rows if row["code"] in allowed_types]
    return ToolOutput(rows, {}, len(rows))


def _list_approval_flow_configs(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump()
    access = authorize_query(db, "approval_configuration")
    total = db.scalar(select(func.count(ApprovalFlow.id))) or 0
    flows = db.scalars(
        select(ApprovalFlow).order_by(ApprovalFlow.id)
        .offset((args.page - 1) * args.page_size).limit(args.page_size)
    )
    data = []
    for flow in flows:
        steps = approval_service.steps_of(flow)
        data.append(query_projection.approval_flow_config({
            "id": flow.id,
            "name": flow.name,
            "doc_type": flow.doc_type,
            "doc_type_label": approval_manage_service.DOC_TYPE_LABELS.get(
                flow.doc_type, flow.doc_type,
            ),
            "created_at": flow.created_at,
            "updated_at": flow.updated_at,
            "steps": [{
                "step": step.step, "name": step.name,
                "approver_ids": list(step.approver_ids),
            } for step in steps],
        }, access))
    result = {"page": args.page, "page_size": args.page_size, "total": int(total)}
    return ToolOutput(data, filters, len(data), _page(result))


def _list_my_approvals(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    filters = args.model_dump(exclude_none=True)
    access = authorize_query(db, "approval_business")
    result = approval_manage_service.list_my_approvals(
        db, staff=access.staff, compact=True, **filters,
    )
    data = [query_projection.approval_list_item(row, access) for row in result["items"]]
    return ToolOutput(data, filters, len(data), _page(result))


def _get_my_approval_detail(
    db: Session, args: BaseModel, identity: McpIdentity,
) -> ToolOutput:
    access = authorize_query(db, "approval_business")
    detail = approval_manage_service.get_my_approval_detail(
        db, doc_type=args.doc_type, doc_id=args.doc_id, staff=access.staff,
        compact=True,
    )
    data = query_projection.my_approval_detail(detail, access)
    return ToolOutput(data, {
        "doc_type": args.doc_type, "doc_id": args.doc_id,
    }, 1)


MCP_TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec for spec in (
        ToolSpec("erp_search_purchase_plans", "purchase_plans", "按关键词、供应商和状态分页查询本人数据范围内的采购计划。", PurchasePlanSearchArguments, _search_purchase_plans),
        ToolSpec("erp_preview_purchase_needs", "purchase_needs", "按20日销量测算采购需求，直接按商品规格行SQL分页；最多100行，不返回图片或自由文本。", PurchaseNeedsArguments, _preview_purchase_needs),
        ToolSpec("erp_get_purchase_plan", "purchase_plans", "按本人可见范围查询采购计划固定摘要；不返回备注或无限商品明细，金额按采购财务权限裁剪。", PurchasePlanDetailArguments, _get_purchase_plan),
        ToolSpec("erp_list_purchase_plan_items", "purchase_plans", "先校验父计划可见范围，再分页查询商品与数量；价格/金额按采购财务权限裁剪。", PurchasePlanItemArguments, _list_purchase_plan_items),
        ToolSpec("erp_get_purchase_plan_creation_request", "purchase_creation_requests", "仅供PURCHASER查询当前员工自己的计划幂等请求状态；成功时只返回计划ID，不回传结果JSON、审批或自由文本。", PurchasePlanCreationRequestArguments, _get_purchase_plan_creation_request),
        ToolSpec("erp_search_purchase_orders", "purchase_orders", "按关键词、计划、供应商和状态分页查询本人数据范围内的采购订单。", PurchaseOrderSearchArguments, _search_purchase_orders),
        ToolSpec("erp_get_purchase_order", "purchase_orders", "按当前采购数据范围查询采购订单固定摘要；不载入明细、审批意见或附件内容。", PurchaseOrderDetailArguments, _get_purchase_order),
        ToolSpec("erp_list_purchase_order_items", "purchase_orders", "先复核父订单本人/合法审批范围，再SQL分页查询商品、规格和数量；金额遵循采购权限。", PurchaseOrderItemArguments, _list_purchase_order_items),
        ToolSpec("erp_order_list_orders", "sales_orders", "按当前凭据公司分页查询销售订单；关键字仅搜索订单号，不外发收货人、电话、地址、运单号或备注。", SalesOrderSearchArguments, _search_sales_orders),
        ToolSpec("erp_order_get_order", "sales_orders", "查询当前凭据公司内的销售订单固定摘要，不返回个人联系方式、地址、运单号、备注或审批自由文本。", SalesOrderDetailArguments, _get_sales_order),
        ToolSpec("erp_order_list_order_items", "sales_orders", "先核验订单存在，再SQL分页返回商品名称/规格/数量；商品图片与自由文本不外发，金额遵循订单模块权限。", SalesOrderItemsArguments, _list_sales_order_items),
        ToolSpec("erp_order_list_order_logs", "sales_orders", "先核验订单存在，再合并分页查询订单/审批事件；仅返回来源、白名单动作、环节状态与时间，不外发操作人、摘要、原因、审批意见或详情。", SalesOrderLogsArguments, _list_sales_order_logs),
        ToolSpec("erp_ship_list_shipments", "sales_orders", "按当前凭据公司SQL分页查询发货摘要；运单号仅用于检索且输出脱敏，不返回同步消息、备注、操作人或嵌套明细。", ShipmentSearchArguments, _list_shipments),
        ToolSpec("erp_ship_list_order_shipments", "sales_orders", "复核订单存在后按当前公司范围分页查询其发货摘要；不返回自由文本或操作人信息。", OrderShipmentsArguments, _list_shipments),
        ToolSpec("erp_ship_get_shipment", "sales_orders", "查询当前凭据公司内的发货记录固定摘要；运单号仅显示末四位，不含平台回传消息/备注/人员身份。", ShipmentDetailArguments, _get_shipment),
        ToolSpec("erp_ship_list_shipment_items", "sales_orders", "先复核发货记录及父订单，再SQL分页返回商品规格和数量；不返回条码、成本或图片。", ShipmentItemsArguments, _list_shipment_items),
        ToolSpec("erp_order_list_order_flags", "order_flag_dictionary", "查询订单标识编码与中文名称；仅需要订单模块权限，无业务数据或副作用。", EmptyQueryArguments, _list_order_flags),
        ToolSpec("erp_search_purchase_returns", "purchase_returns", "按关键词、供应商、采购订单、仓库和状态分页查询当前数据范围内的采购退货单；金额仅按凭据财务/采购权限返回。", PurchaseReturnSearchArguments, _search_purchase_returns),
        ToolSpec("erp_get_purchase_return", "purchase_returns", "查询当前数据范围内采购退货单摘要；不返回描述或嵌套明细，明细请分页读取。", PurchaseReturnDetailArguments, _get_purchase_return),
        ToolSpec("erp_list_purchase_return_items", "purchase_returns", "分页查询采购退货单商品、规格和数量；金额仅按凭据财务/采购权限返回。", PurchaseReturnItemArguments, _list_purchase_return_items),
        ToolSpec("erp_get_purchase_return_stock_out", "purchase_returns", "查询采购退货单关联出库摘要，不触发出库或库存同步。", PurchaseReturnDetailArguments, _get_purchase_return_stock_out),
        ToolSpec("erp_get_purchase_return_refund", "purchase_returns", "查询采购退货单关联退款收入摘要；不返回描述、凭证或银行信息。", PurchaseReturnDetailArguments, _get_purchase_return_refund),
        ToolSpec("erp_search_products", "product_catalog", "按当前凭据所属公司分页查询商品基础资料；不返回价格、成本、税率、描述或图片地址。", ProductSearchArguments, _search_products),
        ToolSpec("erp_get_product", "product_catalog", "按商品ID查询当前公司内的固定基础资料；不返回价格、成本、税率、描述或图片地址，规格明细请分页查询。", ProductDetailArguments, _get_product),
        ToolSpec("erp_list_product_skus", "product_catalog", "分页查询当前公司内指定商品的规格编码和条码；不返回售价、成本或图片地址。", ProductSkuSearchArguments, _list_product_skus),
        ToolSpec("erp_search_combos", "product_catalog", "按当前凭据所属公司分页查询组合商品固定摘要；不返回备注或组合明细。", ComboSearchArguments, _search_combos),
        ToolSpec("erp_get_combo", "product_catalog", "按组合商品ID查询当前公司内的固定摘要；组成明细请使用分页工具查询。", ComboDetailArguments, _get_combo),
        ToolSpec("erp_list_combo_items", "product_catalog", "分页查询当前公司内指定组合商品的组成商品与数量；不返回价格、成本、图片或备注。", ComboItemSearchArguments, _list_combo_items),
        ToolSpec("erp_product_expand_combo", "product_catalog", "按输入数量分页预览当前公司内组合商品展开项；遵循启用组合选择规则，不返回价格、成本或备注。", ComboExpandArguments, _expand_combo),
        ToolSpec("erp_company_get_company", "company_directory", "查询当前凭据所属公司的公开名称信息；不返回法人、电话或税号。", CompanyDetailArguments, _get_company),
        ToolSpec("erp_company_list_departments", "company_directory", "按当前公司分页查询部门名称与层级；不接受客户端公司ID。", PageArguments, _list_departments),
        ToolSpec("erp_company_get_department", "company_directory", "仅查询当前公司内部门固定字段；跨公司或不存在均不返回记录。", DepartmentDetailArguments, _get_department),
        ToolSpec("erp_company_list_positions", "company_directory", "按当前公司分页查询职务名称；不接受客户端公司ID。", PageArguments, _list_positions),
        ToolSpec("erp_company_get_position", "company_directory", "仅查询当前公司内职务固定字段；跨公司或不存在均不返回记录。", PositionDetailArguments, _get_position),
        ToolSpec("erp_company_list_staff", "company_directory", "按当前公司分页搜索员工；关键字仅匹配姓名/工号，联系方式与登录账号不参与匹配或返回。", StaffSearchArguments, _list_company_staff),
        ToolSpec("erp_company_get_staff", "company_directory", "仅查询当前公司员工固定组织字段；不返回手机号、登录账号、密码状态或个人备注。", StaffDetailArguments, _get_company_staff),
        ToolSpec("erp_company_get_staff_data_scope", "company_directory", "按设置读取权限查询本公司员工的数据范围；无显式配置时返回SELF；不返回部门ID、角色分配或其他安全配置。", StaffDetailArguments, _get_company_staff_data_scope),
        ToolSpec("erp_assistant_capabilities", "assistant_capabilities", "按当前员工实时角色查询助手功能开关和非敏感服务状态；不返回API密钥、模型名或完整权限清单。", AssistantCapabilitiesArguments, _get_assistant_capabilities),
        ToolSpec("erp_product_list_categories", "product_settings", "按当前凭据所属公司分页查询商品分类固定字段；不返回自由文本。", PageArguments, _list_product_categories),
        ToolSpec("erp_product_list_brands", "product_settings", "按当前凭据所属公司分页查询品牌编号与名称；不返回图片地址。", PageArguments, _list_product_brands),
        ToolSpec("erp_product_list_units", "product_settings", "按当前凭据所属公司分页查询计量单位编号、名称和编码。", PageArguments, _list_product_units),
        ToolSpec("erp_product_list_unit_groups", "product_settings", "按当前凭据所属公司分页查询单位组摘要与单位项数量；具体换算项请调用分页子工具。", PageArguments, _list_product_unit_groups),
        ToolSpec("erp_product_list_unit_group_items", "product_settings", "按当前公司内的单位组分页查询换算单位与换算系数。", ProductReferenceItemsArguments, _list_product_unit_group_items),
        ToolSpec("erp_product_list_spec_types", "product_settings", "按当前凭据所属公司分页查询规格类型及关联分类固定字段。", PageArguments, _list_product_spec_types),
        ToolSpec("erp_product_list_spec_values", "product_settings", "校验参数并分页查询当前公司指定规格类型下的规格值。", ProductSpecValuesArguments, _list_product_spec_values),
        ToolSpec("erp_search_suppliers", "supplier_catalog", "按当前凭据公司分页查询供应商摘要；电话和地址脱敏，不返回联系人、邮箱、备注或银行账户。", SupplierSearchArguments, _search_suppliers),
        ToolSpec("erp_get_supplier", "supplier_catalog", "查询当前凭据公司内的供应商固定字段详情；联系方式脱敏，不返回联系人、邮箱、备注或银行账户。", SupplierDetailArguments, _get_supplier),
        ToolSpec("erp_list_supplier_accounts", "party_accounts", "分页查询供应商银行账户摘要；账号仅显示末四位，不返回开户地址、金蝶映射或同步错误；余额仅财务权限可见。", SupplierAccountArguments, _list_supplier_accounts),
        ToolSpec("erp_list_customer_accounts", "party_accounts", "分页查询客户银行账户摘要；账号仅显示末四位，不返回开户地址、金蝶映射或同步错误；余额仅财务权限可见。", CustomerAccountArguments, _list_customer_accounts),
        ToolSpec("erp_list_supplier_prices", "supplier_prices", "按当前凭据公司分页查询供应商商品价格；只返回固定商品标识、价格和状态字段。", SupplierPriceArguments, _list_supplier_prices),
        ToolSpec("erp_search_customers", "customer_catalog", "仅供AFTER_SALE/ADMIN按当前凭据公司分页查询客户摘要；联系方式脱敏，不返回联系人、邮箱或备注。", CustomerSearchArguments, _search_customers),
        ToolSpec("erp_get_customer", "customer_catalog", "仅供AFTER_SALE/ADMIN查询当前凭据公司内的客户固定字段详情；联系方式脱敏。", CustomerDetailArguments, _get_customer),
        ToolSpec("erp_search_after_sale_returns", "after_sale", "仅供AFTER_SALE/ADMIN按当前凭据公司分页查询退货摘要；金额按财务权限、电话和物流单号脱敏，不返回原因和无限嵌套明细。", AfterSaleReturnSearchArguments, _search_after_sale_returns),
        ToolSpec("erp_get_after_sale_return", "after_sale", "查询退货单固定字段摘要；电话和物流单号脱敏，金额按财务权限，商品明细请分页读取。", AfterSaleDetailArguments, _get_after_sale_return),
        ToolSpec("erp_list_after_sale_return_items", "after_sale", "分页查询退货单商品与数量；图片和自由文本不外发，退款金额仅按财务权限返回。", AfterSaleReturnItemsArguments, _list_after_sale_return_items),
        ToolSpec("erp_search_return_registers", "after_sale", "按当前凭据公司分页查询退换货登记；寄件电话及物流单号脱敏，不返回寄件人、图片或自由文本。", ReturnRegisterSearchArguments, _search_return_registers),
        ToolSpec("erp_search_after_sale_exchanges", "after_sale", "仅供AFTER_SALE/ADMIN按当前凭据公司分页查询换货摘要；金额按财务权限，不返回原因和无限嵌套明细。", AfterSaleExchangeSearchArguments, _search_after_sale_exchanges),
        ToolSpec("erp_get_after_sale_exchange", "after_sale", "查询换货单固定字段摘要；补差金额按财务权限，商品明细请分页读取。", AfterSaleExchangeDetailArguments, _get_after_sale_exchange),
        ToolSpec("erp_list_after_sale_exchange_items", "after_sale", "分页查询换货商品、规格和数量；图片与自由文本不外发。", AfterSaleExchangeItemsArguments, _list_after_sale_exchange_items),
        ToolSpec("erp_search_stock_flows", "stock_flows", "按当前凭据公司分页查询库存流水；仅限仓库角色，不返回成本、备注或图片地址。", StockFlowSearchArguments, _search_stock_flows),
        ToolSpec("erp_stock_check_list_stock_checks", "stock_checks", "按仓库、状态和单号分页查询当前员工数据范围内的盘点单；数量汇总在SQL中计算，不返回备注、审批文本或操作人身份。", StockCheckSearchArguments, _search_stock_checks),
        ToolSpec("erp_stock_check_get_stock_check", "stock_checks", "查询本人或合法审批可见范围内的盘点单安全摘要；商品明细请使用独立分页工具。", StockCheckDetailArguments, _get_stock_check),
        ToolSpec("erp_stock_check_list_items", "stock_checks", "复核父盘点单可见性后，SQL分页返回商品/规格/库位与盘点数量；不返回条码、成本、备注或审批文本。", StockCheckItemsArguments, _list_stock_check_items),
        ToolSpec("erp_stock_in_list_pending_putaways", "stock_documents", "按可见且审核通过的采购入库汇总待上架商品；SQL按商品规格分页，只返回数量，不返回仓库操作人。", PendingPutawayArguments, _pending_putaways),
        ToolSpec("erp_stock_in_list_putaways", "stock_documents", "按当前凭据公司分页查询数据范围中的采购入库/调拨入库上架记录；按来源单据范围过滤，不返回操作人身份。", PutawaySearchArguments, _search_putaways),
        ToolSpec("erp_stock_in_list_stock_ins", "stock_documents", "按当前制单人范围SQL分页查询入库摘要；商品行数量独立分页，列表不返回备注或人员身份。", StockInSearchArguments, _search_stock_ins),
        ToolSpec("erp_stock_in_get_stock_in", "stock_documents", "查询本人或合法审批可见的入库固定摘要，不加载无界商品/审批数据。", StockInDetailArguments, _get_stock_in),
        ToolSpec("erp_stock_in_list_stock_in_items", "stock_documents", "复核父入库单可见性后SQL分页查询商品规格和数量；成本金额仅当前财务凭据可见。", StockInItemsArguments, _list_stock_in_items),
        ToolSpec("erp_stock_out_list_stock_outs", "stock_documents", "按当前制单人范围SQL分页查询出库摘要；不返回备注或人员身份。", StockOutSearchArguments, _search_stock_outs),
        ToolSpec("erp_stock_out_get_stock_out", "stock_documents", "查询本人或合法审批可见的出库固定摘要，不加载无限商品/审批数据。", StockOutDetailArguments, _get_stock_out),
        ToolSpec("erp_stock_out_list_stock_out_items", "stock_documents", "复核父出库单可见性后SQL分页查询商品、规格、库位和数量；成本及条码不外发。", StockOutItemsArguments, _list_stock_out_items),
        ToolSpec("erp_transfer_list_transfers", "transfer_queries", "仓库角色按当前凭据公司及制单数据范围SQL分页查询调拨单摘要；不返回备注或制单人。", TransferSearchArguments, _search_transfers),
        ToolSpec("erp_transfer_get_transfer", "transfer_queries", "复核本人或合法审批可见范围后查询调拨单固定摘要；商品明细使用独立分页工具。", TransferDetailArguments, _get_transfer),
        ToolSpec("erp_transfer_list_transfer_items", "transfer_queries", "复核调拨单可见性后SQL分页查询商品、规格和数量；不返回条码或成本。", TransferItemsArguments, _list_transfer_items),
        ToolSpec("erp_transfer_list_transfer_outs", "transfer_queries", "仓库角色按当前凭据公司及制单数据范围SQL分页查询调拨出库摘要；明细独立分页。", TransferChildSearchArguments, _list_transfer_outs),
        ToolSpec("erp_transfer_get_transfer_out", "transfer_queries", "复核本人或合法审批可见的调拨出库固定摘要；不返回备注或操作人。", TransferOutDetailArguments, _get_transfer_out),
        ToolSpec("erp_transfer_list_transfer_out_items", "transfer_queries", "复核调拨出库单可见性后SQL分页查询商品、规格、库位和数量；条码及成本不外发。", TransferOutItemsArguments, _list_transfer_out_items),
        ToolSpec("erp_transfer_list_transfer_ins", "transfer_queries", "仓库角色按当前凭据公司及制单数据范围SQL分页查询调拨入库摘要；明细独立分页。", TransferChildSearchArguments, _list_transfer_ins),
        ToolSpec("erp_transfer_get_transfer_in", "transfer_queries", "复核本人或合法审批可见的调拨入库固定摘要；不返回备注或操作人。", TransferInDetailArguments, _get_transfer_in),
        ToolSpec("erp_transfer_list_transfer_in_items", "transfer_queries", "复核调拨入库单可见性后SQL分页查询商品、规格、库位和数量；不返回自由文本。", TransferInItemsArguments, _list_transfer_in_items),
        ToolSpec("erp_stock_query_list_stock_summary", "stock_inventory", "仓库角色按当前凭据公司和仓库/商品查询库存摘要；总量、占用量和预警过滤在SQL分页，成本金额仅财务权限可见。", StockSummaryArguments, _search_stock_summary),
        ToolSpec("erp_stock_query_list_stock_locations", "stock_inventory", "按当前凭据公司和仓库/库位/商品规格SQL分页查询分库位数量；不返回库位容量或成本。", StockLocationsArguments, _search_stock_locations),
        ToolSpec("erp_stock_query_list_stock_warnings", "stock_inventory", "按当前凭据公司SQL分页查询商品规格库存预警；只读计算，不调用刷新接口。", StockWarningsArguments, _search_stock_warnings),
        ToolSpec("erp_stock_query_list_stock_costs", "stock_inventory", "仓库角色按当前凭据公司分页查询库存商品规格及数量；成本单价和总成本仅实时财务权限与凭据同时允许时返回。", StockCostsArguments, _search_stock_costs),
        ToolSpec("erp_stock_query_list_location_adjusts", "stock_inventory", "按当前凭据公司分页查询库位调整单安全摘要；不返回备注、审批意见或操作人身份。", LocationAdjustSearchArguments, _search_location_adjusts),
        ToolSpec("erp_stock_query_get_location_adjust", "stock_inventory", "查询当前凭据公司内的库位调整固定摘要；商品行须使用独立分页工具。", LocationAdjustDetailArguments, _get_location_adjust),
        ToolSpec("erp_stock_query_list_location_adjust_items", "stock_inventory", "校验调整单存在后SQL分页查询商品、规格、来源/目标库位及数量；不返回自由文本或成本。", LocationAdjustItemsArguments, _list_location_adjust_items),
        ToolSpec("erp_search_warehouses", "warehouse_catalog", "按当前凭据公司分页查询仓库摘要；地址脱敏，不返回内部区域路由或备注。", WarehouseSearchArguments, _search_warehouses),
        ToolSpec("erp_get_warehouse", "warehouse_catalog", "查询当前凭据公司内的仓库固定字段详情；地址脱敏，不返回内部同步/锁定配置、发货路由或备注。", WarehouseDetailArguments, _get_warehouse),
        ToolSpec("erp_logistics_list_logistics", "logistics_catalog", "按当前凭据公司分页查询物流方式摘要；仅搜索名称/公司名，不返回联系人、电话或地址。", LogisticsSearchArguments, _search_logistics),
        ToolSpec("erp_logistics_get_logistics", "logistics_catalog", "查询当前凭据公司内的物流方式固定摘要；不返回联系人、电话或地址。", LogisticsDetailArguments, _get_logistics),
        ToolSpec("erp_logistics_list_freight_templates", "freight_catalog", "按当前凭据公司分页查询运费模板规则；运输费用仅财务权限可见。", FreightTemplateSearchArguments, _list_freight_templates),
        ToolSpec("erp_logistics_get_freight_template", "freight_catalog", "查询当前凭据公司内的运费模板规则；不含财务权限时隐藏费用金额和税点。", FreightTemplateDetailArguments, _get_freight_template),
        ToolSpec("erp_logistics_calculate_freight", "freight_catalog", "按当前凭据公司内的运费模板试算；仅返回计费口径给普通业务角色，所有费用金额仅财务权限可见。", FreightCalculationArguments, _calculate_freight),
        ToolSpec("erp_logistics_match_area_rule", "area_rule_catalog", "按物流方式、省市和重量计算命中区域规则；费用金额及税点仅财务权限可见。", AreaRuleMatchArguments, _match_area_rule),
        ToolSpec("erp_logistics_list_area_rules", "area_rule_catalog", "按当前凭据公司和物流方式分页查询区域重量规则；不外发运费金额。", AreaRuleSearchArguments, _list_area_rules),
        ToolSpec("erp_logistics_get_area_rule", "area_rule_catalog", "查询当前凭据公司内的区域物流规则固定详情；不外发运费金额。", AreaRuleDetailArguments, _get_area_rule),
        ToolSpec("erp_logistics_list_print_templates", "print_template_catalog", "按当前凭据公司分页查询打印模板安全元数据；不返回模板内容、脚本或单据字段映射。", PrintTemplateSearchArguments, _list_print_templates),
        ToolSpec("erp_logistics_get_print_template", "print_template_catalog", "查询当前凭据公司的打印模板固定元数据；始终不返回模板内容、脚本或字段映射。", PrintTemplateDetailArguments, _get_print_template),
        ToolSpec("erp_print_get_print_batch", "print_batch", "复核批次内每张订单的当前可见范围后返回批次摘要与最多100个订单号；电话/地址脱敏，不返回面单内容或自由文本。", PrintBatchArguments, _get_print_batch),
        ToolSpec("erp_shop_list_platforms", "shop_catalog", "按当前凭据公司SQL分页查询平台名称/编码；关键字不匹配任何凭据。", PlatformSearchArguments, _list_platforms),
        ToolSpec("erp_shop_get_platform", "shop_catalog", "按ID查询当前凭据公司的平台固定名称/编码。", PlatformDetailArguments, _get_platform),
        ToolSpec("erp_shop_list_shops", "shop_catalog", "按当前凭据公司SQL分页查询店铺配置安全摘要；不搜索或返回登录账号、URL、地址、短信模板、自由匹配规则或API凭据。", ShopSearchArguments, _list_shops),
        ToolSpec("erp_shop_get_shop", "shop_catalog", "查询当前凭据公司的店铺安全固定字段，不返回账号、网址、地址、短信模板或API密钥。", ShopDetailArguments, _get_shop),
        ToolSpec("erp_shop_get_api_status", "shop_catalog", "只返回指定店铺API配置是否存在的布尔值，不读取或外发API配置字段。", ShopDetailArguments, _get_shop_api_status),
        ToolSpec("erp_list_warehouse_zones", "warehouse_catalog", "按当前凭据公司分页查询指定仓库的库区摘要；仓库ID必填，不读取库位或库存数量。", WarehouseZoneSearchArguments, _list_warehouse_zones),
        ToolSpec("erp_get_warehouse_zone", "warehouse_catalog", "按库区ID查询当前凭据公司仓库内的固定元数据；不读取库位或库存数量。", WarehouseZoneDetailArguments, _get_warehouse_zone),
        ToolSpec("erp_search_locations", "warehouse_catalog", "按当前凭据公司分页查询指定仓库的库位元数据；仓库ID必填，不返回容量、位置细节或库存数量。", LocationSearchArguments, _search_locations),
        ToolSpec("erp_get_location", "warehouse_catalog", "按库位ID查询当前凭据公司的固定元数据；不返回容量、坐标或库存数量。", LocationDetailArguments, _get_location),
        ToolSpec("erp_warehouse_get_location_capacity", "location_capacity", "仓库角色按当前凭据公司查询指定库位容量、现存量和剩余量；不修改库存或库位状态。", LocationCapacityArguments, _get_location_capacity),
        ToolSpec("erp_wave_list_wave_strategies", "wave_queries", "按当前凭据公司和策略名称/启用状态SQL分页查询波次策略固定摘要；不搜索或返回自由描述、店铺配置。", WaveStrategySearchArguments, _search_wave_strategies),
        ToolSpec("erp_wave_get_wave_strategy", "wave_queries", "查询当前凭据公司的波次策略固定摘要；不返回自由描述或店铺配置。", WaveStrategyDetailArguments, _get_wave_strategy),
        ToolSpec("erp_wave_get_wave_strategy_pending_count", "wave_queries", "按当前凭据公司和策略条件在SQL统计可入波订单数；只返回数量，不加载或外发订单信息。", WavePendingCountArguments, _get_wave_strategy_pending_count),
        ToolSpec("erp_wave_list_waves", "wave_queries", "按当前凭据公司、策略/物流/波次号及时间SQL分页查询波次摘要；不载入收件信息、明细或制单人。", WaveSearchArguments, _search_waves),
        ToolSpec("erp_wave_get_wave", "wave_queries", "查询当前凭据公司的波次固定摘要与SQL关联数量；订单及装载号另用独立分页工具。", WaveDetailArguments, _get_wave),
        ToolSpec("erp_wave_list_wave_orders", "wave_queries", "复核波次存在后SQL分页查询订单号、仓库/物流名称、状态与装载顺序；不返回收件人、地址、运单或金额。", WaveOrdersArguments, _list_wave_orders),
        ToolSpec("erp_wave_list_load_numbers", "wave_queries", "复核波次存在后SQL分页查询装载号与物流类型。", WaveLoadNumbersArguments, _list_wave_load_numbers),
        ToolSpec("erp_search_purchase_prepays", "purchase_prepays", "按采购员当前数据范围分页查询预付款申请；银行账号、备注和凭证不外发。", PurchasePrepaySearchArguments, _search_purchase_prepays),
        ToolSpec("erp_get_purchase_prepay", "purchase_prepays", "按当前采购数据范围查询预付款固定摘要；不返回银行账号、备注、审批意见或凭证明细。", PurchasePrepayDetailArguments, _get_purchase_prepay),
        ToolSpec("erp_get_purchase_prepay_voucher", "purchase_prepays", "先校验预付款申请可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", PurchasePrepayDetailArguments, _get_prepay_voucher),
        ToolSpec("erp_search_purchase_invoices", "purchase_invoices", "按财务当前数据范围分页查询采购发票摘要；成本分摊和备注不外发。", PurchaseInvoiceSearchArguments, _search_purchase_invoices),
        ToolSpec("erp_get_purchase_invoice", "purchase_invoices", "按当前财务数据范围查询采购发票固定摘要；不加载成本分摊、库存关联列表、描述、制单人或审批信息。", PurchaseInvoiceDetailArguments, _get_purchase_invoice),
        ToolSpec("erp_get_purchase_invoice_cost_detail", "purchase_invoices", "先复核采购发票本人/合法审批范围，再分页返回成本分摊固定字段；不返回描述或任意JSON字段。", PurchaseInvoiceCostDetailArguments, _get_purchase_invoice_cost_detail),
        ToolSpec("erp_report_purchase_orders", "purchase_report", "按当前采购数据范围分页汇总供应商采购订单；summary覆盖全部匹配分组，金额按采购权限裁剪。", PurchaseOrderReportArguments, _purchase_order_report),
        ToolSpec("erp_report_purchase_inbounds", "purchase_report", "按入库单和关联采购订单的双重制单范围分页汇总审核入库；成本金额仅财务授权可见。", PurchaseInboundReportArguments, _purchase_inbound_report),
        ToolSpec("erp_report_purchase_invoices", "purchase_invoice_report", "按财务数据范围分页汇总供应商采购发票；summary覆盖全部匹配分组，金额仅财务授权返回。", PurchaseInvoiceReportArguments, _purchase_invoice_report),
        ToolSpec("erp_report_inventory_in_transit", "inventory_report", "按当前凭据公司分页查询有效仓库良品现存量与本人数据范围内已审核采购未到货数量；聚合和summary均在SQL执行。", InventoryTransitReportArguments, _inventory_in_transit_report),
        ToolSpec("erp_invoice_list_invoices", "invoice_management", "按当前凭据公司分页查询开票管理记录；关键字仅匹配发票号码/抬头/公司，不搜索税号或申请人；金额字段固定投影。", InvoiceSearchArguments, _list_invoices),
        ToolSpec("erp_invoice_get_invoice", "invoice_management", "查询当前凭据公司内的开票记录固定摘要；不返回纳税人识别号或申请人。", InvoiceDetailArguments, _get_invoice),
        ToolSpec("erp_invoice_list_supplier_account_balances", "supplier_balances", "按当前凭据公司和供应商名称/编码分页查询账户余额；账号仅显示末四位，不搜索或返回金蝶映射/同步错误。", SupplierAccountBalanceSearchArguments, _list_supplier_account_balances),
        ToolSpec("erp_invoice_get_supplier_account_balance", "supplier_balances", "查询当前凭据公司内的供应商账户余额固定摘要；账号仅显示末四位，不返回开户地址或金蝶映射/同步错误。", SupplierAccountBalanceDetailArguments, _get_supplier_account_balance),
        ToolSpec("erp_account_subject_get_subject_meta", "finance_accounting", "查询会计科目类别、借贷方向和金蝶种子版本元数据；不执行种子导入。", EmptyQueryArguments, _get_account_subject_meta),
        ToolSpec("erp_account_subject_list_subjects", "finance_accounting", "在当前凭据公司的财务范围分页查询会计科目固定字段；不返回备注或余额。", AccountSubjectSearchArguments, _search_account_subjects),
        ToolSpec("erp_account_subject_get_subject", "finance_accounting", "按ID查询会计科目固定字段；不返回余额或备注。", AccountSubjectDetailArguments, _get_account_subject),
        ToolSpec("erp_account_subject_list_project_categories", "finance_accounting", "在当前凭据公司的财务范围分页查询项目类别；不返回备注。", ProjectCategorySearchArguments, _search_project_categories),
        ToolSpec("erp_account_subject_get_project_category", "finance_accounting", "按ID查询项目类别固定字段；不返回备注。", ProjectCategoryDetailArguments, _get_project_category),
        ToolSpec("erp_voucher_list_vouchers", "finance_vouchers", "按当前财务员工数据范围SQL分页查询凭证摘要；关键字仅匹配凭证字号/编号，不回显摘要、参考文本或人员身份。", VoucherSearchArguments, _search_vouchers),
        ToolSpec("erp_voucher_get_voucher", "finance_vouchers", "复核凭证本人数据范围后查询固定摘要与分录数；参考文本及人员身份不外发，分录另行分页。", VoucherDetailArguments, _get_voucher),
        ToolSpec("erp_voucher_list_voucher_items", "finance_vouchers", "复核凭证本人数据范围后SQL分页返回科目及借贷金额；摘要自由文本不外发。", VoucherItemsArguments, _list_voucher_items),
        ToolSpec("erp_search_expenses", "finance_expenses", "按财务当前数据范围分页查询费用单；仅财务授权可见金额，描述和凭证详情不外发。", ExpenseSearchArguments, _search_expenses),
        ToolSpec("erp_finance_doc_get_expense", "finance_expenses", "查询当前财务数据范围内费用单固定摘要；描述、凭证与审批意见不外发。", ExpenseDetailArguments, _get_expense),
        ToolSpec("erp_finance_doc_get_expense_voucher", "finance_expenses", "先校验费用单可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", ExpenseDetailArguments, _get_expense_voucher),
        ToolSpec("erp_search_incomes", "finance_incomes", "按财务当前数据范围分页查询收入单；描述和凭证详情不外发。", IncomeSearchArguments, _search_incomes),
        ToolSpec("erp_finance_doc_get_income", "finance_incomes", "查询当前财务数据范围内收入单固定摘要；描述、凭证与审批意见不外发。", IncomeDetailArguments, _get_income),
        ToolSpec("erp_finance_doc_get_income_voucher", "finance_incomes", "先校验收入单可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", IncomeDetailArguments, _get_income_voucher),
        ToolSpec("erp_search_payments", "finance_payments", "按财务当前数据范围分页查询付款单摘要；银行账号和凭证不外发。", PaymentSearchArguments, _search_payments),
        ToolSpec("erp_finance_doc_get_payment", "finance_payments", "查询当前财务数据范围内付款单固定摘要；不返回银行账户、审批意见或凭证明细。", PaymentDetailArguments, _get_payment),
        ToolSpec("erp_finance_doc_get_payment_voucher", "finance_payments", "先校验付款单可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", PaymentDetailArguments, _get_payment_voucher),
        ToolSpec("erp_search_receipts", "finance_receipts", "按财务当前数据范围分页查询收款单摘要；联系方式和凭证不外发。", ReceiptSearchArguments, _search_receipts),
        ToolSpec("erp_finance_doc_get_receipt", "finance_receipts", "查询当前财务数据范围内收款单固定摘要；不返回客户联系方式、审批意见或凭证明细。", ReceiptDetailArguments, _get_receipt),
        ToolSpec("erp_finance_doc_get_receipt_voucher", "finance_receipts", "先校验收款单可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", ReceiptDetailArguments, _get_receipt_voucher),
        ToolSpec("erp_search_settlements", "finance_settlements", "按财务当前数据范围分页查询平台结算单；来源文件、备注和凭证详情不外发。", SettlementSearchArguments, _search_settlements),
        ToolSpec("erp_finance_doc_get_settlement", "finance_settlements", "查询当前财务数据范围内平台结算固定摘要；来源文件、备注和凭证不外发。", SettlementDetailArguments, _get_settlement),
        ToolSpec("erp_finance_doc_get_settlement_voucher", "finance_settlements", "先校验平台结算单可见范围，再返回关联凭证安全摘要；不返回分录摘要、参考文本或人员身份。", SettlementDetailArguments, _get_settlement_voucher),
        ToolSpec("erp_list_my_notifications", "my_notifications", "查询当前员工可见的站内通知；游标绑定员工与公司，查询不会标记已读或重投。", MyNotificationsArguments, _list_my_notifications),
        ToolSpec("erp_notification_count_unread", "my_notifications", "统计当前员工仍可见的未读站内通知；逐项复核单据权限，不改已读状态。", EmptyQueryArguments, _count_my_unread_notifications),
        ToolSpec("erp_get_my_notification", "my_notifications", "只查询当前员工和公司仍可见的指定站内通知；复用列表序列化，不标记已读。", MyNotificationDetailArguments, _get_my_notification),
        ToolSpec("erp_get_stock_settings", "stock_settings", "读取本公司库存开关；未配置时只返回瞬态默认值，不写数据库。", EmptyQueryArguments, _get_stock_settings),
        ToolSpec("erp_stock_settlement_list_shop_stocks", "shop_stocks", "按当前凭据公司和店铺/仓库/商品过滤并SQL分页查询独享库存摘要；不触发库存同步，不返回店铺账号、地址、备注或操作人。", ShopStockSearchArguments, _list_shop_stocks),
        ToolSpec("erp_stock_settlement_get_shop_stock", "shop_stocks", "查询当前凭据公司的店铺独享库存固定摘要；库存与占用在SQL聚合，不触发同步且不返回店铺账号、地址、备注或操作人。", ShopStockDetailArguments, _get_shop_stock),
        ToolSpec("erp_stock_settlement_list_periods", "stock_settlements", "按当前凭据公司分页查询库存结存期间及数量汇总；结存成本合计仅实时财务权限与凭据scope同时允许时返回。", PageArguments, _list_stock_settlement_periods),
        ToolSpec("erp_stock_settlement_list_settlements", "stock_settlements", "按当前凭据公司和日期/商品/规格SQL分页查询库存结存；历史成本金额受财务权限和凭据scope限制。", StockSettlementSearchArguments, _search_stock_settlements),
        ToolSpec("erp_stock_settlement_list_cost_rewrite_logs", "stock_settlements", "按当前凭据公司和批次/商品/时间分页查询成本回写留痕；不返回操作人，成本变更数值仅财务权限与凭据scope均允许时返回。", StockCostRewriteLogArguments, _list_stock_cost_rewrite_logs),
        ToolSpec("erp_list_approval_flows", "approval_business", "分页查询当前角色可办理的审批流程摘要，不返回审批人配置。", ApprovalFlowArguments, _list_approval_flows),
        ToolSpec("erp_get_approval_records", "approval_business", "复核单据访问权限后分页查询审批留痕；不返回审批人身份、意见或原因。", ApprovalRecordArguments, _get_approval_records),
        ToolSpec("erp_list_approval_doc_types", "approval_business", "查询当前角色职责内的审批单据类型，不列出其他角色的业务类型。", EmptyQueryArguments, _list_approval_doc_types),
        ToolSpec("erp_list_approval_flow_configs", "approval_configuration", "仅限审批流配置授权者分页查询流程元数据和环节数量；不返回审批人身份。", ApprovalFlowConfigArguments, _list_approval_flow_configs),
        ToolSpec("erp_dashboard_get_dashboard_overview", "dashboard", "按首页权限返回今日聚合指标和有界销售趋势；不输出最近订单、客户信息、热销商品或价格列表。", DashboardOverviewArguments, _get_dashboard_overview),
        ToolSpec("erp_company_list_ip_rules", "company_ip_rules", "仅限管理员分页查询本公司IP规则；不返回自由备注或其他公司的规则。", PageArguments, _list_ip_rules),
        ToolSpec("erp_company_get_ip_rule", "company_ip_rules", "仅限管理员查询本公司指定IP规则；不返回自由备注或其他公司的规则。", IpRuleDetailArguments, _get_ip_rule),
        ToolSpec("erp_assistant_get_admin_config", "assistant_admin", "仅限管理员查询助手启用状态、密钥是否已配置和受控限额；不返回密钥、模型名、服务地址或价格。", EmptyQueryArguments, _get_admin_assistant_config),
        ToolSpec("erp_assistant_get_admin_usage", "assistant_admin", "仅限管理员分页查询本公司助手用量台账；不返回模型、工具名、失败文本或对话内容。", AssistantUsageArguments, _get_admin_assistant_usage),
        ToolSpec("erp_assistant_get_my_usage", "assistant_usage", "分页查询当前员工本人助手用量；不接受员工/公司参数，也不返回模型、工具名、失败文本或对话内容。", AssistantUsageArguments, _get_my_assistant_usage),
        ToolSpec("erp_list_my_approvals", "approval_business", "按当前员工本人待审批、已提交或已处理范围分页查询；不返回审批人身份、意见或原因。", MyApprovalSearchArguments, _list_my_approvals),
        ToolSpec("erp_get_my_approval_detail", "approval_business", "仅查询当前员工参与或可办理的审批单据；固定外发单据摘要及审批状态，不返回意见、原因、身份或无限明细。", MyApprovalDetailArguments, _get_my_approval_detail),
    )
}


def visible_tool_names(identity: McpIdentity) -> list[str]:
    names = []
    permissions, roles = set(identity.permissions), set(identity.roles)
    for name, spec in MCP_TOOL_SPECS.items():
        capability = CAPABILITIES[spec.capability]
        if name not in identity.tools:
            continue
        required_permissions = (
            ({capability.permission} if capability.permission else set())
            if isinstance(capability.permission, str)
            else set(capability.permission)
        )
        required_permissions = {
            {
                "approval:configure": "module:approval",
                "settings:manage": "module:settings",
            }.get(permission, permission)
            for permission in required_permissions
        }
        if required_permissions and "*" not in permissions and not required_permissions.intersection(permissions):
            continue
        if capability.roles and "ADMIN" not in roles and not roles.intersection(capability.roles):
            continue
        names.append(name)
    if ACCESS_TOOL in identity.tools:
        names.append(ACCESS_TOOL)
    return sorted(names)


def get_tool(name: str) -> ToolSpec | None:
    return MCP_TOOL_SPECS.get(name)

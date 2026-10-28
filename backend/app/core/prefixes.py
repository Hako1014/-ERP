from enum import Enum


class SerialPrefix(str, Enum):
    """单据编号前缀，全局唯一，禁止各模块自造编号。"""

    PURCHASE_PLAN = "CGJH"      # 采购计划
    PURCHASE_ORDER = "CGDD"     # 采购订单
    PURCHASE_PREPAY = "YFSQ"    # 采购预付款申请
    PURCHASE_RETURN = "CGTH"    # 采购退货单
    PURCHASE_INVOICE = "CGFP"   # 采购发票
    INCOME = "SRD"              # 收入单
    STOCK_IN = "RKD"            # 入库单
    STOCK_OUT = "CKD"           # 出库单
    TRANSFER = "DBD"            # 调拨单
    TRANSFER_OUT = "DBC"        # 调拨出库单
    TRANSFER_IN = "DBR"         # 调拨入库单
    STOCK_CHECK = "PDD"         # 盘点单
    LOCATION_ADJUST = "KWTZ"    # 库位调整单
    COST_REWRITE = "CBHX"       # 库存成本回写批次
    ORDER = "DD"                # 订单
    RETURN_ORDER = "THD"        # 售后退货单
    EXCHANGE_ORDER = "HHD"      # 换货单
    WAVE = "BC"                 # 波次
    PRINT_BATCH = "DYPC"        # 打印批次
    WAYBILL = "YDH"             # 本地运单号（真实取号在物流对接后覆盖）
    SHIPMENT = "FHD"            # 订单包裹发货记录
    VOUCHER = "PZ"              # 会计凭证
    PAYMENT = "FKD"             # 付款单
    RECEIPT = "SKD"             # 收款单
    EXPENSE = "FYD"             # 费用单
    SETTLEMENT = "JSD"          # 结算单
    INVOICE = "FPD"             # 发票

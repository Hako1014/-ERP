from decimal import ROUND_HALF_UP, Decimal

MONEY_QUANT = Decimal("0.01")


def to_decimal(value: Decimal | str | int) -> Decimal:
    """将金额输入转换为 Decimal，禁止 float 参与金额计算。"""
    if isinstance(value, float):
        raise TypeError("金额禁止使用 float，请使用 Decimal 或字符串")
    return Decimal(str(value))


def round_money(value: Decimal | str | int) -> Decimal:
    """将金额四舍五入到分（两位小数，四舍五入）。"""
    return to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

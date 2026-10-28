"""会计科目与项目核算类别服务（TASK-042）。

科目字段与金蝶财务系统常用科目结构保持一致，种子数据采用幂等导入：
已存在的科目默认跳过，不覆盖财务人员后续维护的名称、辅助核算或启停状态。
具体金蝶账套版本仍需客户确认，种子版本在接口元数据中明确标注。
"""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.models.finance import AccountSubject, ProjectCategory, VoucherItem
from app.models.org import Staff

SUBJECT_FIELDS = {
    "code",
    "name",
    "category",
    "balance_direction",
    "is_auxiliary",
    "is_qty_amount",
    "measure_unit",
    "is_cash",
    "is_bank",
    "is_journal",
    "is_cash_equivalent",
    "project_category_id",
    "is_enabled",
}
PROJECT_CATEGORY_FIELDS = {"code", "name", "remark"}

SUBJECT_CATEGORIES: dict[str, str] = {
    "ASSET": "资产",
    "LIABILITY": "负债",
    "EQUITY": "所有者权益",
    "COST": "成本",
    "PROFIT_LOSS": "损益",
}
BALANCE_DIRECTIONS: dict[str, str] = {
    "DEBIT": "借方",
    "CREDIT": "贷方",
}

KINGDEE_SEED_VERSION = "KINGDEE_STANDARD_CHART_V1"
KINGDEE_SEED_NOTE = "金蝶常用一级科目模板，具体账套版本待客户确认"

# 代码、名称和方向参考中国小企业/企业会计准则常用一级科目，导入时保持幂等。
KINGDEE_SUBJECT_SEED: tuple[dict[str, Any], ...] = (
    {"code": "1001", "name": "库存现金", "category": "ASSET", "balance_direction": "DEBIT", "is_cash": True},
    {"code": "1002", "name": "银行存款", "category": "ASSET", "balance_direction": "DEBIT", "is_bank": True},
    {"code": "1012", "name": "其他货币资金", "category": "ASSET", "balance_direction": "DEBIT", "is_cash_equivalent": True},
    {"code": "1101", "name": "交易性金融资产", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1121", "name": "应收票据", "category": "ASSET", "balance_direction": "DEBIT", "is_auxiliary": True},
    {"code": "1122", "name": "应收账款", "category": "ASSET", "balance_direction": "DEBIT", "is_auxiliary": True},
    {"code": "1123", "name": "预付账款", "category": "ASSET", "balance_direction": "DEBIT", "is_auxiliary": True},
    {"code": "1131", "name": "应收股利", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1132", "name": "应收利息", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1221", "name": "其他应收款", "category": "ASSET", "balance_direction": "DEBIT", "is_auxiliary": True},
    {"code": "1231", "name": "坏账准备", "category": "ASSET", "balance_direction": "CREDIT"},
    {"code": "1401", "name": "材料采购", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1402", "name": "在途物资", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1403", "name": "原材料", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1405", "name": "库存商品", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1408", "name": "委托加工物资", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1471", "name": "存货跌价准备", "category": "ASSET", "balance_direction": "CREDIT"},
    {"code": "1501", "name": "债权投资", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1511", "name": "长期股权投资", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1601", "name": "固定资产", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1602", "name": "累计折旧", "category": "ASSET", "balance_direction": "CREDIT"},
    {"code": "1604", "name": "在建工程", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1606", "name": "固定资产清理", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1701", "name": "无形资产", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1702", "name": "累计摊销", "category": "ASSET", "balance_direction": "CREDIT"},
    {"code": "1801", "name": "长期待摊费用", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1811", "name": "递延所得税资产", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "1901", "name": "待处理财产损溢", "category": "ASSET", "balance_direction": "DEBIT"},
    {"code": "2001", "name": "短期借款", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2201", "name": "应付票据", "category": "LIABILITY", "balance_direction": "CREDIT", "is_auxiliary": True},
    {"code": "2202", "name": "应付账款", "category": "LIABILITY", "balance_direction": "CREDIT", "is_auxiliary": True},
    {"code": "2203", "name": "预收账款", "category": "LIABILITY", "balance_direction": "CREDIT", "is_auxiliary": True},
    {"code": "2211", "name": "应付职工薪酬", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2221", "name": "应交税费", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2231", "name": "应付利息", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2232", "name": "应付股利", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2241", "name": "其他应付款", "category": "LIABILITY", "balance_direction": "CREDIT", "is_auxiliary": True},
    {"code": "2501", "name": "长期借款", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2502", "name": "应付债券", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "2701", "name": "长期应付款", "category": "LIABILITY", "balance_direction": "CREDIT"},
    {"code": "4001", "name": "实收资本", "category": "EQUITY", "balance_direction": "CREDIT"},
    {"code": "4002", "name": "资本公积", "category": "EQUITY", "balance_direction": "CREDIT"},
    {"code": "4101", "name": "盈余公积", "category": "EQUITY", "balance_direction": "CREDIT"},
    {"code": "4103", "name": "本年利润", "category": "EQUITY", "balance_direction": "CREDIT"},
    {"code": "4104", "name": "利润分配", "category": "EQUITY", "balance_direction": "CREDIT"},
    {"code": "5001", "name": "生产成本", "category": "COST", "balance_direction": "DEBIT"},
    {"code": "5101", "name": "制造费用", "category": "COST", "balance_direction": "DEBIT"},
    {"code": "5201", "name": "劳务成本", "category": "COST", "balance_direction": "DEBIT"},
    {"code": "5301", "name": "研发支出", "category": "COST", "balance_direction": "DEBIT"},
    {"code": "6001", "name": "主营业务收入", "category": "PROFIT_LOSS", "balance_direction": "CREDIT"},
    {"code": "6051", "name": "其他业务收入", "category": "PROFIT_LOSS", "balance_direction": "CREDIT"},
    {"code": "6101", "name": "公允价值变动损益", "category": "PROFIT_LOSS", "balance_direction": "CREDIT"},
    {"code": "6111", "name": "投资收益", "category": "PROFIT_LOSS", "balance_direction": "CREDIT"},
    {"code": "6301", "name": "营业外收入", "category": "PROFIT_LOSS", "balance_direction": "CREDIT"},
    {"code": "6401", "name": "主营业务成本", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6402", "name": "其他业务成本", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6403", "name": "税金及附加", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6601", "name": "销售费用", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6602", "name": "管理费用", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6603", "name": "财务费用", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6701", "name": "资产减值损失", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6711", "name": "营业外支出", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
    {"code": "6801", "name": "所得税费用", "category": "PROFIT_LOSS", "balance_direction": "DEBIT"},
)


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _require_text(value: Any, label: str, max_length: int) -> str:
    if value is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}"
        )
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _require_text(value, label, max_length)


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc
    if result < 1:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为正整数")
    return result


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


def _normalize_category(value: Any) -> str | None:
    if value in (None, ""):
        return None
    category = _require_text(value, "科目类别", 32).upper()
    if category not in SUBJECT_CATEGORIES:
        raise BizException(ErrorCode.BAD_REQUEST, f"科目类别非法：{value}")
    return category


def _normalize_balance_direction(value: Any) -> str:
    direction = _require_text(value, "余额方向", 8).upper()
    if direction not in BALANCE_DIRECTIONS:
        raise BizException(ErrorCode.BAD_REQUEST, "余额方向仅支持 DEBIT 或 CREDIT")
    return direction


def get_project_category_or_404(
    db: Session, category_id: int
) -> ProjectCategory:
    category = db.get(ProjectCategory, category_id)
    if category is None:
        raise BizException(ErrorCode.NOT_FOUND, "项目核算类别不存在")
    return category


def get_subject_or_404(db: Session, subject_id: int) -> AccountSubject:
    subject = db.get(AccountSubject, subject_id)
    if subject is None:
        raise BizException(ErrorCode.NOT_FOUND, "会计科目不存在")
    return subject


def project_category_dict(category: ProjectCategory) -> dict:
    return {
        "id": category.id,
        "code": category.code,
        "name": category.name,
        "remark": category.remark,
        "created_at": category.created_at,
        "updated_at": category.updated_at,
    }


def subject_dict(
    db: Session, subject: AccountSubject
) -> dict:
    project_category = (
        db.get(ProjectCategory, subject.project_category_id)
        if subject.project_category_id
        else None
    )
    return {
        "id": subject.id,
        "code": subject.code,
        "name": subject.name,
        "category": subject.category,
        "category_name": SUBJECT_CATEGORIES.get(subject.category or ""),
        "balance_direction": subject.balance_direction,
        "balance_direction_name": BALANCE_DIRECTIONS.get(
            subject.balance_direction
        ),
        "is_auxiliary": subject.is_auxiliary,
        "is_qty_amount": subject.is_qty_amount,
        "measure_unit": subject.measure_unit,
        "is_cash": subject.is_cash,
        "is_bank": subject.is_bank,
        "is_journal": subject.is_journal,
        "is_cash_equivalent": subject.is_cash_equivalent,
        "project_category_id": subject.project_category_id,
        "project_category_code": (
            project_category.code if project_category else None
        ),
        "project_category_name": (
            project_category.name if project_category else None
        ),
        "is_enabled": subject.is_enabled,
        "created_at": subject.created_at,
        "updated_at": subject.updated_at,
    }


def list_meta() -> dict:
    return {
        "subject_categories": [
            {"code": code, "name": name}
            for code, name in SUBJECT_CATEGORIES.items()
        ],
        "balance_directions": [
            {"code": code, "name": name}
            for code, name in BALANCE_DIRECTIONS.items()
        ],
        "seed_version": KINGDEE_SEED_VERSION,
        "seed_note": KINGDEE_SEED_NOTE,
    }


def list_project_categories(db: Session) -> list[dict]:
    categories = list(
        db.scalars(
            select(ProjectCategory).order_by(ProjectCategory.code)
        )
    )
    return [project_category_dict(category) for category in categories]


def create_project_category(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ProjectCategory:
    _check_unknown_fields(data, PROJECT_CATEGORY_FIELDS)
    code = _require_text(data.get("code"), "类别代码", 32)
    name = _require_text(data.get("name"), "类别名称", 64)
    if db.scalar(
        select(ProjectCategory.id).where(ProjectCategory.code == code)
    ):
        raise BizException(ErrorCode.CONFLICT, f"项目核算类别代码已存在：{code}")

    category = ProjectCategory(
        code=code,
        name=name,
        remark=_optional_text(data.get("remark"), "备注", 255),
    )
    db.add(category)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="project_category",
        target_id=str(category.id),
        summary=f"新增项目核算类别：{category.code} {category.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return category


def update_project_category(
    db: Session,
    *,
    category_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ProjectCategory:
    _check_unknown_fields(data, PROJECT_CATEGORY_FIELDS)
    category = get_project_category_or_404(db, category_id)

    if "code" in data:
        code = _require_text(data["code"], "类别代码", 32)
        duplicate = db.scalar(
            select(ProjectCategory.id).where(
                ProjectCategory.code == code,
                ProjectCategory.id != category.id,
            )
        )
        if duplicate:
            raise BizException(
                ErrorCode.CONFLICT, f"项目核算类别代码已存在：{code}"
            )
        category.code = code
    if "name" in data:
        category.name = _require_text(data["name"], "类别名称", 64)
    if "remark" in data:
        category.remark = _optional_text(data["remark"], "备注", 255)

    write_audit(
        db,
        action="UPDATE",
        target_type="project_category",
        target_id=str(category.id),
        summary=f"修改项目核算类别：{category.code} {category.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return category


def delete_project_category(
    db: Session,
    *,
    category_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    category = get_project_category_or_404(db, category_id)
    used_count = db.scalar(
        select(func.count(AccountSubject.id)).where(
            AccountSubject.project_category_id == category.id
        )
    )
    if used_count:
        raise BizException(
            ErrorCode.CONFLICT,
            f"项目核算类别已被 {used_count} 个会计科目使用，不能删除",
        )
    db.delete(category)
    write_audit(
        db,
        action="DELETE",
        target_type="project_category",
        target_id=str(category.id),
        summary=f"删除项目核算类别：{category.code} {category.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _validate_project_category(
    db: Session, project_category_id: int | None
) -> None:
    if project_category_id is not None:
        get_project_category_or_404(db, project_category_id)


def list_subjects(
    db: Session,
    *,
    keyword: str | None = None,
    category: str | None = None,
    balance_direction: str | None = None,
    is_enabled: bool | None = None,
    is_auxiliary: bool | None = None,
    is_qty_amount: bool | None = None,
    project_category_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                AccountSubject.code.like(like),
                AccountSubject.name.like(like),
            )
        )
    if category is not None:
        conditions.append(AccountSubject.category == _normalize_category(category))
    if balance_direction is not None:
        conditions.append(
            AccountSubject.balance_direction
            == _normalize_balance_direction(balance_direction)
        )
    if is_enabled is not None:
        conditions.append(AccountSubject.is_enabled.is_(is_enabled))
    if is_auxiliary is not None:
        conditions.append(AccountSubject.is_auxiliary.is_(is_auxiliary))
    if is_qty_amount is not None:
        conditions.append(AccountSubject.is_qty_amount.is_(is_qty_amount))
    if project_category_id is not None:
        conditions.append(
            AccountSubject.project_category_id == project_category_id
        )

    total = db.scalar(
        select(func.count(AccountSubject.id)).where(*conditions)
    ) or 0
    subjects = list(
        db.scalars(
            select(AccountSubject)
            .where(*conditions)
            .order_by(AccountSubject.code)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [subject_dict(db, subject) for subject in subjects],
    }


def create_subject(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> AccountSubject:
    _check_unknown_fields(data, SUBJECT_FIELDS)
    code = _require_text(data.get("code"), "科目代码", 64)
    if db.scalar(
        select(AccountSubject.id).where(AccountSubject.code == code)
    ):
        raise BizException(ErrorCode.CONFLICT, f"会计科目代码已存在：{code}")

    category = _normalize_category(data.get("category"))
    balance_direction = _normalize_balance_direction(
        data.get("balance_direction", "DEBIT")
    )
    is_qty_amount = _to_bool(
        data.get("is_qty_amount", False), "数量金额辅助核算"
    )
    measure_unit = _optional_text(data.get("measure_unit"), "计量单位", 32)
    if is_qty_amount and measure_unit is None:
        raise BizException(
            ErrorCode.BAD_REQUEST, "启用数量金额辅助核算时必须填写计量单位"
        )
    project_category_id = _optional_id(
        data.get("project_category_id"), "核算项目"
    )
    _validate_project_category(db, project_category_id)

    subject = AccountSubject(
        code=code,
        name=_require_text(data.get("name"), "科目名称", 128),
        category=category,
        balance_direction=balance_direction,
        is_auxiliary=_to_bool(data.get("is_auxiliary", False), "往来业务核算"),
        is_qty_amount=is_qty_amount,
        measure_unit=measure_unit,
        is_cash=_to_bool(data.get("is_cash", False), "现金科目"),
        is_bank=_to_bool(data.get("is_bank", False), "银行科目"),
        is_journal=_to_bool(data.get("is_journal", False), "出日记账"),
        is_cash_equivalent=_to_bool(
            data.get("is_cash_equivalent", False), "现金等价物"
        ),
        project_category_id=project_category_id,
        is_enabled=_to_bool(data.get("is_enabled", True), "是否启用"),
    )
    db.add(subject)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="account_subject",
        target_id=str(subject.id),
        summary=f"新增会计科目：{subject.code} {subject.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return subject


def update_subject(
    db: Session,
    *,
    subject_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> AccountSubject:
    _check_unknown_fields(data, SUBJECT_FIELDS)
    subject = get_subject_or_404(db, subject_id)

    if "code" in data:
        code = _require_text(data["code"], "科目代码", 64)
        duplicate = db.scalar(
            select(AccountSubject.id).where(
                AccountSubject.code == code,
                AccountSubject.id != subject.id,
            )
        )
        if duplicate:
            raise BizException(ErrorCode.CONFLICT, f"会计科目代码已存在：{code}")
        subject.code = code
    if "name" in data:
        subject.name = _require_text(data["name"], "科目名称", 128)
    if "category" in data:
        subject.category = _normalize_category(data["category"])
    if "balance_direction" in data:
        subject.balance_direction = _normalize_balance_direction(
            data["balance_direction"]
        )
    for field, label in (
        ("is_auxiliary", "往来业务核算"),
        ("is_qty_amount", "数量金额辅助核算"),
        ("is_cash", "现金科目"),
        ("is_bank", "银行科目"),
        ("is_journal", "出日记账"),
        ("is_cash_equivalent", "现金等价物"),
        ("is_enabled", "是否启用"),
    ):
        if field in data:
            setattr(subject, field, _to_bool(data[field], label))
    if "measure_unit" in data:
        subject.measure_unit = _optional_text(data["measure_unit"], "计量单位", 32)
    if "project_category_id" in data:
        project_category_id = _optional_id(data["project_category_id"], "核算项目")
        _validate_project_category(db, project_category_id)
        subject.project_category_id = project_category_id

    if subject.is_qty_amount and not subject.measure_unit:
        raise BizException(
            ErrorCode.BAD_REQUEST, "启用数量金额辅助核算时必须填写计量单位"
        )

    write_audit(
        db,
        action="UPDATE",
        target_type="account_subject",
        target_id=str(subject.id),
        summary=f"修改会计科目：{subject.code} {subject.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return subject


def delete_subject(
    db: Session,
    *,
    subject_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    subject = get_subject_or_404(db, subject_id)
    used_count = db.scalar(
        select(func.count(VoucherItem.id)).where(
            VoucherItem.subject_id == subject.id
        )
    )
    if used_count:
        raise BizException(
            ErrorCode.CONFLICT,
            f"会计科目已被 {used_count} 条凭证分录使用，不能删除，请改为停用",
        )
    db.delete(subject)
    write_audit(
        db,
        action="DELETE",
        target_type="account_subject",
        target_id=str(subject.id),
        summary=f"删除会计科目：{subject.code} {subject.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def import_kingdee_subjects(
    db: Session,
    *,
    staff: Staff,
    update_existing: bool = False,
    commit: bool = True,
) -> dict:
    """幂等导入金蝶常用一级科目。

    默认只新增缺失科目，`update_existing=True` 时才用种子补齐字段。
    """
    created = 0
    updated = 0
    skipped = 0
    from app.integrations.kingdee import get_kingdee_adapter

    source_subjects = get_kingdee_adapter().list_subjects()
    if not isinstance(source_subjects, list) or any(
        not isinstance(item, dict) or not item.get("code") or not item.get("name")
        for item in source_subjects
    ):
        raise BizException(ErrorCode.CONFLICT, "金蝶科目数据格式错误")
    for item in source_subjects:
        subject = db.scalar(
            select(AccountSubject).where(
                AccountSubject.code == item["code"]
            )
        )
        if subject is None:
            subject = AccountSubject(
                code=item["code"],
                name=item["name"],
                category=item.get("category"),
                balance_direction=item.get("balance_direction", "DEBIT"),
                is_auxiliary=item.get("is_auxiliary", False),
                is_qty_amount=item.get("is_qty_amount", False),
                measure_unit=item.get("measure_unit"),
                is_cash=item.get("is_cash", False),
                is_bank=item.get("is_bank", False),
                is_journal=item.get("is_journal", False),
                is_cash_equivalent=item.get("is_cash_equivalent", False),
                project_category_id=item.get("project_category_id"),
                is_enabled=True,
            )
            db.add(subject)
            created += 1
            continue
        if not update_existing:
            skipped += 1
            continue
        for field in (
            "name",
            "category",
            "balance_direction",
            "is_auxiliary",
            "is_qty_amount",
            "measure_unit",
            "is_cash",
            "is_bank",
            "is_journal",
            "is_cash_equivalent",
        ):
            if field in item:
                setattr(subject, field, item[field])
        updated += 1

    db.flush()
    write_audit(
        db,
        action="IMPORT",
        target_type="account_subject",
        target_id="seed",
        summary=f"导入金蝶科目种子：{KINGDEE_SEED_VERSION}",
        detail={
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "update_existing": update_existing,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "seed_version": KINGDEE_SEED_VERSION,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_seed": len(source_subjects),
    }

"""Prepare a dedicated TASK-088 MySQL schema without touching the ERP schema.

This script is intentionally opt-in and refuses any target other than the
fixed ``erp_task088_test`` schema. It never drops or truncates data.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings

TARGET_DATABASE = "erp_task088_test"
SOURCE_DATABASE = "erp"
COMPANY_NAME = "TASK-088 隔离测试公司"
ROLE_ACCOUNTS = {
    "ADMIN": ("系统管理员.md", "系统管理员"),
    "VP": ("分管副总.md", "分管副总"),
    "FINANCE": ("财务.md", "财务"),
    "PURCHASER": ("采购员.md", "采购员"),
    "WAREHOUSE": ("仓库人员.md", "仓库人员"),
    "QUALITY": ("商控质检.md", "商控/质检"),
    "AFTER_SALE": ("售后客服.md", "售后/客服"),
}


def _credentials(path: Path) -> tuple[str, str]:
    content = path.read_text(encoding="utf-8")
    account = re.search(r"^- 登录账号：`([^`]+)`$", content, re.MULTILINE)
    password = re.search(r"^- 登录密码：`([^`]+)`$", content, re.MULTILINE)
    if not account or not password:
        raise RuntimeError(f"角色账号文件缺少字段：{path.name}")
    return account.group(1), password.group(1)


def _database_url(source_url: URL) -> URL:
    if not source_url.drivername.startswith("mysql"):
        raise RuntimeError("TASK-088 真实数据库验收只允许 MySQL")
    if source_url.database not in {SOURCE_DATABASE, TARGET_DATABASE}:
        raise RuntimeError("来源连接数据库不是约定的 erp；为安全起见拒绝继续")
    return source_url.set(database=TARGET_DATABASE)


def _seed(target_url: URL) -> None:
    from app.core.security import hash_password
    from app.models.basic import Unit
    from app.models.org import Company, Department, Staff
    from app.models.party import Supplier
    from app.models.product import Product
    from app.models.rbac import Role, StaffRole

    # Ensure SQLAlchemy has imported all model declarations for schema checks.
    import app.models  # noqa: F401

    engine = create_engine(target_url, pool_pre_ping=True, future=True)
    accounts_dir = Path(__file__).resolve().parents[2] / "docs" / "角色账号"
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            companies = list(db.scalars(select(Company)))
            existing_company = next(
                (company for company in companies if company.full_name == COMPANY_NAME),
                None,
            )
            if existing_company is not None:
                expected_accounts = {
                    _credentials(accounts_dir / filename)[0]
                    for filename, _name in ROLE_ACCOUNTS.values()
                }
                actual_accounts = set(
                    db.scalars(
                        select(Staff.login_account).where(
                            Staff.company_id == existing_company.id
                        )
                    )
                )
                supplier = db.scalar(
                    select(Supplier).where(Supplier.code == "TASK088-SUP-001")
                )
                products = set(
                    db.scalars(
                        select(Product.code).where(
                            Product.code.in_(
                                ("TASK088-P-001", "TASK088-P-002")
                            )
                        )
                    )
                )
                if (
                    expected_accounts.issubset(actual_accounts)
                    and supplier is not None
                    and products == {"TASK088-P-001", "TASK088-P-002"}
                ):
                    print("TASK-088 隔离测试库已初始化；保留现有测试记录。")
                    return
                raise RuntimeError(
                    "目标库已有数据但 TASK-088 测试种子不完整；未改动该库，请人工核查。"
                )
            if companies:
                raise RuntimeError(
                    "目标库包含非 TASK-088 公司数据；为避免覆盖或混用，拒绝写入。"
                )
            existing_rows = {
                table: int(db.scalar(text(f"SELECT COUNT(*) FROM `{table}`")) or 0)
                for table in ("staff", "role", "supplier", "product", "purchase_plan")
            }
            if any(existing_rows.values()):
                raise RuntimeError(
                    "目标库已包含员工、角色、主数据或采购计划记录；拒绝混用。"
                )

            company = Company(full_name=COMPANY_NAME, short_name="TASK088")
            db.add(company)
            db.flush()
            department = Department(company_id=company.id, name="TASK-088 测试部")
            unit = Unit(name="个", code="TASK088-PIECE")
            supplier = Supplier(
                code="TASK088-SUP-001",
                name="TASK-088 测试供应商",
                is_active=True,
            )
            db.add_all([department, unit, supplier])
            db.flush()

            for role_code, (_filename, role_name) in ROLE_ACCOUNTS.items():
                db.add(Role(company_id=company.id, code=role_code, name=role_name))
            db.flush()

            roles = {
                role.code: role
                for role in db.scalars(select(Role).where(Role.company_id == company.id))
            }
            for role_code, (filename, role_name) in ROLE_ACCOUNTS.items():
                account, password = _credentials(accounts_dir / filename)
                staff = Staff(
                    company_id=company.id,
                    department_id=department.id,
                    name=role_name,
                    staff_no=f"TASK088-{role_code}",
                    login_account=account,
                    password_hash=hash_password(password),
                    is_active=True,
                    is_distribution=False,
                )
                db.add(staff)
                db.flush()
                db.add(StaffRole(staff_id=staff.id, role_id=roles[role_code].id))

            db.add_all(
                [
                    Product(
                        name="TASK-088 同名测试商品",
                        code="TASK088-P-001",
                        default_supplier_id=supplier.id,
                        main_unit_id=unit.id,
                        measure_type="SINGLE",
                        is_disabled=False,
                    ),
                    Product(
                        name="TASK-088 同名测试商品",
                        code="TASK088-P-002",
                        default_supplier_id=supplier.id,
                        main_unit_id=unit.id,
                        measure_type="SINGLE",
                        is_disabled=False,
                    ),
                ]
            )
            db.commit()
            print(
                "已在 erp_task088_test 初始化 7 个本地角色账号、1 个测试供应商、"
                "2 个同名商品和单位；未写入 erp。"
            )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-create",
        required=True,
        choices=(TARGET_DATABASE,),
        help=f"显式确认仅创建/迁移/初始化 {TARGET_DATABASE}；不会删除已有数据",
    )
    args = parser.parse_args()
    if args.confirm_create != TARGET_DATABASE:
        raise RuntimeError("测试库名称确认失败")

    source_url = make_url(get_settings().database_url)
    target_url = _database_url(source_url)
    root_url = source_url.set(database=None)
    engine = create_engine(root_url, pool_pre_ping=True, future=True)
    try:
        with engine.begin() as connection:
            current_database, server_version = connection.execute(
                text("SELECT DATABASE(), VERSION()")
            ).one()
            if current_database not in {SOURCE_DATABASE, None}:
                raise RuntimeError("来源连接意外指向其他数据库；拒绝创建测试库")
            exists = bool(
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.schemata "
                        "WHERE schema_name = :name"
                    ),
                    {"name": TARGET_DATABASE},
                )
            )
            if not exists:
                # Identifier is a fixed constant selected by argparse, not user input.
                connection.exec_driver_sql(
                    f"CREATE DATABASE `{TARGET_DATABASE}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            else:
                table_count = int(
                    connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_schema = :name"
                        ),
                        {"name": TARGET_DATABASE},
                    )
                    or 0
                )
                if table_count and not connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = :name AND table_name = 'alembic_version'"
                    ),
                    {"name": TARGET_DATABASE},
                ):
                    raise RuntimeError(
                        "目标库已存在非本项目表且没有 Alembic 标记；拒绝改动。"
                    )
            print(
                f"已确认 MySQL {server_version}；迁移目标严格限定为 {TARGET_DATABASE}。"
            )
    finally:
        engine.dispose()

    os.environ["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        target_url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.upgrade(alembic_config, "head")
    _seed(target_url)


if __name__ == "__main__":
    main()

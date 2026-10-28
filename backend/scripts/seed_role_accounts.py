"""为系统角色创建可重复执行的演示账号。

账号使用角色名称的拼音，密码统一为 123456。脚本只维护映射表中的
角色账号，不修改其他员工账号。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.org import Staff
from app.models.rbac import Role, StaffRole

DEFAULT_PASSWORD = "123456"
ROLE_ACCOUNTS: dict[str, str] = {
    # 系统管理员
    "ADMIN": "guanliyuan",
    # 分管副总
    "VP": "fuzong",
    # 财务
    "FINANCE": "caiwu",
    # 采购员
    "PURCHASER": "caigouyuan",
    # 仓库人员
    "WAREHOUSE": "cangku",
    # 商控 / 质检
    "QUALITY": "zhijian",
    # 售后 / 客服
    "AFTER_SALE": "shouhou",
}


@dataclass(frozen=True)
class SeedResult:
    action: str
    role_code: str
    account: str
    staff_id: int


def seed_role_accounts(
    db: Session,
    *,
    password: str = DEFAULT_PASSWORD,
    dry_run: bool = False,
) -> list[SeedResult]:
    results: list[SeedResult] = []

    for role_code, account in ROLE_ACCOUNTS.items():
        role = db.scalar(select(Role).where(Role.code == role_code))
        if role is None:
            raise RuntimeError(f"角色不存在：{role_code}")

        staff = db.scalar(
            select(Staff).where(Staff.login_account == account)
        )
        action = "CREATE" if staff is None else "UPDATE"
        staff_no = f"ROLE-{role_code}"
        staff_no_owner = db.scalar(
            select(Staff).where(Staff.staff_no == staff_no)
        )
        if (
            staff_no_owner is not None
            and (staff is None or staff_no_owner.id != staff.id)
        ):
            raise RuntimeError(
                f"员工编号冲突：{staff_no} 已被账号 "
                f"{staff_no_owner.login_account or staff_no_owner.id} 使用"
            )

        if staff is None:
            staff = Staff(
                company_id=role.company_id,
                name=role.name,
                staff_no=staff_no,
                login_account=account,
                password_hash=hash_password(password),
                remark=f"系统角色：{role.code}（{role.name}）",
                is_distribution=False,
                is_active=True,
            )
            db.add(staff)
            db.flush()
        else:
            staff.company_id = role.company_id
            staff.name = role.name
            staff.staff_no = staff.staff_no or staff_no
            staff.password_hash = hash_password(password)
            staff.remark = f"系统角色：{role.code}（{role.name}）"
            staff.is_active = True

        link = db.scalar(
            select(StaffRole).where(
                StaffRole.staff_id == staff.id,
                StaffRole.role_id == role.id,
            )
        )
        if link is None:
            db.add(StaffRole(staff_id=staff.id, role_id=role.id))

        results.append(
            SeedResult(
                action=action,
                role_code=role.code,
                account=account,
                staff_id=staff.id,
            )
        )

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="创建系统角色演示账号")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验并显示将要执行的操作，不提交事务",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        results = seed_role_accounts(db, dry_run=args.dry_run)

    for item in results:
        print(
            f"{item.action:6} {item.role_code:10} "
            f"{item.account:12} staff_id={item.staff_id}"
        )
    if args.dry_run:
        print("dry-run completed")


if __name__ == "__main__":
    main()

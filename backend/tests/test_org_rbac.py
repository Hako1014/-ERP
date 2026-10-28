import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  注册全部模型
from app.db.base import Base
from app.models.org import Company, Staff
from app.repositories.org_repo import get_staff_by_login_account, get_staff_by_phone


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_tables_created():
    tables = set(inspect(_engine()).get_table_names())
    for name in [
        "company",
        "department",
        "position",
        "staff",
        "role",
        "permission",
        "staff_role",
        "role_permission",
        "data_scope",
        "ip_rule",
    ]:
        assert name in tables


def test_staff_unique_indexes():
    cols = {
        col
        for uniq in inspect(_engine()).get_unique_constraints("staff")
        for col in uniq["column_names"]
    }
    assert {"staff_no", "login_account", "phone"} <= cols


def test_staff_duplicate_login_rejected():
    Session = sessionmaker(bind=_engine())
    with Session() as db:
        db.add(Staff(company_id=1, name="张三", login_account="zhangsan", phone="13800000001"))
        db.add(Staff(company_id=1, name="李四", login_account="zhangsan", phone="13800000002"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_org_repo_lookup():
    engine = _engine()
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Company(id=1, full_name="示例公司"))
        db.add(Staff(company_id=1, name="张三", login_account="zs", phone="13800000001"))
        db.commit()
    with Session() as db:
        assert get_staff_by_login_account(db, "zs").name == "张三"
        assert get_staff_by_phone(db, "13800000001").name == "张三"

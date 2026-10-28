from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# id 列（主键/外键）统一类型：MySQL 用 BIGINT，SQLite 测试用 INTEGER 以支持自增。
BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CompanyOwnedBase(Base):
    """具有公司归属字段的业务模型基类。"""

    __abstract__ = True

    company_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, nullable=True, index=True
    )


class TimestampMixin:
    """公共时间字段：应用层统一存 UTC，展示层转 Asia/Shanghai。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

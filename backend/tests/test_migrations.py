from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import mysql


def test_alembic_upgrade_head(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    db_path = (tmp_path / "migrate.db").as_posix()
    url = f"sqlite:///{db_path}"
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    head = ScriptDirectory.from_config(cfg).get_current_head()
    engine = create_engine(url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        assert context.get_current_revision() == head
    indexes = {
        index["name"] for index in inspect(engine).get_indexes("approval_record")
    }
    assert indexes >= {
        "ix_approval_record_approver_action_doc",
        "ix_approval_record_action_status_doc",
        "ix_approval_record_doc_history",
    }
    order_indexes = {index["name"] for index in inspect(engine).get_indexes("order")}
    assert "ix_order_sales_time" in order_indexes
    credential_columns = {
        column["name"]: column for column in inspect(engine).get_columns("mcp_credential")
    }
    assert credential_columns["expires_at"]["nullable"] is True
    for table in ("product", "order", "order_item", "purchase_plan", "stock_in"):
        assert "company_id" in {
            column["name"] for column in inspect(engine).get_columns(table)
        }
    assert "company_id" not in {
        column["name"] for column in inspect(engine).get_columns("company")
    }
    engine.dispose()


def test_legacy_company_ownership_upgrade_and_rollback(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    url = f"sqlite:///{(tmp_path / 'ownership.db').as_posix()}"
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "0029")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO company (id, full_name) VALUES (1, '迁移验收公司')"))
        conn.execute(text(
            "INSERT INTO audit_log (id, action, target_type) VALUES (1, 'TEST', 'migration')"
        ))

    command.upgrade(cfg, "0030")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT company_id FROM audit_log WHERE action = 'TEST'"
        )).scalar_one() == 1

    command.downgrade(cfg, "0029")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'TEST' AND target_type = 'migration'"
        )).scalar_one() == 1
        assert "company_id" not in {
            column["name"] for column in inspect(engine).get_columns("audit_log")
        }
    engine.dispose()


def test_approval_query_indexes_render_mysql_57_and_8_offline():
    import importlib.util

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0027_approval_query_indexes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "approval_query_indexes_0027", migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    expected_indexes = {
        "ix_approval_record_approver_action_doc",
        "ix_approval_record_action_status_doc",
        "ix_approval_record_doc_history",
    }
    for version in ((5, 7, 42), (8, 0, 36)):
        output = StringIO()
        dialect = mysql.dialect()
        dialect.server_version_info = version
        context = MigrationContext.configure(
            dialect=dialect,
            opts={"as_sql": True, "output_buffer": output},
        )
        with Operations.context(context):
            migration.upgrade()
        statements = output.getvalue().lower()
        assert statements.count("create index") == 3
        assert all(index in statements for index in expected_indexes)
        assert statements.count("on approval_record") == 3


def test_dashboard_order_time_index_renders_mysql_57_and_8_offline():
    import importlib.util

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0028_dashboard_order_time_index.py"
    )
    spec = importlib.util.spec_from_file_location(
        "dashboard_order_time_index_0028", migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for version in ((5, 7, 42), (8, 0, 36)):
        output = StringIO()
        dialect = mysql.dialect()
        dialect.server_version_info = version
        context = MigrationContext.configure(
            dialect=dialect,
            opts={"as_sql": True, "output_buffer": output},
        )
        with Operations.context(context):
            migration.upgrade()
        statements = output.getvalue().lower()
        assert statements.count("create index") == 1
        assert "ix_order_sales_time" in statements
        assert "on `order` (pay_status, pay_time, order_time)" in statements

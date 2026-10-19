"""Ordered SQLite migrations, applied atomically without rebuilding existing tables."""
from importlib.resources import files
import sqlite3

CURRENT_VERSION = 4


def migrate(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if not 0 <= version <= CURRENT_VERSION:
            raise ValueError("工作区数据库版本不兼容")
        for target, name in ((1, "001_initial.sql"), (2, "002_reference_access.sql"), (3, "003_research_workflow.sql"), (4, "004_admin_configuration.sql")):
            if target <= version:
                continue
            statement = ""
            for line in files(__package__).joinpath(name).read_text(encoding="utf-8").splitlines():
                statement += line + "\n"
                if sqlite3.complete_statement(statement):
                    db.execute(statement)
                    statement = ""
            if statement.strip():
                raise ValueError("数据库迁移脚本不完整")
            db.execute(f"PRAGMA user_version={target}")
        db.commit()
    except BaseException:
        db.rollback()
        raise

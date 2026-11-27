"""Migrations run at startup: new files are applied once, in order, and a failing one changes nothing."""
import shutil
from pathlib import Path

import pytest

from db import MIGRATIONS_DIR, db


@pytest.fixture
def migrations(tmp_path):
    """A copy of the real migrations folder, plus a cleanup of anything the test adds."""
    folder = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, folder)
    yield folder
    db.execute("DROP TABLE IF EXISTS migration_test_a, migration_test_b")
    db.execute("DELETE FROM schema_migrations WHERE version LIKE '9%%'")


def _exists(table: str) -> bool:
    return db._one("SELECT to_regclass(%s) IS NOT NULL", (table,))


def test_existing_schema_is_up_to_date():
    assert db.migrate() == []
    assert db.schema_version() == sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))[-1]


def test_new_migrations_are_applied_once_in_order(migrations: Path):
    (migrations / "901_add_b.sql").write_text("CREATE TABLE migration_test_b (a_id INT REFERENCES migration_test_a(id));")
    (migrations / "900_add_a.sql").write_text("CREATE TABLE migration_test_a (id INT PRIMARY KEY);")
    assert db.migrate(migrations) == ["900_add_a", "901_add_b"]  # 900 first, though written second
    assert _exists("migration_test_a") and _exists("migration_test_b")
    assert db.schema_version() == "901_add_b"
    assert db.migrate(migrations) == []  # nothing runs twice


def test_a_failing_migration_changes_nothing(migrations: Path):
    (migrations / "900_add_a.sql").write_text("CREATE TABLE migration_test_a (id INT PRIMARY KEY);")
    (migrations / "901_broken.sql").write_text("CREATE TABLE migration_test_b (id INT); SELECT no_such_column FROM users;")
    with pytest.raises(Exception):
        db.migrate(migrations)
    # The whole batch rolled back: neither table exists and neither version is recorded.
    assert not _exists("migration_test_a") and not _exists("migration_test_b")
    assert db._one("SELECT COUNT(*) FROM schema_migrations WHERE version LIKE '9%%'") == 0
    assert db.ping()  # the connection is still usable


def test_migrates_a_brand_new_database():
    """Startup on an empty database creates every table (run against a scratch schema)."""
    db.execute("DROP SCHEMA IF EXISTS migration_scratch CASCADE")
    db.execute("CREATE SCHEMA migration_scratch")
    try:
        db.execute("SET search_path TO migration_scratch")
        assert db.schema_version() is None
        assert db.migrate() == sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
        tables = {r["table_name"] for r in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'migration_scratch'")}
        assert {"users", "categories", "user_interests", "providers", "articles", "article_categories",
                "notifications", "settings", "schema_migrations"} <= tables
    finally:
        db.execute("SET search_path TO public")
        db.execute("DROP SCHEMA migration_scratch CASCADE")


def test_conflict_keyword_migration_keeps_admin_changes():
    """Migration 006 adds War & Conflict keywords but never overwrites a weight an admin set."""
    sql = (MIGRATIONS_DIR / "006_more_conflict_keywords.sql").read_text()
    war = db.category_by_key("war_conflict")
    original = war.keywords
    try:
        db.set_category_keywords(war.id, {"war": 0.6, "gaza": 0.1})  # an admin turned "gaza" down
        db.execute(sql)
        keywords = db.get_category(war.id).keywords
        assert keywords["gaza"] == 0.1 and keywords["war"] == 0.6   # kept
        assert keywords["rapid support forces"] == 0.9 and keywords["israeli strikes"] == 0.8  # added
    finally:
        db.set_category_keywords(war.id, original)

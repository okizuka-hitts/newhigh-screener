"""db 層のテスト(初期化の冪等性・接続・パラメータ化upsert)。"""

import sqlite3

import pytest

from screener import config
from screener.db import connect, init_db, upsert
from screener.db.schema import TABLES


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {r["name"] for r in rows}


def test_init_db_creates_three_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    names = _table_names(conn)
    assert {"listed_info", "daily_quotes", "statements"} <= names
    assert set(TABLES) == {"listed_info", "daily_quotes", "statements"}


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    init_db(conn)
    upsert(conn, "listed_info", [{"code": "13010", "company_name": "極洋"}])
    # 再初期化してもデータは消えず、エラーにもならない。
    init_db(conn)
    rows = conn.execute("SELECT code FROM listed_info").fetchall()
    assert [r["code"] for r in rows] == ["13010"]


def test_connect_enables_foreign_keys_and_row_factory(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    init_db(conn)
    upsert(conn, "listed_info", [{"code": "1", "company_name": "A"}])
    row = conn.execute("SELECT code, company_name FROM listed_info").fetchone()
    assert row["code"] == "1" and row["company_name"] == "A"


def test_connect_memory(monkeypatch):
    conn = connect(":memory:")
    init_db(conn)
    assert "listed_info" in _table_names(conn)


def test_connect_uses_config_default(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "screener.db"
    monkeypatch.setenv(config.DB_PATH_ENV, str(target))
    conn = connect()
    init_db(conn)
    assert target.exists()  # 親ディレクトリが作成されている


def test_upsert_roundtrip_daily_quotes(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    n = upsert(
        conn,
        "daily_quotes",
        [
            {"code": "1301", "date": "2026-07-10", "close": 100.0, "volume": 5.0},
            {"code": "1301", "date": "2026-07-09", "close": 99.0, "volume": 4.0},
        ],
    )
    assert n == 2
    rows = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? ORDER BY date", ("1301",)
    ).fetchall()
    assert [r["close"] for r in rows] == [99.0, 100.0]


def test_upsert_is_idempotent_and_updates_on_conflict(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    upsert(conn, "daily_quotes", [{"code": "1", "date": "2026-07-10", "close": 100.0}])
    # 同じ主キーで再upsert → 行は増えず、値が更新される。
    upsert(conn, "daily_quotes", [{"code": "1", "date": "2026-07-10", "close": 123.0}])
    rows = conn.execute("SELECT close FROM daily_quotes").fetchall()
    assert len(rows) == 1
    assert rows[0]["close"] == 123.0


def test_upsert_empty_rows_is_noop(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    assert upsert(conn, "daily_quotes", []) == 0


def test_upsert_unknown_table_raises(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    with pytest.raises(ValueError):
        upsert(conn, "malicious; DROP TABLE listed_info", [{"x": 1}])


def test_upsert_unknown_column_raises(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    with pytest.raises(ValueError):
        upsert(conn, "listed_info", [{"code": "1", "evil": "x"}])


def test_upsert_rejects_injection_in_value_safely(tmp_path):
    # 値に含まれるSQLはプレースホルダ経由なので実行されず、ただの文字列として保存される。
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    payload = "1'); DROP TABLE listed_info;--"
    upsert(conn, "listed_info", [{"code": payload, "company_name": "x"}])
    assert "listed_info" in _table_names(conn)
    row = conn.execute("SELECT code FROM listed_info").fetchone()
    assert row["code"] == payload


def test_statements_upsert(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    upsert(
        conn,
        "statements",
        [
            {
                "disclosure_number": "20260710123456",
                "code": "1301",
                "type_of_current_period": "1Q",
                "net_sales": 1000.0,
            }
        ],
    )
    row = conn.execute("SELECT code, net_sales FROM statements").fetchone()
    assert row["code"] == "1301" and row["net_sales"] == 1000.0


def test_schema_columns_match_ddl(tmp_path):
    # 許可リストの列がDDLの実列と一致していること(取りこぼし検出)。
    from screener.db.schema import TABLE_COLUMNS

    conn = connect(tmp_path / "t.db")
    init_db(conn)
    for table, cols in TABLE_COLUMNS.items():
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual = {r["name"] for r in info}
        assert set(cols) == actual, table


def test_raw_sqlite_connection_type(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert isinstance(conn, sqlite3.Connection)

"""DBスキーマ定義と初期化・パラメータ化upsert。

取得データを蓄積する3テーブル(上場銘柄一覧・日足・財務)を定義する。
`init_db` は冪等(何度実行しても同じ状態)。`upsert` は列名を固定の許可リストで
検証したうえでパラメータ化クエリを組み立て、値は必ずプレースホルダで渡す。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence

#: スキーマのバージョン。破壊的変更時に上げる。
SCHEMA_VERSION = 1

# 各テーブルの列(順序が挿入時の列順になる)。この許可リスト以外の列名は受け付けない。
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "listed_info": (
        "code",
        "company_name",
        "sector33_code",
        "sector33_name",
        "updated_at",
    ),
    "daily_quotes": (
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjustment_factor",
        "adjustment_open",
        "adjustment_high",
        "adjustment_low",
        "adjustment_close",
        "adjustment_volume",
    ),
    "statements": (
        "disclosure_number",
        "code",
        "disclosed_date",
        "type_of_current_period",
        "type_of_document",
        "net_sales",
        "operating_profit",
        "ordinary_profit",
        "profit",
        "fiscal_year_end",
    ),
}

# 各テーブルの主キー列(upsertの競合ターゲット)。
TABLE_PRIMARY_KEY: dict[str, tuple[str, ...]] = {
    "listed_info": ("code",),
    "daily_quotes": ("code", "date"),
    "statements": ("disclosure_number",),
}

#: 定義済みテーブル名の集合。
TABLES: frozenset[str] = frozenset(TABLE_COLUMNS)

# DDLは静的な文字列のみ(ユーザ入力を含めない)。CREATE ... IF NOT EXISTS で冪等。
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listed_info (
    code            TEXT PRIMARY KEY,
    company_name    TEXT,
    sector33_code   TEXT,
    sector33_name   TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS daily_quotes (
    code               TEXT NOT NULL,
    date               TEXT NOT NULL,
    open               REAL,
    high               REAL,
    low                REAL,
    close              REAL,
    volume             REAL,
    adjustment_factor  REAL,
    adjustment_open    REAL,
    adjustment_high    REAL,
    adjustment_low     REAL,
    adjustment_close   REAL,
    adjustment_volume  REAL,
    PRIMARY KEY (code, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_quotes_date ON daily_quotes (date);

CREATE TABLE IF NOT EXISTS statements (
    disclosure_number       TEXT PRIMARY KEY,
    code                    TEXT NOT NULL,
    disclosed_date          TEXT,
    type_of_current_period  TEXT,
    type_of_document        TEXT,
    net_sales               REAL,
    operating_profit        REAL,
    ordinary_profit         REAL,
    profit                  REAL,
    fiscal_year_end         TEXT
);

CREATE INDEX IF NOT EXISTS idx_statements_code ON statements (code);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """3テーブルとインデックスを作成する。冪等。"""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def _quote_ident(name: str) -> str:
    """SQLite識別子を二重引用符でクォートする(許可リスト検証済みの列名のみに使う)。"""
    return '"' + name.replace('"', '""') + '"'


def upsert(conn: sqlite3.Connection, table: str, rows: Sequence[Mapping[str, object]]) -> int:
    """指定テーブルへ行をupsertする(主キー衝突時は非キー列を更新)。

    列名は `TABLE_COLUMNS` の許可リストで検証し、値はすべてプレースホルダで渡す
    (SQLインジェクション対策・security.md)。同一入力の再実行は冪等。

    Returns:
        処理した行数。
    """
    if table not in TABLE_COLUMNS:
        raise ValueError(f"未知のテーブルです: {table}")
    if not rows:
        return 0

    allowed = TABLE_COLUMNS[table]
    pk = TABLE_PRIMARY_KEY[table]
    non_pk = [c for c in allowed if c not in pk]

    cols_sql = ", ".join(_quote_ident(c) for c in allowed)
    placeholders = ", ".join("?" for _ in allowed)
    conflict_sql = ", ".join(_quote_ident(c) for c in pk)
    update_sql = ", ".join(f"{_quote_ident(c)} = excluded.{_quote_ident(c)}" for c in non_pk)

    if update_sql:
        sql = (
            f"INSERT INTO {_quote_ident(table)} ({cols_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
        )
    else:
        # 全列が主キー(現状のテーブルには無いが安全側): 衝突時は何もしない。
        sql = (
            f"INSERT INTO {_quote_ident(table)} ({cols_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )

    params: list[tuple[object, ...]] = []
    for row in rows:
        unknown = set(row) - set(allowed)
        if unknown:
            raise ValueError(f"{table} に未知の列があります: {sorted(unknown)}")
        params.append(tuple(row.get(c) for c in allowed))

    conn.executemany(sql, params)
    conn.commit()
    return len(params)

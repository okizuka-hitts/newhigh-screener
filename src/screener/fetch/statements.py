"""財務データ(statements)の取得と保存。

J-Quants `/fins/statements` を取得し、四半期・通期の財務値を `statements` テーブルへ
冪等にupsertする。DisclosureNumber を主キーとして重複を防ぐ。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from screener import config
from screener.api.client import JQuantsClient
from screener.db import upsert

logger = logging.getLogger("screener.fetch")

_DATA_KEY = "statements"


def _to_float(value: Any) -> float | None:
    """J-Quantsの数値は文字列や空文字で来るため、安全にfloat化する。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    """APIの1財務レコードを statements の行に変換する。"""
    return {
        "disclosure_number": item.get("DisclosureNumber"),
        "code": item.get("LocalCode") or item.get("Code"),
        "disclosed_date": item.get("DisclosedDate"),
        "type_of_current_period": item.get("TypeOfCurrentPeriod"),
        "type_of_document": item.get("TypeOfDocument"),
        "net_sales": _to_float(item.get("NetSales")),
        "operating_profit": _to_float(item.get("OperatingProfit")),
        "ordinary_profit": _to_float(item.get("OrdinaryProfit")),
        "profit": _to_float(item.get("Profit")),
        "fiscal_year_end": item.get("CurrentFiscalYearEndDate"),
    }


def _codes_from_db(conn: sqlite3.Connection) -> list[str]:
    return [r["code"] for r in conn.execute("SELECT code FROM listed_info ORDER BY code")]


def fetch_statements(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    codes: Sequence[str] | None = None,
) -> int:
    """財務データを取得してDBへ保存する。保存件数(行数)を返す。

    Args:
        client: J-Quantsクライアント。
        conn: 保存先DB接続。
        codes: 対象銘柄コード。省略時は listed_info の全銘柄。
    """
    target: Iterable[str] = codes if codes is not None else _codes_from_db(conn)

    total = 0
    for code in target:
        items = client.get_paginated(config.STATEMENTS_ENDPOINT, _DATA_KEY, {"code": code})
        rows = [_row_from_api(i) for i in items if i.get("DisclosureNumber")]
        total += upsert(conn, "statements", rows)
    logger.info("財務データ: 保存 %d件", total)
    return total

"""財務データ(statements)の取得と保存。

J-Quants(V2) `/fins/summary` を取得し、四半期・通期の財務値を `statements` テーブルへ
冪等にupsertする。DiscNo(開示番号)を主キーとして重複を防ぐ。
(BS/PL/CF明細の `/fins/details` はライトプラン対象外のため使用しない。)
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


def _to_float(value: Any) -> float | None:
    """J-Quantsの数値は文字列や空文字で来るため、安全にfloat化する。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    """APIの1財務レコードを statements の行に変換する。

    V2 `/fins/summary` のフィールド: DiscNo(開示番号), Code, DiscDate(開示日),
    CurPerType(当会計期間種別 1Q/2Q/3Q/FY), DocType(書類種別), Sales(売上高),
    OP(営業利益), OdP(経常利益), NP(当期純利益), CurFYEn(当会計年度末)。
    """
    return {
        "disclosure_number": item.get("DiscNo"),
        "code": item.get("Code"),
        "disclosed_date": item.get("DiscDate"),
        "type_of_current_period": item.get("CurPerType"),
        "type_of_document": item.get("DocType"),
        "net_sales": _to_float(item.get("Sales")),
        "operating_profit": _to_float(item.get("OP")),
        "ordinary_profit": _to_float(item.get("OdP")),
        "profit": _to_float(item.get("NP")),
        "fiscal_year_end": item.get("CurFYEn"),
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
        items = client.get_paginated(config.STATEMENTS_ENDPOINT, params={"code": code})
        rows = [_row_from_api(i) for i in items if i.get("DiscNo")]
        total += upsert(conn, "statements", rows)
    logger.info("財務データ: 保存 %d件", total)
    return total

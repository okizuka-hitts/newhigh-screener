"""財務データ(statements)の取得と保存。

J-Quants(V2) `/fins/summary` を取得し、四半期・通期の財務値を `statements` テーブルへ
冪等にupsertする。DiscNo(開示番号)を主キーとして重複を防ぐ。
(BS/PL/CF明細の `/fins/details` はライトプラン対象外のため使用しない。)

取得方式は2つ:
- **by-date(既定・一括)**: 期間内の全営業日をループし `?date=` で当日の全開示を取得する。
- **by-code(対象限定)**: `codes` 指定時は `?code=` で銘柄別に取得する。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

from screener import config
from screener.api.client import JQuantsClient
from screener.db import upsert
from screener.fetch.calendar import trading_days
from screener.fetch.daily_quotes import fetch_window

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


def _save(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    rows = [_row_from_api(i) for i in items if i.get("DiscNo")]
    return upsert(conn, "statements", rows)


def fetch_statements(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    reference_date: _dt.date | None = None,
    dates: Sequence[str] | None = None,
    codes: Sequence[str] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> int:
    """財務データを取得してDBへ保存する。保存件数(行数)を返す。

    Args:
        client: J-Quantsクライアント。
        conn: 保存先DB接続。
        reference_date: by-date時の期間基準日。省略時は本日。
        dates: by-dateで取得する営業日リスト。省略時はカレンダーから営業日を求める。
        codes: 指定時はby-code(銘柄別)で取得する。
        on_progress: by-dateループで1営業日処理するごとに呼ぶ進捗フック(NS-18)。
    """
    total = 0
    if codes is not None:
        for code in codes:
            items = client.get_paginated(config.STATEMENTS_ENDPOINT, params={"code": code})
            total += _save(conn, items)
        logger.info("財務データ(by-code): %d銘柄 / 保存 %d件", len(codes), total)
        return total

    reference_date = reference_date or _dt.date.today()
    from_date, to_date = fetch_window(reference_date)
    day_list = list(dates) if dates is not None else trading_days(client, from_date, to_date)
    for day in day_list:
        items = client.get_paginated(config.STATEMENTS_ENDPOINT, params={"date": day})
        total += _save(conn, items)
        if on_progress is not None:
            on_progress()
    logger.info("財務データ(by-date): %d営業日 / 保存 %d件", len(day_list), total)
    return total

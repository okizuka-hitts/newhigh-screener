"""日足四本値・出来高(daily_quotes)の取得と保存。

J-Quants(V2) `/equities/bars/daily` を取得し、直近3か月＋過去52週を満たす期間で
`daily_quotes` テーブルへ冪等にupsertする。分割補正に必要な AdjFactor 等の列も併せて保存する。

取得方式は2つ:
- **by-date(既定・一括)**: 期間内の全営業日をループし `?date=` で当日全銘柄を取得する。
  リクエスト数が営業日数(約252)で済み、全銘柄フル取得を大幅に高速化する。
- **by-code(対象限定)**: `codes` 指定時は `?code=&from=&to=` で銘柄別に取得する。
  分割補正の再取得(NS-10)や部分取得に使う。
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from collections.abc import Sequence
from typing import Any

from screener import config
from screener.api.client import JQuantsClient
from screener.db import upsert
from screener.fetch.calendar import trading_days

logger = logging.getLogger("screener.fetch")


def fetch_window(reference: _dt.date) -> tuple[str, str]:
    """取得期間 (from, to) をISO文字列で返す。

    52週分(=直近3か月を包含)を遡り、基準日までを対象とする。
    """
    earliest = reference - _dt.timedelta(weeks=config.LOOKBACK_WEEKS)
    return earliest.isoformat(), reference.isoformat()


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    """APIの1日足レコードを daily_quotes の行に変換する。

    V2 `/equities/bars/daily` のフィールド: Date, Code, O/H/L/C(四本値),
    Vo(出来高), AdjFactor(調整係数), AdjO/AdjH/AdjL/AdjC(調整後四本値), AdjVo(調整後出来高)。
    """
    return {
        "code": item.get("Code"),
        "date": item.get("Date"),
        "open": item.get("O"),
        "high": item.get("H"),
        "low": item.get("L"),
        "close": item.get("C"),
        "volume": item.get("Vo"),
        "adjustment_factor": item.get("AdjFactor"),
        "adjustment_open": item.get("AdjO"),
        "adjustment_high": item.get("AdjH"),
        "adjustment_low": item.get("AdjL"),
        "adjustment_close": item.get("AdjC"),
        "adjustment_volume": item.get("AdjVo"),
    }


def _save(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    rows = [_row_from_api(i) for i in items if i.get("Code") and i.get("Date")]
    return upsert(conn, "daily_quotes", rows)


def fetch_daily_quotes(
    client: JQuantsClient,
    conn: sqlite3.Connection,
    *,
    reference_date: _dt.date | None = None,
    dates: Sequence[str] | None = None,
    codes: Sequence[str] | None = None,
) -> int:
    """日足を取得してDBへ保存する。保存件数(行数)を返す。

    Args:
        client: J-Quantsクライアント。
        conn: 保存先DB接続。
        reference_date: 期間の基準日。省略時は本日。
        dates: by-dateで取得する営業日リスト。省略時はカレンダーから営業日を求める。
        codes: 指定時はby-code(銘柄別)で取得する(分割補正の再取得等)。
    """
    reference_date = reference_date or _dt.date.today()
    from_date, to_date = fetch_window(reference_date)

    total = 0
    if codes is not None:
        for code in codes:
            items = client.get_paginated(
                config.DAILY_QUOTES_ENDPOINT,
                params={"code": code, "from": from_date, "to": to_date},
            )
            total += _save(conn, items)
        logger.info("日足(by-code): %d銘柄 / 保存 %d件", len(codes), total)
        return total

    day_list = list(dates) if dates is not None else trading_days(client, from_date, to_date)
    for day in day_list:
        items = client.get_paginated(config.DAILY_QUOTES_ENDPOINT, params={"date": day})
        total += _save(conn, items)
    logger.info("日足(by-date): %s〜%s / %d営業日 / 保存 %d件", from_date, to_date, len(day_list), total)
    return total

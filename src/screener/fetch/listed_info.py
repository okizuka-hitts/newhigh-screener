"""上場銘柄一覧(listed_info)の取得と保存。

J-Quants `/listed/info` を取得し、銘柄コード・銘柄名・Sector33業種コード/名称を
`listed_info` テーブルへ冪等にupsertする。後続の日足・財務取得の対象リストとなる。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from screener import config
from screener.api.client import JQuantsClient
from screener.db import upsert

logger = logging.getLogger("screener.fetch")

#: `/listed/info` 応答内の銘柄配列キー。
_DATA_KEY = "info"


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    """APIの1銘柄レコードを listed_info の行に変換する。欠損フィールドは None。"""
    return {
        "code": item.get("Code"),
        "company_name": item.get("CompanyName"),
        "sector33_code": item.get("Sector33Code"),
        "sector33_name": item.get("Sector33CodeName"),
        "updated_at": item.get("Date"),
    }


def fetch_listed_info(client: JQuantsClient, conn: sqlite3.Connection) -> int:
    """上場銘柄一覧を取得してDBへ保存する。保存件数を返す。

    Args:
        client: 認証・レート制御済みのJ-Quantsクライアント。
        conn: 保存先DB接続(init_db済みであること)。
    """
    items = client.get_paginated(config.LISTED_INFO_ENDPOINT, _DATA_KEY)
    rows = [_row_from_api(item) for item in items if item.get("Code")]
    saved = upsert(conn, "listed_info", rows)
    logger.info("上場銘柄一覧: 取得 %d件 / 保存 %d件", len(items), saved)
    return saved

"""取引カレンダー(営業日)ユーティリティ。

J-Quants(V2) `/markets/calendar` から営業日を取得する。日足・財務の一括取得を
「銘柄ループ」ではなく「営業日ループ(by-date)」で行うために使う(リクエスト数を大幅削減)。
"""

from __future__ import annotations

from screener import config
from screener.api.client import JQuantsClient

# HolDiv(休日区分)の営業日値: "1"=営業日, "2"=東証半日立会日。
# "0"=土日, "3"=祝日(平日の非営業日)は非営業日として除外する。
_BUSINESS_HOLDIV = frozenset({"1", "2"})


def trading_days(client: JQuantsClient, from_date: str, to_date: str) -> list[str]:
    """[from_date, to_date] の営業日(ISO文字列)を昇順で返す。

    休場日(土日・祝日)は除外する。1リクエストで期間分のカレンダーを取得する。
    """
    items = client.get_paginated(
        config.CALENDAR_ENDPOINT, params={"from": from_date, "to": to_date}
    )
    days = [i["Date"] for i in items if str(i.get("HolDiv")) in _BUSINESS_HOLDIV and i.get("Date")]
    return sorted(days)

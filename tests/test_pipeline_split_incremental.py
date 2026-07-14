"""差分モードでの分割補正の過去データ上書き(NS-17)のテスト。

フック注入で差分日に分割イベント(AdjFactor≠1・Adjustment*付き)を与え、注入後に
過去分の株価・出来高が補正値へ置き換わることを検証する。ネットワーク非依存。
"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import find_split_affected_codes_in_dates, run_fetch

CODE = "13010"


def test_find_split_affected_codes_in_dates_empty_returns_empty():
    conn = connect(":memory:")
    init_db(conn)
    assert find_split_affected_codes_in_dates(conn, []) == []


def _weekdays(from_iso: str, to_iso: str) -> list[str]:
    d = dt.date.fromisoformat(from_iso)
    end = dt.date.fromisoformat(to_iso)
    out: list[str] = []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _client(handler):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )
    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


def _seed_presplit(conn, dates: list[str]) -> None:
    """分割前の状態(生値=補正値)で過去日をシードする。"""
    upsert(
        conn,
        "daily_quotes",
        [
            {
                "code": CODE, "date": d, "close": 100.0, "volume": 1000.0,
                "adjustment_factor": 1.0, "adjustment_close": 100.0, "adjustment_volume": 1000.0,
            }
            for d in dates
        ],
    )


def test_incremental_split_overwrites_past_data():
    conn = connect(":memory:")
    init_db(conn)
    past = ["2026-07-06", "2026-07-07", "2026-07-08"]
    _seed_presplit(conn, past)
    bycode_calls: list[str] = []

    def handler(method, url, params=None, headers=None, body=None):
        params = params or {}
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            return HttpResponse(200, {"data": [{"Code": CODE, "CoName": "極洋"}]})
        if url.endswith(config.CALENDAR_ENDPOINT):
            days = _weekdays(params["from"], params["to"])
            return HttpResponse(200, {"data": [{"Date": d, "HolDiv": "1"} for d in days]})
        if url.endswith(config.STATEMENTS_ENDPOINT):
            return HttpResponse(200, {"data": []})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            if "code" in params:
                # by-code 全期間再取得: 補正済み系列(AdjC=50, AdjVo=2000)を全日返す。
                bycode_calls.append(params["code"])
                days = _weekdays(params["from"], params["to"])
                return HttpResponse(200, {"data": [
                    {"Code": CODE, "Date": d, "C": 100.0, "Vo": 1000.0,
                     "AdjFactor": 0.5 if d == "2026-07-09" else 1.0,
                     "AdjC": 50.0, "AdjVo": 2000.0}
                    for d in days
                ]})
            # by-date 差分取得。07-09 が分割ex日(AdjFactor=0.5)。
            date = params["date"]
            factor = 0.5 if date == "2026-07-09" else 1.0
            return HttpResponse(200, {"data": [
                {"Code": CODE, "Date": date, "C": 100.0, "Vo": 1000.0,
                 "AdjFactor": factor, "AdjC": 50.0, "AdjVo": 2000.0}
            ]})
        raise AssertionError(url)

    summary = run_fetch(_client(handler), conn, reference_date=dt.date(2026, 7, 10))

    assert summary["mode"] == "incremental"
    assert summary["adjusted_codes"] == 1
    assert summary["split_refetched"] > 0
    assert bycode_calls == [CODE]  # 分割銘柄のみ再取得

    # 過去日(差分では再取得しない日)が補正値で上書きされている。
    row = conn.execute(
        "SELECT close, volume FROM daily_quotes WHERE code=? AND date=?", (CODE, "2026-07-06")
    ).fetchone()
    assert row["close"] == 50.0  # 100 → 補正後 50
    assert row["volume"] == 2000.0  # 1000 → 補正後 2000


def test_incremental_no_split_no_bycode_refetch():
    conn = connect(":memory:")
    init_db(conn)
    _seed_presplit(conn, ["2026-07-08"])
    bycode_calls: list[str] = []

    def handler(method, url, params=None, headers=None, body=None):
        params = params or {}
        if url.endswith(config.LISTED_INFO_ENDPOINT):
            return HttpResponse(200, {"data": [{"Code": CODE, "CoName": "極洋"}]})
        if url.endswith(config.CALENDAR_ENDPOINT):
            days = _weekdays(params["from"], params["to"])
            return HttpResponse(200, {"data": [{"Date": d, "HolDiv": "1"} for d in days]})
        if url.endswith(config.STATEMENTS_ENDPOINT):
            return HttpResponse(200, {"data": []})
        if url.endswith(config.DAILY_QUOTES_ENDPOINT):
            if "code" in params:
                bycode_calls.append(params["code"])
                return HttpResponse(200, {"data": []})
            return HttpResponse(200, {"data": [
                {"Code": CODE, "Date": params["date"], "C": 100.0, "AdjFactor": 1.0}
            ]})
        raise AssertionError(url)

    summary = run_fetch(_client(handler), conn, reference_date=dt.date(2026, 7, 10))

    assert summary["mode"] == "incremental"
    assert summary["split_refetched"] == 0
    assert summary["adjusted_codes"] == 0
    assert bycode_calls == []  # 分割なし → by-code 再取得は発生しない

"""日足(V2 /equities/bars/daily) 取得・保存のテスト(by-date / by-code)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import fetch_daily_quotes, fetch_window

REF = dt.date(2026, 7, 10)


def _client(handler):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def transport(method, url, params=None, headers=None, body=None):
        return HttpResponse(200, handler(url, params or {}))

    return JQuantsClient(api_key="k", transport=transport, rate_limiter=limiter)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _bar(code, date, close, volume=1000.0):
    return {
        "Code": code, "Date": date,
        "O": close - 1, "H": close + 2, "L": close - 2, "C": close,
        "Vo": volume, "Va": close * volume,
        "AdjFactor": 1.0, "AdjO": close - 1, "AdjH": close + 2,
        "AdjL": close - 2, "AdjC": close, "AdjVo": volume,
    }


# --- by-date (既定・一括) ----------------------------------------------------

def test_by_date_saves_all_stocks_per_day():
    # ?date= は当日全銘柄を返す想定。営業日を明示指定(dates=)。
    days = {
        "2026-07-09": [_bar("13010", "2026-07-09", 100.0), _bar("72030", "2026-07-09", 200.0)],
        "2026-07-10": [_bar("13010", "2026-07-10", 101.0), _bar("72030", "2026-07-10", 201.0)],
    }

    def handler(url, params):
        return {"data": days[params["date"]]}

    conn = _conn()
    n = fetch_daily_quotes(_client(handler), conn, dates=list(days), reference_date=REF)
    assert n == 4
    codes = {r["code"] for r in conn.execute("SELECT DISTINCT code FROM daily_quotes")}
    assert codes == {"13010", "72030"}


def test_by_date_uses_calendar_when_dates_omitted():
    # dates 省略時はカレンダーから営業日を取得してループする。
    def handler(url, params):
        if url.endswith(config.CALENDAR_ENDPOINT):
            return {"data": [
                {"Date": "2026-07-09", "HolDiv": "1"},
                {"Date": "2026-07-11", "HolDiv": "0"},  # 土曜=非営業日 → 除外
                {"Date": "2026-07-10", "HolDiv": "1"},
            ]}
        return {"data": [_bar("13010", params["date"], 100.0)]}

    conn = _conn()
    n = fetch_daily_quotes(_client(handler), conn, reference_date=REF)
    assert n == 2  # 営業日2日分のみ(土曜は取得しない)
    dates = {r["date"] for r in conn.execute("SELECT date FROM daily_quotes")}
    assert dates == {"2026-07-09", "2026-07-10"}


def test_by_date_saves_ohlcv_and_adjustment():
    def handler(url, params):
        return {"data": [_bar("13010", "2026-07-10", 100.0)]}

    conn = _conn()
    fetch_daily_quotes(_client(handler), conn, dates=["2026-07-10"], reference_date=REF)
    row = conn.execute(
        "SELECT open, high, low, close, volume, adjustment_factor, adjustment_close "
        "FROM daily_quotes"
    ).fetchone()
    assert row["close"] == 100.0 and row["high"] == 102.0 and row["volume"] == 1000.0
    assert row["adjustment_factor"] == 1.0 and row["adjustment_close"] == 100.0


def test_by_date_is_idempotent():
    def handler(url, params):
        return {"data": [_bar("13010", "2026-07-10", 100.0)]}

    conn = _conn()
    fetch_daily_quotes(_client(handler), conn, dates=["2026-07-10"], reference_date=REF)
    fetch_daily_quotes(_client(handler), conn, dates=["2026-07-10"], reference_date=REF)
    assert conn.execute("SELECT COUNT(*) AS c FROM daily_quotes").fetchone()["c"] == 1


def test_record_without_date_is_skipped():
    def handler(url, params):
        return {"data": [{"Code": "13010", "C": 1.0}]}  # Date欠落

    conn = _conn()
    n = fetch_daily_quotes(_client(handler), conn, dates=["2026-07-10"], reference_date=REF)
    assert n == 0


# --- by-code (対象限定・再取得用) -------------------------------------------

def test_by_code_uses_from_to_window():
    seen = {}

    def handler(url, params):
        seen.update(params)
        return {"data": [_bar(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    n = fetch_daily_quotes(_client(handler), conn, codes=["13010"], reference_date=REF)
    assert n == 1
    assert seen["code"] == "13010"
    assert seen["from"] == fetch_window(REF)[0]
    assert seen["to"] == "2026-07-10"


def test_by_code_pagination_continues():
    pages = {
        None: {"data": [_bar("13010", "2026-07-08", 98.0)], "pagination_key": "p1"},
        "p1": {"data": [_bar("13010", "2026-07-09", 99.0)]},
    }

    def handler(url, params):
        return pages[params.get("pagination_key")]

    conn = _conn()
    n = fetch_daily_quotes(_client(handler), conn, codes=["13010"], reference_date=REF)
    assert n == 2


# --- 共通 --------------------------------------------------------------------

def test_window_covers_three_months_and_52_weeks():
    frm, to = fetch_window(REF)
    assert to == "2026-07-10"
    span_days = (REF - dt.date.fromisoformat(frm)).days
    assert span_days >= 7 * config.LOOKBACK_WEEKS
    assert span_days >= 90


def test_by_code_default_codes_from_listed_info_not_used_in_by_date():
    # listed_info があっても by-date 既定では銘柄ループしない(dates指定で確認)。
    conn = _conn()
    upsert(conn, "listed_info", [{"code": "13010"}])

    def handler(url, params):
        assert "code" not in params  # by-dateではcodeを送らない
        return {"data": [_bar("13010", params["date"], 10.0)]}

    n = fetch_daily_quotes(_client(handler), conn, dates=["2026-07-10"], reference_date=REF)
    assert n == 1

"""日足(V2 /equities/bars/daily) 取得・保存のテスト。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import fetch_daily_quotes, fetch_window


def _client(responder):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )

    def handler(method, url, params=None, headers=None, body=None):
        return HttpResponse(200, responder(params))

    return JQuantsClient(api_key="k", transport=handler, rate_limiter=limiter)


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


def test_saves_ohlcv_for_code():
    def responder(params):
        return {"data": [_bar(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 1
    row = conn.execute(
        "SELECT open, high, low, close, volume FROM daily_quotes WHERE code = ?", ("13010",)
    ).fetchone()
    assert row["close"] == 100.0 and row["high"] == 102.0 and row["volume"] == 1000.0


def test_adjustment_columns_saved():
    def responder(params):
        return {"data": [_bar(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    fetch_daily_quotes(_client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10))
    row = conn.execute(
        "SELECT adjustment_factor, adjustment_close, adjustment_volume FROM daily_quotes"
    ).fetchone()
    assert row["adjustment_factor"] == 1.0
    assert row["adjustment_close"] == 100.0
    assert row["adjustment_volume"] == 1000.0


def test_window_covers_three_months_and_52_weeks():
    ref = dt.date(2026, 7, 10)
    frm, to = fetch_window(ref)
    assert to == "2026-07-10"
    span_days = (ref - dt.date.fromisoformat(frm)).days
    assert span_days >= 7 * config.LOOKBACK_WEEKS
    assert span_days >= 90


def test_window_passed_as_from_to_params():
    seen = {}

    def responder(params):
        seen.update(params)
        return {"data": []}

    fetch_daily_quotes(
        _client(responder), _conn(), codes=["13010"], reference_date=dt.date(2026, 7, 10)
    )
    assert seen["from"] == fetch_window(dt.date(2026, 7, 10))[0]
    assert seen["to"] == "2026-07-10"
    assert seen["code"] == "13010"


def test_rerun_is_idempotent():
    def responder(params):
        return {"data": [_bar(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    fetch_daily_quotes(_client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10))
    fetch_daily_quotes(_client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10))
    count = conn.execute("SELECT COUNT(*) AS c FROM daily_quotes").fetchone()["c"]
    assert count == 1


def test_pagination_continues():
    pages = {
        None: {"data": [_bar("13010", "2026-07-08", 98.0)], "pagination_key": "p1"},
        "p1": {"data": [_bar("13010", "2026-07-09", 99.0)]},
    }

    def responder(params):
        return pages[params.get("pagination_key")]

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 2


def test_codes_default_from_listed_info():
    from screener.db import upsert

    conn = _conn()
    upsert(conn, "listed_info", [{"code": "13010"}, {"code": "72030"}])

    def responder(params):
        return {"data": [_bar(params["code"], "2026-07-10", 50.0)]}

    n = fetch_daily_quotes(_client(responder), conn, reference_date=dt.date(2026, 7, 10))
    assert n == 2
    codes = {r["code"] for r in conn.execute("SELECT DISTINCT code FROM daily_quotes")}
    assert codes == {"13010", "72030"}


def test_record_without_date_is_skipped():
    def responder(params):
        return {"data": [{"Code": "13010", "C": 1.0}]}  # Date欠落

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["13010"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 0

"""日足取得・保存のテスト(保存・期間決定・冪等・ページング・境界)。"""

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
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return HttpResponse(200, {"idToken": "t"})
        return HttpResponse(200, responder(params))

    return JQuantsClient(api_key="rt", transport=handler, rate_limiter=limiter, time_func=clock.time)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _quote(code, date, close, volume=1000.0):
    return {
        "Code": code,
        "Date": date,
        "Open": close - 1,
        "High": close + 2,
        "Low": close - 2,
        "Close": close,
        "Volume": volume,
        "AdjustmentFactor": 1.0,
        "AdjustmentClose": close,
    }


def test_saves_ohlcv_for_code():
    def responder(params):
        return {"daily_quotes": [_quote(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["1301"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 1
    row = conn.execute(
        "SELECT open, high, low, close, volume FROM daily_quotes WHERE code = ?", ("1301",)
    ).fetchone()
    assert row["close"] == 100.0 and row["high"] == 102.0 and row["volume"] == 1000.0


def test_window_covers_three_months_and_52_weeks():
    ref = dt.date(2026, 7, 10)
    frm, to = fetch_window(ref)
    assert to == "2026-07-10"
    span_days = (ref - dt.date.fromisoformat(frm)).days
    # 52週(364日)を遡る = 3か月(約92日)も包含。
    assert span_days >= 7 * config.LOOKBACK_WEEKS
    assert span_days >= 90


def test_window_passed_as_from_to_params():
    seen = {}

    def responder(params):
        seen.update(params)
        return {"daily_quotes": []}

    fetch_daily_quotes(
        _client(responder), _conn(), codes=["1301"], reference_date=dt.date(2026, 7, 10)
    )
    assert seen["from"] == fetch_window(dt.date(2026, 7, 10))[0]
    assert seen["to"] == "2026-07-10"


def test_rerun_is_idempotent():
    def responder(params):
        return {"daily_quotes": [_quote(params["code"], "2026-07-10", 100.0)]}

    conn = _conn()
    fetch_daily_quotes(_client(responder), conn, codes=["1301"], reference_date=dt.date(2026, 7, 10))
    fetch_daily_quotes(_client(responder), conn, codes=["1301"], reference_date=dt.date(2026, 7, 10))
    count = conn.execute("SELECT COUNT(*) AS c FROM daily_quotes").fetchone()["c"]
    assert count == 1


def test_pagination_continues():
    pages = {
        None: {"daily_quotes": [_quote("1301", "2026-07-08", 98.0)], "pagination_key": "p1"},
        "p1": {"daily_quotes": [_quote("1301", "2026-07-09", 99.0)]},
    }

    def responder(params):
        return pages[params.get("pagination_key")]

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["1301"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 2


def test_codes_default_from_listed_info():
    from screener.db import upsert

    conn = _conn()
    upsert(conn, "listed_info", [{"code": "1301"}, {"code": "7203"}])

    def responder(params):
        return {"daily_quotes": [_quote(params["code"], "2026-07-10", 50.0)]}

    n = fetch_daily_quotes(_client(responder), conn, reference_date=dt.date(2026, 7, 10))
    assert n == 2
    codes = {r["code"] for r in conn.execute("SELECT DISTINCT code FROM daily_quotes")}
    assert codes == {"1301", "7203"}


def test_record_without_date_is_skipped():
    def responder(params):
        return {"daily_quotes": [{"Code": "1301", "Close": 1.0}]}  # Date欠落

    conn = _conn()
    n = fetch_daily_quotes(
        _client(responder), conn, codes=["1301"], reference_date=dt.date(2026, 7, 10)
    )
    assert n == 0

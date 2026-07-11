"""分割補正のテスト(検知・再取得・補正済み値の一致・冪等・注入フックE2E)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import (
    detect_and_adjust,
    fetch_daily_quotes,
    find_split_affected_codes,
)

REF = dt.date(2026, 7, 10)


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


# 2:1分割を注入するデータ。分割前日(07-08)は生値100だが補正後は50、
# 出来高は生値1000が補正後2000。分割日(07-09)にAdjustmentFactor=0.5。
def _split_responder(params):
    return {
        "daily_quotes": [
            {
                "Code": params["code"],
                "Date": "2026-07-08",
                "Open": 100.0,
                "High": 100.0,
                "Low": 100.0,
                "Close": 100.0,
                "Volume": 1000.0,
                "AdjustmentFactor": 1.0,
                "AdjustmentOpen": 50.0,
                "AdjustmentHigh": 50.0,
                "AdjustmentLow": 50.0,
                "AdjustmentClose": 50.0,
                "AdjustmentVolume": 2000.0,
            },
            {
                "Code": params["code"],
                "Date": "2026-07-09",
                "Open": 50.0,
                "High": 50.0,
                "Low": 50.0,
                "Close": 50.0,
                "Volume": 2000.0,
                "AdjustmentFactor": 0.5,  # 分割検知トリガ
                "AdjustmentOpen": 50.0,
                "AdjustmentHigh": 50.0,
                "AdjustmentLow": 50.0,
                "AdjustmentClose": 50.0,
                "AdjustmentVolume": 2000.0,
            },
        ]
    }


def _no_split_responder(params):
    return {
        "daily_quotes": [
            {
                "Code": params["code"],
                "Date": "2026-07-08",
                "Open": 10.0,
                "High": 10.0,
                "Low": 10.0,
                "Close": 10.0,
                "Volume": 500.0,
                "AdjustmentFactor": 1.0,
                "AdjustmentClose": 10.0,
                "AdjustmentVolume": 500.0,
            }
        ]
    }


def test_detects_split_affected_code():
    conn = _conn()
    fetch_daily_quotes(_client(_split_responder), conn, codes=["1301"], reference_date=REF)
    assert find_split_affected_codes(conn) == ["1301"]


def test_no_split_is_not_detected():
    conn = _conn()
    fetch_daily_quotes(_client(_no_split_responder), conn, codes=["7203"], reference_date=REF)
    assert find_split_affected_codes(conn) == []


def test_adjustment_fires_and_matches_independent_calc():
    conn = _conn()
    client = _client(_split_responder)
    fetch_daily_quotes(client, conn, codes=["1301"], reference_date=REF)

    affected = detect_and_adjust(client, conn, reference_date=REF)
    assert affected == ["1301"]

    # 補正後の分割前日(07-08)の値は独立計算(生値×累積係数 0.5)と一致。
    row = conn.execute(
        "SELECT close, volume FROM daily_quotes WHERE code = ? AND date = ?",
        ("1301", "2026-07-08"),
    ).fetchone()
    raw_close, cumulative_factor = 100.0, 0.5
    assert row["close"] == raw_close * cumulative_factor  # 50.0
    assert row["volume"] == 1000.0 / cumulative_factor  # 出来高は逆方向: 2000.0


def test_adjustment_is_idempotent():
    conn = _conn()
    client = _client(_split_responder)
    fetch_daily_quotes(client, conn, codes=["1301"], reference_date=REF)
    detect_and_adjust(client, conn, reference_date=REF)
    first = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("1301", "2026-07-08")
    ).fetchone()["close"]
    # 2度目の補正でも値は変わらない。
    detect_and_adjust(client, conn, reference_date=REF)
    second = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("1301", "2026-07-08")
    ).fetchone()["close"]
    assert first == second == 50.0


def test_no_split_does_not_trigger_adjustment():
    conn = _conn()
    client = _client(_no_split_responder)
    fetch_daily_quotes(client, conn, codes=["7203"], reference_date=REF)
    affected = detect_and_adjust(client, conn, reference_date=REF)
    assert affected == []
    row = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("7203", "2026-07-08")
    ).fetchone()
    assert row["close"] == 10.0  # 未補正で不変

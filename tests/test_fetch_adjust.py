"""分割補正のテスト(検知・補正値一致・冪等・再取得なし=NS-12・新規分割再補正)。"""

import datetime as dt

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db, upsert
from screener.fetch import detect_and_adjust, fetch_daily_quotes, find_split_affected_codes

REF = dt.date(2026, 7, 10)


class CountingClient:
    """日足エンドポイントの呼び出し回数を数える注入クライアント。"""

    def __init__(self, responder):
        clock = FakeClock()
        limiter = RateLimiter(
            config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
        )
        self.daily_calls = 0

        def transport(method, url, params=None, headers=None, body=None):
            if url.endswith(config.DAILY_QUOTES_ENDPOINT):
                self.daily_calls += 1
            return HttpResponse(200, responder(url, params or {}))

        self.client = JQuantsClient(api_key="k", transport=transport, rate_limiter=limiter)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _split_rows(code, adj_close=50.0, adj_vol=2000.0):
    # 分割前日(07-08): 生値100/出来高1000だが補正後 close=adj_close・volume=adj_vol。
    # 分割日(07-09): AdjFactor=0.5(検知トリガ)。
    return [
        {"Code": code, "Date": "2026-07-08", "O": 100.0, "H": 100.0, "L": 100.0, "C": 100.0,
         "Vo": 1000.0, "AdjFactor": 1.0, "AdjO": adj_close, "AdjH": adj_close, "AdjL": adj_close,
         "AdjC": adj_close, "AdjVo": adj_vol},
        {"Code": code, "Date": "2026-07-09", "O": 50.0, "H": 50.0, "L": 50.0, "C": 50.0,
         "Vo": 2000.0, "AdjFactor": 0.5, "AdjO": 50.0, "AdjH": 50.0, "AdjL": 50.0,
         "AdjC": 50.0, "AdjVo": 2000.0},
    ]


def _split_responder(url, params):
    return {"data": _split_rows(params.get("code", "13010"))}


def _no_split_responder(url, params):
    return {"data": [
        {"Code": params.get("code", "72030"), "Date": "2026-07-08", "O": 10.0, "H": 10.0,
         "L": 10.0, "C": 10.0, "Vo": 500.0, "AdjFactor": 1.0, "AdjC": 10.0, "AdjVo": 500.0},
    ]}


def _seed(conn, responder, code):
    cc = CountingClient(responder)
    fetch_daily_quotes(cc.client, conn, codes=[code], reference_date=REF)
    return cc


def test_detects_split_affected_code():
    conn = _conn()
    _seed(conn, _split_responder, "13010")
    assert find_split_affected_codes(conn) == ["13010"]


def test_no_split_is_not_detected():
    conn = _conn()
    _seed(conn, _no_split_responder, "72030")
    assert find_split_affected_codes(conn) == []


def test_adjustment_matches_independent_calc():
    conn = _conn()
    _seed(conn, _split_responder, "13010")
    assert detect_and_adjust(conn) == ["13010"]
    row = conn.execute(
        "SELECT close, volume FROM daily_quotes WHERE code = ? AND date = ?",
        ("13010", "2026-07-08"),
    ).fetchone()
    assert row["close"] == 100.0 * 0.5  # 生値×累積係数 = 50.0
    assert row["volume"] == 1000.0 / 0.5  # 2000.0


def test_detect_and_adjust_makes_no_api_calls():
    # NS-12: 補正は追加のAPI再取得を行わない(DB操作のみ)。
    conn = _conn()
    cc = _seed(conn, _split_responder, "13010")
    assert cc.daily_calls == 1  # 初期の一括取得(ここではby-code)で1回のみ
    detect_and_adjust(conn)
    detect_and_adjust(conn)
    assert cc.daily_calls == 1  # 補正では日足APIを一切呼ばない


def test_adjustment_is_idempotent():
    conn = _conn()
    _seed(conn, _split_responder, "13010")
    detect_and_adjust(conn)
    first = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    detect_and_adjust(conn)
    second = conn.execute(
        "SELECT close FROM daily_quotes WHERE code = ? AND date = ?", ("13010", "2026-07-08")
    ).fetchone()["close"]
    assert first == second == 50.0


def test_new_split_retriggers_correction():
    # AC2: 一括取得が Adjustment* を更新(新規分割)すると、再度補正が発火する。
    conn = _conn()
    _seed(conn, _split_responder, "13010")  # 補正後 close=50
    detect_and_adjust(conn)
    assert conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-08")
    ).fetchone()["close"] == 50.0

    # 追加分割で AdjC がさらに後方調整(50→25)。一括取得(ここではby-code)で更新。
    def new_split(url, params):
        return {"data": _split_rows("13010", adj_close=25.0, adj_vol=4000.0)}

    cc2 = CountingClient(new_split)
    fetch_daily_quotes(cc2.client, conn, codes=["13010"], reference_date=REF)  # AdjCを25へ更新
    # 一括取得は close を生値100へ戻すため、再補正が必要。
    affected = detect_and_adjust(conn)
    assert "13010" in affected
    assert conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("13010", "2026-07-08")
    ).fetchone()["close"] == 25.0


def test_no_split_not_adjusted():
    conn = _conn()
    _seed(conn, _no_split_responder, "72030")
    assert detect_and_adjust(conn) == []
    assert conn.execute(
        "SELECT close FROM daily_quotes WHERE code=? AND date=?", ("72030", "2026-07-08")
    ).fetchone()["close"] == 10.0


def test_apply_adjustment_direct():
    conn = _conn()
    upsert(conn, "daily_quotes", [
        {"code": "1", "date": "2026-07-08", "close": 100.0, "volume": 1000.0,
         "adjustment_close": 50.0, "adjustment_volume": 2000.0, "adjustment_factor": 0.5},
    ])
    from screener.fetch import apply_adjustment
    apply_adjustment(conn, "1")
    row = conn.execute("SELECT close, volume FROM daily_quotes").fetchone()
    assert row["close"] == 50.0 and row["volume"] == 2000.0

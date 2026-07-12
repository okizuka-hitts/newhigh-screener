"""財務データ(V2 /fins/summary) 取得・保存のテスト。"""

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter
from screener.db import connect, init_db
from screener.fetch import fetch_statements


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


def _stmt(disc, code, period, sales="1000"):
    return {
        "DiscNo": disc, "Code": code, "DiscDate": "2026-05-10",
        "CurPerType": period, "DocType": "FYFinancialStatements_Consolidated_JP",
        "Sales": sales, "OP": "200", "OdP": "180", "NP": "150",
        "CurFYEn": "2026-03-31",
    }


def test_saves_statement_values():
    def responder(params):
        return {"data": [_stmt("1001", params["code"], "FY")]}

    conn = _conn()
    n = fetch_statements(_client(responder), conn, codes=["13010"])
    assert n == 1
    row = conn.execute(
        "SELECT code, net_sales, operating_profit, ordinary_profit, profit, "
        "type_of_current_period, fiscal_year_end FROM statements"
    ).fetchone()
    assert row["code"] == "13010"
    assert row["net_sales"] == 1000.0
    assert row["operating_profit"] == 200.0
    assert row["ordinary_profit"] == 180.0
    assert row["profit"] == 150.0
    assert row["type_of_current_period"] == "FY"
    assert row["fiscal_year_end"] == "2026-03-31"


def test_quarter_and_full_year_distinguished():
    def responder(params):
        return {"data": [_stmt("2001", params["code"], "1Q"), _stmt("2002", params["code"], "FY")]}

    conn = _conn()
    n = fetch_statements(_client(responder), conn, codes=["72030"])
    assert n == 2
    periods = {
        r["type_of_current_period"]
        for r in conn.execute("SELECT type_of_current_period FROM statements")
    }
    assert periods == {"1Q", "FY"}


def test_rerun_is_idempotent():
    def responder(params):
        return {"data": [_stmt("3001", params["code"], "FY")]}

    conn = _conn()
    fetch_statements(_client(responder), conn, codes=["13010"])
    fetch_statements(_client(responder), conn, codes=["13010"])
    count = conn.execute("SELECT COUNT(*) AS c FROM statements").fetchone()["c"]
    assert count == 1


def test_empty_and_nonnumeric_values_become_none():
    def responder(params):
        return {"data": [_stmt("4001", params["code"], "FY", sales="")]}

    conn = _conn()
    fetch_statements(_client(responder), conn, codes=["13010"])
    row = conn.execute("SELECT net_sales FROM statements").fetchone()
    assert row["net_sales"] is None


def test_record_without_disclosure_number_skipped():
    def responder(params):
        return {"data": [{"Code": params["code"], "Sales": "1"}]}

    conn = _conn()
    assert fetch_statements(_client(responder), conn, codes=["13010"]) == 0


def test_codes_default_from_listed_info():
    from screener.db import upsert

    conn = _conn()
    upsert(conn, "listed_info", [{"code": "13010"}, {"code": "72030"}])

    def responder(params):
        return {"data": [_stmt(f"d-{params['code']}", params["code"], "FY")]}

    assert fetch_statements(_client(responder), conn) == 2

"""財務データ取得・保存のテスト(保存・四半期/通期・冪等・数値変換)。"""

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
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return HttpResponse(200, {"idToken": "t"})
        return HttpResponse(200, responder(params))

    return JQuantsClient(api_key="rt", transport=handler, rate_limiter=limiter, time_func=clock.time)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _stmt(disclosure, code, period, net_sales="1000"):
    return {
        "DisclosureNumber": disclosure,
        "LocalCode": code,
        "DisclosedDate": "2026-05-10",
        "TypeOfCurrentPeriod": period,
        "TypeOfDocument": "FYFinancialStatements_Consolidated_JP",
        "NetSales": net_sales,
        "OperatingProfit": "200",
        "Profit": "150",
        "CurrentFiscalYearEndDate": "2026-03-31",
    }


def test_saves_statement_values():
    def responder(params):
        return {"statements": [_stmt("1001", params["code"], "FY")]}

    conn = _conn()
    n = fetch_statements(_client(responder), conn, codes=["1301"])
    assert n == 1
    row = conn.execute(
        "SELECT code, net_sales, operating_profit, type_of_current_period FROM statements"
    ).fetchone()
    assert row["code"] == "1301"
    assert row["net_sales"] == 1000.0
    assert row["operating_profit"] == 200.0
    assert row["type_of_current_period"] == "FY"


def test_quarter_and_full_year_distinguished():
    def responder(params):
        return {
            "statements": [
                _stmt("2001", params["code"], "1Q"),
                _stmt("2002", params["code"], "FY"),
            ]
        }

    conn = _conn()
    n = fetch_statements(_client(responder), conn, codes=["7203"])
    assert n == 2
    periods = {
        r["type_of_current_period"]
        for r in conn.execute("SELECT type_of_current_period FROM statements")
    }
    assert periods == {"1Q", "FY"}


def test_rerun_is_idempotent():
    def responder(params):
        return {"statements": [_stmt("3001", params["code"], "FY")]}

    conn = _conn()
    fetch_statements(_client(responder), conn, codes=["1301"])
    fetch_statements(_client(responder), conn, codes=["1301"])
    count = conn.execute("SELECT COUNT(*) AS c FROM statements").fetchone()["c"]
    assert count == 1


def test_empty_and_nonnumeric_values_become_none():
    def responder(params):
        return {"statements": [_stmt("4001", params["code"], "FY", net_sales="")]}

    conn = _conn()
    fetch_statements(_client(responder), conn, codes=["1301"])
    row = conn.execute("SELECT net_sales FROM statements").fetchone()
    assert row["net_sales"] is None


def test_record_without_disclosure_number_skipped():
    def responder(params):
        return {"statements": [{"LocalCode": params["code"], "NetSales": "1"}]}

    conn = _conn()
    n = fetch_statements(_client(responder), conn, codes=["1301"])
    assert n == 0


def test_codes_default_from_listed_info():
    from screener.db import upsert

    conn = _conn()
    upsert(conn, "listed_info", [{"code": "1301"}, {"code": "7203"}])

    def responder(params):
        return {"statements": [_stmt(f"d-{params['code']}", params["code"], "FY")]}

    n = fetch_statements(_client(responder), conn)
    assert n == 2

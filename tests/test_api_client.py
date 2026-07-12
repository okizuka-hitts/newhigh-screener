"""JQuantsClient(V2) のテスト(X-API-KEY認証・ページング・注入フック・秘匿・異常系)。

すべて注入フック(スタブトランスポート)経由でネットワークを使わない。
"""

import logging

import pytest
from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter

API_KEY = "secret-api-key-xyz"


class StubTransport:
    """呼び出しを記録し、ハンドラで応答を返す注入フック。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def __call__(self, method, url, *, params=None, headers=None, body=None):
        self.calls.append(
            {"method": method, "url": url, "params": params, "headers": headers, "body": body}
        )
        return self.handler(method, url, params, headers, body)


def _client(handler):
    clock = FakeClock()
    limiter = RateLimiter(
        config.effective_rate_limit_per_min(), time_func=clock.time, sleep_func=clock.sleep
    )
    transport = StubTransport(handler)
    client = JQuantsClient(api_key=API_KEY, transport=transport, rate_limiter=limiter)
    return client, transport


def test_sends_api_key_header():
    def handler(method, url, params, headers, body):
        assert headers[config.API_KEY_HEADER] == API_KEY
        assert "Authorization" not in headers  # Bearer方式は使わない
        return HttpResponse(200, {"data": [{"x": 1}]})

    client, transport = _client(handler)
    assert client.get_paginated("/equities/master") == [{"x": 1}]
    # V2は認証エンドポイントを踏まず、いきなりデータ取得(1リクエスト)。
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"].endswith("/equities/master")


def test_default_data_key_is_data():
    def handler(method, url, params, headers, body):
        return HttpResponse(200, {"data": [{"a": 1}, {"a": 2}]})

    client, _ = _client(handler)
    assert client.get_paginated("/x") == [{"a": 1}, {"a": 2}]


def test_non_200_raises_with_status():
    def handler(method, url, params, headers, body):
        return HttpResponse(403, {"message": "forbidden"})

    client, _ = _client(handler)
    with pytest.raises(RuntimeError) as excinfo:
        client.get_paginated("/x")
    assert "403" in str(excinfo.value)


def test_error_does_not_leak_api_key():
    def handler(method, url, params, headers, body):
        return HttpResponse(500, {})

    client, _ = _client(handler)
    with pytest.raises(RuntimeError) as excinfo:
        client.get_paginated("/x")
    assert API_KEY not in str(excinfo.value)


def test_pagination_aggregates_all_pages():
    pages = {
        None: {"data": [{"i": 1}], "pagination_key": "p1"},
        "p1": {"data": [{"i": 2}], "pagination_key": "p2"},
        "p2": {"data": [{"i": 3}]},
    }

    def handler(method, url, params, headers, body):
        key = params.get("pagination_key") if params else None
        return HttpResponse(200, pages[key])

    client, _ = _client(handler)
    assert client.get_paginated("/prices") == [{"i": 1}, {"i": 2}, {"i": 3}]


def test_single_page_when_no_pagination_key():
    def handler(method, url, params, headers, body):
        return HttpResponse(200, {"data": [{"i": 1}, {"i": 2}]})

    client, transport = _client(handler)
    assert client.get_paginated("/x") == [{"i": 1}, {"i": 2}]
    assert len(transport.calls) == 1  # 追加ページ取得はしない


def test_query_params_passed_through():
    seen = {}

    def handler(method, url, params, headers, body):
        seen.update(params or {})
        return HttpResponse(200, {"data": []})

    client, _ = _client(handler)
    client.get_paginated("/equities/bars/daily", params={"code": "13010", "from": "2026-01-01"})
    assert seen["code"] == "13010" and seen["from"] == "2026-01-01"


def test_api_key_not_logged(caplog):
    def handler(method, url, params, headers, body):
        return HttpResponse(200, {"data": []})

    client, _ = _client(handler)
    with caplog.at_level(logging.DEBUG, logger="screener.api"):
        client.get_paginated("/x")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert API_KEY not in text
    assert "実測リクエストレート" in text


def test_uses_config_key_when_omitted(monkeypatch):
    monkeypatch.setenv(config.API_KEY_ENV, "env-key")
    client = JQuantsClient(transport=lambda *a, **k: HttpResponse(200, {"data": []}))
    assert client._api_key == "env-key"


def test_default_transport_covered(monkeypatch):
    from screener.api import client as client_mod

    class FakeResp:
        status = 200

        def read(self):
            return b'{"data": [{"Code": "13010"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        assert req.full_url.startswith("https://")
        assert req.get_header(config.API_KEY_HEADER.capitalize()) == "k"  # ヘッダに載る
        return FakeResp()

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    resp = client_mod._default_transport(
        "GET", config.JQUANTS_BASE_URL + "/equities/master", headers={config.API_KEY_HEADER: "k"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"][0]["Code"] == "13010"


def test_default_transport_http_error(monkeypatch):
    import urllib.error

    from screener.api import client as client_mod

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    resp = client_mod._default_transport("GET", "https://api.jquants.com/v2/x", headers={"A": "b"})
    assert resp.status_code == 403
    assert resp.json() == {}

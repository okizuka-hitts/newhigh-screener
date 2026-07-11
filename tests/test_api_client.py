"""JQuantsClient のテスト(認証・自動更新・401再試行・ページング・注入フック・秘匿)。

すべて注入フック(スタブトランスポート)経由でネットワークを使わない。
"""

import logging

import pytest

from helpers import FakeClock

from screener import config
from screener.api.client import HttpResponse, JQuantsClient
from screener.api.rate_limiter import RateLimiter

REFRESH_TOKEN = "refresh-secret-xyz"
ID_TOKEN = "id-token-abc123"


class StubTransport:
    """呼び出しを記録し、キューまたはハンドラで応答を返す注入フック。"""

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
        config.effective_rate_limit_per_min(),
        time_func=clock.time,
        sleep_func=clock.sleep,
    )
    transport = StubTransport(handler)
    client = JQuantsClient(
        api_key=REFRESH_TOKEN,
        transport=transport,
        rate_limiter=limiter,
        time_func=clock.time,
    )
    return client, transport, clock


def _auth_ok(params):
    assert params["refreshtoken"] == REFRESH_TOKEN
    return HttpResponse(200, {"idToken": ID_TOKEN})


def test_refresh_id_token_and_use_bearer():
    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return _auth_ok(params)
        assert headers["Authorization"] == f"Bearer {ID_TOKEN}"
        return HttpResponse(200, {"data": [{"x": 1}]})

    client, transport, _ = _client(handler)
    payload = client.get_paginated("/x", "data")
    assert payload == [{"x": 1}]
    # 1回目: auth_refresh、2回目: データ取得。
    assert transport.calls[0]["url"].endswith(config.AUTH_REFRESH_ENDPOINT)


def test_token_is_cached_between_requests():
    auth_count = {"n": 0}

    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            auth_count["n"] += 1
            return _auth_ok(params)
        return HttpResponse(200, {"data": []})

    client, _, _ = _client(handler)
    client.get_paginated("/a", "data")
    client.get_paginated("/b", "data")
    assert auth_count["n"] == 1  # TTL内は再認証しない


def test_token_refreshes_after_ttl_expiry():
    auth_count = {"n": 0}

    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            auth_count["n"] += 1
            return _auth_ok(params)
        return HttpResponse(200, {"data": []})

    client, _, clock = _client(handler)
    client.get_paginated("/a", "data")
    clock.now += config.ID_TOKEN_TTL_SECONDS + 1  # 有効期限切れ
    client.get_paginated("/b", "data")
    assert auth_count["n"] == 2


def test_401_triggers_single_refresh_and_retry():
    state = {"auth": 0, "get": 0}

    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            state["auth"] += 1
            return _auth_ok(params)
        state["get"] += 1
        if state["get"] == 1:
            return HttpResponse(401, {})  # 最初は失効
        return HttpResponse(200, {"data": [{"ok": True}]})

    client, _, _ = _client(handler)
    result = client.get_paginated("/x", "data")
    assert result == [{"ok": True}]
    assert state["auth"] == 2  # 初回 + 401後の再取得
    assert state["get"] == 2


def test_auth_failure_raises_without_leaking_secret():
    def handler(method, url, params, headers, body):
        return HttpResponse(403, {"message": "forbidden"})

    client, _, _ = _client(handler)
    with pytest.raises(RuntimeError) as excinfo:
        client.get_paginated("/x", "data")
    assert REFRESH_TOKEN not in str(excinfo.value)


def test_missing_id_token_in_response_raises():
    def handler(method, url, params, headers, body):
        return HttpResponse(200, {})  # idToken 欠落

    client, _, _ = _client(handler)
    with pytest.raises(RuntimeError):
        client.get_paginated("/x", "data")


def test_non_200_data_error_raises():
    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return _auth_ok(params)
        return HttpResponse(500, {})

    client, _, _ = _client(handler)
    with pytest.raises(RuntimeError) as excinfo:
        client.get_paginated("/x", "data")
    assert "500" in str(excinfo.value)


def test_pagination_aggregates_all_pages():
    pages = {
        None: {"data": [{"i": 1}], "pagination_key": "p1"},
        "p1": {"data": [{"i": 2}], "pagination_key": "p2"},
        "p2": {"data": [{"i": 3}]},
    }

    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return _auth_ok(params)
        key = params.get("pagination_key") if params else None
        return HttpResponse(200, pages[key])

    client, _, _ = _client(handler)
    result = client.get_paginated("/prices", "data")
    assert result == [{"i": 1}, {"i": 2}, {"i": 3}]


def test_secret_and_token_not_logged(caplog):
    def handler(method, url, params, headers, body):
        if url.endswith(config.AUTH_REFRESH_ENDPOINT):
            return _auth_ok(params)
        return HttpResponse(200, {"data": []})

    client, _, _ = _client(handler)
    with caplog.at_level(logging.DEBUG, logger="screener.api"):
        client.get_paginated("/x", "data")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert REFRESH_TOKEN not in text
    assert ID_TOKEN not in text
    # 実測レートはログに出る。
    assert "実測リクエストレート" in text


def test_default_transport_covered(monkeypatch):
    # 既定トランスポート(urllib)をネットワークなしで通す。
    import io

    from screener.api import client as client_mod

    class FakeResp:
        status = 200

        def read(self):
            return b'{"idToken": "t"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        assert req.full_url.startswith("https://")
        return FakeResp()

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    resp = client_mod._default_transport(
        "POST", config.JQUANTS_BASE_URL + config.AUTH_REFRESH_ENDPOINT, params={"refreshtoken": "x"}
    )
    assert resp.status_code == 200
    assert resp.json()["idToken"] == "t"
    _ = io  # noqa: F841 (import 経由の網羅確認用)


def test_default_transport_http_error(monkeypatch):
    import urllib.error

    from screener.api import client as client_mod

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", fake_urlopen)
    resp = client_mod._default_transport("GET", "https://api.jquants.com/v1/x", headers={"A": "b"})
    assert resp.status_code == 403
    assert resp.json() == {}


def test_default_transport_uses_real_config_key(monkeypatch):
    # api_key 省略時は config.get_api_key() を使う経路の確認。
    monkeypatch.setenv(config.API_KEY_ENV, "env-token")
    client = JQuantsClient(transport=lambda *a, **k: HttpResponse(200, {"idToken": "z"}))
    assert client._api_key == "env-token"

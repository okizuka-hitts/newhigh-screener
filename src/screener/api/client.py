"""J-Quants APIクライアント(認証・トークン自動更新・レート制御・注入フック・ページング)。

- 認証: `.env` の `JQUANTS_API_KEY`(リフレッシュトークン)から idToken を取得し、
  有効期限内はキャッシュ、期限切れ/401時に自動再取得する。
- レート制御: `RateLimiter` で実効上限(上限×0.5)以内に抑え、実測レートをログに記録する。
- 注入フック: HTTPトランスポート(`Transport`)を差し替え可能にし、ユニットテストは
  ネットワークを使わずスタブ応答で検証する。
- ページング: `pagination_key` を透過的に辿って全件を集約する。

セキュリティ: APIキー・idトークン・ヘッダをログ/例外メッセージに出さない。
TLS検証は無効化しない。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from screener import config
from screener.api.rate_limiter import RateLimiter

logger = logging.getLogger("screener.api")


@dataclass
class HttpResponse:
    """トランスポートが返す最小のHTTP応答表現。"""

    status_code: int
    payload: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return self.payload


# トランスポートの型: (method, url, params, headers, body) -> HttpResponse
Transport = Callable[..., HttpResponse]


def _default_transport(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> HttpResponse:
    """stdlibのurllibによる既定トランスポート(TLS検証は既定=有効)。

    実ネットワークを使うためユニットテストでは注入フックで差し替える。
    """
    full_url = url
    if params:
        full_url = f"{url}?{urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(full_url, data=data, method=method)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=config.DEFAULT_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            return HttpResponse(resp.status, json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:  # 4xx/5xx をステータス付きで返す
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return HttpResponse(exc.code, payload)


class JQuantsClient:
    """J-Quants API への認証付きアクセスを提供するクライアント。"""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
        rate_limiter: RateLimiter | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.get_api_key()
        self._transport = transport or _default_transport
        self._rate = rate_limiter or RateLimiter(config.effective_rate_limit_per_min())
        self._time = time_func or __import__("time").monotonic
        self._id_token: str | None = None
        self._token_acquired_at: float | None = None

    # --- 認証 ----------------------------------------------------------------

    def _refresh_id_token(self) -> None:
        self._rate.acquire()
        resp = self._transport(
            "POST",
            config.JQUANTS_BASE_URL + config.AUTH_REFRESH_ENDPOINT,
            params={"refreshtoken": self._api_key},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                "J-Quantsの認証(idトークン取得)に失敗しました。"
                f"status={resp.status_code}。JQUANTS_API_KEY を確認してください。"
            )
        token = resp.json().get("idToken")
        if not token:
            raise RuntimeError("認証応答に idToken が含まれていません。")
        self._id_token = token
        self._token_acquired_at = self._time()

    def _token_is_valid(self) -> bool:
        if self._id_token is None or self._token_acquired_at is None:
            return False
        return (self._time() - self._token_acquired_at) < config.ID_TOKEN_TTL_SECONDS

    def _ensure_token(self) -> None:
        if not self._token_is_valid():
            self._refresh_id_token()

    # --- リクエスト ----------------------------------------------------------

    def _authorized_get(self, endpoint: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
        self._ensure_token()
        resp = self._do_get(endpoint, params)
        if resp.status_code == 401:
            # idトークンが途中で失効 → 強制再取得して1度だけ再試行。
            self._id_token = None
            self._ensure_token()
            resp = self._do_get(endpoint, params)
        if resp.status_code != 200:
            raise RuntimeError(
                f"J-Quants APIエラー: {endpoint} が status {resp.status_code} を返しました。"
            )
        logger.info(
            "J-Quants実測リクエストレート: %.1f req/min (実効上限 %.1f req/min)",
            self._rate.measured_rate_per_min(),
            config.effective_rate_limit_per_min(),
        )
        return resp.json()

    def _do_get(self, endpoint: str, params: Mapping[str, Any] | None) -> HttpResponse:
        self._rate.acquire()
        headers = {"Authorization": f"Bearer {self._id_token}"}
        return self._transport(
            "GET", config.JQUANTS_BASE_URL + endpoint, params=params, headers=headers
        )

    def get_paginated(
        self, endpoint: str, data_key: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """`pagination_key` を辿り、`data_key` のリストを全ページ集約して返す。"""
        query: dict[str, Any] = dict(params or {})
        items: list[dict[str, Any]] = []
        while True:
            payload = self._authorized_get(endpoint, query)
            items.extend(payload.get(data_key, []))
            next_key = payload.get("pagination_key")
            if not next_key:
                break
            query["pagination_key"] = next_key
        return items

"""プロジェクト全体の設定値・定数を集約するモジュール。

レート上限・DBパス・APIエンドポイント等をハードコードで散在させず、ここに一元化する
(code-style.md「設定値・定数は1か所に集約」)。実行時に決まる値(APIキー・DBパス)は
環境変数で上書き可能とし、取得用のヘルパ関数を提供する。
"""

from __future__ import annotations

import os
from pathlib import Path

# --- J-Quants API エンドポイント ---------------------------------------------

#: J-Quants API のベースURL(ライトプラン)。
JQUANTS_BASE_URL = "https://api.jquants.com/v1"

#: idトークンをリフレッシュトークンから取得するエンドポイント。
AUTH_REFRESH_ENDPOINT = "/token/auth_refresh"
#: 上場銘柄一覧。
LISTED_INFO_ENDPOINT = "/listed/info"
#: 日足四本値。
DAILY_QUOTES_ENDPOINT = "/prices/daily_quotes"
#: 財務データ(四半期・通期)。
STATEMENTS_ENDPOINT = "/fins/statements"

# --- 認証・機密情報 -----------------------------------------------------------

#: リフレッシュトークン(=APIキー)を格納する環境変数名。値そのものは扱わない。
API_KEY_ENV = "JQUANTS_API_KEY"

# --- レート制限(NFR-1) ------------------------------------------------------
# J-Quantsライトプランへのアクセス頻度を制御するための定数。実際のアクセスは
# この上限に安全係数を掛けた「実効上限」以内に収める(EPIC NS-1 受け入れ基準)。
# 上限値はJ-Quantsの公表値に基づく保守的な既定値であり、必要に応じてここだけを更新する。

#: ライトプランで許容すると想定するリクエスト数(毎分)。保守的な既定値。
JQUANTS_RATE_LIMIT_PER_MIN = 300
#: 上限に対する安全係数。実装+検証の合計でこの割合以内に抑える(50%)。
RATE_SAFETY_FACTOR = 0.5

# --- データ取得ウィンドウ(FR-1) --------------------------------------------

#: 日足を取得する直近期間(月)。全営業日を欠損なく揃える対象。
RECENT_MONTHS = 3
#: 52週高値判定に必要な遡及週数。
LOOKBACK_WEEKS = 52

# --- ローカルDB ---------------------------------------------------------------

#: DBパスを上書きする環境変数名(テスト・検証で一時DBを指すために使う)。
DB_PATH_ENV = "SCREENER_DB_PATH"
#: 既定のDB配置先。生データはgitignore対象の `data/` 配下に置く。
DEFAULT_DB_PATH = Path("data") / "screener.db"


def effective_rate_limit_per_min() -> float:
    """安全係数適用後の実効リクエスト上限(毎分)を返す。

    レートリミッタ(api層)はこの値を超えないようにアクセス間隔を制御する。
    """
    return JQUANTS_RATE_LIMIT_PER_MIN * RATE_SAFETY_FACTOR


def get_api_key() -> str:
    """`.env`/環境変数からリフレッシュトークン(APIキー)を取得する。

    未設定なら、キー値を漏らさない明確なメッセージで `RuntimeError` を送出する。
    """
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"環境変数 {API_KEY_ENV} が未設定です。.env に J-Quants のリフレッシュトークンを設定してください。"
        )
    return key


def get_db_path() -> Path:
    """使用するDBパスを返す。`SCREENER_DB_PATH` があればそれを優先する。"""
    override = os.environ.get(DB_PATH_ENV)
    if override:
        return Path(override)
    return DEFAULT_DB_PATH

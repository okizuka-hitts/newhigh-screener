"""プロジェクト全体の設定値・定数を集約するモジュール。

レート上限・DBパス・APIエンドポイント等をハードコードで散在させず、ここに一元化する
(code-style.md「設定値・定数は1か所に集約」)。実行時に決まる値(APIキー・DBパス)は
環境変数で上書き可能とし、取得用のヘルパ関数を提供する。

APIは J-Quants API **V2** に準拠する(V1は廃止)。特定バージョンを前提にせず、値は最新の
公式仕様(https://jpx-jquants.com/ja/spec)に合わせてここだけを更新する。
"""

from __future__ import annotations

import os
from pathlib import Path

# --- J-Quants API (V2) エンドポイント ----------------------------------------

#: J-Quants API のベースURL(V2)。
JQUANTS_BASE_URL = "https://api.jquants.com/v2"

#: 上場銘柄一覧。
LISTED_INFO_ENDPOINT = "/equities/master"
#: 日足四本値。
DAILY_QUOTES_ENDPOINT = "/equities/bars/daily"
#: 財務サマリ(四半期・通期)。V2ライトプランで利用可能(/fins/details はライト対象外)。
STATEMENTS_ENDPOINT = "/fins/summary"
#: 取引カレンダー(営業日・休日区分)。
CALENDAR_ENDPOINT = "/markets/calendar"

#: V2レスポンスの配列ラッパのキー(全エンドポイント共通)。
RESPONSE_DATA_KEY = "data"

# --- 認証・機密情報(V2) -----------------------------------------------------

#: APIキーを格納する環境変数名。値そのものは扱わない。
API_KEY_ENV = "JQUANTS_API_KEY"
#: V2認証はこのHTTPヘッダにAPIキーを載せる(静的キー。トークンのリフレッシュは無い)。
API_KEY_HEADER = "X-API-KEY"

# --- レート制限(NFR-1) ------------------------------------------------------
# J-Quants V2のプラン別レート上限(リクエスト/分)。実際のアクセスは上限に安全係数を
# 掛けた「実効上限」以内に収める(EPIC NS-1 受け入れ基準)。上限値は公式仕様の値を用い、
# 変更時はここだけを更新する。V2ライトプランの公表値 = 60 req/min。

#: ライトプランのリクエスト上限(毎分)。J-Quants V2公式のレートリミット表より。
JQUANTS_RATE_LIMIT_PER_MIN = 60
#: 上限に対する安全係数。実装+検証の合計でこの割合以内に抑える(50%)。
RATE_SAFETY_FACTOR = 0.5

# --- HTTP ---------------------------------------------------------------------

#: HTTPリクエストのタイムアウト(秒)。
DEFAULT_TIMEOUT_SECONDS = 30.0

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

# --- .env の自動ロード(NFR-3) ------------------------------------------------

#: 自動ロードするファイル名(カレントディレクトリ基準)。生データ同様gitignore済み。
ENV_FILE_NAME = ".env"


def load_env_file(path: str | os.PathLike[str] | None = None) -> None:
    """カレントディレクトリの `.env` を読み、未設定の環境変数だけを補充する。

    なぜ:READMEの手順(`cp .env.example .env`)どおり `.env` にキーを置くだけで
    `screener fetch` が認証できるようにする(NFR-3「APIキーは `.env` で指定」)。
    CLIエントリポイントで1回呼ぶ。

    - **既存のプロセス環境変数を上書きしない**(`os.environ.setdefault` と同義)。
      CI・テストの `SCREENER_DB_PATH` 等の明示的な上書きを壊さないため。
    - ファイルが無ければ何もしない。`.claude/rules/security.md`「依存は最小限に」に沿い
      `python-dotenv` を足さず軽量に自前パースする。
    """
    env_path = Path(path) if path is not None else Path(ENV_FILE_NAME)
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # 値を囲む対の引用符(' または ")のみ剥がす。中身は変更しない。
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def effective_rate_limit_per_min() -> float:
    """安全係数適用後の実効リクエスト上限(毎分)を返す。

    レートリミッタ(api層)はこの値を超えないようにアクセス間隔を制御する。
    """
    return JQUANTS_RATE_LIMIT_PER_MIN * RATE_SAFETY_FACTOR


def get_api_key() -> str:
    """`.env`/環境変数からAPIキー(V2の X-API-KEY 値)を取得する。

    未設定なら、キー値を漏らさない明確なメッセージで `RuntimeError` を送出する。
    """
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"環境変数 {API_KEY_ENV} が未設定です。.env に J-Quants(V2)のAPIキーを設定してください。"
        )
    return key


def get_db_path() -> Path:
    """使用するDBパスを返す。`SCREENER_DB_PATH` があればそれを優先する。"""
    override = os.environ.get(DB_PATH_ENV)
    if override:
        return Path(override)
    return DEFAULT_DB_PATH

"""データ取得オーケストレーション層(FR-1)。

api層(取得)とdb層(保存)を束ね、fetchパイプラインを構成する。
上位のcli/detect/tuneから利用され、api/dbより上位に位置する(一方向依存)。
"""

from screener.fetch.listed_info import fetch_listed_info

__all__ = ["fetch_listed_info"]

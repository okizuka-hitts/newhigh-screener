"""SQLiteアクセス層。

スキーマ定義・接続・初期化を提供する。上位層(cli/detect/tune/api)からのみ利用され、
この層は上位に依存しない(一方向依存)。SQLは必ずパラメータ化する。
"""

from screener.db.connection import connect
from screener.db.schema import SCHEMA_VERSION, TABLES, init_db, upsert

__all__ = ["connect", "init_db", "upsert", "SCHEMA_VERSION", "TABLES"]

"""データ完全性検査(fetch --verify)。

以下を検査し、欠損があれば理由付きで報告する:
- 上場銘柄一覧が存在する
- 対象期間(直近3か月を包含する52週)に日足が存在し、遡及が52週分に達している
- 各上場銘柄の日足に、観測された営業日カレンダー上の内部欠損が無い
  (観測された取引日の集合を営業日カレンダーとみなすため、休場日を欠損と誤検知しない)
- 財務データが存在する
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass, field

from screener import config
from screener.fetch.daily_quotes import fetch_window

#: 52週遡及判定の許容日数(窓の始点が週末・休場で数日ずれることを許容)。
_LOOKBACK_TOLERANCE_DAYS = 14


@dataclass
class VerifyReport:
    """完全性検査の結果。"""

    complete: bool
    issues: list[str] = field(default_factory=list)


def verify_data(
    conn: sqlite3.Connection, *, reference_date: _dt.date | None = None
) -> VerifyReport:
    """DBのデータ完全性を検査してレポートを返す。"""
    reference_date = reference_date or _dt.date.today()
    from_date, to_date = fetch_window(reference_date)
    issues: list[str] = []

    listed = [r["code"] for r in conn.execute("SELECT code FROM listed_info ORDER BY code")]
    if not listed:
        issues.append("上場銘柄一覧が空です(fetch未実行の可能性)")

    trading_dates = [
        r["date"]
        for r in conn.execute(
            "SELECT DISTINCT date FROM daily_quotes WHERE date BETWEEN ? AND ? ORDER BY date",
            (from_date, to_date),
        )
    ]
    if not trading_dates:
        issues.append("対象期間の日足がありません")
    else:
        earliest = _dt.date.fromisoformat(trading_dates[0])
        required_days = 7 * config.LOOKBACK_WEEKS - _LOOKBACK_TOLERANCE_DAYS
        if (reference_date - earliest).days < required_days:
            issues.append(f"日足の遡及が52週分に不足しています(最古の取引日 {trading_dates[0]})")

        for code in listed:
            dates = [
                r["date"]
                for r in conn.execute(
                    "SELECT date FROM daily_quotes "
                    "WHERE code = ? AND date BETWEEN ? AND ? ORDER BY date",
                    (code, from_date, to_date),
                )
            ]
            if not dates:
                issues.append(f"{code}: 対象期間の日足がありません")
                continue
            # 各銘柄の観測範囲 [初日, 最終日] 内で、営業日カレンダーの欠損を検査する。
            expected = [d for d in trading_dates if dates[0] <= d <= dates[-1]]
            missing = sorted(set(expected) - set(dates))
            if missing:
                issues.append(f"{code}: 営業日 {len(missing)}件の日足欠損(例 {missing[0]})")

    stmt_count = conn.execute("SELECT COUNT(*) AS c FROM statements").fetchone()["c"]
    if stmt_count == 0:
        issues.append("財務データが空です")

    return VerifyReport(complete=not issues, issues=issues)

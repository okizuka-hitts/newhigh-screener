"""データ完全性検査(verify_data)のテスト。"""

import datetime as dt

from screener import config
from screener.db import connect, init_db, upsert
from screener.fetch import verify_data

REF = dt.date(2026, 7, 10)


def _conn():
    conn = connect(":memory:")
    init_db(conn)
    return conn


def _full_calendar():
    """窓の始点(52週前)から基準日までの月〜金を営業日カレンダーとして返す。"""
    frm = REF - dt.timedelta(weeks=config.LOOKBACK_WEEKS)
    days = []
    d = frm
    while d <= REF:
        if d.weekday() < 5:  # 平日のみ(休場日=土日を除外)
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


def _seed_complete(conn, codes=("1301", "7203")):
    upsert(conn, "listed_info", [{"code": c} for c in codes])
    cal = _full_calendar()
    for c in codes:
        upsert(
            conn,
            "daily_quotes",
            [{"code": c, "date": d, "close": 100.0, "adjustment_factor": 1.0} for d in cal],
        )
    upsert(conn, "statements", [{"disclosure_number": "d1", "code": codes[0]}])
    return cal


def test_complete_db_reports_complete():
    conn = _conn()
    _seed_complete(conn)
    report = verify_data(conn, reference_date=REF)
    assert report.complete
    assert report.issues == []


def test_empty_db_is_incomplete():
    conn = _conn()
    report = verify_data(conn, reference_date=REF)
    assert not report.complete
    assert any("上場銘柄一覧" in i for i in report.issues)


def test_internal_gap_is_detected():
    conn = _conn()
    cal = _seed_complete(conn)
    # 1301 の途中の1営業日を削除して欠損を作る。
    victim = cal[len(cal) // 2]
    conn.execute("DELETE FROM daily_quotes WHERE code = ? AND date = ?", ("1301", victim))
    conn.commit()
    report = verify_data(conn, reference_date=REF)
    assert not report.complete
    assert any("1301" in i and "欠損" in i for i in report.issues)


def test_holidays_not_flagged_as_missing():
    # カレンダーに土日が無くても(＝全銘柄が休場日を持たない)欠損扱いにならない。
    conn = _conn()
    _seed_complete(conn)
    report = verify_data(conn, reference_date=REF)
    assert report.complete


def test_insufficient_lookback_detected():
    conn = _conn()
    upsert(conn, "listed_info", [{"code": "1301"}])
    # 直近1週間しか日足が無い → 52週遡及に不足。
    recent = [
        {"code": "1301", "date": (REF - dt.timedelta(days=n)).isoformat(), "close": 1.0}
        for n in range(5)
    ]
    upsert(conn, "daily_quotes", recent)
    upsert(conn, "statements", [{"disclosure_number": "d1", "code": "1301"}])
    report = verify_data(conn, reference_date=REF)
    assert not report.complete
    assert any("52週" in i for i in report.issues)


def test_missing_statements_detected():
    conn = _conn()
    _seed_complete(conn)
    conn.execute("DELETE FROM statements")
    conn.commit()
    report = verify_data(conn, reference_date=REF)
    assert not report.complete
    assert any("財務データ" in i for i in report.issues)


def test_code_without_any_quotes_detected():
    conn = _conn()
    _seed_complete(conn, codes=("1301",))
    upsert(conn, "listed_info", [{"code": "9999"}])  # 日足の無い銘柄を追加
    report = verify_data(conn, reference_date=REF)
    assert not report.complete
    assert any("9999" in i for i in report.issues)

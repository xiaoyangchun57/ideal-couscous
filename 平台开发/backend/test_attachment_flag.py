"""影像抽样审核标红引擎单元测试：覆盖 GPS 偏离 / 时间异常 / 关联异常项 三条规则。"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A  # noqa


def make_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, gps_lat REAL, gps_lng REAL)")
    # 站点 1 坐标约 (28.6833, 115.7333)
    db.execute("INSERT INTO sites VALUES (1, 28.6833, 115.7333)")
    db.execute("CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, start_date TEXT, plan_date TEXT)")
    db.execute("INSERT INTO insp_plans VALUES (10, '2026-07-20', NULL)")
    db.execute("CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER, result TEXT)")
    db.execute("INSERT INTO insp_plan_items VALUES (100, 10, 'abnormal')")
    db.execute("INSERT INTO insp_plan_items VALUES (101, 10, 'normal')")
    db.commit()
    return db


def ev(db, **att):
    base = dict(site_id=None, source_type='', source_id=0,
                gps_lat=None, gps_lng=None, taken_at=None)
    base.update(att)
    return A._evaluate_attachment_flag(db, base)


def test_gps_far():
    db = make_db()
    # 偏离约 1.5km：28.69,115.75
    r = ev(db, site_id=1, gps_lat=28.69, gps_lng=115.75, taken_at='2026-07-22 10:00:00')
    assert r['is_flagged'] == 1, r
    assert 'GPS偏离' in r['flag_reason'], r
    print('PASS gps_far ->', r['flag_reason'])


def test_gps_near_ok():
    db = make_db()
    # 站内 5m：站点 28.6833,115.7333
    r = ev(db, site_id=1, gps_lat=28.6835, gps_lng=115.7335, taken_at='2026-07-22 10:00:00')
    assert r['is_flagged'] == 0, r
    print('PASS gps_near_ok -> 不标红')


def test_future_time():
    db = make_db()
    r = ev(db, site_id=1, taken_at='2099-01-01 00:00:00')
    assert r['is_flagged'] == 1 and '未来' in r['flag_reason'], r
    print('PASS future_time ->', r['flag_reason'])


def test_abnormal_item():
    db = make_db()
    r = ev(db, site_id=1, source_type='inspection', source_id=100, taken_at='2026-07-22 10:00:00')
    assert r['is_flagged'] == 1 and '异常' in r['flag_reason'], r
    print('PASS abnormal_item ->', r['flag_reason'])


def test_normal_clean():
    db = make_db()
    r = ev(db, site_id=1, source_type='inspection', source_id=101,
           gps_lat=28.6835, gps_lng=115.7335, taken_at='2026-07-22 10:00:00')
    assert r['is_flagged'] == 0, r
    print('PASS normal_clean -> 不标红')


def test_missing_metadata_stays_for_manual_review():
    db = make_db()
    result = ev(db, site_id=1, source_type='inspection', source_id=101)
    assert result['is_flagged'] == 1, result
    assert result['flag_rule'] == 'metadata_missing', result


def test_normal_complete_metadata_is_not_flagged():
    db = make_db()
    result = ev(
        db,
        site_id=1,
        source_type='inspection',
        source_id=101,
        gps_lat=28.6835,
        gps_lng=115.7335,
        taken_at='2026-07-22 10:00:00',
    )
    assert result['is_flagged'] == 0, result


def test_haversine():
    # 同点距离≈0
    assert abs(A._haversine(28.68, 115.73, 28.68, 115.73)) < 1
    d = A._haversine(28.6833, 115.7333, 28.69, 115.75)
    assert 1000 < d < 2000, d
    print('PASS haversine 站外约 %.0f 米' % d)


if __name__ == '__main__':
    test_haversine()
    test_gps_far()
    test_gps_near_ok()
    test_future_time()
    test_abnormal_item()
    test_normal_clean()
    test_missing_metadata_stays_for_manual_review()
    test_normal_complete_metadata_is_not_flagged()
    print('\nALL_FLAG_TESTS_PASSED')

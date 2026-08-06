"""影像业务归属筛选契约测试。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A  # noqa: E402


def make_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE operation_attachments (id INTEGER, source_type TEXT, category TEXT)')
    db.executemany(
        'INSERT INTO operation_attachments VALUES (?,?,?)',
        [
            (1, 'workorder', '现场照片'),
            (2, 'inspection', '现场照片'),
            (3, '', '现场照片'),
            (4, 'vehicle', '养护记录'),
            (5, '', '养护记录'),
            (6, 'unknown_module', '其他'),
        ],
    )
    return db


def ids_for(db, business_type):
    sql, params = A._attachment_business_filter(business_type, '')
    return [row['id'] for row in db.execute(
        f'SELECT id FROM operation_attachments WHERE {sql} ORDER BY id', params
    )]


def test_source_is_authoritative_and_groups_are_exclusive():
    db = make_db()
    assert ids_for(db, 'workorder') == [1]
    assert ids_for(db, 'inspection') == [2, 3]
    assert ids_for(db, 'vehicle') == [4]
    assert ids_for(db, 'maintenance') == [5]
    assert ids_for(db, 'other') == [6]


def test_unknown_group_is_rejected():
    assert A._attachment_business_filter('not-a-group') == (None, [])

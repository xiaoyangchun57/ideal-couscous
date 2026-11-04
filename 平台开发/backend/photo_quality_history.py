"""Local-only preview/conversion of the retired photo-remediation requirement.

The CLI reads the source in read-only mode by default. It never deletes photos,
rewrites execution results, or changes vehicle/parts/approval records.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from photo_quality import (R30ContractError, column_exists, ensure_schema,
                           inspection_attachment_context, record_photo_quality,
                           table_exists)


def preview(db, item_ids=None):
    selected = set(map(int, item_ids or []))
    result = {'items': [], 'manual_review': []}
    if not table_exists(db, 'insp_plan_items'):
        return result
    for raw in db.execute('SELECT * FROM insp_plan_items ORDER BY id'):
        item = dict(raw)
        if selected and item['id'] not in selected:
            continue
        if not (item.get('review_status') == 3 or item.get('evidence_status') in
                ('supplement_required', 'replacement_submitted')):
            continue
        reason = str(item.get('supplement_reason') or item.get('review_comment') or '')
        explicit = item.get('evidence_status') in ('supplement_required', 'replacement_submitted')
        if not explicit and not any(word in reason for word in ('照片', '影像', '补拍', '水印', '拍摄')):
            result['manual_review'].append({'item_id': item['id'], 'reason': '无法确认仅因照片质量产生整改'})
            continue
        plan = db.execute('SELECT * FROM insp_plans WHERE id=?', (item['plan_id'],)).fetchone()
        if plan and dict(plan).get('rework_of_plan_id'):
            result['manual_review'].append({'item_id': item['id'], 'reason': '历史独立整改包涉及资源关联，保留另行核对'})
            continue
        photos, pending = [], False
        if table_exists(db, 'operation_attachments'):
            for raw_photo in db.execute("SELECT * FROM operation_attachments WHERE source_type='inspection' AND source_id=?", (item['id'],)):
                photo = dict(raw_photo)
                if photo.get('is_deleted'):
                    continue
                pending |= photo.get('review_status') == 'pending'
                if photo.get('review_status') not in ('approved', 'rejected'):
                    continue
                issue = ''
                try:
                    context = inspection_attachment_context(db, photo['id'])
                    if context['attribution_status'] != 'reliable':
                        issue = '实际检查时间或责任人不明确，未计扣分'
                except R30ContractError as error:
                    issue = error.message
                if not photo.get('reviewer_id') or not photo.get('reviewed_at'):
                    issue = '缺少逐张审核人或审核时间，未计扣分'
                if photo.get('review_status') == 'rejected' and not str(photo.get('reject_reason') or '').strip():
                    issue = '缺少逐张不合格原因，未计扣分'
                photos.append({'attachment_id': photo['id'], 'eligible': not issue, 'note': issue})
        result['items'].append({
            'item_id': item['id'], 'plan_id': item['plan_id'], 'before': item,
            'review_status_after': 1 if pending else (2 if item.get('result') not in (None, '') else 0),
            'photos': photos, 'has_execution_result': item.get('result') not in (None, ''),
            'note': '' if any(p['eligible'] for p in photos) else '原意见保留，无可靠逐张依据，未计扣分',
        })
    return result


def convert_local(db, item_ids):
    """Apply explicitly selected local candidates atomically, preserving before snapshots."""
    if not item_ids:
        raise ValueError('本地修正必须明确选择检查项')
    report = preview(db, item_ids)
    if report['manual_review']:
        raise ValueError('所选项含非照片原因或独立整改包，请先核对预览')
    report.update({'converted_items': 0, 'quality_records': 0})
    db.execute('SAVEPOINT photo_quality_history')
    try:
        ensure_schema(db)
        db.execute('''CREATE TABLE IF NOT EXISTS photo_quality_history_audits (
            item_id INTEGER PRIMARY KEY, before_json TEXT NOT NULL,
            converted_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))''')
        plans = set()
        for candidate in report['items']:
            item = candidate['before']
            db.execute('INSERT OR IGNORE INTO photo_quality_history_audits(item_id,before_json) VALUES (?,?)',
                       (item['id'], json.dumps(item, ensure_ascii=False, sort_keys=True)))
            for photo in candidate['photos']:
                if not photo['eligible'] or db.execute('SELECT 1 FROM photo_quality_evaluations WHERE attachment_id=? LIMIT 1', (photo['attachment_id'],)).fetchone():
                    continue
                attachment = db.execute('SELECT * FROM operation_attachments WHERE id=?', (photo['attachment_id'],)).fetchone()
                outcome = record_photo_quality(db, attachment['id'], attachment['review_status'],
                    attachment['reject_reason'] or '', attachment['reviewer_id'], attachment['reviewed_at'],
                    idempotency_key=f'photo-history-v1:{attachment["id"]}', correction_type='legacy_migration')
                report['quality_records'] += int(outcome['changed'])
            updates = ['review_status=?', 'r30_history_quality_status=?', 'r30_history_quality_reason=?']
            values = [candidate['review_status_after'], 'unscored' if candidate['note'] else 'recorded',
                      item.get('review_comment') or item.get('supplement_reason') or candidate['note']]
            for column, value in (('evidence_status', 'historical_quality_only'),
                                  ('supplement_required_at', ''), ('rework_required_at', '')):
                if column_exists(db, 'insp_plan_items', column):
                    updates.append(column + '=?')
                    values.append(value)
            db.execute('UPDATE insp_plan_items SET ' + ','.join(updates) + ' WHERE id=?', values + [item['id']])
            plans.add(item['plan_id'])
            report['converted_items'] += 1
            if (table_exists(db, 'notifications')
                    and all(column_exists(db, 'notifications', name)
                            for name in ('source_type', 'source_id', 'dedupe_key', 'is_read'))):
                remaining = db.execute('SELECT 1 FROM insp_plan_items WHERE plan_id=? AND site_id=? AND review_status=3 LIMIT 1',
                                       (item['plan_id'], item['site_id'])).fetchone()
                if not remaining:
                    db.execute("UPDATE notifications SET is_read=1 WHERE source_type='inspection_rework' AND source_id=? AND dedupe_key=?",
                               (item['plan_id'], f'inspection_rework:{item["plan_id"]}:{item["site_id"]}'))
        for plan_id in plans:
            active = "COALESCE(execution_status,'active')='active'" if column_exists(db, 'insp_plan_items', 'execution_status') else '1=1'
            checkout = 'check_out_time IS NULL' if column_exists(db, 'insp_plan_items', 'check_out_time') else 'result IS NULL'
            count = db.execute(f'SELECT COUNT(*),SUM(CASE WHEN {checkout} THEN 1 ELSE 0 END),SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) FROM insp_plan_items WHERE plan_id=? AND {active}', (plan_id,)).fetchone()
            status = 'completed' if count[0] and not count[1] else 'active'
            db.execute("UPDATE insp_plans SET status=? WHERE id=? AND status!='cancelled'", (status, plan_id))
            if column_exists(db, 'insp_plans', 'completion_rate'):
                db.execute('UPDATE insp_plans SET completion_rate=? WHERE id=?', (round((count[2] or 0) * 100 / count[0], 1) if count[0] else 0, plan_id))
            if (column_exists(db, 'insp_plans', 'plan_schedule_id')
                    and column_exists(db, 'plan_schedules', 'field_status')):
                schedule_id = db.execute('SELECT plan_schedule_id FROM insp_plans WHERE id=?', (plan_id,)).fetchone()[0]
                siblings = db.execute(f"""SELECT p.status FROM insp_plans p
                    WHERE p.plan_schedule_id=? AND p.status!='cancelled'""", (schedule_id,)).fetchall()
                field_status = 'completed' if siblings and all(row['status'] == 'completed' for row in siblings) else 'active'
                open_items = db.execute(f'''SELECT 1 FROM insp_plan_items WHERE plan_id IN
                    (SELECT id FROM insp_plans WHERE plan_schedule_id=? AND status!='cancelled')
                    AND {active} AND {checkout} LIMIT 1''', (schedule_id,)).fetchone()
                if open_items:
                    field_status = 'active'
                db.execute('UPDATE plan_schedules SET field_status=? WHERE id=?', (field_status, schedule_id))
                if field_status != 'completed' and column_exists(db, 'plan_schedules', 'field_completed_at'):
                    db.execute('UPDATE plan_schedules SET field_completed_at=NULL WHERE id=?', (schedule_id,))
        db.execute('RELEASE SAVEPOINT photo_quality_history')
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT photo_quality_history')
        db.execute('RELEASE SAVEPOINT photo_quality_history')
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--item-id', type=int, action='append')
    parser.add_argument('--apply-local', action='store_true')
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    if args.apply_local:
        local_root = Path(__file__).resolve().parents[2] / 'photo-rework-20261104'
        if not database.is_relative_to(local_root.resolve()):
            parser.error('只允许写入本轮 photo-rework-20261104 目录中的独立本地演练库')
    uri = database.as_uri() + ('?mode=rw' if args.apply_local else '?mode=ro')
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        report = convert_local(db, args.item_id) if args.apply_local else preview(db, args.item_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

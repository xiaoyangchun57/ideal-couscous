"""Photo quality subset of the reviewed R30 implementation (2026-10-27)."""


from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass
class R30ContractError(Exception):
    message: str
    code: str
    status: int = 409
    details: dict | None = None

    def __str__(self):
        return self.message


def table_exists(db, name):
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def column_exists(db, table, column):
    if not table_exists(db, table):
        return False
    return any(row[1] == column for row in db.execute(f'PRAGMA table_info({table})').fetchall())


def _add_column(db, table, definition):
    column = definition.split()[0]
    if table_exists(db, table) and not column_exists(db, table, column):
        db.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')


def ensure_schema(db):
    """Apply the additive R30 schema to a production-shaped or narrow test DB."""
    _add_column(db, 'insp_plan_items', "r30_history_quality_status TEXT DEFAULT ''")
    _add_column(db, 'insp_plan_items', "r30_history_quality_reason TEXT DEFAULT ''")
    _add_column(db, 'operation_attachments', "quality_score_month TEXT DEFAULT ''")
    _add_column(db, 'operation_attachments', 'quality_responsible_user_id INTEGER')
    _add_column(db, 'operation_attachments', 'quality_decision_id INTEGER')

    db.execute('''CREATE TABLE IF NOT EXISTS photo_quality_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attachment_id INTEGER NOT NULL,
        previous_status TEXT DEFAULT '',
        status TEXT NOT NULL,
        reason TEXT DEFAULT '',
        reviewer_id INTEGER,
        reviewed_at TEXT NOT NULL,
        responsible_user_id INTEGER,
        inspection_completed_at TEXT,
        score_month TEXT DEFAULT '',
        attribution_status TEXT NOT NULL DEFAULT 'reliable',
        correction_type TEXT NOT NULL DEFAULT 'review',
        related_attachment_id INTEGER,
        idempotency_key TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_photo_quality_attachment
        ON photo_quality_evaluations(attachment_id, id DESC)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_photo_quality_person_month
        ON photo_quality_evaluations(responsible_user_id, score_month, id DESC)''')
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_photo_quality_idempotency
        ON photo_quality_evaluations(idempotency_key)
        WHERE idempotency_key<>''""")




def _parse_business_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(BUSINESS_TIMEZONE).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or '').strip()
    if not text:
        raise R30ContractError('缺少实际检查时间', 'INSPECTION_COMPLETED_AT_REQUIRED', 409)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(BUSINESS_TIMEZONE).replace(tzinfo=None)
        return parsed
    except ValueError as exc:
        raise R30ContractError(
            '实际检查时间格式无效', 'INSPECTION_COMPLETED_AT_INVALID', 409,
            {'completed_at': value},
        ) from exc


def actual_inspection_time(item):
    """Use persisted execution facts; never review, creation or migration time."""
    item = dict(item)
    if item.get('result') in (None, ''):
        return ''
    value = item.get('completed_at') or item.get('check_time')
    if not value:
        return ''
    try:
        return _parse_business_datetime(value).strftime('%Y-%m-%d %H:%M:%S')
    except R30ContractError:
        return ''


def _row_dict(row):
    return dict(row) if row is not None else None


def inspection_attachment_context(db, attachment_id):
    if not table_exists(db, 'operation_attachments'):
        raise R30ContractError('照片不存在', 'ATTACHMENT_NOT_FOUND', 404)
    attachment = db.execute(
        'SELECT * FROM operation_attachments WHERE id=?', (attachment_id,)
    ).fetchone()
    if not attachment:
        raise R30ContractError('照片不存在', 'ATTACHMENT_NOT_FOUND', 404)
    attachment = _row_dict(attachment)
    if attachment.get('is_deleted'):
        raise R30ContractError('照片已删除，无法评价', 'ATTACHMENT_DELETED', 409)
    if str(attachment.get('source_type') or '') != 'inspection':
        raise R30ContractError('照片不属于巡检业务', 'ATTACHMENT_TYPE_NOT_REVIEWABLE', 400)
    item_id = attachment.get('source_id') or attachment.get('item_id')
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        item_id = 0
    if not item_id or not table_exists(db, 'insp_plan_items'):
        raise R30ContractError(
            '照片缺少可靠检查项归属', 'ATTACHMENT_ITEM_REQUIRED', 409,
            {'attachment_id': attachment_id},
        )
    plan_join = 'LEFT JOIN insp_plans p ON p.id=i.plan_id' if table_exists(db, 'insp_plans') else ''
    assignee = 'p.assignee_id AS responsible_user_id' if plan_join else 'NULL AS responsible_user_id'
    item = db.execute(f'''SELECT i.*, {assignee}
        FROM insp_plan_items i {plan_join} WHERE i.id=?''', (item_id,)).fetchone()
    if not item:
        raise R30ContractError(
            '照片关联的检查项不存在', 'ATTACHMENT_ITEM_REQUIRED', 409,
            {'attachment_id': attachment_id, 'item_id': item_id},
        )
    item = _row_dict(item)
    if attachment.get('site_id') not in (None, item.get('site_id')):
        raise R30ContractError(
            '照片与检查项站点不一致', 'ATTACHMENT_RELATION_MISMATCH', 409,
            {'attachment_id': attachment_id, 'item_id': item_id},
        )
    if attachment.get('plan_id') not in (None, 0, '', item.get('plan_id')):
        raise R30ContractError(
            '照片与检查项计划不一致', 'ATTACHMENT_RELATION_MISMATCH', 409,
            {'attachment_id': attachment_id, 'item_id': item_id},
        )
    completed_at = actual_inspection_time(item)
    responsible_user_id = item.get('responsible_user_id')
    attribution_status = 'reliable' if completed_at and responsible_user_id else 'unknown'
    score_month = completed_at[:7] if attribution_status == 'reliable' else ''
    return {
        'attachment': attachment,
        'item': item,
        'item_id': item_id,
        'plan_id': item.get('plan_id'),
        'site_id': item.get('site_id'),
        'responsible_user_id': responsible_user_id,
        'inspection_completed_at': completed_at or None,
        'score_month': score_month,
        'attribution_status': attribution_status,
    }


def _quality_status(value):
    normalized = str(value or '').strip().lower()
    if normalized in {'approve', 'approved', 'qualified'}:
        return 'qualified', 'approved'
    if normalized in {'reject', 'rejected', 'unqualified'}:
        return 'unqualified', 'rejected'
    raise R30ContractError('无效照片质量结论', 'PHOTO_QUALITY_ACTION_INVALID', 400)


def record_photo_quality(
        db, attachment_id, status, reason, reviewer_id, reviewed_at,
        *, idempotency_key='', expected_current_status=None,
        correction_type='', related_attachment_id=None):
    ensure_schema(db)
    context = inspection_attachment_context(db, attachment_id)
    attachment = context['attachment']
    quality_status, storage_status = _quality_status(status)
    reason = str(reason or '').strip()
    if quality_status == 'unqualified' and not reason:
        raise R30ContractError('不合格照片必须填写原因', 'PHOTO_QUALITY_REASON_REQUIRED', 400)
    current_storage = str(attachment.get('review_status') or 'pending').strip().lower()
    source_voided = current_storage == 'voided' or bool(attachment.get('voided_at'))
    persisted_status = 'voided' if source_voided else storage_status
    latest_quality = db.execute('''SELECT * FROM photo_quality_evaluations
        WHERE attachment_id=? ORDER BY id DESC LIMIT 1''', (attachment_id,)).fetchone()
    current_quality = current_storage
    current_reason = str(attachment.get('reject_reason') or '').strip()
    if latest_quality:
        # Rejudgment changes quality only, never the original responsibility/month.
        for key in ('responsible_user_id', 'inspection_completed_at', 'score_month', 'attribution_status'):
            context[key] = latest_quality[key]
        _, current_quality = _quality_status(latest_quality['status'])
        current_reason = str(latest_quality['reason'] or '').strip()
    elif source_voided and table_exists(db, 'attachment_void_audits'):
        original = db.execute('''SELECT original_review_status FROM attachment_void_audits
            WHERE attachment_id=?''', (attachment_id,)).fetchone()
        current_quality = str(original['original_review_status'] or 'pending') if original else 'pending'
    idempotency_key = str(idempotency_key or '').strip()
    if idempotency_key:
        prior = db.execute(
            'SELECT * FROM photo_quality_evaluations WHERE idempotency_key=?',
            (idempotency_key,),
        ).fetchone()
        if prior:
            prior = _row_dict(prior)
            if (int(prior['attachment_id']) != int(attachment_id)
                    or prior['status'] != quality_status
                    or str(prior['reason'] or '').strip() != reason):
                raise R30ContractError(
                    '幂等键已用于不同的照片质量请求', 'PHOTO_QUALITY_IDEMPOTENCY_CONFLICT', 409)
            if source_voided and current_storage != 'voided':
                db.execute("UPDATE operation_attachments SET review_status='voided' WHERE id=?", (attachment_id,))
            return {
                'attachment_id': attachment_id,
                'quality_status': prior['status'],
                'storage_status': 'voided' if source_voided else current_storage,
                'score_month': prior['score_month'] or '',
                'responsible_user_id': prior['responsible_user_id'],
                'attribution_status': prior['attribution_status'],
                'decision_id': prior['id'],
                'changed': False,
                'idempotent_replay': True,
            }
    if expected_current_status is not None:
        _, expected_storage = _quality_status(expected_current_status) \
            if expected_current_status not in ('pending', None, '') else ('pending', 'pending')
        if current_quality != expected_storage:
            raise R30ContractError(
                '照片质量状态已变化，请刷新后重试', 'PHOTO_QUALITY_STATE_CONFLICT', 409,
                {'current_status': current_quality},
            )
    if (correction_type != 'legacy_migration' and current_quality == storage_status
            and current_storage == persisted_status
            and (not source_voided or latest_quality is not None)
            and (storage_status != 'rejected' or current_reason == reason)):
        prior = db.execute('''SELECT * FROM photo_quality_evaluations
            WHERE attachment_id=? ORDER BY id DESC LIMIT 1''', (attachment_id,)).fetchone()
        return {
            'attachment_id': attachment_id,
            'quality_status': quality_status,
            'storage_status': persisted_status,
            'score_month': context['score_month'],
            'responsible_user_id': context['responsible_user_id'],
            'attribution_status': context['attribution_status'],
            'decision_id': prior['id'] if prior else attachment.get('quality_decision_id'),
            'changed': False,
            'idempotent_replay': False,
        }
    if not correction_type:
        correction_type = 'review' if current_quality in {'', 'pending', 'none'} else 'rejudgment'
    cursor = db.execute('''INSERT INTO photo_quality_evaluations
        (attachment_id,previous_status,status,reason,reviewer_id,reviewed_at,
         responsible_user_id,inspection_completed_at,score_month,attribution_status,
         correction_type,related_attachment_id,idempotency_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            attachment_id, current_quality, quality_status, reason, reviewer_id, reviewed_at,
            context['responsible_user_id'], context['inspection_completed_at'],
            context['score_month'], context['attribution_status'], correction_type,
            related_attachment_id, idempotency_key,
        ))
    decision_id = cursor.lastrowid
    assignments = [
        'review_status=?', 'reviewer_id=?', 'reviewed_at=?', 'reject_reason=?',
        'quality_score_month=?', 'quality_responsible_user_id=?', 'quality_decision_id=?',
    ]
    db.execute(f'''UPDATE operation_attachments SET {','.join(assignments)} WHERE id=?''', (
        persisted_status, reviewer_id, reviewed_at,
        reason if storage_status == 'rejected' else '', context['score_month'],
        context['responsible_user_id'], decision_id, attachment_id,
    ))
    return {
        'attachment_id': attachment_id,
        'quality_status': quality_status,
        'storage_status': persisted_status,
        'previous_storage_status': current_storage,
        'score_month': context['score_month'],
        'responsible_user_id': context['responsible_user_id'],
        'attribution_status': context['attribution_status'],
        'decision_id': decision_id,
        'changed': True,
        'idempotent_replay': False,
        'evidence_qualification': attachment.get('evidence_qualification'),
    }


def photo_quality_deductions(db, assignee_ids, start_at, end_at, allowed_site_ids=None):
    """Return current deductions without loading the per-photo audit history."""
    ids = sorted({int(value) for value in assignee_ids})
    if not ids or not all(table_exists(db, name) for name in (
            'operation_attachments', 'insp_plan_items', 'insp_plans', 'photo_quality_evaluations')):
        return {}
    sites = None if allowed_site_ids is None else sorted({int(value) for value in allowed_site_ids})
    if sites == []:
        return {}
    item_link = 'COALESCE(a.item_id,a.source_id)' if column_exists(db, 'operation_attachments', 'item_id') else 'a.source_id'
    site_clause = '' if sites is None else f" AND i.site_id IN ({','.join('?' for _ in sites)})"
    rows = db.execute(f'''SELECT q.responsible_user_id, COUNT(*) AS deduction
        FROM operation_attachments a
        JOIN insp_plan_items i ON i.id={item_link}
        JOIN insp_plans p ON p.id=i.plan_id
        JOIN photo_quality_evaluations q ON q.id=(SELECT MAX(id)
            FROM photo_quality_evaluations WHERE attachment_id=a.id)
        WHERE a.source_type='inspection' AND COALESCE(a.is_deleted,0)=0
          AND q.responsible_user_id IN ({','.join('?' for _ in ids)})
          AND q.inspection_completed_at>=? AND q.inspection_completed_at<=?
          AND q.status='unqualified' AND q.attribution_status='reliable'{site_clause}
        GROUP BY q.responsible_user_id''', (*ids, start_at, end_at, *(sites or []))).fetchall()
    return {row['responsible_user_id']: row['deduction'] for row in rows}


def current_photo_quality(
        db, assignee_id, start_at, end_at, allowed_site_ids=None, limit=None,
        include_unknown=False):
    if not (table_exists(db, 'operation_attachments') and table_exists(db, 'insp_plan_items')
            and table_exists(db, 'insp_plans') and table_exists(db, 'photo_quality_evaluations')):
        return []
    params = [int(assignee_id)]
    site_clause = ''
    if allowed_site_ids is not None:
        ids = sorted({int(site_id) for site_id in allowed_site_ids})
        if not ids:
            return []
        site_clause = f" AND i.site_id IN ({','.join('?' for _ in ids)})"
        params.extend(ids)
    limit_clause = ''
    item_link = 'COALESCE(a.item_id,a.source_id)' if column_exists(db, 'operation_attachments', 'item_id') else 'a.source_id'
    check_time = 'i.check_time' if column_exists(db, 'insp_plan_items', 'check_time') else "''"
    voided_at = 'a.voided_at' if column_exists(db, 'operation_attachments', 'voided_at') else "''"
    rows = db.execute(f'''SELECT a.id AS attachment_id,a.review_status,a.reject_reason,
            a.reviewed_at,a.evidence_qualification,a.evidence_reason,a.stored_path,a.description,
            a.quality_score_month,a.quality_responsible_user_id,a.quality_decision_id,
            i.id AS item_id,i.item_name,i.plan_id,i.site_id,i.completed_at,i.result,
            {check_time} AS check_time, {voided_at} AS voided_at,
            CASE WHEN q.id IS NOT NULL THEN q.responsible_user_id
                 ELSE p.assignee_id END AS responsible_user_id
        FROM operation_attachments a
        JOIN insp_plan_items i ON i.id={item_link}
        JOIN insp_plans p ON p.id=i.plan_id
        LEFT JOIN photo_quality_evaluations q ON q.id=(SELECT MAX(id)
            FROM photo_quality_evaluations WHERE attachment_id=a.id)
        WHERE a.source_type='inspection' AND COALESCE(a.is_deleted,0)=0
          AND (CASE WHEN q.id IS NOT NULL THEN q.responsible_user_id
                    ELSE p.assignee_id END)=? {site_clause}
        ORDER BY i.completed_at DESC,a.id DESC{limit_clause}''', params).fetchall()
    result = []
    for row in rows:
        item = _row_dict(row)
        history = []
        if table_exists(db, 'photo_quality_evaluations'):
            history = [_row_dict(evaluation) for evaluation in db.execute('''SELECT
                    id,previous_status,status,reason,reviewer_id,reviewed_at,
                    responsible_user_id,inspection_completed_at,score_month,
                    attribution_status,correction_type,related_attachment_id,idempotency_key
                FROM photo_quality_evaluations WHERE attachment_id=?
                ORDER BY id DESC''', (item['attachment_id'],)).fetchall()]
        latest = history[0] if history else None
        # Recorded attribution remains authoritative when execution facts change.
        if latest:
            item['completed_at'] = latest['inspection_completed_at'] or ''
            item['score_month'] = latest['score_month'] or ''
            item['responsible_user_id'] = latest['responsible_user_id']
            item['attribution_status'] = latest['attribution_status']
        else:
            item['completed_at'] = actual_inspection_time(item)
            item['score_month'] = item['completed_at'][:7]
            item['attribution_status'] = 'unknown'
        if item['completed_at']:
            if not start_at <= item['completed_at'] <= end_at:
                continue
        elif not include_unknown:
            continue
        item['quality_status'] = (latest['status'] if latest else
            'unqualified' if item['review_status'] == 'rejected'
            else 'qualified' if item['review_status'] == 'approved'
            else 'pending')
        if item.get('voided_at'):
            item['review_status'] = 'voided'
        item['deduction'] = (1 if latest and item['quality_status'] == 'unqualified'
                             and item['attribution_status'] == 'reliable' else 0)
        item['history'] = history
        result.append(item)
    result.sort(key=lambda item: (item['completed_at'], item['attachment_id']), reverse=True)
    return result[:int(limit)] if limit else result

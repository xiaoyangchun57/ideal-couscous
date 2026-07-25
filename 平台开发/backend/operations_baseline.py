import json
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request


def _review_durations(db, start, end):
    """按 review_id 配对 opened/submitted，返回 (中位耗时秒, 样本数) 或无样本 (None, 0)。"""
    rows = db.execute(
        "SELECT event_name, occurred_at, context_json FROM analytics_events "
        "WHERE event_name IN ('review.opened','review.submitted') AND occurred_at>=? AND occurred_at<?",
        (start, end),
    ).fetchall()
    opened, submitted = {}, {}
    for r in rows:
        try:
            ctx = json.loads(r['context_json'] or '{}')
        except Exception:
            ctx = {}
        rid = ctx.get('review_id')
        if not rid:
            continue
        bucket = opened if r['event_name'] == 'review.opened' else submitted
        bucket[rid] = r['occurred_at']
    durations = []
    for rid, opened_at in opened.items():
        sub = submitted.get(rid)
        if not sub:
            continue
        try:
            d0 = datetime.strptime(opened_at, '%Y-%m-%d %H:%M:%S')
            d1 = datetime.strptime(sub, '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        secs = (d1 - d0).total_seconds()
        if secs >= 0:
            durations.append(secs)
    if not durations:
        return None, 0
    durations.sort()
    n = len(durations)
    mid = durations[n // 2] if n % 2 else (durations[n // 2 - 1] + durations[n // 2]) / 2
    return mid, n


def _has_table(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _metric(numerator, denominator, source, state='ready'):
    if denominator is None or denominator == 0:
        return {'value': None, 'numerator': numerator or 0, 'denominator': denominator or 0, 'source': source, 'state': 'collecting'}
    return {'value': round(numerator / denominator * 100, 1), 'numerator': numerator, 'denominator': denominator, 'source': source, 'state': state}


def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _event_counts(db, start, end):
    if not _has_table(db, 'analytics_events'):
        return {}
    rows = db.execute(
        '''
        SELECT event_name, COUNT(*)
        FROM analytics_events
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY event_name
        ''',
        (start, end),
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def build_baseline(db, start, end):
    north_star = {}
    if _has_table(db, 'insp_plan_items'):
        total = _scalar(db, "SELECT COUNT(*) FROM insp_plan_items WHERE COALESCE(execution_status,'active')='active'")
        done = _scalar(db, "SELECT COUNT(*) FROM insp_plan_items WHERE result IS NOT NULL AND COALESCE(execution_status,'active')='active'")
        north_star['inspection_coverage'] = _metric(done, total, 'insp_plan_items')
    else:
        north_star['inspection_coverage'] = _metric(0, 0, 'insp_plan_items')

    for key, table, closed_states, time_column in (
        ('work_order_online_closure_rate', 'work_orders', ('closed', 'resolved'), 'created_at'),
        ('alert_online_handling_rate', 'alerts', ('resolved', 'closed'), 'created_at'),
        ('review_online_completion_rate', 'data_reviews', ('approved', 'rejected', 'reviewed', 'archived'), 'created_at'),
    ):
        if not _has_table(db, table):
            north_star[key] = _metric(0, 0, table)
            continue
        total = _scalar(db, f"SELECT COUNT(*) FROM {table} WHERE {time_column}>=? AND {time_column}<?", (start, end))
        placeholders = ','.join('?' for _ in closed_states)
        done = _scalar(db, f"SELECT COUNT(*) FROM {table} WHERE {time_column}>=? AND {time_column}<? AND status IN ({placeholders})", (start, end, *closed_states))
        north_star[key] = _metric(done, total, table)

    events = _event_counts(db, start, end)
    offline_total = events.get('inspection.item.queued', 0)
    offline_synced = events.get('inspection.item.synced', 0)
    frontline = {
        'offline_closure_success_rate': _metric(offline_synced, offline_total, 'analytics_events'),
        'station_open_samples': {'value': events.get('inspection.station_opened', 0), 'state': 'ready' if events else 'collecting', 'source': 'analytics_events'},
        'checkin_samples': {'value': events.get('inspection.checkin.queued', 0), 'state': 'ready' if events else 'collecting', 'source': 'analytics_events'},
    }

    # ---- 采集段：事件齐了才出值，否则继续采集 ----
    dur, dur_n = _review_durations(db, start, end)
    if dur is None:
        review_duration = {'state': 'collecting', 'reason': '审核打开/提交事件样本不足'}
    else:
        review_duration = {'value': round(dur, 1), 'unit': 's', 'samples': dur_n, 'state': 'ready', 'source': 'analytics_events'}

    rep_opened = events.get('report.opened', 0)
    rep_exported = events.get('report.exported', 0)
    if rep_opened == 0:
        report_self_service_rate = {'state': 'collecting', 'reason': '报表打开/导出事件样本不足'}
    else:
        report_self_service_rate = {
            'value': round(rep_exported / rep_opened * 100, 1),
            'numerator': rep_exported, 'denominator': rep_opened, 'state': 'ready', 'source': 'analytics_events',
        }

    aq = events.get('action_queue.entered', 0)
    action_queue_decision_rate = {
        'value': aq, 'state': 'ready' if aq > 0 else 'collecting', 'source': 'analytics_events',
        'note': '行动队列驱动决策次数（样本）',
    }

    return {
        'north_star': north_star,
        'frontline': frontline,
        'collection': {
            'review_duration': review_duration,
            'report_self_service_rate': report_self_service_rate,
            'action_queue_decision_rate': action_queue_decision_rate,
        },
    }


def _period_range(period):
    days = {'7d': 7, '30d': 30, 'month': 30, 'quarter': 90, 'year': 365}.get(period, 30)
    end = datetime.now()
    start = end - timedelta(days=days)
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


def create_operations_baseline_blueprint(get_db, login_required):
    blueprint = Blueprint('operations_baseline', __name__, url_prefix='/api/operations')

    @blueprint.route('/baseline')
    @login_required
    def baseline():
        if g.current_user.get('role') not in ('admin', 'manager'):
            return jsonify({'error': '仅管理员和主管可查看运营基线'}), 403
        period = request.args.get('period', '30d')
        start, end = _period_range(period)
        with get_db() as db:
            data = build_baseline(db, start, end)
        return jsonify({'period': period, 'start': start, 'end': end, **data})

    return blueprint

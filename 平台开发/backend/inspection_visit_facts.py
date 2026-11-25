"""Append-only inspection arrival/departure facts and optimistic correction guards."""
import hashlib
import json


def exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS inspection_visit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL, request_key TEXT UNIQUE, parent_event_id INTEGER,
        payload TEXT NOT NULL DEFAULT '{}')''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_inspection_visit_target
        ON inspection_visit_events(plan_id,site_id,id)''')


def events(db, plan_id, site_id):
    if not exists(db, 'inspection_visit_events'):
        return []
    return [dict(row) for row in db.execute('''SELECT * FROM inspection_visit_events
        WHERE plan_id=? AND site_id=? ORDER BY id DESC LIMIT 20''', (plan_id, site_id))]


def facts_guard(db, plan_id, site_id):
    plan = dict(db.execute('SELECT * FROM insp_plans WHERE id=?', (plan_id,)).fetchone())
    facts = {'plan': plan, 'items': [dict(row) for row in db.execute(
        'SELECT * FROM insp_plan_items WHERE plan_id=? ORDER BY id', (plan_id,))]}
    if plan.get('plan_schedule_id') is not None:
        row = db.execute('SELECT * FROM plan_schedules WHERE id=?',
                         (plan['plan_schedule_id'],)).fetchone()
        facts['schedule'] = {key: dict(row).get(key) for key in ('status', 'version')} if row else None
    if exists(db, 'inspection_checkins'):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(inspection_checkins)')}
        scoped = ' AND plan_id=?' if 'plan_id' in columns else ''
        params = (site_id, plan_id) if scoped else (site_id,)
        facts['arrivals'] = [dict(row) for row in db.execute(
            'SELECT * FROM inspection_checkins WHERE site_id=?' + scoped + ' ORDER BY id', params)]
    if exists(db, 'operation_attachments'):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(operation_attachments)')}
        links, params = [], []
        if 'item_id' in columns:
            links.append('item_id IN (SELECT id FROM insp_plan_items WHERE plan_id=?)')
            params.append(plan_id)
        if 'plan_id' in columns:
            links.append('plan_id=?')
            params.append(plan_id)
        if {'source_type', 'source_id'} <= columns:
            links.append("(source_type='inspection' AND source_id IN "
                         "(SELECT id FROM insp_plan_items WHERE plan_id=?))")
            params.append(plan_id)
        if 'extra_json' in columns:
            links.append("""CAST(json_extract(CASE WHEN json_valid(extra_json)
                THEN extra_json ELSE '{}' END, '$.plan_id') AS INTEGER)=?""")
            params.append(plan_id)
        if links:
            facts['attachments'] = [dict(row) for row in db.execute(
                'SELECT * FROM operation_attachments WHERE ' + ' OR '.join(links) + ' ORDER BY id', params)]
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def state(db, plan_id, site_id):
    history = events(db, plan_id, site_id)
    guard = facts_guard(db, plan_id, site_id)
    latest = history[0] if history else None
    token = hashlib.sha256(f'{guard}:{latest["id"] if latest else 0}'.encode()).hexdigest()
    payload = json.loads(latest['payload']) if latest else {}
    departures = db.execute("""SELECT COUNT(*) AS total,
        SUM(CASE WHEN check_out_time IS NOT NULL THEN 1 ELSE 0 END) AS left_count,
        MAX(check_out_time) AS left_at FROM insp_plan_items WHERE plan_id=? AND site_id=?
        AND COALESCE(execution_status,'active')='active'""", (plan_id, site_id)).fetchone()
    checked_out = bool(departures['total'] and departures['total'] == departures['left_count'])
    return {'visit_version': token, 'visit_event_id': latest['id'] if latest else None,
        'checked_out': checked_out, 'check_out_time': departures['left_at'],
        'can_correct_checkout': bool(latest and latest['event_type'] == 'departure'
            and payload.get('guard') == guard),
        'visit_events': [{**row, 'payload': json.loads(row['payload'])} for row in reversed(history)]}


def record(db, plan_id, site_id, actor_id, kind, now, payload, request_key=None, parent=None):
    ensure_schema(db)
    cursor = db.execute('''INSERT INTO inspection_visit_events
        (plan_id,site_id,actor_id,event_type,occurred_at,request_key,parent_event_id,payload)
        VALUES (?,?,?,?,?,?,?,?)''', (plan_id, site_id, actor_id, kind, now,
        request_key, parent, json.dumps(payload, ensure_ascii=False)))
    return cursor.lastrowid


def replay(db, request_key):
    return exists(db, 'inspection_visit_events') and db.execute(
        'SELECT id FROM inspection_visit_events WHERE request_key=?', (request_key,)).fetchone()


def legacy_first_visit(db, plan_id, site_id, actor_id, matches_legacy):
    """Identify one original check-in; inspect all history before any replay."""
    if not exists(db, 'inspection_checkins'):
        return None, '没有可核实的本站到站记录'
    columns = {row['name'] for row in db.execute('PRAGMA table_info(inspection_checkins)')}
    plan = db.execute('SELECT generate_date FROM insp_plans WHERE id=?', (plan_id,)).fetchone()
    # Unscoped rows in this task's lifetime cannot silently be assigned elsewhere.
    unscoped = "(user_id=? AND (datetime(check_time)>=date(?) OR datetime(check_time) IS NULL))"
    params = [site_id, actor_id, plan['generate_date']]
    if 'plan_id' in columns:
        where = f'(plan_id=? OR ((plan_id IS NULL OR plan_id=0) AND {unscoped}))'
        params.insert(1, plan_id)
    else:
        where = unscoped
    arrivals = [dict(row) for row in db.execute(
        'SELECT * FROM inspection_checkins WHERE site_id=? AND ' + where + ' ORDER BY id', params)]
    if not arrivals:
        return None, '没有可归属到本人原任务的本站到站记录'
    if len(arrivals) != 1:
        return None, '存在多次或无法唯一归属的到站记录，旧版不能确认本次离站'
    arrival = arrivals[0]
    if arrival['user_id'] != actor_id or (not arrival.get('plan_id') and not matches_legacy(arrival)):
        return None, '到站记录不能唯一归属到本人原任务'
    key = f'legacy-departure:{actor_id}:{plan_id}:{site_id}:checkin:{arrival["id"]}'
    history = [dict(row) for row in db.execute('''SELECT * FROM inspection_visit_events
        WHERE plan_id=? AND site_id=? ORDER BY id''', (plan_id, site_id))] if exists(db, 'inspection_visit_events') else []
    # Do not use events(), whose display history is capped at twenty rows.
    if any(row['event_type'] not in ('arrival', 'departure') for row in history):
        return None, '已有纠正、返站或其他到离站历史，旧版不能确认本次离站'
    arrival_events = [row for row in history if row['event_type'] == 'arrival']
    departures = [row for row in history if row['event_type'] == 'departure']
    if (len(arrival_events) > 1 or len(departures) > 1
            or any(row['actor_id'] != actor_id for row in history)
            or any(row['occurred_at'] != arrival['check_time'] for row in arrival_events)
            or any(row['request_key'] != key for row in departures)
            or (departures and arrival_events and departures[0]['id'] < arrival_events[0]['id'])):
        return None, '到离站历史不能唯一对应首次到站，旧版不能确认本次离站'
    return {'arrival': arrival, 'request_key': key,
            'departure': departures[0] if departures else None}, None

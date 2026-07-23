import json

from flask import Blueprint, g, jsonify, request


FIELD_EVENT_NAMES = {
    'inspection.station_opened',
    'inspection.checkin.queued',
    'inspection.checkin.synced',
    'inspection.photo.captured',
    'inspection.item.queued',
    'inspection.item.synced',
    'inspection.sync.failed',
}

CONTEXT_KEYS = {'site_id', 'item_id', 'plan_id', 'entry', 'offline', 'operation_id', 'sync_reason', 'error_code'}


def create_telemetry_blueprint(get_db, login_required):
    blueprint = Blueprint('telemetry', __name__, url_prefix='/api/telemetry')

    @blueprint.route('/events', methods=['POST'])
    @login_required
    def create_event():
        data = request.get_json(silent=True) or {}
        event_id = str(data.get('event_id') or '').strip()
        event_name = str(data.get('event_name') or '').strip()
        occurred_at = str(data.get('occurred_at') or '').strip()
        context = data.get('context') or {}
        app_version = str(data.get('app_version') or '').strip()[:64]

        if not event_id or len(event_id) > 128 or event_name not in FIELD_EVENT_NAMES or not occurred_at:
            return jsonify({'error': 'invalid telemetry event'}), 400
        if not isinstance(context, dict):
            return jsonify({'error': 'invalid telemetry context'}), 400
        safe_context = {key: context[key] for key in CONTEXT_KEYS if key in context}
        context_json = json.dumps(safe_context, ensure_ascii=False, separators=(',', ':'))
        if len(context_json.encode('utf-8')) > 2048:
            return jsonify({'error': 'telemetry context too large'}), 400

        with get_db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS analytics_events (
                event_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                event_name TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                received_at TEXT DEFAULT (datetime('now','localtime')),
                app_version TEXT,
                context_json TEXT NOT NULL
            )''')
            existing = db.execute('SELECT event_id FROM analytics_events WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                return jsonify({'success': True, 'duplicate': True})
            db.execute('''INSERT INTO analytics_events
                (event_id, user_id, event_name, occurred_at, app_version, context_json)
                VALUES (?,?,?,?,?,?)''',
                (event_id, g.current_user['id'], event_name, occurred_at, app_version, context_json))
            db.commit()
        return jsonify({'success': True, 'duplicate': False}), 201

    return blueprint

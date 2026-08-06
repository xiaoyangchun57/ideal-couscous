"""Import confirmed operational master data extracted from the collaboration workbook.

The JSON input must be produced by dev_scripts/extract_operational_workbook.mjs.
The command is dry-run by default; pass --apply to create a timestamped backup and
commit the migration.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import date, datetime


ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(ROOT, 'data', 'water.db')
DEFAULT_INPUT = os.path.join(ROOT, 'data', 'operational_workbook_import.json')

USERNAMES = {
    '肖永平': 'admin',
    '万松': 'wansong',
    '李程亮': 'lichengliang',
    '周雄雄': 'zhouxiongxiong',
    '熊自伟': 'xiongziwei',
    '姜伟': 'jiangwei',
    '张玉雪': 'zhangyuxue',
}
ROLE_MAP = {'管理员': 'admin', '运维员': 'operator', '审核员': 'reviewer'}


def table_exists(db, table):
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def table_columns(db, table):
    if not table_exists(db, table):
        return set()
    return {row['name'] for row in db.execute(f'PRAGMA table_info("{table}")')}


def trailing_sequence(code):
    match = re.search(r'(\d+)$', str(code or ''))
    return int(match.group(1)) if match else None


def device_type(name):
    mappings = [
        ('高锰酸盐', 'codmn_analyzer'), ('氨氮', 'ammonia_analyzer'),
        ('总磷', 'tp_analyzer'), ('总氮', 'tn_analyzer'),
        ('温度', 'thermometer'), ('溶解氧', 'do_sensor'), ('PH', 'ph_meter'),
        ('pH', 'ph_meter'), ('浊度', 'turbidity_meter'), ('电导率', 'conductivity_meter'),
        ('泵', 'submersible_pump'), ('浮筒', 'sample_float'), ('灭火器', 'fire_extinguisher'),
        ('照明', 'lighting'),
    ]
    for keyword, value in mappings:
        if keyword in name:
            return value
    return 'station_facility'


def device_status(condition):
    if condition in ('是', '正常', '好'):
        return 'online'
    if condition in ('否', '无'):
        return 'offline'
    if condition:
        return 'maintenance'
    return 'unknown'


def parse_date(value):
    text = str(value or '').strip()
    if not text:
        return None
    for fmt in ('%Y.%m.%d', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def reference_counts(db, field, value, excluded=()):
    result = {}
    tables = [row['name'] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in tables:
        if table in excluded or field not in table_columns(db, table):
            continue
        count = db.execute(
            f'SELECT COUNT(*) AS count FROM "{table}" WHERE "{field}"=?', (value,)
        ).fetchone()['count']
        if count:
            result[table] = count
    return result


def validate_payload(payload):
    expected = {
        'people': 7,
        'sites': 36,
        'pilot_sites': 4,
        'vehicles': 4,
        'inspection_items': 14,
    }
    for key, count in expected.items():
        if len(payload.get(key) or []) != count:
            raise RuntimeError(f'{key} count is {len(payload.get(key) or [])}, expected {count}')
    if payload.get('quality', {}).get('owners_not_in_people'):
        raise RuntimeError('Station owners are missing from personnel master data')
    names = {site['name'] for site in payload['sites']}
    if any(site['name'] not in names for site in payload['pilot_sites']):
        raise RuntimeError('Pilot station is not present in station master data')


def ensure_schema(db):
    additions = {
        'sites': {
            'is_pilot': 'INTEGER DEFAULT 0',
            'operation_frequency': "TEXT DEFAULT ''",
        },
        'device_shadows': {
            'management_scope': "TEXT DEFAULT 'managed'",
            'monitoring_enabled': 'INTEGER DEFAULT 0',
        },
    }
    for table, fields in additions.items():
        existing = table_columns(db, table)
        for name, field_type in fields.items():
            if name not in existing:
                db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {field_type}')


def sync_sites(db, payload):
    pilots = {row['name']: row['frequency'] for row in payload['pilot_sites']}
    phones = {row['name']: row['phone'] for row in payload['people']}
    existing = db.execute('SELECT id, code, name FROM sites ORDER BY id').fetchall()
    by_sequence = {trailing_sequence(row['code']): row for row in existing if trailing_sequence(row['code'])}
    site_ids = {}
    for site in payload['sites']:
        sequence = int(site['sequence'])
        row = by_sequence.get(sequence)
        values = (
            site['name'], f'{sequence:03d}', 'water_quality', site['owner'],
            phones.get(site['owner'], ''), 1 if site['name'] in pilots else 0,
            pilots.get(site['name'], ''),
        )
        if row:
            db.execute('''UPDATE sites SET name=?, code=?, type=?, manager=?, phone=?,
                          is_pilot=?, operation_frequency=? WHERE id=?''', values + (row['id'],))
            site_ids[site['name']] = row['id']
        else:
            cursor = db.execute('''INSERT INTO sites
                (name, code, type, manager, phone, is_pilot, operation_frequency, status)
                VALUES (?,?,?,?,?,?,?,'normal')''', values)
            site_ids[site['name']] = cursor.lastrowid

    target_ids = set(site_ids.values())
    for row in db.execute('SELECT id, name FROM sites ORDER BY id').fetchall():
        if row['id'] in target_ids:
            continue
        db.execute('DELETE FROM user_sites WHERE site_id=?', (row['id'],))
        refs = reference_counts(db, 'site_id', row['id'], excluded=('sites', 'user_sites'))
        removable = {'maintenance_plans', 'inspection_schedules'}
        blocking_refs = {table: count for table, count in refs.items() if table not in removable}
        if blocking_refs:
            raise RuntimeError(f"Cannot remove station {row['name']}: references remain {blocking_refs}")
        if refs.get('inspection_schedules'):
            schedule_ids = [item['id'] for item in db.execute(
                'SELECT id FROM inspection_schedules WHERE site_id=?', (row['id'],)
            )]
            if schedule_ids:
                placeholders = ','.join('?' * len(schedule_ids))
                for table_name in [item['name'] for item in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )]:
                    if table_name == 'inspection_schedules' or 'schedule_id' not in table_columns(db, table_name):
                        continue
                    db.execute(
                        f'DELETE FROM "{table_name}" WHERE schedule_id IN ({placeholders})',
                        tuple(schedule_ids),
                    )
            db.execute('DELETE FROM inspection_schedules WHERE site_id=?', (row['id'],))
        if refs.get('maintenance_plans'):
            db.execute('DELETE FROM maintenance_plans WHERE site_id=?', (row['id'],))
        db.execute('DELETE FROM sites WHERE id=?', (row['id'],))
    return site_ids


def sync_users(db, payload, site_ids):
    desired_ids = set()
    db.execute('DELETE FROM user_sites')
    for person in payload['people']:
        username = USERNAMES[person['name']]
        row = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        roles = [ROLE_MAP[role] for role in person['roles'] if role in ROLE_MAP]
        primary = 'admin' if 'admin' in roles else ('reviewer' if 'reviewer' in roles else 'operator')
        if row:
            user_id = row['id']
            db.execute('''UPDATE users SET real_name=?, display_name=?, login_name=?, phone=?,
                          role=?, status='active' WHERE id=?''',
                       (person['name'], person['name'], person['name'], person['phone'], primary, user_id))
        else:
            password = 'admin123' if username == 'admin' else 'yw123456'
            cursor = db.execute('''INSERT INTO users
                (username, password_hash, role, display_name, real_name, phone, status, login_name)
                VALUES (?,?,?,?,?,?,'active',?)''',
                (username, hashlib.sha256(password.encode()).hexdigest(), primary,
                 person['name'], person['name'], person['phone'], person['name']))
            user_id = cursor.lastrowid
        desired_ids.add(user_id)
        if table_exists(db, 'user_roles'):
            db.execute('DELETE FROM user_roles WHERE user_id=?', (user_id,))
            for role in sorted(set(roles)):
                db.execute('INSERT INTO user_roles (user_id, role) VALUES (?,?)', (user_id, role))

    if desired_ids:
        placeholders = ','.join('?' * len(desired_ids))
        db.execute(f"UPDATE users SET status='inactive' WHERE id NOT IN ({placeholders})", tuple(desired_ids))

    users_by_name = {
        row['real_name']: row['id'] for row in db.execute(
            "SELECT id, real_name FROM users WHERE status='active'"
        )
    }
    assignments = {name: [] for name in users_by_name}
    for site in payload['sites']:
        user_id = users_by_name[site['owner']]
        site_id = site_ids[site['name']]
        db.execute('INSERT INTO user_sites (user_id, site_id) VALUES (?,?)', (user_id, site_id))
        assignments[site['owner']].append(site_id)
    for name, user_id in users_by_name.items():
        db.execute('UPDATE users SET site_ids=? WHERE id=?',
                   (json.dumps(assignments.get(name, []), ensure_ascii=False), user_id))


def sync_devices_and_reagents(db, payload, site_ids):
    db.execute("DELETE FROM device_shadows WHERE device_code LIKE 'WB-%'")
    device_count = 0
    inventory_rows = {}
    for block in payload['equipment_blocks']:
        site_id = site_ids.get(block['site'])
        if not site_id:
            continue
        site_sequence = next(site['sequence'] for site in payload['sites'] if site['name'] == block['site'])
        for index, device in enumerate(block['devices'], 1):
            manufacturer = device.get('manufacturer') or ''
            if re.fullmatch(r'\d+(?:\.\d+)?米', manufacturer):
                manufacturer = ''
            # 厂商仅描述设备来源，不再影响设备的运维责任或可执行操作。
            scope = 'managed'
            db.execute('''INSERT INTO device_shadows
                (site_id, device_code, device_name, device_type, device_model, manufacturer,
                 install_date, status, management_scope, monitoring_enabled)
                VALUES (?,?,?,?,?,?, '', ?,?,0)''',
                (site_id, f'WB-{int(site_sequence):03d}-{index:02d}', device['name'],
                 device_type(device['name']), device.get('model') or '', manufacturer,
                 device_status(device.get('condition') or ''), scope))
            device_count += 1
            reagent_name = (device.get('reagent_name') or '').strip()
            if reagent_name:
                inventory_rows[(site_id, reagent_name)] = (device, manufacturer)

    if table_exists(db, 'reagent_inventory') and table_exists(db, 'reagents'):
        placeholders = ','.join('?' * len(site_ids))
        db.execute(f'DELETE FROM reagent_inventory WHERE site_id IN ({placeholders})', tuple(site_ids.values()))
        for (site_id, reagent_name), (device, manufacturer) in inventory_rows.items():
            reagent = db.execute('SELECT id FROM reagents WHERE name=?', (reagent_name,)).fetchone()
            unit = device.get('unit') or '套'
            if reagent:
                reagent_id = reagent['id']
                db.execute('UPDATE reagents SET manufacturer=?, unit=? WHERE id=?',
                           (manufacturer if manufacturer != '外接设备' else '', unit, reagent_id))
            else:
                reagent_id = db.execute('''INSERT INTO reagents
                    (name, manufacturer, spec, unit, shelf_life_days) VALUES (?,?, '', ?,365)''',
                    (reagent_name, manufacturer if manufacturer != '外接设备' else '', unit)).lastrowid
            replaced = parse_date(device.get('reagent_batch'))
            remaining = device.get('remaining_days')
            expected = None
            if isinstance(remaining, (int, float)):
                expected = int(remaining)
                if replaced:
                    expected += max((date.today() - replaced).days, 0)
            db.execute('''INSERT INTO reagent_inventory
                (site_id, reagent_id, current_qty, last_replaced_at, expected_duration_days, qc_status)
                VALUES (?,?,?,?,?,'passed')''',
                (site_id, reagent_id, 1, replaced.isoformat() if replaced else None, expected))
    return device_count, len(inventory_rows)


def clear_unconfigured_monitoring_artifacts(db, site_ids):
    """Remove pending auto alerts created before imported assets had a monitoring gate."""
    placeholders = ','.join('?' * len(site_ids))
    params = tuple(site_ids.values())
    alert_rows = db.execute(f'''SELECT id FROM alerts
        WHERE site_id IN ({placeholders}) AND metric IN ('device_status','data_gap')
          AND status='pending'
          AND NOT EXISTS (SELECT 1 FROM device_shadows d
                          WHERE d.site_id=alerts.site_id AND d.monitoring_enabled=1)''', params).fetchall()
    alert_ids = [row['id'] for row in alert_rows]
    if not alert_ids:
        return {'alerts': 0, 'work_orders': 0}

    alert_ph = ','.join('?' * len(alert_ids))
    orders = db.execute(f'''SELECT id, order_no FROM work_orders
        WHERE related_alert_id IN ({alert_ph}) AND source='auto'
          AND metric IN ('device_status','data_gap')''', tuple(alert_ids)).fetchall()
    order_ids = [row['id'] for row in orders]
    if order_ids:
        order_ph = ','.join('?' * len(order_ids))
        for table in [row['name'] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]:
            if table == 'work_orders':
                continue
            columns = table_columns(db, table)
            for field in ('work_order_id', 'order_id'):
                if field in columns:
                    db.execute(f'DELETE FROM "{table}" WHERE "{field}" IN ({order_ph})', tuple(order_ids))
        if table_exists(db, 'operation_attachments'):
            db.execute(f'''DELETE FROM operation_attachments
                WHERE source_type IN ('workorder','work_order','order') AND source_id IN ({order_ph})''',
                       tuple(order_ids))
        if table_exists(db, 'timeline_events'):
            db.execute(f'''DELETE FROM timeline_events
                WHERE source_type='order' AND source_id IN ({order_ph})''', tuple(order_ids))
            for order in orders:
                db.execute("DELETE FROM timeline_events WHERE source_type='order' AND remark LIKE ?",
                           (f"%{order['order_no']}%",))
        db.execute(f'DELETE FROM work_orders WHERE id IN ({order_ph})', tuple(order_ids))

    if table_exists(db, 'notifications'):
        db.execute(f'''DELETE FROM notifications
            WHERE source_type='alert' AND source_id IN ({alert_ph})''', tuple(alert_ids))
    if table_exists(db, 'timeline_events'):
        db.execute(f'''DELETE FROM timeline_events
            WHERE source_type='alert' AND source_id IN ({alert_ph})''', tuple(alert_ids))
    db.execute(f'DELETE FROM alerts WHERE id IN ({alert_ph})', tuple(alert_ids))
    return {'alerts': len(alert_ids), 'work_orders': len(order_ids)}


def sync_inspection_templates(db, payload):
    grouped = {'weekly': [], 'monthly': []}
    for item in payload['inspection_items']:
        grouped[item['frequency']].append(item)
    names = {'weekly': '水质站点每周巡检模板', 'monthly': '水质站点每月巡检模板'}
    for frequency, items in grouped.items():
        template = db.execute('''SELECT id FROM inspection_templates
            WHERE category='水质监测' AND frequency=? ORDER BY id LIMIT 1''', (frequency,)).fetchone()
        if template:
            template_id = template['id']
            db.execute('UPDATE inspection_templates SET template_name=? WHERE id=?',
                       (names[frequency], template_id))
        else:
            template_id = db.execute('''INSERT INTO inspection_templates
                (template_name, category, frequency, description, sort_order, status)
                VALUES (?,'水质监测',?,?,?,'active')''',
                (names[frequency], frequency, '工作簿确认的水质站点巡检模板',
                 1 if frequency == 'weekly' else 2)).lastrowid
        existing = {
            row['sort_order']: row for row in db.execute(
                'SELECT id, sort_order, inspection_standard FROM inspection_template_items WHERE template_id=?',
                (template_id,)
            )
        }
        for sort_order, item in enumerate(items, 1):
            category = '质控校准' if '仪器质控' in item['name'] else (
                '台账登记' if '登记本' in item['name'] else '站房环境'
            )
            current = existing.get(sort_order)
            standard = (current['inspection_standard'] if current else '') or ''
            standard = standard.replace('校准结果', '质控结果').replace('仪器校准', '仪器质控')
            values = (item['name'], category, 1 if item['photo_count'] else 0,
                      1 if item['need_review'] else 0, item['photo_count'], standard)
            if current:
                db.execute('''UPDATE inspection_template_items SET item_name=?, category=?,
                    photo_required=?, need_review=?, max_photos=?, inspection_standard=? WHERE id=?''',
                    values + (current['id'],))
            else:
                db.execute('''INSERT INTO inspection_template_items
                    (template_id, item_name, category, photo_required, need_review, max_photos,
                     inspection_standard, sort_order) VALUES (?,?,?,?,?,?,?,?)''',
                    (template_id,) + values + (sort_order,))
        stale_items = [row['id'] for order, row in existing.items() if int(order or 0) > len(items)]
        if stale_items:
            placeholders = ','.join('?' * len(stale_items))
            if table_exists(db, 'inspection_schedules') and 'template_item_id' in table_columns(db, 'inspection_schedules'):
                db.execute(
                    f'DELETE FROM inspection_schedules WHERE template_item_id IN ({placeholders})',
                    tuple(stale_items),
                )
            db.execute(
                f'DELETE FROM inspection_template_items WHERE id IN ({placeholders})',
                tuple(stale_items),
            )


def clear_orphaned_rows(db):
    """Remove rows whose declared parent no longer exists; these cannot form valid history."""
    deleted = {}
    rules = [
        ('parts_request_items', 'request_id', 'parts_requests', 'id'),
        ('vehicle_use_records', 'application_id', 'vehicle_applications', 'id'),
        ('plan_departure_confirmations', 'schedule_id', 'plan_schedules', 'id'),
        ('plan_departure_confirmations', 'user_id', 'users', 'id'),
        ('inventory_logs', 'part_id', 'spare_parts_inventory', 'id'),
    ]
    for table, child_field, parent, parent_field in rules:
        if not table_exists(db, table) or not table_exists(db, parent):
            continue
        cursor = db.execute(f'''DELETE FROM "{table}"
            WHERE "{child_field}" IS NOT NULL AND "{child_field}" NOT IN
                  (SELECT "{parent_field}" FROM "{parent}")''')
        if cursor.rowcount:
            deleted[table] = cursor.rowcount

    if table_exists(db, 'insp_plans') and table_exists(db, 'users'):
        orphan_plans = [row['id'] for row in db.execute('''SELECT id FROM insp_plans
            WHERE assignee_id IS NOT NULL AND assignee_id NOT IN (SELECT id FROM users)''')]
        if orphan_plans:
            placeholders = ','.join('?' * len(orphan_plans))
            for table in ('insp_plan_items', 'inspection_v2_items'):
                if table_exists(db, table) and 'plan_id' in table_columns(db, table):
                    db.execute(f'DELETE FROM "{table}" WHERE plan_id IN ({placeholders})', tuple(orphan_plans))
            db.execute(f'DELETE FROM insp_plans WHERE id IN ({placeholders})', tuple(orphan_plans))
            deleted['insp_plans'] = len(orphan_plans)

    return deleted


def clear_spare_parts_if_unreferenced(db):
    if not table_exists(db, 'spare_parts_inventory'):
        return 0
    rows = db.execute('SELECT id, part_name FROM spare_parts_inventory').fetchall()
    for row in rows:
        refs = reference_counts(db, 'part_id', row['id'], excluded=('spare_parts_inventory', 'inventory_logs'))
        if refs:
            raise RuntimeError(f"Cannot clear spare part {row['part_name']}: references remain {refs}")
        if table_exists(db, 'inventory_logs'):
            db.execute('DELETE FROM inventory_logs WHERE part_id=?', (row['id'],))
    db.execute('DELETE FROM spare_parts_inventory')
    return len(rows)


def sync_vehicles(db, payload):
    desired_plates = {row['plate_no'] for row in payload['vehicles']}
    for vehicle in payload['vehicles']:
        row = db.execute('SELECT id FROM vehicles WHERE plate_no=?', (vehicle['plate_no'],)).fetchone()
        values = (
            vehicle['vehicle_name'], vehicle['vehicle_name'], vehicle['seats'] or 5,
            'electric' if vehicle['energy_type'] == '新能源' else 'gasoline',
            vehicle['current_mileage'], vehicle['next_maintenance_mileage'],
            vehicle['annual_inspection_expiry'] or None,
        )
        if row:
            db.execute('''UPDATE vehicles SET vehicle_name=?, model=?, seats=?, fuel_type=?,
                          current_mileage=?, next_maintenance_mileage=?, annual_inspection_expiry=?,
                          department='水质运维', status='idle' WHERE id=?''', values + (row['id'],))
        else:
            db.execute('''INSERT INTO vehicles
                (plate_no, vehicle_name, model, seats, fuel_type, current_mileage,
                 next_maintenance_mileage, annual_inspection_expiry, department, status)
                VALUES (?,?,?,?,?,?,?,?, '水质运维','idle')''', (vehicle['plate_no'],) + values)

    retired = []
    for row in db.execute('SELECT id, plate_no FROM vehicles ORDER BY id').fetchall():
        if row['plate_no'] in desired_plates:
            continue
        refs = reference_counts(db, 'vehicle_id', row['id'], excluded=('vehicles',))
        if refs:
            db.execute("UPDATE vehicles SET status='retired' WHERE id=?", (row['id'],))
            retired.append({'plate_no': row['plate_no'], 'references': refs})
        else:
            db.execute('DELETE FROM vehicles WHERE id=?', (row['id'],))
    return retired


def current_summary(db):
    def count(table):
        return db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table_exists(db, table) else 0
    return {
        'sites': count('sites'), 'active_users': db.execute(
            "SELECT COUNT(*) FROM users WHERE status='active'"
        ).fetchone()[0],
        'devices': count('device_shadows'), 'reagent_inventory': count('reagent_inventory'),
        'spare_parts': count('spare_parts_inventory'), 'vehicles': count('vehicles'),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default=DEFAULT_INPUT)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    with open(args.input, encoding='utf-8') as source:
        payload = json.load(source)
    validate_payload(payload)

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    before = current_summary(db)
    extra_sites = [dict(row) for row in db.execute(
        'SELECT id, name, code FROM sites WHERE id NOT IN (%s)' % ','.join('?' * len(payload['sites'])),
        tuple(row['id'] for row in db.execute('SELECT id FROM sites ORDER BY id LIMIT ?', (len(payload['sites']),)))
    ).fetchall()] if before['sites'] > len(payload['sites']) else []
    extra_site_refs = {
        row['name']: reference_counts(db, 'site_id', row['id'], excluded=('sites', 'user_sites'))
        for row in extra_sites
    }
    vehicle_refs = {}
    for row in db.execute('SELECT id, plate_no FROM vehicles').fetchall():
        refs = reference_counts(db, 'vehicle_id', row['id'], excluded=('vehicles',))
        if refs:
            vehicle_refs[row['plate_no']] = refs
    dry_report = {
        'mode': 'apply' if args.apply else 'dry-run',
        'before': before,
        'source': {
            'people': len(payload['people']), 'sites': len(payload['sites']),
            'pilot_sites': [row['name'] for row in payload['pilot_sites']],
            'equipment': payload['quality']['equipment_count'],
            'sites_without_equipment': payload['quality']['sites_without_equipment_blocks'],
            'vehicles': [row['plate_no'] for row in payload['vehicles']],
        },
        'current_extra_sites': extra_sites,
        'current_extra_site_references': extra_site_refs,
        'current_vehicle_references': vehicle_refs,
    }
    if not args.apply:
        print(json.dumps(dry_report, ensure_ascii=False, indent=2))
        db.close()
        return

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = f'{args.db}.before_operational_workbook_{stamp}.bak'
    db.close()
    shutil.copy2(args.db, backup)
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('BEGIN IMMEDIATE')
        ensure_schema(db)
        site_ids = sync_sites(db, payload)
        sync_users(db, payload, site_ids)
        device_count, reagent_count = sync_devices_and_reagents(db, payload, site_ids)
        monitoring_cleanup = clear_unconfigured_monitoring_artifacts(db, site_ids)
        sync_inspection_templates(db, payload)
        cleared_parts = clear_spare_parts_if_unreferenced(db)
        retired = sync_vehicles(db, payload)
        orphan_cleanup = clear_orphaned_rows(db)
        db.commit()
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        after = current_summary(db)
        result = {
            **dry_report,
            'backup': backup,
            'after': after,
            'imported': {
                'devices': device_count, 'reagent_inventory': reagent_count,
                'cleared_spare_parts': cleared_parts, 'retired_legacy_vehicles': retired,
                'orphan_cleanup': orphan_cleanup,
                'monitoring_cleanup': monitoring_cleanup,
            },
            'integrity_check': integrity,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    main()

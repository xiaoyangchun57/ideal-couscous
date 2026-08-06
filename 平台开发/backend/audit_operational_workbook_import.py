"""Read-only audit for the operational workbook master-data import."""
import argparse
import json
import os
import sqlite3


ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default=os.path.join(ROOT, 'data', 'operational_workbook_import.json'))
    parser.add_argument('--db', default=os.path.join(ROOT, 'data', 'water.db'))
    args = parser.parse_args()
    with open(args.input, encoding='utf-8') as source:
        payload = json.load(source)
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    errors = []
    diagnostics = {}

    sites = [dict(row) for row in db.execute(
        'SELECT name, code, manager, is_pilot, operation_frequency FROM sites ORDER BY code'
    )]
    expected_sites = [{
        'name': row['name'], 'code': f"{int(row['sequence']):03d}", 'manager': row['owner'],
        'is_pilot': 1 if any(pilot['name'] == row['name'] for pilot in payload['pilot_sites']) else 0,
        'operation_frequency': next((pilot['frequency'] for pilot in payload['pilot_sites'] if pilot['name'] == row['name']), ''),
    } for row in payload['sites']]
    if sites != expected_sites:
        errors.append('station master data does not match workbook')

    people = [dict(row) for row in db.execute(
        "SELECT real_name, phone FROM users WHERE status='active' ORDER BY real_name"
    )]
    expected_people = sorted(
        ({'real_name': row['name'], 'phone': row['phone']} for row in payload['people']),
        key=lambda row: row['real_name'],
    )
    if people != expected_people:
        errors.append('active personnel does not match workbook')

    expected_roles = sorted(
        (row['name'], {'管理员': 'admin', '运维员': 'operator', '审核员': 'reviewer'}[role])
        for row in payload['people'] for role in row['roles']
    )
    actual_roles = sorted(
        (row['real_name'], row['role']) for row in db.execute('''SELECT u.real_name, ur.role
            FROM user_roles ur JOIN users u ON u.id=ur.user_id WHERE u.status='active' ''')
    )
    if actual_roles != expected_roles:
        errors.append('personnel roles do not match workbook')

    device_count = db.execute("SELECT COUNT(*) FROM device_shadows WHERE device_code LIKE 'WB-%'").fetchone()[0]
    if device_count != payload['quality']['equipment_count']:
        errors.append(f'device count is {device_count}, expected {payload["quality"]["equipment_count"]}')
    duplicate_device_codes = db.execute('''SELECT COUNT(*) FROM (
        SELECT device_code FROM device_shadows GROUP BY device_code HAVING COUNT(*)>1)''').fetchone()[0]
    if duplicate_device_codes:
        errors.append(f'{duplicate_device_codes} duplicate device codes')
    unmanaged_count = db.execute(
        "SELECT COUNT(*) FROM device_shadows WHERE device_code LIKE 'WB-%' AND management_scope != 'managed'"
    ).fetchone()[0]
    if unmanaged_count:
        errors.append(f'{unmanaged_count} workbook devices do not use the unified managed lifecycle')
    monitoring_enabled = db.execute(
        "SELECT COUNT(*) FROM device_shadows WHERE device_code LIKE 'WB-%' AND monitoring_enabled=1"
    ).fetchone()[0]
    if monitoring_enabled:
        errors.append(f'{monitoring_enabled} workbook devices unexpectedly have monitoring enabled')
    pending_monitoring_alerts = db.execute('''SELECT COUNT(*) FROM alerts
        WHERE status='pending' AND metric IN ('device_status','data_gap')''').fetchone()[0]
    if pending_monitoring_alerts:
        errors.append(f'{pending_monitoring_alerts} pending monitoring alerts remain while monitoring is disabled')

    site_names = {row['name'] for row in payload['sites']}
    expected_inventory = len({
        (block['site'], device['reagent_name'])
        for block in payload['equipment_blocks'] if block['site'] in site_names
        for device in block['devices'] if device.get('reagent_name')
    })
    actual_inventory = db.execute('SELECT COUNT(*) FROM reagent_inventory').fetchone()[0]
    if actual_inventory != expected_inventory:
        errors.append(f'reagent inventory count is {actual_inventory}, expected {expected_inventory}')

    active_plates = {
        row['plate_no'] for row in db.execute("SELECT plate_no FROM vehicles WHERE status<>'retired'")
    }
    expected_plates = {row['plate_no'] for row in payload['vehicles']}
    if active_plates != expected_plates:
        errors.append('active vehicle plates do not match workbook')
    spare_parts = db.execute('SELECT COUNT(*) FROM spare_parts_inventory').fetchone()[0]
    if spare_parts:
        errors.append(f'spare-parts inventory is not empty: {spare_parts}')

    for frequency in ('weekly', 'monthly'):
        actual_items = [dict(row) for row in db.execute('''SELECT i.item_name AS name,
                i.max_photos AS photo_count, i.need_review
            FROM inspection_template_items i JOIN inspection_templates t ON t.id=i.template_id
            WHERE t.category='水质监测' AND t.frequency=? ORDER BY i.sort_order''', (frequency,))]
        expected_items = [{
            'name': row['name'], 'photo_count': row['photo_count'],
            'need_review': 1 if row['need_review'] else 0,
        } for row in payload['inspection_items'] if row['frequency'] == frequency]
        if actual_items != expected_items:
            errors.append(f'{frequency} inspection template does not match workbook')
            diagnostics[f'{frequency}_inspection'] = {
                'actual': actual_items,
                'expected': expected_items,
            }

    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_key_errors = [tuple(row) for row in db.execute('PRAGMA foreign_key_check')]
    if integrity != 'ok':
        errors.append(f'integrity check failed: {integrity}')
    if foreign_key_errors:
        errors.append(f'foreign key check returned {len(foreign_key_errors)} rows')
        diagnostics['foreign_key_errors'] = foreign_key_errors[:20]
    summary = {
        'ok': not errors,
        'counts': {
            'sites': len(sites), 'people': len(people), 'devices': device_count,
            'external_devices': external_count, 'reagent_inventory': actual_inventory,
            'active_vehicles': len(active_plates), 'spare_parts': spare_parts,
            'monitoring_enabled': monitoring_enabled,
            'pending_monitoring_alerts': pending_monitoring_alerts,
        },
        'pilot_sites': [row['name'] for row in sites if row['is_pilot']],
        'integrity_check': integrity,
        'foreign_key_errors': len(foreign_key_errors),
        'errors': errors,
        'diagnostics': diagnostics,
    }
    db.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == '__main__':
    main()

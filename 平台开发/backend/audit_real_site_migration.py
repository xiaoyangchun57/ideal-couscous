"""Read-only integrity audit after replacing station master data."""
import json
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'data', 'water.db')
OUT_DIR = os.path.join(ROOT, '..', '..', 'docs', 'migration-reports')


def columns(db, table):
    return [row[1] for row in db.execute('PRAGMA table_info("%s")' % table)]


def find_site_refs(value, valid_ids, path=''):
    invalid = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = (path + '.' if path else '') + str(key)
            if key in ('site_id', 'siteId') and isinstance(item, (int, str)) and str(item).isdigit() and int(item) not in valid_ids:
                invalid.append({'path': next_path, 'value': int(item)})
            elif key in ('site_ids', 'siteIds', 'sites') and isinstance(item, list):
                for index, site_id in enumerate(item):
                    if isinstance(site_id, (int, str)) and str(site_id).isdigit() and int(site_id) not in valid_ids:
                        invalid.append({'path': next_path + '[' + str(index) + ']', 'value': int(site_id)})
            invalid.extend(find_site_refs(item, valid_ids, next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            invalid.extend(find_site_refs(item, valid_ids, path + '[' + str(index) + ']'))
    return invalid


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        sites = [dict(row) for row in db.execute('SELECT id,name,code,manager,gps_lat,gps_lng,type FROM sites ORDER BY id')]
        valid_ids = {row['id'] for row in sites}
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        invalid_direct, json_refs = {}, []
        for table in tables:
            cols = columns(db, table)
            if 'site_id' in cols:
                rows = db.execute('SELECT rowid, site_id FROM "%s" WHERE site_id IS NOT NULL AND site_id NOT IN (%s)' % (table, ','.join('?' * len(valid_ids))), tuple(valid_ids)).fetchall()
                if rows: invalid_direct[table] = [dict(row) for row in rows]
            for field in ('plan_data', 'previous_plan_data'):
                if field not in cols: continue
                for row in db.execute('SELECT rowid AS audit_rowid, "%s" AS content FROM "%s" WHERE "%s" IS NOT NULL AND "%s" != ""' % (field, table, field, field)):
                    try: refs = find_site_refs(json.loads(row['content']), valid_ids)
                    except Exception: refs = []
                    if refs: json_refs.append({'table': table, 'rowid': row['audit_rowid'], 'field': field, 'references': refs})
        users = [dict(row) for row in db.execute("SELECT u.id,u.username,u.real_name,u.role,u.status,COUNT(us.site_id) site_count FROM users u LEFT JOIN user_sites us ON us.user_id=u.id GROUP BY u.id ORDER BY u.id")]
        users_without_sites = [row for row in users if row['status'] == 'active' and row['role'] in ('operator', 'manager') and row['site_count'] == 0]
        equipment = db.execute('SELECT COUNT(*) FROM device_shadows').fetchone()[0] if 'device_shadows' in tables else 0
        site_gaps = [row for row in sites if not row['manager'] or row['gps_lat'] is None or row['gps_lng'] is None or not row['type']]
        report = {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'site_count': len(sites),
            'invalid_direct_site_references': invalid_direct, 'invalid_json_site_references': json_refs,
            'active_operator_or_manager_without_sites': users_without_sites, 'station_master_gaps': site_gaps,
            'device_shadow_count': equipment,
        }
    finally:
        db.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_path = os.path.join(OUT_DIR, 'site-migration-audit-' + stamp + '.json')
    md_path = os.path.join(OUT_DIR, 'site-migration-audit-' + stamp + '.md')
    with open(json_path, 'w', encoding='utf-8') as output: json.dump(report, output, ensure_ascii=False, indent=2)
    with open(md_path, 'w', encoding='utf-8') as output:
        output.write('# 站点迁移影响审计\n\n')
        output.write('- 审计时间：%s\n- 当前站点：%s\n- 直接失效站点引用表：%s\n- JSON 失效站点引用：%s\n- 无站点授权的在职运维/主管：%s\n- 站点主数据缺项：%s\n- 当前设备台账：%s 条\n' % (
            report['generated_at'], report['site_count'], len(invalid_direct), len(json_refs), len(users_without_sites), len(site_gaps), equipment))
        if invalid_direct: output.write('\n## 直接失效引用\n```json\n%s\n```\n' % json.dumps(invalid_direct, ensure_ascii=False, indent=2))
        if json_refs: output.write('\n## JSON 失效引用\n```json\n%s\n```\n' % json.dumps(json_refs, ensure_ascii=False, indent=2))
    print(json.dumps({'json': json_path, 'markdown': md_path, 'summary': report}, ensure_ascii=False))


if __name__ == '__main__':
    main()

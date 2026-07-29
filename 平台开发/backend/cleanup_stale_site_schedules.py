"""Archive cleanup for schedules whose JSON still references removed demo sites."""
import json
import os
import shutil
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'data', 'water.db')

def contains_invalid_site(value, valid_ids):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('site_id', 'siteId') and str(item).isdigit() and int(item) not in valid_ids: return True
            if key in ('site_ids', 'siteIds', 'sites') and isinstance(item, list) and any(str(v).isdigit() and int(v) not in valid_ids for v in item): return True
            if contains_invalid_site(item, valid_ids): return True
    if isinstance(value, list): return any(contains_invalid_site(item, valid_ids) for item in value)
    return False

def main():
    backup = DB_PATH + '.before_schedule_cleanup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak'
    shutil.copy2(DB_PATH, backup)
    db = sqlite3.connect(DB_PATH)
    try:
        valid_ids = {r[0] for r in db.execute('SELECT id FROM sites')}
        stale = []
        for row in db.execute('SELECT id,plan_data FROM plan_schedules'):
            try: bad = contains_invalid_site(json.loads(row[1] or '{}'), valid_ids)
            except Exception: bad = False
            if bad: stale.append(row[0])
        if stale:
            placeholders = ','.join('?' * len(stale))
            for table in [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
                cols = {r[1] for r in db.execute('PRAGMA table_info("%s")' % table)}
                if 'plan_schedule_id' in cols:
                    db.execute('DELETE FROM "%s" WHERE plan_schedule_id IN (%s)' % (table, placeholders), stale)
            db.execute('DELETE FROM plan_schedules WHERE id IN (%s)' % placeholders, stale)
        db.commit()
    except Exception:
        db.rollback(); shutil.copy2(backup, DB_PATH); raise
    finally: db.close()
    print(json.dumps({'removed_schedule_ids': stale, 'backup': backup}, ensure_ascii=False))

if __name__ == '__main__': main()

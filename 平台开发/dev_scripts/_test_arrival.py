import sqlite3
db = sqlite3.connect('data/water.db'); db.row_factory = sqlite3.Row
allowed = [r['site_id'] for r in db.execute('SELECT site_id FROM user_sites WHERE user_id=3')]
date = '2026-07-18'
sc = f' AND da.site_id IN ({", ".join("?" * len(allowed))})'
scp = list(allowed)
print('sc =', repr(sc[:60]), '...')
print('nparams =', 1 + len(scp), 'qmarks in sc =', sc.count('?'))
try:
    hd = db.execute(f'SELECT COUNT(*) as c FROM data_arrival WHERE date=?{sc}', (date,) + tuple(scp)).fetchone()['c']
    print('has_data =', hd)
    if hd > 0:
        total = db.execute(f'SELECT AVG(arrival_rate) as avg FROM data_arrival WHERE date=?{sc}', (date,) + tuple(scp)).fetchone()
        print('total =', total)
        rows = db.execute(f'''SELECT da.metric, COUNT(da.site_id) as site_count, ROUND(AVG(da.arrival_rate),1) as avg_rate, 0 as below_threshold FROM data_arrival da WHERE da.date=?{sc} GROUP BY da.metric''', (date,) + tuple(scp)).fetchall()
        print('rows =', len(rows))
except Exception:
    import traceback; traceback.print_exc()

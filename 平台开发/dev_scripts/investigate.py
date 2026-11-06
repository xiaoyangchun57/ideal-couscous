"""巡检模块现状摸底"""
import sqlite3
db = sqlite3.connect('E:/杂七杂八/水质运维/平台开发/backend/data/water.db')
db.row_factory = sqlite3.Row

print('=== 1. 模板系统（两套并行） ===')
print(f'inspection_templates: {db.execute("SELECT COUNT(*) FROM inspection_templates").fetchone()[0]}')
cats = [r['category'] for r in db.execute("SELECT DISTINCT category FROM inspection_templates WHERE category!=''").fetchall()]
print(f'  分类: {cats}')
freqs = [r['frequency'] for r in db.execute("SELECT DISTINCT frequency FROM inspection_templates WHERE frequency!=''").fetchall()]
print(f'  频次: {freqs}')

print(f'inspection_v2_templates: {db.execute("SELECT COUNT(*) FROM inspection_v2_templates").fetchone()[0]}')

print('\n=== 2. 检查项表 ===')
fl = [r['frequency_level'] for r in db.execute("SELECT DISTINCT frequency_level FROM inspection_template_items WHERE frequency_level!=''").fetchall()]
print(f'inspection_template_items 频次级别: {fl}')
ci = [r['category'] for r in db.execute("SELECT DISTINCT category FROM inspection_template_items WHERE category!=''").fetchall()]
print(f'inspection_template_items 分类: {ci}')

fl2 = [r['frequency_level'] for r in db.execute("SELECT DISTINCT frequency_level FROM inspection_v2_template_items WHERE frequency_level!=''").fetchall()]
print(f'inspection_v2_template_items 频次级别: {fl2}')
ci2 = [r[0] for r in db.execute("SELECT DISTINCT category FROM inspection_v2_template_items WHERE category!=''").fetchall()]
print(f'inspection_v2_template_items 分类: {ci2}')

print('\n=== 3. 计划表 ===')
plans = db.execute("SELECT id, plan_name, period, status FROM insp_plans LIMIT 10").fetchall()
print('insp_plans 前10:')
for r in plans:
    print(f'  id={r["id"]} name={r["plan_name"][:20]} period={r["period"]} status={r["status"]}')
s1 = [r['status'] for r in db.execute("SELECT DISTINCT status FROM insp_plans").fetchall()]
print(f'  状态集: {s1}')

print('\ninspection_v2_plans:')
v2 = db.execute("SELECT id, plan_name, period, status FROM inspection_v2_plans").fetchall()
for r in v2:
    print(f'  id={r["id"]} name={r["plan_name"][:20]} period={r["period"]} status={r["status"]}')
s2 = [r['status'] for r in db.execute("SELECT DISTINCT status FROM inspection_v2_plans").fetchall()]
print(f'  状态集: {s2}')

print('\n=== 4. 车辆关联 ===')
veh_cols = [c[1] for c in db.execute("PRAGMA table_info(insp_plans)").fetchall()]
print(f'insp_plans 含 vehicle_id? {\"vehicle_id\" in veh_cols}')
print(f'inspection_v2_plans 含 vehicle_id? {\"vehicle_id\" in [c[1] for c in db.execute(\"PRAGMA table_info(inspection_v2_plans)\").fetchall()]}')
vehs = db.execute("SELECT id, plate_no FROM vehicles").fetchall()
print(f'车辆数据: {[(r["id"], r["plate_no"]) for r in vehs]}')

print('\n=== 5. 前端路由引用的表 ===')
print('inspection-v2/plans -> insp_plans (旧表)')
print('inspection-v2/plans/review -> inspection_v2_items (新表)')
print('weekly-plans -> weekly_inspection_plans (独立表)')

db.close()

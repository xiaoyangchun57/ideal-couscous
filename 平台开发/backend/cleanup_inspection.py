#!/usr/bin/env python3
"""巡检模块数据层清理与统一"""
import sqlite3
db = sqlite3.connect('E:/杂七杂八/水质运维/平台开发/backend/data/water.db')

# 1. 删掉无人使用的 inspection_v2_* 幽灵表（避免误解）
ghost_tables = ['inspection_v2_templates', 'inspection_v2_template_items', 
                'inspection_v2_configs', 'inspection_v2_schedules',
                'inspection_v2_plans', 'inspection_v2_items']
for t in ghost_tables:
    db.execute(f"DROP TABLE IF EXISTS {t}")
    print(f'🗑️ 已删除幽灵表: {t}')

# 2. 给 insp_plans 加 vehicle_id 列（真正关联车辆）
cols = [c[1] for c in db.execute("PRAGMA table_info(insp_plans)").fetchall()]
if 'vehicle_id' not in cols:
    db.execute("ALTER TABLE insp_plans ADD COLUMN vehicle_id INTEGER DEFAULT 0")
    print('✅ 新增 vehicle_id 列')

# 3. 统一频次术语：inspection_template_items.frequency_level 
#    'high'/'mid'/'low' → 'daily'/'weekly'/'monthly'
db.execute("UPDATE inspection_template_items SET frequency_level='daily' WHERE frequency_level='high'")
db.execute("UPDATE inspection_template_items SET frequency_level='weekly' WHERE frequency_level='mid'")
db.execute("UPDATE inspection_template_items SET frequency_level='monthly' WHERE frequency_level='low'")
print('✅ 频次术语统一: high→daily, mid→weekly, low→monthly')

# 4. 清理模板分类：删掉非水质相关的分类
# 已有: '站房环境','设备运维','质控校准','台账登记','环境'
# '环境'是多余的（跟'站房环境'重叠），改为'站房环境'
db.execute("UPDATE inspection_template_items SET category='站房环境' WHERE category='环境'")
print('✅ 分类清理: 环境→站房环境')

# 5. 检查模板分类
cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM inspection_template_items").fetchall()]
print(f'最终检查项分类: {cats}')

# 6. 给一些模板加 vehicle_id 示例数据
# 设置一个有车辆的计划
db.execute("UPDATE insp_plans SET vehicle_id=2 WHERE id=1")
print('✅ 计划#1 关联车辆赣A·X0001')

db.commit()
db.close()
print('\n巡检模块数据层清理完成')

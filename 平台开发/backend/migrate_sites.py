"""站点数据迁移脚本：解析2026年度水文监测设施设备站点统计表.xlsx
生成 site_data.json 供 app.py 的 seed_sites() 使用"""
import json, re, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import openpyxl

EXCEL_PATH = os.path.join(os.path.dirname(__file__), '..', '2026年度水文监测设施设备站点统计表.xlsx')
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'site_data.json')

# 类型映射：Excel类别 → 系统内部类型
TYPE_MAP = {
    '雨量站': 'rainfall', '水位站': 'water_level', '水文站': 'hydrology',
    '墒情站': 'soil_moisture', '自动蒸发站': 'evaporation',
    '地下水监测站': 'groundwater', '站院': 'station_yard',
}

# 设备模板（每类站点默认配哪些设备）
DEVICE_TEMPLATES = {
    'rainfall': [('翻斗式雨量计','rainfall_gauge'),('电子雨量计','electronic_rainfall')],
    'water_level': [('雷达水位计','radar_water_level'),('压力式水位计','pressure_water_level'),('流速计','flow_meter')],
    'hydrology': [('水文综合采集仪','hydro_collector'),('流速仪','current_meter'),('雨量计','rainfall_meter'),('水位计','water_level_meter')],
    'soil_moisture': [('土壤水分传感器','soil_moisture_sensor'),('土壤温度计','soil_temperature')],
    'evaporation': [('蒸发皿','evaporation_pan'),('气象百叶箱','weather_screen'),('风速仪','anemometer')],
    'groundwater': [('地下水位计','groundwater_level'),('水质在线监测仪','water_quality_monitor')],
    'station_yard': [('视频监控','video_surveillance'),('安防报警','security_alarm'),('环境传感器','env_sensor')],
}

def dms_to_decimal(dms_str):
    """将 DMS 坐标转为十进制 如 '115°40′40″' → 115.6778"""
    if not dms_str: return None
    dms_str = str(dms_str).strip().replace('"', '').replace('″', '').replace("'", '′').replace('′', '′')
    # 已经是十进制
    try:
        val = float(dms_str)
        if -180 <= val <= 180: return round(val, 6)
    except: pass
    # 解析 DMS: 115°40′40″ 或 115°44′48.95″
    m = re.match(r'([-+]?\d+(?:\.\d+)?)\s*[°](?:\s*(\d+(?:\.\d+)?)\s*[′\'])?(?:\s*(\d+(?:\.\d+)?)\s*["″])?', str(dms_str))
    if m:
        deg, mn, sec = m.group(1), m.group(2), m.group(3)
        deg = float(deg); mn = float(mn or 0); sec = float(sec or 0)
        return round(deg + mn/60 + sec/3600, 6)
    return None

def parse_excel():
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb.active
    sites = []
    current_type = ''
    for row in ws.iter_rows(values_only=True, min_row=2):
        stype, seq, name, code, addr, lon_str, lat_str, note = row
        if stype: current_type = str(stype).strip()
        sys_type = TYPE_MAP.get(current_type)
        if not sys_type: continue
        lon = dms_to_decimal(lon_str) if lon_str else None
        lat = dms_to_decimal(lat_str) if lat_str else None
        # 生成站码：如果Excel有站码则用，否则自动生成
        site_code = str(code).strip() if code and str(code).strip() else f'ST-{seq:03d}'
        sites.append({
            'code': site_code,
            'name': str(name).strip() if name else f'{current_type}{seq}',
            'type': sys_type,
            'lat': lat,
            'lng': lon,
            'address': str(addr).strip() if addr else '',
            'note': str(note).strip() if note else '',
        })
    # 去重站码 + 修复已知坐标异常
    # 南昌市经度应在 115-117° 范围，"红旗"站原始数据经度为 16°（漏了首位1）
    for s in sites:
        if s['lng'] and s['lng'] < 100:
            s['lng'] = round(s['lng'] + 100, 6)
            print(f"  [Fix] 修正 {s['name']} 经度: {s['lng']}")
    seen_codes = {}
    for s in sites:
        code = s['code']
        if code in seen_codes:
            seen_codes[code] += 1
            s['code'] = f"{code}-{seen_codes[code]}"
        else:
            seen_codes[code] = 1
    return sites

def main():
    sites = parse_excel()
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(sites, f, ensure_ascii=False, indent=2)
    # 统计
    from collections import Counter
    types = Counter(s['type'] for s in sites)
    print(f"✅ 已生成 {len(sites)} 个站点数据 → {OUTPUT_PATH}")
    print(f"\n站点类型分布:")
    for t, c in types.most_common():
        print(f"  {t}: {c}")
    # 设备数据
    print(f"\n各类型设备模板:")
    for t, devs in DEVICE_TEMPLATES.items():
        print(f"  {t}: {', '.join(d[0] for d in devs)}")
    # 坐标统计
    has_coord = sum(1 for s in sites if s['lat'] and s['lng'])
    print(f"\n有坐标站点: {has_coord}/{len(sites)}")

if __name__ == '__main__':
    main()

"""
SL651-2014 TCP Server

接收科蓝平台通过"国家水站协议"转发的实时水文数据，
解析帧 → 写入 sensor_data_raw → 触发告警判断。

启动方式：
  python sl651_server.py [port]      # 独立启动
  或集成到 app.py 一起启动（推荐）
"""

import asyncio
import logging
import sqlite3
import os
import signal
import sys
from datetime import datetime

from sl651_parser import find_frame, parse_frame

logger = logging.getLogger('sl651-server')

# 默认监听端口
DEFAULT_PORT = 5005

# 数据库路径（与app.py保持一致）
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'water.db')

# 映射配置路径
MAPPING_PATH = os.path.join(os.path.dirname(__file__), 'sl651_mapping.json')

# 连接状态
_connections = set()

# ============================================================
# 映射配置加载
# ============================================================

def load_mapping():
    """加载测点/指标映射配置"""
    default = {
        'debug_mode': True,
        'metric_mapping': {
            'W': 'water_level', 'R': 'rainfall', 'Q': 'flow',
            'T': 'temperature', 'H': 'humidity', 'V': 'wind_speed',
            'P': 'pressure', 'E': 'evaporation',
        },
        'per_station': {},
        'unknown_metric_action': 'log_and_store',
        'value_transform': {},
    }
    if not os.path.exists(MAPPING_PATH):
        return default
    try:
        import json
        with open(MAPPING_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 合并默认值（保留未配置的字段）
            for k in default:
                if k not in data:
                    data[k] = default[k]
            return data
    except Exception as e:
        logger.warning(f"[Mapping] 配置加载失败: {e}，使用默认映射")
        return default


_MAPPING_CACHE = None

def get_mapping():
    global _MAPPING_CACHE
    if _MAPPING_CACHE is None:
        _MAPPING_CACHE = load_mapping()
    return _MAPPING_CACHE


def reload_mapping():
    """热重载映射配置（不改代码，改完JSON即生效）"""
    global _MAPPING_CACHE
    _MAPPING_CACHE = load_mapping()
    logger.info("[Mapping] 配置已热重载")


def apply_mapping(station: str, table_id: str, raw_value: float, data_time) -> list:
    """
    将SL651表标识 + 原始值 → 映射后的metric列表。
    返回 [{'metric': str, 'value': float, 'unit': str}, ...]
    """
    if isinstance(table_id, bytes):
        table_id = table_id.decode('ascii', errors='replace')

    mapping = get_mapping()
    result = []

    # 1. 先查站专属映射
    station_cfg = mapping.get('per_station', {}).get(station, {})
    override = station_cfg.get('override_metrics', {})

    if table_id in override:
        target_metric = override[table_id]
    elif table_id in mapping.get('metric_mapping', {}):
        target_metric = mapping['metric_mapping'][table_id]
    else:
        # 未知表标识
        action = mapping.get('unknown_metric_action', 'log_and_store')
        if action == 'ignore':
            logger.warning(f"[Mapping] 忽略未知指标: station={station} table={table_id}")
            return []
        # log_and_store: 以原始名称存储
        target_metric = f"raw_{table_id}"
        logger.info(f"[Mapping] 未知指标 {table_id} → 以 {target_metric} 存储")

    # 2. 值变换（单位换算等）
    val = raw_value
    transform = mapping.get('value_transform', {}).get(target_metric, {})
    if transform:
        val = val * transform.get('factor', 1) + transform.get('offset', 0)

    result.append({
        'metric': target_metric,
        'value': round(val, 2),
        'unit': transform.get('unit', '') if transform else '',
    })

    return result


def log_debug_frame(frame_hex: str, station: str, info: str = ''):
    """调试模式：记录原始帧到日志"""
    mapping = get_mapping()
    if mapping.get('debug_mode'):
        logger.debug(f"[Raw] {station} {info} {frame_hex[:120]}")

# ============================================================
# 数据入库 + 告警判断
# ============================================================

def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def lookup_site_by_code(station_code: str):
    """
    通过站址编码查找本地站点。

    SL651站址是11位BCD编码，本地sites.code可能是纯数字或带前缀。
    尝试多种匹配方式：
    - 精确匹配
    - 尾部匹配（取后8/9/10位）
    - 去尾零匹配
    """
    if not station_code or station_code == 'unknown':
        return None

    candidates = set()
    candidates.add(station_code)
    candidates.add(station_code.lstrip('0'))
    # 去掉可能的末尾填充（BCD填充可能导致多余末尾数字）
    for trim in range(1, 5):
        if len(station_code) > trim:
            candidates.add(station_code[:-trim])
            candidates.add(station_code[:-trim].lstrip('0'))

    conn = get_db()
    try:
        for code in candidates:
            row = conn.execute("SELECT id, name, code FROM sites WHERE code=?", (code,)).fetchone()
            if row:
                logger.info(f"[Site] 精确匹配: {station_code} → {row['code']}({row['name']})")
                return dict(row)

        # 尾部模糊匹配（取最后8位）
        for code in candidates:
            tail = code[-8:] if len(code) >= 8 else code
            rows = conn.execute(
                "SELECT id, name, code FROM sites WHERE code=? OR code LIKE ?",
                (tail, f'%{tail}')
            ).fetchall()
            if len(rows) == 1:
                logger.info(f"[Site] 尾部匹配: {station_code} → {rows[0]['code']}({rows[0]['name']})")
                return dict(rows[0])
            elif len(rows) > 1:
                # 多个匹配取最短code的（最可能的那个）
                rows = sorted(rows, key=lambda r: len(r['code']))
                logger.info(f"[Site] 尾部匹配(多结果取最短): {station_code} → {rows[0]['code']}({rows[0]['name']})")
                return dict(rows[0])
    except Exception as e:
        logger.error(f"[Site] 站址查询失败: {e}")
    finally:
        conn.close()

    logger.warning(f"[Site] 未找到站址 {station_code} 对应的站点")
    return None


def ingest_data(station: str, values: list, data_time: datetime):
    """
    将解析出的测值写入数据库。
    先经过映射配置转换，再入库。
    """
    if not values:
        return

    site = lookup_site_by_code(station)
    if not site:
        return

    site_id = site['id']
    ts = data_time.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db()
    try:
        for v in values:
            if v['value'] is None:
                continue

            raw_metric = v['metric']
            raw_val = v['value']

            # ----- 应用映射配置 -----
            # 如果值里带了 table_id，按 table_id 映射
            table_id = v.get('table_id', '')
            if table_id:
                mapped = apply_mapping(station, table_id, raw_val, data_time)
            else:
                # 直接用 metric 名称查 value_transform
                transform = get_mapping().get('value_transform', {}).get(raw_metric, {})
                mapped_val = raw_val * transform.get('factor', 1) + transform.get('offset', 0)
                mapped = [{'metric': raw_metric, 'value': round(mapped_val, 2), 'unit': transform.get('unit', '')}]

            for item in mapped:
                metric, val = item['metric'], item['value']

                # 写入 sensor_data_raw
                conn.execute(
                    "INSERT INTO sensor_data_raw (site_id, metric, value, recorded_at) VALUES (?, ?, ?, ?)",
                    (site_id, metric, val, ts)
                )
                logger.info(f"[Data] {site['name']}({station}) | {metric}={val} | {ts}")

                # 更新站点的最后心跳
                conn.execute(
                    "UPDATE sites SET last_heartbeat=?, status='online' WHERE id=?",
                    (ts, site_id)
                )

                # 简单告警判断
                _check_threshold(conn, site_id, site['name'], metric, val, ts)

        conn.commit()

        # 更新到报率（标记该站点本小时有数据到达）
        hour = ts[:13] + ':00:00'
        conn.execute(
            "INSERT OR IGNORE INTO sensor_data_hourly (site_id, metric, hour, avg_value, min_value, max_value, sample_count) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (site_id, 'arrival_rate', hour, 100, 100, 100)
        )

    except Exception as e:
        logger.error(f"[Data] 写入失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def _check_threshold(conn, site_id, site_name, metric, value, ts):
    """
    简单阈值告警判断。
    真实场景中应使用更复杂的规则引擎（历史比对、趋势分析等）。
    """
    # 水位告警阈值（可配置化）
    THRESHOLDS = {
        'water_level': {'orange': 15.0, 'red': 20.0},
        'rainfall':    {'yellow': 30, 'orange': 50, 'red': 80},  # mm/小时
        'flow':        {'orange': 500, 'red': 1000},
    }

    if metric not in THRESHOLDS:
        return

    limits = THRESHOLDS[metric]
    for level, limit in sorted(limits.items(), key=lambda x: ['yellow','orange','red'].index(x[0])):
        if value >= limit:
            # 检查是否已有未办结的同类告警
            existing = conn.execute(
                "SELECT id FROM alerts WHERE site_id=? AND metric=? AND level=? AND status IN ('pending','acknowledged')",
                (site_id, metric, level)
            ).fetchone()

            if not existing:
                level_cn = {'yellow': '黄色', 'orange': '橙色', 'red': '红色'}
                message = f"{site_name}{level_cn[level]}告警: {metric}={value}（超阈值{limit}）"
                conn.execute(
                    "INSERT INTO alerts (site_id, metric, value, level, message, status, created_at) VALUES (?,?,?,?,?,?,?)",
                    (site_id, metric, value, level, message, 'pending', ts)
                )
                logger.warning(f"[Alert] {message}")
            else:
                # 更新值
                conn.execute(
                    "UPDATE alerts SET value=?, created_at=? WHERE id=?",
                    (value, ts, existing['id'])
                )
            break  # 只触发最高级别告警


# ============================================================
# TCP Server (asyncio)
# ============================================================

class SL651Protocol(asyncio.Protocol):
    """处理单个TCP连接的协议"""

    def __init__(self):
        self.buffer = b''
        self.addr = None
        self.connected_at = None

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        self.addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        self.connected_at = datetime.now()
        _connections.add(self.addr)
        logger.info(f"[Connect] {self.addr} 已连接 (当前连接数: {len(_connections)})")

        # 回应登录确认（SL651标准要求）
        # 简单确认帧：68H 06 06 68H 01 00 00 00 00 00 00 01 16H
        transport.write(bytes.fromhex('68 06 06 68 01 00 00 00 00 00 00 01 16'))

    def data_received(self, data):
        self.buffer += data

        while True:
            frame, self.buffer = find_frame(self.buffer)
            if frame is None:
                break

            self._process_frame(frame)

    def _process_frame(self, frame):
        """处理单个帧"""
        result = parse_frame(frame)

        if not result['valid']:
            logger.warning(f"[Frame] 无效帧: {result['raw_hex'][:40]}...")
            return

        logger.debug(f"[Frame] {result['station']} | {result['frame_type']} | {len(result['values'])}个测值")

        # 只处理包含测量值的帧
        if result['values']:
            ingest_data(
                station=result['station'],
                values=result['values'],
                data_time=result['data_time'] or datetime.now(),
            )

        # 记录未知数据体
        if result['unknown_data']:
            logger.debug(f"[Raw] 未解析数据: {result['unknown_data'][:80]}")

    def connection_lost(self, exc):
        _connections.discard(self.addr)
        duration = datetime.now() - self.connected_at if self.connected_at else 0
        logger.info(f"[Disconnect] {self.addr} 断开 (连接时长: {duration}, 当前连接数: {len(_connections)})")


class SL651Server:
    """SL651 TCP Server 封装"""

    def __init__(self, host='0.0.0.0', port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self._server = None

    async def start(self):
        """启动服务器"""
        loop = asyncio.get_event_loop()
        self._server = await loop.create_server(
            lambda: SL651Protocol(),
            host=self.host,
            port=self.port,
        )
        logger.info(f"[SL651] TCP Server 已启动: {self.host}:{self.port}")
        return self

    async def serve_forever(self):
        """持续运行"""
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """停止服务器"""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("[SL651] TCP Server 已停止")


# ============================================================
# 独立启动入口
# ============================================================

def run_server(port=DEFAULT_PORT):
    """同步方式运行（供Flask子线程调用）"""

    async def _run():
        server = SL651Server(port=port)
        await server.start()
        print(f"[SL651] 国家水站协议接收器已启动，监听端口 {port}")
        print(f"[SL651] 请在科蓝平台配置转发: TCP → {get_local_ip()}:{port}")
        await server.serve_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("[SL651] Server stopped by user")


def get_local_ip():
    """获取本机内网IP"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            print(f"Usage: python {sys.argv[0]} [port]")
            sys.exit(1)

    print("=" * 60)
    print("  SL651-2014 国家水站协议接收器")
    print(f"  监听端口: {port}")
    print(f"  数据库: {DB_PATH}")
    print("=" * 60)

    # 预检查数据库
    if not os.path.exists(DB_PATH):
        logger.error(f"数据库文件不存在: {DB_PATH}")
        sys.exit(1)

    run_server(port)

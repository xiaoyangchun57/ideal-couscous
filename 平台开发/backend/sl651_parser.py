"""
SL651-2014 国家水站协议解析器

用于接收科蓝数据平台转发的"国家水站协议"报文，
解析出站码、时间、水位/雨量/流量等测值，供入库使用。

帧格式：68H L L 68H C A DATA CS 16H
  - 68H: 帧起始标识
  - L: 长度（从C到CS的字节数）
  - C: 控制域（方向/序号）
  - A: 地址域（站址编码，11位BCD码，6字节）
  - DATA: 数据体（可变长度）
  - CS: 校验码（从C到DATA的逐字节异或）
  - 16H: 帧结束标识

参考 SL651-2014《水文监测数据传输规约》
"""

import struct
import logging
from datetime import datetime, timedelta

logger = logging.getLogger('sl651')

# ============================================================
# 常量定义
# ============================================================
FRAME_START  = 0x68
FRAME_END    = 0x16

# 帧类型标识（UP）
UP_LOGIN         = 0x01  # 登录帧
UP_LOGOUT        = 0x02  # 退出帧
UP_HEARTBEAT     = 0x03  # 心跳帧
UP_REALTIME      = 0x0F  # 实时数据帧
UP_HISTORY       = 0x10  # 历史数据帧
UP_EVENT         = 0x11  # 事件数据帧

# 数据表标识
TABLE_WATER      = b'W'  # 水位表
TABLE_RAINFALL   = b'R'  # 雨量表
TABLE_FLOW       = b'Q'  # 流量表
TABLE_TEMP       = b'T'  # 温度表
TABLE_HUMIDITY   = b'H'  # 湿度表
TABLE_WIND       = b'V'  # 风速表
TABLE_PRESSURE   = b'P'  # 气压表
TABLE_EVAPORATION= b'E'  # 蒸发表

# 测量值 → metric 映射表
TABLE_METRIC_MAP = {
    TABLE_WATER:      'water_level',
    TABLE_RAINFALL:   'rainfall',
    TABLE_FLOW:       'flow',
    TABLE_TEMP:       'temperature',
    TABLE_HUMIDITY:   'humidity',
    TABLE_WIND:       'wind_speed',
    TABLE_PRESSURE:   'pressure',
    TABLE_EVAPORATION:'evaporation',
}

# ============================================================
# BCD 工具函数
# ============================================================

def bcd_to_int(b: int) -> int:
    """单字节 BCD 码 → 整数（如 0x12 → 12）"""
    return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)

def bcd_to_int_be(data: bytes) -> int:
    """多字节大端 BCD → 整数"""
    val = 0
    for b in data:
        val = val * 100 + bcd_to_int(b)
    return val

def parse_bcd_time(data: bytes) -> datetime:
    """
    解析7字节BCD时间戳
    格式: YY MM DD HH MM SS (年-月-日 时:分:秒)
    例如: 0x26 0x07 0x01 0x10 0x12 0x30 → 2026-07-01 10:12:30
    """
    if len(data) < 7:
        return datetime.now()
    y = bcd_to_int(data[0])
    m = bcd_to_int(data[1])
    d = bcd_to_int(data[2])
    h = bcd_to_int(data[3])
    mi = bcd_to_int(data[4])
    s = bcd_to_int(data[5])

    # BCD年转4位年份 (2000-2099)
    year = 2000 + y if y < 100 else y

    # 基本校验
    if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
        logger.warning(f"BCD时间不合理: {data[:7].hex()} → {year}-{m}-{d} {h}:{mi}:{s}")
        return datetime.now()

    return datetime(year, m, d, h, mi, s)

def parse_station_addr(data: bytes) -> str:
    """
    解析6字节站址编码 → 数字字符串

    SL651-2014标准：11位BCD码，高半字节高位为F填充。
    实际部署中各厂家实现可能不同，这里尝试多种解析方式：
    1. 标准BCD解码（跳过F填充）
    2. 直接ASCII解码（部分厂家发送ASCII字符串）
    3. 纯数值组合

    返回统一格式的数字字符串（如 "62644100"）
    """
    if len(data) < 6:
        return "unknown"

    # 方式1: BCD解码，跳过F填充nibble
    digits = []
    for i in range(6):
        b = data[i]
        hi = (b >> 4) & 0x0F
        lo = b & 0x0F

        # 高半字节填充位跳过
        if hi != 0x0F:
            if hi <= 9:
                digits.append(str(hi))
        # 低半字节
        if lo <= 9:
            digits.append(str(lo))

    bcd_result = ''.join(digits).lstrip('0') or '0'

    # 方式2: 尝试ASCII解码（如果第一字节可打印ASCII）
    if 0x20 <= data[0] <= 0x7E:
        try:
            ascii_result = data.decode('ascii').strip().lstrip('0')
            if len(ascii_result) <= 12 and ascii_result.isdigit():
                bcd_result = ascii_result
        except:
            pass

    return bcd_result

# ============================================================
# 框架校验
# ============================================================

def calc_cs(data: bytes) -> int:
    """从C到DATA的逐字节异或校验"""
    cs = 0
    for b in data:
        cs ^= b
    return cs

# ============================================================
# 帧解析
# ============================================================

def find_frame(buffer: bytes):
    """
    在字节流中查找完整SL651帧。
    返回 (帧数据, 剩余字节) 或 (None, buffer)
    """
    while len(buffer) >= 5:
        # 找帧头: 68H L L 68H
        if buffer[0] != FRAME_START:
            buffer = buffer[1:]
            continue

        length = buffer[1]  # L: 从C到CS的字节数
        if length < 4:  # 至少 C(1) + A(6) + DATA(?) + CS(1) 至少9
            buffer = buffer[1:]
            continue

        # L 重复校验
        if buffer[2] != length:
            buffer = buffer[1:]
            continue

        # 第二个 68H
        if buffer[3] != FRAME_START:
            buffer = buffer[1:]
            continue

        # 完整帧长度 = 帧头(4) + payload(C+A+DATA+CS=length) + 尾(1)
        frame_len = 4 + length + 1
        if len(buffer) < frame_len:
            break  # 还没收够

        # 检查帧尾
        if buffer[frame_len - 1] != FRAME_END:
            # 帧尾不对，可能是误判帧头，跳过第一个字节继续找
            buffer = buffer[1:]
            continue

        # 校验 CS: 从C开始到DATA结束 = buffer[4 : 4+length-1]
        cs_index = 4 + length - 1
        payload = buffer[4:cs_index]  # C + A + DATA
        frame_cs = buffer[cs_index]
        if calc_cs(payload) != frame_cs:
            logger.warning(f"CS校验失败: frame={buffer[:frame_len].hex()} calced={calc_cs(payload):02x}")
            buffer = buffer[1:]
            continue

        # 完整有效帧
        frame = buffer[:frame_len]
        rest = buffer[frame_len:]
        return frame, rest

    return None, buffer


def parse_frame(frame: bytes):
    """
    解析SL651帧 → 结构化字典

    返回:
    {
        'raw_hex': str,         # 原始帧hex
        'valid': bool,
        'station': str,         # 站址编码（11位数字字符串）
        'direction': str,       # 'up' | 'down'
        'seq': int,             # 帧序号
        'frame_type': str,      # 帧类型描述
        'data_time': datetime,  # 数据时间
        'values': [             # 解析出的测量值
            {'metric': str, 'value': float}
        ],
        'unknown_data': str,    # 未解析的数据体hex
    }
    """
    result = {
        'raw_hex': frame.hex(),
        'valid': True,
        'station': 'unknown',
        'direction': 'up',
        'seq': 0,
        'frame_type': 'unknown',
        'data_time': None,
        'values': [],
        'unknown_data': '',
    }

    try:
        length = frame[1]
        c = frame[4]
        station_bytes = frame[5:11]
        data_body = frame[11:-2]  # C+A后面、CS前面
        cs = frame[-2]

        # 控制域
        direction = 'up' if (c & 0x80) == 0 else 'down'
        seq = c & 0x1F
        result['direction'] = direction
        result['seq'] = seq

        # 站址
        station_str = parse_station_addr(station_bytes)
        result['station'] = station_str

        # 数据体解析
        if len(data_body) >= 2:
            up_type = struct.unpack('>H', data_body[:2])[0]  # 2字节UP标识
            cp_data = data_body[2:]  # CP数据体

            # 根据UP标识判断帧类型
            if up_type == 0x0001:
                result['frame_type'] = 'login'
                if len(cp_data) >= 7:
                    result['data_time'] = parse_bcd_time(cp_data[:7])
                logger.info(f"[SL651] 登录: 站={station_str}")

            elif up_type == 0x0002:
                result['frame_type'] = 'logout'

            elif up_type == 0x0003:
                result['frame_type'] = 'heartbeat'
                result['data_time'] = datetime.now()
                logger.debug(f"[SL651] 心跳: 站={station_str}")

            elif up_type in (0x000F, 0x0010, 0x0011):
                # 实时/历史/事件数据
                result['frame_type'] = {0x000F: 'realtime', 0x0010: 'history', 0x0011: 'event'}[up_type]

                # 解析CP数据体中的测量值
                # 典型格式: DATATIME(7) + TABLE_ID(1) + VALUE(N)
                values, unknown = parse_data_elements(cp_data)
                result['values'] = values
                result['unknown_data'] = unknown

                # 取第一个测量值的时标
                result['data_time'] = datetime.now()
                # 尝试从CP数据中提取时间
                if len(cp_data) >= 7:
                    try:
                        result['data_time'] = parse_bcd_time(cp_data[:7])
                    except:
                        pass

            else:
                result['frame_type'] = f'up=0x{up_type:04x}'
                result['unknown_data'] = data_body.hex()
                logger.debug(f"[SL651] 未知帧类型 up=0x{up_type:04x}, data={data_body.hex()}")

        else:
            result['unknown_data'] = data_body.hex()

    except Exception as e:
        logger.error(f"帧解析错误: {e}, raw={frame.hex()}")
        result['valid'] = False

    return result


def parse_data_elements(data: bytes):
    """
    解析数据体中的测量值。
    典型格式（连续多组）:
      DATATIME(7 BCD) + TABLE_ID(1 byte) + VALUE(N bytes)
    
    简化处理：尝试常用的水位/雨量/流量等
    如果解析失败，记录原始hex。
    """
    values = []
    unknown = data.hex()

    offset = 0
    while offset + 8 <= len(data):
        # 7字节BCD时间
        try:
            dt = parse_bcd_time(data[offset:offset+7])
        except:
            break
        offset += 7

        if offset >= len(data):
            break

        table_id = data[offset:offset+1]
        offset += 1

        metric = TABLE_METRIC_MAP.get(table_id)
        if metric and offset + 4 <= len(data):
            # SL651中浮点数通常以4字节IEEE 754大端传输
            try:
                # 尝试大端浮点
                val = struct.unpack('>f', data[offset:offset+4])[0]
                values.append({'metric': metric, 'value': round(val, 2), 'time': dt, 'table_id': table_id.decode('ascii', errors='replace')})
                offset += 4
            except:
                # 尝试大端短整型
                try:
                    val = struct.unpack('>H', data[offset:offset+2])[0]
                    values.append({'metric': metric, 'value': float(val), 'time': dt, 'table_id': table_id.decode('ascii', errors='replace')})
                    offset += 2
                except:
                    offset += 2  # 跳过未知
        elif metric:
            # 知道metric但不知道长度，跳过16字节
            values.append({'metric': metric, 'value': None, 'time': dt})
            offset += min(4, len(data) - offset)
        else:
            # 未知表标识 — 仍然尝试解析值，交给映射层处理
            tid = table_id.decode('ascii', errors='replace') if isinstance(table_id, bytes) else str(table_id)
            try:
                if offset + 4 <= len(data):
                    val = struct.unpack('>f', data[offset:offset+4])[0]
                    values.append({'metric': f'raw_{tid}', 'value': round(val, 2), 'time': dt, 'table_id': tid})
                    offset += 4
                else:
                    offset += min(2, len(data) - offset)
            except:
                offset += min(2, len(data) - offset)
            continue

    # 如果有未解析的尾部
    if offset < len(data):
        unknown = data[offset:].hex()
    else:
        unknown = ''

    return values, unknown


# ============================================================
# 快速测试
# ============================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    print("SL651-2014 解析器单元测试")
    print("=" * 50)
    print("提示: 本模块需配合 sl651_server.py 使用")
    print("启动: python sl651_server.py [port]")

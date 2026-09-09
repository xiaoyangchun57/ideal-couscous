"""江西省水文监测HEX/BCD帧的接收基础解析。

本模块仅负责帧边界、头字段、BCD时间、长度、流控字符和CRC16。
它不记录原始报文、不写业务监测值，也不把报文字段直接映射为业务站点。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

FRAME_HEAD = b"\x7e\x7e"
STX = 0x02
UP_FLOW_CONTROL = 0x03
DOWN_FLOW_CONTROL = 0x04
MAX_BODY_LENGTH = 4095
MAX_FRAME_BYTES = 8192
PARSER_VERSION = "jx-hydro-hex-bcd-v1"


class FrameError(ValueError):
    """Protocol error with a non-sensitive machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedFrame:
    raw: bytes
    direction: str
    center_address: int
    station_code: str
    station_bytes: bytes
    password: bytes
    function_code: int
    body_length: int
    serial_number: int
    sent_at: datetime
    payload: bytes
    flow_control: int
    crc: int

    @property
    def logical_key(self) -> str:
        # The caller hashes this value before storage; it never belongs in ordinary logs.
        return "|".join(
            (
                self.station_code,
                str(self.function_code),
                str(self.serial_number),
                self.sent_at.strftime("%Y-%m-%dT%H:%M:%S"),
                self.payload.hex(),
            )
        )


def crc16_modbus(data: bytes) -> int:
    """CRC-16/IBM reflected, init FFFF, polynomial A001.

    The provincial examples transmit the calculated 16-bit value high byte first.
    """
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _decode_bcd_byte(value: int) -> tuple[int, int]:
    high, low = value >> 4, value & 0x0F
    if high > 9 or low > 9:
        raise FrameError("invalid_bcd", "BCD nibble is outside 0..9")
    return high, low


def decode_station_code(raw: bytes) -> str:
    if len(raw) != 5:
        raise FrameError("invalid_station_length", "station address must be five BCD bytes")
    digits: list[str] = []
    for value in raw:
        high, low = _decode_bcd_byte(value)
        digits.extend((str(high), str(low)))
    return "".join(digits)


def encode_station_code(station_code: str) -> bytes:
    if len(station_code) != 10 or not station_code.isdecimal():
        raise ValueError("station_code must contain exactly 10 decimal digits")
    return bytes((int(station_code[index]) << 4) | int(station_code[index + 1]) for index in range(0, 10, 2))


def decode_bcd_time(raw: bytes) -> datetime:
    if len(raw) != 6:
        raise FrameError("invalid_bcd_time_length", "send time must be six BCD bytes")
    pairs = [_decode_bcd_byte(value) for value in raw]
    values = [high * 10 + low for high, low in pairs]
    year, month, day, hour, minute, second = values
    try:
        return datetime(2000 + year, month, day, hour, minute, second)
    except ValueError as exc:
        raise FrameError("invalid_bcd_time", "BCD timestamp is not a valid calendar time") from exc


def encode_bcd_time(value: datetime) -> bytes:
    fields = (value.year % 100, value.month, value.day, value.hour, value.minute, value.second)
    return bytes(((field // 10) << 4) | (field % 10) for field in fields)


def expected_frame_size_from_prefix(buffer: bytes) -> int | None:
    """Return full frame size once the two-byte length field is available."""
    if len(buffer) < 13:
        return None
    length_field = int.from_bytes(buffer[11:13], "big")
    body_length = length_field & 0x0FFF
    if body_length > MAX_BODY_LENGTH:
        raise FrameError("body_too_large", "body exceeds protocol maximum")
    # header(13) + STX(1) + body(length) + flow control(1) + CRC(2)
    return 17 + body_length


def extract_frames(buffer: bytes, *, max_buffer_bytes: int = MAX_FRAME_BYTES) -> tuple[list[bytes], bytes, list[str]]:
    """Split a TCP byte stream without logging or returning sensitive payload text.

    Malformed prefixes are discarded one byte at a time; callers receive only error codes.
    An incomplete valid prefix stays buffered up to ``max_buffer_bytes``.
    """
    frames: list[bytes] = []
    errors: list[str] = []
    remaining = buffer
    while remaining:
        start = remaining.find(FRAME_HEAD)
        if start < 0:
            if remaining:
                errors.append("noise")
            remaining = remaining[-1:] if remaining.endswith(FRAME_HEAD[:1]) else b""
            break
        if start:
            errors.append("noise")
            remaining = remaining[start:]
        if len(remaining) < 13:
            break
        try:
            frame_size = expected_frame_size_from_prefix(remaining)
        except FrameError as exc:
            errors.append(exc.code)
            remaining = remaining[1:]
            continue
        if frame_size is None:
            break
        if frame_size > max_buffer_bytes:
            errors.append("frame_too_large")
            remaining = remaining[1:]
            continue
        if len(remaining) < frame_size:
            break
        candidate = remaining[:frame_size]
        frames.append(candidate)
        remaining = remaining[frame_size:]
    if len(remaining) > max_buffer_bytes:
        errors.append("buffer_overflow")
        remaining = b""
    return frames, remaining, errors


def parse_frame(raw: bytes) -> ParsedFrame:
    if len(raw) < 17 or not raw.startswith(FRAME_HEAD):
        raise FrameError("invalid_frame_boundary", "frame boundary is invalid")
    expected_size = expected_frame_size_from_prefix(raw)
    if expected_size is None or len(raw) != expected_size:
        raise FrameError("invalid_frame_length", "frame length does not match header")

    length_field = int.from_bytes(raw[11:13], "big")
    direction_bits = length_field >> 12
    if direction_bits == 0:
        direction = "up"
        center_address = raw[2]
        station_bytes = raw[3:8]
    elif direction_bits == 8:
        direction = "down"
        station_bytes = raw[2:7]
        center_address = raw[7]
    else:
        raise FrameError("invalid_direction", "direction nibble is unsupported")

    station_code = decode_station_code(station_bytes)
    password = raw[8:10]
    function_code = raw[10]
    body_length = length_field & 0x0FFF
    if raw[13] != STX:
        raise FrameError("missing_stx", "body start marker is invalid")
    content_start = 14
    content_end = content_start + body_length
    content = raw[content_start:content_end]
    flow_control = raw[content_end]
    expected_flow = UP_FLOW_CONTROL if direction == "up" else DOWN_FLOW_CONTROL
    if flow_control != expected_flow:
        raise FrameError("invalid_flow_control", "flow control does not match frame direction")
    if len(content) < 8:
        raise FrameError("body_too_short", "body must contain serial number and send time")

    supplied_crc = int.from_bytes(raw[-2:], "big")
    calculated_crc = crc16_modbus(raw[:-2])
    if supplied_crc != calculated_crc:
        raise FrameError("crc_mismatch", "CRC16 check failed")

    return ParsedFrame(
        raw=raw,
        direction=direction,
        center_address=center_address,
        station_code=station_code,
        station_bytes=station_bytes,
        password=password,
        function_code=function_code,
        body_length=body_length,
        serial_number=int.from_bytes(content[:2], "big"),
        sent_at=decode_bcd_time(content[2:8]),
        payload=content[8:],
        flow_control=flow_control,
        crc=supplied_crc,
    )


def build_ack(frame: ParsedFrame, *, now: datetime | None = None) -> bytes:
    """Build the documented downlink receipt for an authenticated uplink frame."""
    if frame.direction != "up":
        raise ValueError("only uplink frames can be acknowledged")
    send_time = encode_bcd_time(now or datetime.now())
    content = frame.serial_number.to_bytes(2, "big") + send_time
    length_field = 0x8000 | len(content)
    prefix = (
        FRAME_HEAD
        + frame.station_bytes
        + bytes((frame.center_address,))
        + frame.password
        + bytes((frame.function_code,))
        + length_field.to_bytes(2, "big")
        + bytes((STX,))
        + content
        + bytes((DOWN_FLOW_CONTROL,))
    )
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


def parse_many(frames: Iterable[bytes]) -> list[ParsedFrame]:
    return [parse_frame(frame) for frame in frames]

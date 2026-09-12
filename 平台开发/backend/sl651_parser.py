"""江西省水文监测HEX/BCD帧的接收基础解析。

本模块仅负责帧边界、头字段、BCD时间、长度、流控字符和CRC16。
它不记录原始报文、不写业务监测值，也不把报文字段直接映射为业务站点。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Iterable

FRAME_HEAD = b"\x7e\x7e"
STX = 0x02
UP_FLOW_CONTROL = 0x03
DOWN_FLOW_CONTROL = 0x04
MAX_BODY_LENGTH = 4095
MAX_FRAME_BYTES = 8192
PARSER_VERSION = "jx-hydro-hex-bcd-v1"
DEFAULT_MAPPING_PATH = Path(__file__).with_name("sl651_mapping.json")
_NUMERIC_FORMAT = re.compile(r"^N\((\d+)(?:,(\d+))?\)$")


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

    protocol_family: str = "sl651"
    parser_version: str = PARSER_VERSION

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


@dataclass(frozen=True)
class ParsedFactor:
    protocol_code: str
    factor: str | None
    raw_value: float | str | None
    unit: str | None
    parse_format: str | None
    quality: str = "valid"


@dataclass(frozen=True)
class ParsedWaterQualityReport:
    station_code: str
    station_type: int
    observed_at: datetime
    factors: list[ParsedFactor]


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


def decode_bcd_observation_time(raw: bytes) -> datetime:
    """Decode the five-byte `F0 F0` observation time as YYMMDDhhmm."""
    if len(raw) != 5:
        raise FrameError("invalid_observation_time_length", "observation time must be five BCD bytes")
    pairs = [_decode_bcd_byte(value) for value in raw]
    year, month, day, hour, minute = [high * 10 + low for high, low in pairs]
    try:
        return datetime(2000 + year, month, day, hour, minute)
    except ValueError as exc:
        raise FrameError("invalid_observation_time", "observation timestamp is not a valid calendar time") from exc


def encode_bcd_observation_time(value: datetime) -> bytes:
    fields = (value.year % 100, value.month, value.day, value.hour, value.minute)
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


def parse_water_quality_payload(payload: bytes, mapping_path: Path = DEFAULT_MAPPING_PATH) -> list[ParsedFactor]:
    """Decode first-phase 32H factor records without assigning a business site.

    Each known factor is its two/three-byte protocol code followed by the fixed-width
    BCD or opaque value declared in the versioned mapping. An unknown trailing code is
    preserved as an unmapped factor so prior valid factors can still be normalized.
    """
    try:
        definitions = json.loads(Path(mapping_path).read_text(encoding="utf-8"))["water_quality_factor_definitions"]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FrameError("factor_mapping_unavailable", "factor mapping is unavailable") from exc
    indexed = [(bytes.fromhex(code), code, value) for code, value in definitions.items()]
    indexed.sort(key=lambda item: len(item[0]), reverse=True)
    values: list[ParsedFactor] = []
    offset = 0
    while offset < len(payload):
        matched = next((item for item in indexed if payload.startswith(item[0], offset)), None)
        if matched is None:
            code = payload[offset:offset + 2].hex().upper()
            values.append(ParsedFactor(code, None, None, None, None, "unmapped"))
            break
        raw_code, code, definition = matched
        offset += len(raw_code)
        parse_format = str(definition["format"])
        numeric = _NUMERIC_FORMAT.match(parse_format)
        if numeric:
            digits = int(numeric.group(1))
            precision = int(numeric.group(2) or 0)
            byte_count = (digits + 1) // 2
            encoded = payload[offset:offset + byte_count]
            if len(encoded) != byte_count:
                raise FrameError("truncated_factor_value", "factor value is truncated")
            decoded_digits: list[str] = []
            try:
                for value in encoded:
                    high, low = _decode_bcd_byte(value)
                    decoded_digits.extend((str(high), str(low)))
            except FrameError:
                # The record boundary is known even when a factor value is corrupt.
                # Keep scanning following factors instead of discarding the batch.
                offset += byte_count
                values.append(ParsedFactor(code, str(definition["code"]), None, definition.get("unit"), parse_format, "invalid"))
                continue
            number = int("".join(decoded_digits)[-digits:]) / (10 ** precision)
            offset += byte_count
            values.append(ParsedFactor(code, str(definition["code"]), number, definition.get("unit"), parse_format))
            continue
        if parse_format.startswith("X(") and parse_format.endswith(")"):
            length = int(parse_format[2:-1])
            encoded = payload[offset:offset + length]
            if len(encoded) != length:
                raise FrameError("truncated_factor_value", "factor value is truncated")
            offset += length
            values.append(ParsedFactor(code, str(definition["code"]), encoded.hex().upper(), definition.get("unit"), parse_format))
            continue
        raise FrameError("unsupported_factor_format", "factor format is unsupported")
    return values


def parse_water_quality_report(payload: bytes, mapping_path: Path = DEFAULT_MAPPING_PATH) -> ParsedWaterQualityReport:
    """Decode the documented 32H body in order, without prefix searching.

    The water-quality report begins with `F1 F1`, five BCD station-address bytes and
    one station-type byte, followed by `F0 F0` and a five-byte observation time.
    """
    minimum_size = 2 + 5 + 1 + 2 + 5
    if len(payload) < minimum_size:
        raise FrameError("water_quality_body_too_short", "32H water-quality body is incomplete")
    if payload[:2] != b"\xf1\xf1":
        raise FrameError("missing_station_identifier", "32H body must start with F1 F1 station identifier")
    station_code = decode_station_code(payload[2:7])
    station_type = payload[7]
    if payload[8:10] != b"\xf0\xf0":
        raise FrameError("missing_observation_time", "station identifier must be followed by F0 F0 observation time")
    observed_at = decode_bcd_observation_time(payload[10:15])
    return ParsedWaterQualityReport(
        station_code=station_code,
        station_type=station_type,
        observed_at=observed_at,
        factors=parse_water_quality_payload(payload[15:], mapping_path),
    )

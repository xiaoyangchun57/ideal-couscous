"""Bounded HJ212 text framing and parsing without business-side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

try:
    from .sl651_parser import FrameError, MAX_FRAME_BYTES
except ImportError:  # pragma: no cover - direct module execution
    from sl651_parser import FrameError, MAX_FRAME_BYTES


HJ212_HEAD = b"##"
HJ212_PARSER_VERSION = "hj212-text-v2"
_LENGTH_BYTES = 4
_TRAILER_BYTES = 6  # Four ASCII CRC digits followed by CRLF.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_PASSWORD = re.compile(r"^[ -~]{1,128}$")
_FACTOR_KEY = re.compile(r"^(?P<code>[A-Za-z0-9]+)-Rtd$")
_FACTOR_FLAG_KEY = re.compile(r"^(?P<code>[A-Za-z0-9]+)-Flag$", re.IGNORECASE)
_NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_PERMANGANATE_FIELD = re.compile(r"^w01019-", re.IGNORECASE)


@dataclass(frozen=True)
class HJ212Factor:
    protocol_code: str
    factor: str
    raw_value: float | None
    quality: str
    issue_code: str | None = None


@dataclass(frozen=True)
class ParsedHJ212Frame:
    raw: bytes
    station_code: str
    password: bytes
    command: str
    qn: str | None
    data_time: datetime | None
    factors: list[HJ212Factor]
    body_length: int

    protocol_family: str = "hj212"
    parser_version: str = HJ212_PARSER_VERSION

    @property
    def logical_key(self) -> str:
        # This is hashed by storage. QN remains an identifier, never a timestamp.
        return "|".join((self.station_code, self.command, self.qn or "", self.raw.hex()))


def expected_hj212_frame_size(buffer: bytes) -> int | None:
    if len(buffer) < len(HJ212_HEAD) + _LENGTH_BYTES:
        return None
    encoded = buffer[len(HJ212_HEAD):len(HJ212_HEAD) + _LENGTH_BYTES]
    if not encoded.isdigit():
        raise FrameError("hj212_invalid_length", "HJ212 length is not four decimal digits")
    body_length = int(encoded)
    if body_length > MAX_FRAME_BYTES - len(HJ212_HEAD) - _LENGTH_BYTES - _TRAILER_BYTES:
        raise FrameError("frame_too_large", "HJ212 body exceeds receiver maximum")
    return len(HJ212_HEAD) + _LENGTH_BYTES + body_length + _TRAILER_BYTES


def _decode_ascii(value: bytes, code: str) -> str:
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FrameError(code, "HJ212 text is not ASCII") from exc


def hj212_crc(data: bytes) -> str:
    """Return the HJ212-2017 Appendix A CRC as four uppercase hex digits."""
    crc = 0xFFFF
    for value in data:
        crc = (crc >> 8) ^ value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return f"{crc & 0xFFFF:04X}"


def _parse_pairs(text: str, *, error_code: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in text.split(";"):
        if not token:
            continue
        if "=" not in token:
            raise FrameError(error_code, "HJ212 field is malformed")
        key, value = token.split("=", 1)
        if not key or key in values:
            raise FrameError(error_code, "HJ212 field is malformed")
        values[key] = value
    return values


def _parse_data_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{14}", value):
        raise FrameError("hj212_invalid_data_time", "HJ212 DataTime is invalid")
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise FrameError("hj212_invalid_data_time", "HJ212 DataTime is invalid") from exc


def _parse_cp_fields(content: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return CP fields and legacy per-factor bare flags without collapsing them."""
    fields: dict[str, str] = {}
    legacy_flags: dict[str, str] = {}
    for token in content.split(";"):
        if not token:
            continue
        token_fields = _parse_pairs(token.replace(",", ";"), error_code="hj212_invalid_factor")
        factor_code = next(
            (match.group("code") for key in token_fields if (match := _FACTOR_KEY.match(key))),
            None,
        )
        for key, value in token_fields.items():
            if key == "Flag" and factor_code is not None:
                legacy_flags[factor_code] = value
                continue
            # Vendor quality-control fragments may have a bare Flag but no Rtd.
            # They remain raw evidence and must not collide with adjacent fragments.
            if key == "Flag" and factor_code is None:
                continue
            if key in fields:
                raise FrameError("hj212_invalid_factor", "HJ212 field is malformed")
            fields[key] = value
    return fields, legacy_flags


def _parse_factors(content: str) -> list[HJ212Factor]:
    """Read CP fields as one record.

    HJ212 devices in the field send ``DataTime`` inside CP and put quality in
    ``<factor>-Flag``.  Older test devices use a comma-separated bare ``Flag``
    beside each Rtd value, which remains supported for backwards compatibility.
    """
    factors: list[HJ212Factor] = []
    fields, legacy_flags = _parse_cp_fields(content)
    for key, value in fields.items():
        match = _FACTOR_KEY.match(key)
        if match is None:
            continue
        code = match.group("code")
        flag = fields.get(f"{code}-Flag", legacy_flags.get(code, fields.get("Flag")))
        # D is a device diagnostic state and F is a fault state. Neither is a
        # publishable measurement, but retaining the Rtd in the raw receipt lets
        # later evidence review distinguish them from malformed values.
        quality = "valid" if flag == "N" else "fault" if flag in {"D", "F"} else "invalid"
        issue_code = None if flag == "N" else "hj212_flag_fault" if flag in {"D", "F"} else "hj212_unknown_flag"
        number = None
        if _NUMERIC.fullmatch(value):
            number = float(value)
        else:
            quality = "invalid"
            issue_code = "hj212_invalid_numeric"
        protocol_code = "HJ212:w01019-Rtd" if code.lower() == "w01019" else f"HJ212:{code.lower()}"
        factors.append(HJ212Factor(protocol_code, code, number, quality, issue_code))
    return factors


def build_hj212_9011_response(frame: ParsedHJ212Frame) -> bytes:
    """Build only the application response confirmed for authenticated CN=3020."""
    del frame  # The evidence does not establish that request fields must be echoed.
    body = b"ST=91;CN=9011;CP=&&QnRtn=1&&"
    return b"##" + f"{len(body):04d}".encode("ascii") + body + hj212_crc(body).encode("ascii") + b"\r\n"


def parse_hj212_frame(raw: bytes) -> ParsedHJ212Frame:
    expected = expected_hj212_frame_size(raw)
    if expected is None or len(raw) != expected or not raw.startswith(HJ212_HEAD):
        raise FrameError("hj212_invalid_frame_boundary", "HJ212 frame boundary is invalid")
    body_end = len(HJ212_HEAD) + _LENGTH_BYTES + int(raw[2:6])
    body = raw[6:body_end]
    supplied = _decode_ascii(raw[body_end:body_end + 4], "hj212_invalid_crc").upper()
    if raw[body_end + 4:] != b"\r\n":
        raise FrameError("hj212_missing_terminator", "HJ212 frame terminator is invalid")
    if not re.fullmatch(r"[0-9A-F]{4}", supplied):
        raise FrameError("hj212_invalid_crc", "HJ212 CRC is not hexadecimal")
    if hj212_crc(body) != supplied:
        raise FrameError("hj212_crc_mismatch", "HJ212 CRC check failed")
    text = _decode_ascii(body, "hj212_non_ascii")
    if ";CP=&&" not in text or not text.endswith("&&"):
        raise FrameError("hj212_invalid_body", "HJ212 CP boundary is invalid")
    header, cp = text.split(";CP=&&", 1)
    fields = _parse_pairs(header, error_code="hj212_invalid_header")
    for key in ("MN", "CN"):
        if not fields.get(key) or not _IDENTIFIER.fullmatch(fields[key]):
            raise FrameError("hj212_invalid_header", "HJ212 required identifier is invalid")
    if not fields.get("PW") or not _PASSWORD.fullmatch(fields["PW"]):
        raise FrameError("hj212_invalid_header", "HJ212 required identifier is invalid")
    if not re.fullmatch(r"\d{4}", fields["CN"]):
        raise FrameError("hj212_invalid_command", "HJ212 CN is invalid")
    qn = fields.get("QN")
    if qn is not None and not _IDENTIFIER.fullmatch(qn):
        raise FrameError("hj212_invalid_header", "HJ212 QN is invalid")
    cp_fields, _ = _parse_cp_fields(cp[:-2])
    cp_data_time = cp_fields.get("DataTime")
    header_data_time = fields.get("DataTime")
    if cp_data_time is not None and header_data_time is not None and cp_data_time != header_data_time:
        raise FrameError("hj212_invalid_data_time", "HJ212 DataTime is inconsistent")
    return ParsedHJ212Frame(
        raw=raw,
        station_code=fields["MN"],
        password=fields["PW"].encode("ascii"),
        command=fields["CN"],
        qn=qn,
        data_time=_parse_data_time(cp_data_time if cp_data_time is not None else header_data_time),
        factors=_parse_factors(cp[:-2]),
        body_length=len(body),
    )

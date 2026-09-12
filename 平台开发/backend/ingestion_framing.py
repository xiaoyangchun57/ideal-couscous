"""Protocol-neutral TCP framing for explicitly supported station protocols."""
from __future__ import annotations

try:
    from .hj212_parser import HJ212_HEAD, expected_hj212_frame_size
    from .sl651_parser import FRAME_HEAD, FrameError, MAX_FRAME_BYTES, expected_frame_size_from_prefix
except ImportError:  # pragma: no cover
    from hj212_parser import HJ212_HEAD, expected_hj212_frame_size
    from sl651_parser import FRAME_HEAD, FrameError, MAX_FRAME_BYTES, expected_frame_size_from_prefix


def extract_ingestion_frames(buffer: bytes, *, max_buffer_bytes: int = MAX_FRAME_BYTES) -> tuple[list[bytes], bytes, list[str]]:
    """Extract binary and HJ212 frames without allowing one malformed prefix to hide another."""
    frames: list[bytes] = []
    errors: list[str] = []
    remaining = buffer
    while remaining:
        starts = [position for position in (remaining.find(FRAME_HEAD), remaining.find(HJ212_HEAD)) if position >= 0]
        if not starts:
            if remaining:
                errors.append("noise")
            remaining = remaining[-1:] if remaining.endswith((FRAME_HEAD[:1], HJ212_HEAD[:1])) else b""
            break
        start = min(starts)
        if start:
            errors.append("noise")
            remaining = remaining[start:]
        try:
            size = expected_frame_size_from_prefix(remaining) if remaining.startswith(FRAME_HEAD) else expected_hj212_frame_size(remaining)
        except FrameError as exc:
            errors.append(exc.code)
            remaining = remaining[1:]
            continue
        if size is None:
            break
        if size > max_buffer_bytes:
            errors.append("frame_too_large")
            remaining = remaining[1:]
            continue
        if len(remaining) < size:
            break
        frames.append(remaining[:size])
        remaining = remaining[size:]
    if len(remaining) > max_buffer_bytes:
        errors.append("buffer_overflow")
        remaining = b""
    return frames, remaining, errors

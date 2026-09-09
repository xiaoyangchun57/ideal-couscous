import unittest
import sys
from pathlib import Path

# Support both `python backend/test_*.py` and unittest package discovery.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from datetime import datetime

from sl651_parser import (
    FrameError,
    UP_FLOW_CONTROL,
    build_ack,
    crc16_modbus,
    encode_bcd_time,
    encode_station_code,
    extract_frames,
    parse_frame,
    parse_water_quality_report,
)


def make_uplink(*, station_code='0012345678', password=b'\x12\x34', serial=3, sent_at=None, payload=b''):
    sent_at = sent_at or datetime(2020, 6, 12, 2, 0, 0)
    content = serial.to_bytes(2, 'big') + encode_bcd_time(sent_at) + payload
    prefix = (
        b'\x7e\x7e' + b'\x10' + encode_station_code(station_code) + password + b'\x32'
        + len(content).to_bytes(2, 'big') + b'\x02' + content + bytes((UP_FLOW_CONTROL,))
    )
    return prefix + crc16_modbus(prefix).to_bytes(2, 'big')


class SL651ParserTest(unittest.TestCase):
    def test_pdf_style_golden_uplink_and_ack(self):
        payload = bytes.fromhex('F1F1001234567851F0F014061202000311030446121122')
        raw = make_uplink(payload=payload)
        frame = parse_frame(raw)
        self.assertEqual(frame.direction, 'up')
        self.assertEqual(frame.station_code, '0012345678')
        self.assertEqual(frame.function_code, 0x32)
        self.assertEqual(frame.serial_number, 3)
        self.assertEqual(frame.sent_at, datetime(2020, 6, 12, 2, 0, 0))
        report = parse_water_quality_report(frame.payload)
        self.assertEqual(report.station_code, frame.station_code)
        self.assertEqual(report.station_type, 0x51)
        self.assertEqual(report.observed_at, datetime(2014, 6, 12, 2, 0))
        self.assertEqual(report.factors[0].protocol_code, '0311')
        self.assertEqual(report.factors[0].raw_value, 30.4)
        ack = build_ack(frame, now=datetime(2020, 6, 12, 2, 1, 0))
        acknowledgement = parse_frame(ack)
        self.assertEqual(acknowledgement.direction, 'down')
        self.assertEqual(acknowledgement.station_code, frame.station_code)
        self.assertEqual(acknowledgement.serial_number, frame.serial_number)
        self.assertEqual(acknowledgement.function_code, 0x32)

    def test_crc_matches_provincial_water_quality_example_tail(self):
        data = bytes.fromhex(
            '7E7E1000123456781234320044020003140612020000'
            'F1F1001234567851F0F01406120200031103044612112247110326'
            '4818000125491001004C1A0033654D1B01022245200000000338121290FF01082603'
        )
        self.assertEqual(crc16_modbus(data), 0xEF48)

    def test_water_quality_body_requires_ordered_station_and_observation_sections(self):
        with self.assertRaisesRegex(FrameError, 'F1 F1'):
            parse_water_quality_report(b'\x00' * 15)
        with self.assertRaisesRegex(FrameError, 'F0 F0'):
            parse_water_quality_report(bytes.fromhex('F1F100123456785100000000000000'))

    def test_half_frames_noise_and_multiple_frames(self):
        first = make_uplink(serial=1)
        second = make_uplink(serial=2)
        frames, remainder, errors = extract_frames(b'noise' + first[:9])
        self.assertEqual(frames, [])
        self.assertTrue(errors)
        frames, remainder, errors = extract_frames(remainder + first[9:] + second)
        self.assertEqual(frames, [first, second])
        self.assertEqual(remainder, b'')
        self.assertEqual(errors, [])

    def test_crc_length_and_bcd_failures_are_rejected(self):
        raw = make_uplink()
        broken_crc = raw[:-1] + bytes((raw[-1] ^ 0x01,))
        with self.assertRaisesRegex(FrameError, 'CRC16') as crc_error:
            parse_frame(broken_crc)
        self.assertEqual(crc_error.exception.code, 'crc_mismatch')

        invalid_bcd = bytearray(raw)
        invalid_bcd[14 + 2] = 0xFA
        invalid_bcd[-2:] = crc16_modbus(bytes(invalid_bcd[:-2])).to_bytes(2, 'big')
        with self.assertRaises(FrameError) as bcd_error:
            parse_frame(bytes(invalid_bcd))
        self.assertIn(bcd_error.exception.code, {'invalid_bcd', 'invalid_bcd_time'})

        invalid_length = bytearray(raw)
        invalid_length[11:13] = (len(raw) + 20).to_bytes(2, 'big')
        with self.assertRaises(FrameError) as length_error:
            parse_frame(bytes(invalid_length))
        self.assertEqual(length_error.exception.code, 'invalid_frame_length')


if __name__ == '__main__':
    unittest.main()

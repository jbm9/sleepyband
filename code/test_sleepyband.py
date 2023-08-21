#!/usr/bin/env python3

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from packets import _crc16, Header  # noqa
from packets import *  # noqa


class TestCRC(unittest.TestCase):
    def test_crc(self):
        cases = [
            ('', 0xffff),
            ('a', 0x9d77),
            ('aaaa', 0x4361),
            ('baaa', 0xd8bd),
            ('bbbbbb', 0xe70a),
            ('your mom', 0xf63b),
        ]

        for i, be in enumerate(cases):
            buf_str, expected = be
            buf = bytes(buf_str, "ASCII")
            got = _crc16(buf)
            self.assertEqual(got, expected, f'Case {i}: e={expected} g={got}')


class TestHeader(unittest.TestCase):
    def test_to_bytes__with_crc(self):
        expected = bytes.fromhex("bbbb2a000000000000000000000000001800000000006444")
        hdr = Header(0x2a, 0, 0, 24, 0)

        self.assertEqual(hdr.crc, 0)
        got = hdr.to_bytes()

        # Check our side effect of setting CRC
        self.assertEqual(hdr.crc, 0x4464)
        self.assertEqual(expected, got)

    def test_to_bytes__zero_crc(self):
        expected = bytes.fromhex("bbbb2a000000000000000000000000001800000000000000")

        hdr = Header(0x2a, 0, 0, 24, 0)

        self.assertEqual(hdr.crc, 0)
        got = hdr.to_bytes(zero_crc=True)

        # This shouldn't have the crc-modifying side effect
        self.assertEqual(hdr.crc, 0)

        self.assertEqual(expected, got)

    def test_roundtrip(self):
        hdr = Header(0x2a, 1, 22, 24, 19)
        hdr_buf = hdr.to_bytes()
        got = Header.from_bytes(hdr_buf)
        self.assertEqual(hdr.kind, 0x2a)
        self.assertEqual(hdr.kind, got.kind)
        self.assertEqual(hdr.timestamp, 1)
        self.assertEqual(hdr.timestamp, got.timestamp)
        self.assertEqual(hdr.seqno, 22)
        self.assertEqual(hdr.seqno, got.seqno)
        self.assertEqual(hdr.length, 24)
        self.assertEqual(hdr.length, got.length)
        self.assertEqual(hdr.response, 19)
        self.assertEqual(hdr.response, got.response)
        self.assertEqual(hdr.crc, 39389)
        self.assertEqual(hdr.crc, got.crc)

    def test_peek_len(self):
        buf = bytes.fromhex("bbbb2a000000000000000000000000001800000000006444")
        got_len = Header.peek_len(buf)
        expected = 0x18
        self.assertEqual(expected, got_len)

    def test_peek__bogus_magic(self):
        buf = bytes.fromhex("badb2a000000000000000000000000001800000000006444")
        self.assertRaises(ValueError, lambda: Header.peek_len(buf))

    def test_peek__bogus_crc(self):
        # Doesn't raise an exception, as we may have a partial buffer
        buf = bytes.fromhex("bbbb2a000000000000000000000000001800000000006004")
        got_len = Header.peek_len(buf)
        expected = 0x18
        self.assertEqual(expected, got_len)

    def test_from_bytes(self):
        buf = bytes.fromhex("bbbb2a000000000000000000000000001800000000006444")
        hdr = Header.from_bytes(buf)
        self.assertEqual(hdr.kind, 0x002a)
        self.assertEqual(hdr.timestamp, 0)
        self.assertEqual(hdr.seqno, 0)
        self.assertEqual(hdr.length, 0x18)
        self.assertEqual(hdr.response, 0)
        self.assertEqual(hdr.crc, 0x4464)


class TestPacket(unittest.TestCase):
    def assert_headers_equal(self, hdr, got, desc):
        self.assertEqual(hdr.kind, got.kind, f'{desc}: header command mismatch')
        self.assertEqual(hdr.timestamp, got.timestamp, f'{desc}: header timestamp mismatch')
        self.assertEqual(hdr.seqno, got.seqno, f'{desc}: header seqno mismatch')
        self.assertEqual(hdr.length, got.length, f'{desc}: header length mismatch')
        self.assertEqual(hdr.response, got.response, f'{desc}: header response mismatch')
        self.assertEqual(hdr.crc, got.crc, f'{desc}: header CRC mismatch')

    def maybe_dump_bufs(self, expected, got):
        if expected == got:
            return
        len_flag = "*****" if len(expected) != len(got) else ""
        print()
        print(f' i  e  g  ({len(expected)} / {len(got)}) {len_flag}')
        for i in range(min(len(got), len(expected))):
            i_flag = "***" if expected[i] != got[i] else ""
            print(f'{i:3d} {expected[i]:02x} {got[i]:02x} {i_flag}')

    def _test_trivial_packet(self, pkt_cls, expected):
        expected = bytes.fromhex(expected)
        pkt = pkt_cls(0x1234)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got, f'{pkt_cls}')

        rt_got = pkt_cls.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, pkt_cls)

    def test_ack_packet__nack(self):
        expected = bytes.fromhex("bbbb00000000000000000000abffffff1d00000000004165f00fcd0000")
        pkt = AckPacket(0xffffffab, 0xcd, 0xf00f)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)
        self.assertEqual(0xf00f, pkt.orig_kind)
        self.assertEqual(0xcd, pkt.status)
        self.assertFalse(pkt.is_success())

        rt_got = AckPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, "AckPacket")
        self.assertEqual(pkt.orig_kind, rt_got.orig_kind)
        self.assertEqual(pkt.status, rt_got.status)

    def test_ack_packet__succ(self):
        # And now a success case
        expected = bytes.fromhex("bbbb00000000000000000000110000001d0000000000fb17002a000000")
        pkt = AckPacket(0x11, 0, 0x2a)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)
        self.assertEqual(0x2a, pkt.orig_kind)
        self.assertEqual(0, pkt.status)
        self.assertTrue(pkt.is_success())

        rt_got = AckPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, "AckPacket")
        self.assertEqual(pkt.orig_kind, rt_got.orig_kind)
        self.assertEqual(pkt.status, rt_got.status)

    def test_session_start_packet(self):
        expected = bytes.fromhex('''
            bbbb0100bc9a000000000000341200002c0000000000
            ecd01928374601342e322e302e363900000000000000''')
        pkt = SessionStartPacket(0x1234, 0x19283746, 1, "4.2.0.69\0\0\0\0\0\0", timestamp=0x9abc)
        got = pkt.to_bytes()

        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)
        self.assertEqual(0x19283746, pkt.host_id)
        self.assertEqual(1, pkt.mode_num)
        self.assertEqual("4.2.0.69\0\0\0\0\0\0", pkt.version_string)

        rt_got = SessionStartPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, "SessionStartPacket")
        self.assertEqual(pkt.host_id, rt_got.host_id)
        self.assertEqual(pkt.mode_num, rt_got.mode_num)
        self.assertEqual(pkt.version_string, rt_got.version_string)

    def test_config_packet(self):
        self._test_trivial_packet(ConfigGetPacket,
                                  "bbbb030000000000000000003412000018000000000018bc")

    def test_send_stored_data_packet(self):
        self._test_trivial_packet(SendStoredDataPacket,
                                  "bbbb100000000000000000003412000018000000000036b7")

    def test_device_reset_packet(self):
        expected = bytes.fromhex("bbbb0b000000000000000000341200001900000000004f8d00")
        pkt = DeviceResetPacket(0x1234, 0)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)

        rt_got = DeviceResetPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, pkt.__class__)
        self.assertEqual(pkt.value, rt_got.value)

    def test_tech_info_packet(self):
        self._test_trivial_packet(TechnicalStatusPacket,
                                  "bbbb1500000000000000000034120000180000000000af7d")

        expected = bytes.fromhex("bbbb1500000000000000000034120000180000000000af7d")
        pkt = TechnicalStatusPacket(0x1234)
        got = pkt.to_bytes()
        self.assertEqual(expected, got)

        rt_got = TechnicalStatusPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, "TechnicalStatusPacket")

    def test_led_packet(self):
        expected = bytes.fromhex("bbbb230098badc0e0000000078563412190000000000fba900")
        pkt = LEDPacket(0x12345678, 0, 0x0edcba98)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)

        rt_got = LEDPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, "LEDPacket")
        self.assertEqual(pkt.value, rt_got.value)

    def test_is_paired_packet(self):
        expected = bytes.fromhex("bbbb2a000000000000000000000000001800000000006444")
        pkt = IsDevicePairedPacket(0)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)

        rt_got = IsDevicePairedPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, pkt.__class__)

    def test_is_paired_response_packet(self):
        buf = bytes.fromhex("bbbb2b000000000000000000000000001d0000000000ff102a00000000")
        pkt = IsDevicePairedResponsePacket.from_bytes(buf)
        self.assertEqual(pkt.header.kind, 0x2B)
        self.assertEqual(pkt.header.timestamp, 0)
        self.assertEqual(pkt.header.seqno, 0)
        self.assertEqual(pkt.header.length, 0x1d)
        self.assertEqual(pkt.header.crc, 0x10ff)
        self.assertEqual(pkt.value, 0)
        self.assertEqual(pkt.header.response, 0)

    def test_get_log_file_packet(self):
        expected = bytes.fromhex("bbbb440000000000000000003412000020000000000014ce6300000000080000")
        pkt = LogGetPacket(0x1234, 99, 2048)
        got = pkt.to_bytes()
        self.maybe_dump_bufs(expected, got)
        self.assertEqual(expected, got)

        rt_got = LogGetPacket.from_bytes(got)
        self.assert_headers_equal(pkt.header, rt_got.header, pkt.__class__)
        self.assertEqual(pkt.offset, rt_got.offset)
        self.assertEqual(pkt.reqlen, rt_got.reqlen)


if __name__ == '__main__':
    unittest.main()

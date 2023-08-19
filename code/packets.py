# Sleepyband packet implementation
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This file contains the packet framing and parsing implementation to
# speak with the device.  The packet bits are entirely self-contained,
# and the parser is designed to work via callbacks in and out, so you
# can swap out the BLE layer with something more appropriate to your
# system


from __future__ import annotations

from abc import ABC
from enum import IntEnum
import logging
import struct
from typing import Optional

# Quick type annotation for shorts
UInt16 = int

class InvalidMagicException(ValueError):
    '''Attempted to parse a packet with invalid magic'''


class CRCMismatchException(ValueError):
    '''Parsed CRC for packet doesn't match recomputed value'''


class PacketType(IntEnum):
    '''The types of packets available

    There doesn't seem to be a particular rhyme or reason to these,
    though it seems like a lot of them come in query/response pairs.

    Each of these corresponds to a FooBarPacket class below.
    '''
    ACK = 0x00

    SESSION_START = 0x01
    SESSION_START_RES = 0x02

    CONFIG = 0x03

    DEVICE_RESET = 0x0b

    SET_PARAMETERS_FILE = 0x0c
    GET_PARAMETERS_FILE = 0x0d
    PARAMETERS_FILE = 0x0e

    UNK = 0x0f

    SEND_STORED_DATA = 0x10

    GET_TECHNICAL_STATUS = 0x15

    LEDS_CONTROL = 0x23

    IS_DEVICE_PAIRED = 0x2a
    IS_DEVICE_PAIRED_RES = 0x2b



def _crc16(buf, s=0xffff) -> UInt16:
    '''CRC-16/CCITT-FALSE implementation

    Props to crccalc.com for making it easy to try out a bunch of
    checksums all at once.

    The parameter `s` can be used to chain multiple blocks if needed.
    '''
    for b in buf:
        m = 128
        while m:
            high_bit = bool(0x8000 & s)
            if b & m:
                high_bit = not high_bit
            s <<= 1

            if high_bit:
                s ^= 0x1021
            m >>= 1
    return s & 0xffff


class Header:
    '''Container for the command packet header fields

    Note that the header is little-endian, while the payloads seem to
    be big-endian.  This is why we can't have nice things.

    kind: the type of the command to send (see `PacketType`)
    timestamp: timestamp for the packet, mostly unused
    seqno: sequence number (should be unique per packet)
    length: length of this packet (including 24 bytes for the header)
    response: an extra chunk of data, used mostly in responses

    '''

    MAGIC = 0xBBBB  # Packet magic

    def __init__(self,
                 kind: PacketType,
                 timestamp: int,
                 seqno: UInt16,
                 length: UInt16 = 24,
                 response: UInt16 = 0,
                 crc: UInt16 = 0) -> None:
        self.kind = kind
        self.timestamp = timestamp
        self.seqno = seqno
        self.length = length
        self.crc = crc
        self.response = response

    def __str__(self) -> str:
        return f'[0x{self.packetid:x}: {self.kind:04x}] ts={self.timestamp} crc={self.crc:04x}'

    def to_bytes(self, zero_crc=False, skip_crc_compute=False) -> bytes:
        '''Get a packed bytes version of this header

        Side effect: this updates the crc attribute, unless zero_crc
        is True.

        zero_crc: don't do CRC (and don't update CRC in-place)

        '''
        header = struct.pack("<HHQLHLH",
                             self.MAGIC,
                             self.kind,
                             self.timestamp,
                             self.seqno,
                             self.length,
                             self.response,
                             0)
        result = header

        if zero_crc:
            return result

        if not skip_crc_compute:
            cksum = _crc16(result)
            self.crc = cksum

        crc_pk = struct.pack("<H", self.crc)

        result = header[:-2] + crc_pk

        return result

    @classmethod
    def unpack_with_checks(cls, buf, skip_crc_check=False):
        '''(Internal) Unpack a header and raise exception if bogus

        This is the single place to unpack a packet header

        Raises InvalidMagicException if the packet magic is wrong

        Raises CRCMismatchException if the CRC is incorrect (unless
        skip_crc_check is passed in as True)

        skip_crc_check: don't enforce CRC.  This is needed because the
        CRC is computed over the whole payload with the CRC field set
        to 0x0000.  When we're just trying to figure out how long a
        packet is, we need to extract the header without this check in
        place.  We could do a quick hard-coded check in peek_len, but
        doing it this way reduces the number of places we could screw
        up.

        Returns a big ugly tuple, suitable for the initializer.

        '''
        fields = struct.unpack("<HHQLHLH", buf[:24])
        magic, kind, ts, seqno, buflen, response, crc = fields

        if magic != cls.MAGIC:
            raise InvalidMagicException("Incorrect magic")


        bufp = buf[:22] + bytes([0,0]) + buf[24:buflen]
        crc_computed = _crc16(bufp)

        if not skip_crc_check and crc != crc_computed:
            raise CRCMismatchException(f"Got a mismatched CRC for packet of type {kind:04x}/{len(buf)}: {crc:04x} vs {crc_computed:04x}")

        return (kind, ts, seqno, buflen, response, crc)


    @classmethod
    def peek_len(cls, buf: bytes) -> UInt16:
        '''Given a complete header buf, return the length

        Returns the length of the packet we're currently reading in

        Doesn't catch InvalidMagicException, so callers should protect
        against that if needed.

        Note that this requires all 24 bytes of the header, even
        though it doesn't strictly need it.  It could work on a single
        20 byte chunk with a bit of specialization.

        '''
        fields = cls.unpack_with_checks(buf, skip_crc_check=True)
        kind, ts, seqno, buflen, response, crc = fields

        return buflen

    @classmethod
    def from_bytes(cls, buf: bytes) -> Header:
        '''Parses a complete packet from buf

        Returns a Header on success

        Doesn't catch InvalidMagicException or CRCMismatchException;
        callers should handle those themselves.

        '''
        l = cls.peek_len(buf)
        kind, ts, seqno, buflen, response, crc = cls.unpack_with_checks(buf[:l])
        result = Header(kind, ts, seqno, buflen, response)
        result.crc = crc
        return result


class BasePacket(ABC):
    '''Base class for all packets

    Please read the following before adding packet types.

    The structure of these classes is a bit strict and slightly wonky,
    but it allows us to minimize the code needed for both creating and
    parsing packets from a fully abstract API.  This means that each
    packet's implementation is really minimalist, if a little bit
    inside-out.

    Firstly, each class should override the COMMAND attribute in the
    class definition.  This is used to determine the PacketType for
    the packets, and also for the automagic packet parsing code.

    For a worked example, check out AckPacket.  Its payload contains
    three values: the original packet type, a byte of status, and an
    unknown short value.  Its constructor has the signature

    `(seqno, status=None, orig_kind=None, timestamp=0, ...)`

    This weird `__init__` signature allows packets to be put together
    as either "stuffed" packets (with the payload's values) or as
    "hollow" packets (with the payload values in an uninitialized
    state).  To send packets, we create "stuffed" versions and call
    `to_bytes()` on it.  When receiving packets, we create a "hollow"
    version from the parsed `Header`, and then use `update_payload` to
    fill in the instance's attributes from the payload buffer.

    To send a completed packet out on the wire, a Packet class must
    implement the `payload` method, which packs those values into an
    appropriate bytes buffer for the packet assembly within
    `BasePacket`

    To parse inbound packets, a Packet class implements the
    `update_payload` method.  This unpacks the values from the buffer
    and updates the appropriate attributes of `self` in doing so.

    Initializers for all classes should take a seqno as the first,
    non-optional parameter, then all packet-specific parameters as
    kwargs with defaults, and then the remaining header arguments with
    the same defaults as BasePacket.

    Once the weird `__init__` and payload handling bits are
    implemented, the Packet class can implement its own little helper
    methods.  In the case of Ackpacket, there's `is_success()` to let
    consumers tell if it's an ACK or a NAK packet.
    '''
    COMMAND = 0xefbe  # PacketType: override this in all child classes
    def __init__(self, seqno, timestamp=0, response=0, crc=None, length=24):
        self.header = Header(self.COMMAND,
                             timestamp=timestamp,
                             seqno=seqno,
                             length=length,
                             response=response)
        if crc:
            self.header.crc = crc

    def payload(self) -> bytes:
        '''Pack our payload parameters into a buffer for transmission

        Override this if you packet has a payload.

        This is used to serialize a packet for transmission.  All
        values that are not sent in the header go into the payload;
        this method is used to pack them down.
        '''
        return bytes([])

    def update_payload(self, buf: bytes) -> None:
        '''Update payload parameters by parsing the contents of buf

        Override this if you packet has a payload.

        This is the complement to payload(): it takes a packed buffer
        and updates the object's attributes with the contents of that
        buffer.  This is used entirely for the side-effects.
        '''
        # NB: BasePacket has nothing, so this does nothing.

    def to_bytes(self) -> bytes:
        '''Serialize a packet out for transmission
        '''
        header_buf = self.header.to_bytes(zero_crc=True)
        payload_buf = self.payload()

        crc_buf = header_buf + payload_buf

        crc = _crc16(crc_buf)
        self.header.crc = crc  # TODO this feels wrong

        crc_buf = struct.pack("<H", crc)

        return header_buf[:22] + crc_buf + payload_buf

    @classmethod
    def from_bytes(cls, buf: bytes):
        '''(Internal, do not override) Parse an incoming packet

        Note that this returns an instance of cls, so all subclasses
        will create instances of themselves.

        '''
        header = Header.from_bytes(buf)
        if header.kind != cls.COMMAND:
            raise ValueError(f'from_bytes for type {header.kind:04x}, expected {cls.COMMAND:04x}')

        result = cls(header.seqno,
                     timestamp=header.timestamp,
                     response=header.response,
                     crc=header.crc)

        result.update_payload(buf[24:header.length])

        return result


class AckPacket(BasePacket):
    '''ACK/NAK packet

    This is used primarily for NAK, for instance if you have a bug in
    your CRC implementation.

    It's also used to ACK some packets, but most packets have
    full-blown FOO_RES response packets that come back instead.  And
    some packets get both.

    The sequence number for this packet matches the number of the
    packet it's responding to.

    The main thing to look at for this is `is_success()`, thought it's
    possible that the header's response field is used, as well as the
    unknown bytes in the payload.

    XXX TODO: There are a couple of bytes in here that seem to be zero
    all the time.  No idea what they are.

    '''
    COMMAND = PacketType.ACK

    def __init__(self, seqno, status=None, orig_kind=None, timestamp=0, response=0, crc=None):
        super(AckPacket, self).__init__(seqno, timestamp, response, crc, length=24+5)
        self.status = status
        self.orig_kind = orig_kind
        self.unk_3_5 = 0  # XXX TODO This seems to be 0 in all
                          # generated packets, but inbound is unknown.

    def payload(self):
        return struct.pack(">HBH", self.orig_kind, self.status, self.unk_3_5)

    def update_payload(self, buf):
        self.orig_kind, self.status, self.unk_3_5 = struct.unpack(">HBH", buf)

    def is_success(self):
        return 0 == self.status


class SessionStartPacket(BasePacket):
    '''Start a session (?)

    I'm not entirely sure why this exists, but it's always used before
    sending real commands over.

    It gets both an ACK and a `SessionStartRespPacket` response when
    it succeeds, or can get a NAK AckPacket back.
    '''
    COMMAND = PacketType.SESSION_START

    # We use ISO8859-1 to encode strings because it's basically the
    # same as a binary string.  Every character in the range 0-255
    # maps directly to a byte of the same value, so it round-trips
    # perfectly cleanly.  I get the impression that the device uses
    # ASCII, but 8859-1 is "ASCII with all the high bits, too."  If
    # it's actually UTF-8, I apologize for my ASCII-centrism.
    ENCODING = "ISO8859-1"

    def __init__(self, seqno, host_id, mode_num, version_string, timestamp=0, response=0, crc=None):
        '''Initializer

        host_id: a 32b value, seems to be the first bit of the phone's MAC
        mode_num: seems to be zero for normal operation, and 2 in the diagnostics mode
        '''
        self.version_string = version_string
        version_bytes = self._version_bytes()
        length = 24 + 5 + len(version_bytes) + 1

        super(SessionStartPacket, self).__init__(seqno, timestamp, response, crc, length=length)
        self.host_id = host_id
        self.mode_num = mode_num

    def _version_bytes(self) -> bytes:
        '''(Internal) Helper method to get the version string as bytes

        A running theme in this kind of system is the hassle of moving
        strings around.  Python makes this particularly painful, but
        it's mostly just being honest about the friction between
        human-semantic strings and blessedly agnostic bytes.
        '''
        return bytes(self.version_string, self.ENCODING)

    def payload(self) -> bytes:
        return struct.pack(">LB", self.host_id, self.mode_num) + self._version_bytes() + bytes([0])

    def update_payload(self, buf):
        self.host_id, self.mode_num = struct.unpack(">LB", buf[:5])
        version_bytes = buf[5:-1]
        self.version_string = str(version_bytes, self.ENCODING)

class SessionStartRespPacket(BasePacket):
    '''Reponse to SessionStartPacket

    This is a very incomplete implementation.  We don't actually check
    anything that comes back, and treat its very existence as a
    go-ahead for all further operations.

    TODO: Actually do something with the half-KB splat of crap it sends here

    '''
    COMMAND = PacketType.SESSION_START_RES
    ENCODING = "ISO8859-1"

    def __init__(self, seqno, config=None, timestamp=0, response=0, crc=None):
        length = 24 + 512
        if config is None:
            config = bytes([0]*512)
        super(SessionStartRespPacket, self).__init__(seqno, timestamp, response, crc, length=length)

        self.config = config

    def payload(self):
        return self.config

    def update_payload(self, buf):
        self.config = buf


class ConfigGetPacket(BasePacket):
    '''Queries the band for its configuration.  No args.
    '''
    COMMAND = PacketType.CONFIG

class TechnicalStatusPacket(BasePacket):
    '''Queries the band for its "Technical Status Info".  No args.
    '''
    COMMAND = PacketType.GET_TECHNICAL_STATUS

class SendStoredDataPacket(BasePacket):
    '''Queries the band to start sending over stored data.

    This results in a deluge of data packets.
    '''
    COMMAND = PacketType.SEND_STORED_DATA


class LEDPacket(BasePacket):
    '''Toggle the LED states

    NB: This is used as the base class for other one-byte commands, so
    tread carefully if you need to change things here
    '''
    COMMAND = PacketType.LEDS_CONTROL

    def __init__(self, seqno, value=None, timestamp=0, response=0, crc=None):
        super(LEDPacket, self).__init__(seqno, timestamp, response, crc, length=24+1)
        self.value = value

    def payload(self):
        return bytes([self.value])

    def update_payload(self, buf):
        self.value = buf[0]


class DeviceResetPacket(LEDPacket):
    '''Request a device reset

    This takes a single byte argument, which should be zero AFICT.
    '''
    COMMAND = PacketType.DEVICE_RESET


class IsDevicePairedPacket(BasePacket):
    '''The first packet sent over

    Either gets a NAK or an IsDevicePairedPacket.  If you get a NAK,
    you usually need to reset the band by pulling the battery.
    '''
    COMMAND = PacketType.IS_DEVICE_PAIRED


class IsDevicePairedResponsePacket(BasePacket):
    '''Response to IsDevicePaired request

    The actual data seems to come in via the header's response field,
    as it's only zero if the device if the device isn't yet paired.
    If you send an IDP request while paired, it changes to nonzero.
    '''
    COMMAND = PacketType.IS_DEVICE_PAIRED_RES

    def __init__(self, seqno, value=0, timestamp=0, response=0, crc=None):
        length = 24 + 5

        super(IsDevicePairedResponsePacket, self).__init__(seqno, timestamp, response, crc, length=length)

        self.value = value

    def payload(self):
        return struct.pack(">HHB", 0x2a, self.value, 0)  # XXX TODO what are the other bytes?

    def update_payload(self, buf):
        self.value = struct.unpack(">H", buf[2:4])[0]

    def is_paired(self):
        return self.header.response != 0


class PacketStateMachine:
    '''Packet Parsing State Machine

    This has two external interfaces: a method to throw new buffers of
    bytes into the queue, and then a callback to call any time a
    packet is decoded.

    Every time a buffer is appended, it immediately attempts to decode
    the next packet in queue.  If it finds one, it does the callback,
    then immediately tries to decode any remaining buffers (there
    really shouldn't be any, but just in case).  It loops this way
    until there are no decodable buffers in queue.

    buffers with invalid magic are skipped, in the hopes of finding a
    good one.  This could be changed to move the cursor a single byte
    at a time, but I think the current buf-wise approach is adequate.

    TODO: Handle bad inbound CRC errors
    '''
    def __init__(self, packet_cb):
        '''Create a PacketStateMachine

        packet_cb: the callback to call every time a packet is
        decoded.  This is called with the packet (and that's it).

        This parser does a bit of ugly magic to automatically find the
        appropriate kind of Packet class to decode with and send back.

        If no Packet class exists for a packet of a given kind, it
        dumps the hex of the packet to logging as `ERROR` and moves
        on.  This should give you a good opportunity to eyeball the
        newest packet type and start a decoder for it.
        '''
        self.bufs = []  # List of inbound bufs
        self.pkt_cb = packet_cb

    def _find_packet_type_for_buf(self, kind, root_cls=BasePacket):
        '''(Internal) Magic to find the class for the next packet

        This returns the Packet class to parse the next packet on the
        queue.  It is awful yet beautiful.
        '''
        for cls in root_cls.__subclasses__():
            if cls.COMMAND == kind:
                return cls
            cand = self._find_packet_type_for_buf(kind, cls)
            if cand is not None:
                return cand
        return None

    def _attempt_parse(self):
        '''Attempt a parse of our backlog

        This code assumes that packets don't get concatenated within a
        set of bufs.  This seems to be the case in my experience, but
        my experience is quite limited.  For now, we parse
        buf-at-a-time, not byte-at-a-time, and assume any space left
        over within a buffer after a packet is noise and ignore it.

        Note that this can raise InvalidMagicException, which the
        caller should handle appropriately

        If successful, this returns the packet along with the number
        of buffers that should be dequeued, as a tuple.  If there is
        insufficient data, it returns (None,0)
        '''
        header_buf = self.bufs[0] + self.bufs[1]

        pktlen = Header.peek_len(header_buf)

        # Now string together some bufs
        num_bufs_consumed = 1
        parse_buf = self.bufs[0]

        while len(parse_buf) < pktlen and num_bufs_consumed < len(self.bufs):
            parse_buf += self.bufs[num_bufs_consumed]
            num_bufs_consumed += 1

        if len(parse_buf) < pktlen:
            return (None, 0)

        hdr = Header.from_bytes(parse_buf)

        cls = self._find_packet_type_for_buf(hdr.kind)
        if cls is None:
            logging.error(f'No packet decoder for type {hdr.kind:04x}!')
            logging.error(parse_buf.hex())
            return (None, num_bufs_consumed)

        pkt = cls.from_bytes(parse_buf)

        return (pkt, num_bufs_consumed)

    def rx_buf(self, buf):
        logging.debug('        PSM RX <<<< %s' % buf.hex())
        self.bufs.append(buf)

        # There are no packets that fit within a single byte
        # transaction buffer, so we always wait until we've gotten
        # at least two bufs in before proceeding.
        while len(self.bufs) > 1:
            try:
                pkt, n_used = self._attempt_parse()
                self.bufs = self.bufs[n_used:]

                if pkt is None:
                    # Don't have enough data to parse a packet yet, so
                    # exit until we get enough bufs in queue.
                    return

                if self.pkt_cb:
                    self.pkt_cb(pkt)
                else:
                    logging.debug('Got packet: %s' % pkt)
                # And now resume the while loop, in case we somehow
                # wound up with a bunch of packets pending in queue.

            except InvalidMagicException:
                logging.error('Got a buffer with invalid magic in the head, popping it and retrying')
                self.bufs.pop(0)
                # Re-enter the while loop to recursively pop down
                # noise and parse any packets in queue.


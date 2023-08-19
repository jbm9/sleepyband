# Sleepyband protocol implementation
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This file contains the protocol implementation to speak with the
# device.  It's designed to work via callbacks in and out, so you can
# swap out the BLE layer with something more appropriate to your
# system


from __future__ import annotations

from enum import Enum
import logging

from packets import *


ConnectionState = Enum('ConnectionState', ["DISCONNECTED",  # As labeled
                                           "CONNECTING",    # Starting connection
                                           "CONNECTED"])    # Connected to services

SessionState = Enum('SessionState', ["NOT_STARTED",
                                     "IDP_FAILED",
                                     "IDP_PENDING",
                                     "SS_FAILED",
                                     "SS_PENDING",
                                     "STARTED"])

class ProtocolMachine:
    '''Tracks the state of our current line, and muxes data

    callback for session start confirmation/denial
    callbacks registered for inbound packets
    callbacks registered to hand back response packets

    methods to request actions (send isDevCon, send startsess etc)
    callbacks to when those actions are completed, with disposition

    helper methods to let other parts of the system know what they can do currently

    helper method to allocate sequence ids

    '''
    def __init__(self, ss_callback):
        self.connection_state = ConnectionState.DISCONNECTED
        self.session_state = SessionState.NOT_STARTED
        self.session_state_cb = ss_callback

        self.ble_conn = None
        self.psm = PacketStateMachine(self.on_packet)

        self.packets = {}  # seqno => callbacks for all packets in-flight

        self.seqno = 1  # 0 is reserved for IDP

        self.host_id = 0x1234  # XXX TODO Actually fill this in
        self.version_str = '9' + '\0'*13  # XXX TODO Actually fill this in

    def _seqno(self):
        result = self.seqno
        self.seqno += 1
        return result

    def update_session_state(self, new_state):
        old_state = self.session_state
        self.session_state = new_state
        self.session_state_cb(self, old_state, new_state)

    def in_session(self):
        return self.session_state == SessionState.STARTED

    def set_led_value(self, value, cb):
        seqno = self._seqno()
        self._enqueue(LEDPacket(seqno, value), cb)
        return seqno

    def request_device_reset(self, cb, reason=0):
        logging.debug(f'Requesting device reset, reason={reason}')
        seqno = self._seqno()
        self._enqueue(DeviceResetPacket(seqno, reason), cb)
        return seqno

    def on_packet(self, pkt):
        logging.debug(f'Got packet: {pkt}')

        if pkt.header.kind == PacketType.ACK:
            return self.handle_ack(pkt)
        if pkt.header.kind == PacketType.IS_DEVICE_PAIRED_RES:
            return self.handle_is_device_paired_res(pkt)
        if pkt.header.kind == PacketType.SESSION_START_RES:
            return self.handle_session_start_resp(pkt)

        return None

    def handle_ack(self, pkt):
        '''Handler for ACK packets
        '''
        # grab the seqno, we use it a lot
        seqno = pkt.header.seqno

        if pkt.is_success():
            logging.debug(f'Success for packet {seqno}')
            # XXX TODO optional ACK handler for application
        else:
            logging.debug(f'Got an error for packet {seqno}: {pkt.status}')
            # XXX TODO optional NAK handler for application

        if seqno in self.packets:
            cb = self._lookup_cb(seqno)
            if cb is not None:
                cb(seqno, pkt.is_success(), pkt.header.response)

        del self.packets[seqno]

    def handle_is_device_paired_res(self, pkt):
        if not pkt.is_paired():
            self.update_session_state(SessionState.SS_PENDING)
            resp = SessionStartPacket(self._seqno(), self.host_id, 0, self.version_str)

            def handle_nak(seqno, succeeded, response):
                logging.debug(f'[{seqno}] Got a NAK for SessionStart')
                self.update_session_state(SessionState.SS_FAILED)

            self._enqueue(resp, handle_nak)
        else:
            logging.debug(f'Got a non-successful IDP response: {pkt.response}')
            self.update_session_state(SessionState.IDP_FAILED)
            # XXX TODO Get session state callbacks working, so the app can handle this

    def handle_session_start_resp(self, pkt):
        logging.debug('Got session start response, good to go!')
        self.update_session_state(SessionState.STARTED)
        # XXX TODO Add session start callback

    def _enqueue(self, pkt, cb=None):
        self.ble_conn.enqueue(pkt)
        self.packets[pkt.header.seqno] = (pkt, cb)

    def _lookup_cb(self, seqno):
        return self.packets[seqno][1]

    def handle_idp_ack(self, seqno, succeeded, response):
        if succeeded:
            # Shouldn't ever hit this, actually?
            return

        self.update_session_state(SessionState.IDP_FAILED)
        logging.error(f'got NAK for IDP: {response}')

    def request_idp(self):
        logging.debug(f'Requesting IsDevicePaired')
        self.connection_state = ConnectionState.CONNECTED
        self.update_session_state(SessionState.IDP_PENDING)

        cb = self.handle_idp_ack

        self._enqueue(IsDevicePairedPacket(0), cb)

    def on_connect_success(self, ble_device):
        logging.debug("Connect success")
        self.ble_conn = ble_device

        self.request_idp()

    def on_rx_buf(self, characteristic, buf):
        # Just throw this over to a PacketStateMachine created in __init__
        self.psm.rx_buf(buf)
        pass

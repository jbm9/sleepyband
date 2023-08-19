#!/usr/bin/env python3

# Sleepyband LED blinker
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This demo app connects to a band and then cycles through the LED
# colors.

import logging

import sys
import threading
import time

from band import Band
from sleepyband import ProtocolMachine, SessionState

logging.basicConfig(level=logging.DEBUG)


class BlinkDemoRunner:
    '''Encapsulates an LED-blinking demo

    This connects to the band and then cycles through the LED options.
    '''
    def __init__(self, mac_address=None):
        self.device = None
        self.pm = ProtocolMachine(self.session_state_cb)
        pm = self.pm

        manager, gatt_thread = Band.find_band(self.on_connect_success,
                                              pm.on_rx_buf)

        self.manager = manager
        self.gatt_thread = gatt_thread

        self.led_no = 0

    def on_connect_success(self, ble_device):
        self.device = ble_device
        self.pm.on_connect_success(ble_device)

    def session_state_cb(self, pm, old_state, new_state):
        logging.debug(f'Session state moved from {old_state} to {new_state}')

        if new_state == SessionState.IDP_FAILED:
            def cb(seqno, succeded, response):
                logging.debug(f'Yep, got an ack for reset: {succeded} / 0x{response:04x}')
                self.pm.request_idp()
            self.pm.request_device_reset(cb)

    def blinky_loop(self):
        while True:
            if not self.device:
                time.sleep(0.1)
                continue

            logging.debug(f'loop: {self.device.connected} / {self.device.connerr}')
            if not self.device.connected:
                logging.debug('\t\tretry')
                self.device.connect()

            else:
                if self.pm.in_session():
                    def led_callback(seqno, succeeded, response):
                        logging.debug(f'[{seqno}] Set LEDs to {self.led_no}')

                    seqno = self.pm.set_led_value(self.led_no, led_callback)
                    logging.debug(f'[{seqno}] Attempting to set LEDs to {self.led_no}')
                    self.led_no = (self.led_no + 1) % 4

            time.sleep(1)

    def stop(self):
        self.manager.stop()
        # self.gatt_thread.stop()


blinker = BlinkDemoRunner()
try:
    blinker.blinky_loop()
except KeyboardInterrupt:
    blinker.stop()
    sys.exit(1)

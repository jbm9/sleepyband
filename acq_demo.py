#!/usr/bin/env python3

# Sleepyband Acquisition Demo
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This demo app connects to a band and then tries to start a data
# acquisition run

import logging

import sys
import threading
import time

from sleepyband.band import Band
from sleepyband.protocol_machine import ProtocolMachine, SessionState

logging.basicConfig(level=logging.DEBUG)


class AcqRunner:
    '''Encapsulates a Acquisition demo

    This connects to the band and tries to start an acquisition run
    '''

    def __init__(self, mac_address=None):
        self.device = None
        self.pm = ProtocolMachine(self.session_state_cb)

        manager, gatt_thread = Band.find_band(self.on_connect_success,
                                              self.pm.on_rx_buf)

        self.manager = manager
        self.gatt_thread = gatt_thread

        self.logfile = open(f'acqlog_{int(time.time())}.dump', 'w')

        self.data_captured_file = open(f"acq_session_{int(time.time())}.raw", "wb")

        self.made_request = False

    def on_connect_success(self, ble_device):
        self.device = ble_device
        self.device.attach_traffic_log(self.logfile)
        self.pm.on_connect_success(ble_device)
        self.last_idp = time.time()

    def session_state_cb(self, pm, old_state, new_state):
        logging.debug(f'Session state moved from {old_state} to {new_state}')

        if new_state == SessionState.IDP_FAILED:
            def cb(seqno, succeded, response):
                logging.debug(f'Yep, got an ack for reset: {succeded} / 0x{response:04x}')
                pm.request_idp()
            pm.request_device_reset(cb)

    def loop(self):
        while True:
            if not self.device:
                time.sleep(0.1)
                continue

            self.logfile.flush()

            # logging.debug(f'loop: {self.device.connected} / {self.device.connerr}')
            if not self.device.connected:
                logging.debug('\t\tretry')
                self.device.connect()

            else:
                if not self.made_request and self.pm.in_session():
                    def ssd_callback(seqno, succeeded, response):
                        logging.debug(f'[{seqno}] Start Acq response: {succeeded}/{response}')

                    def chunk_callback(databuf):
                        self.data_captured_file.write(databuf)
                        logging.debug(f'Got data buffer.')

                    seqno = self.pm.request_acquisition_start(ssd_callback, chunk_callback)
                    self.made_request = True
                    logging.debug(f'[{seqno}] Attempting to start acquisition')

            time.sleep(1)

    def stop(self):
        self.manager.stop()
        # self.gatt_thread.stop()


dumper = AcqRunner()
try:
    dumper.loop()
except KeyboardInterrupt:
    dumper.stop()
    sys.exit(1)

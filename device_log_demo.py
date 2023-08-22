#!/usr/bin/env python3

# Sleepyband Stored Log Dumper
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This demo app connects to a band and then requests the log file off
# of it.

import logging

import sys
import threading
import time

from sleepyband.band import Band
from sleepyband.protocol_machine import ProtocolMachine, SessionState

logging.basicConfig(level=logging.DEBUG)


class LogDumperDemoRunner:
    '''Encapsulates a Log Dumper demo

    This connects to the band and asks for the logs it has
    '''
    def __init__(self, mac_address=None):
        self.device = None
        self.pm = ProtocolMachine(self.session_state_cb)

        manager, gatt_thread = Band.find_band(self.on_connect_success,
                                              self.pm.on_rx_buf,
                                              mac_address)

        self.manager = manager
        self.gatt_thread = gatt_thread

        self.request_sent = False
        self.logfile = open(f'log_{int(time.time())}.dump', 'w')

        self.device_log = bytes()
        self.device_log_file = open(f"device_log_{int(time.time())}.raw", "wb")

        self.finished = False

    def on_connect_success(self, ble_device):
        self.device = ble_device
        self.device.attach_traffic_log(self.logfile)
        self.pm.on_connect_success(ble_device)

    def session_state_cb(self, pm, old_state, new_state):
        logging.debug(f'Session state moved from {old_state} to {new_state}')

        if new_state == SessionState.IDP_FAILED:
            def cb(seqno, succeded, response):
                logging.debug(f'Yep, got an ack for reset: {succeded} / 0x{response:04x}')
                pm.request_idp()
            pm.request_device_reset(cb)

    def loop(self):
        while not self.finished:
            if not self.device:
                time.sleep(0.1)
                continue

            self.logfile.flush()

            logging.debug(f'loop: {self.device.connected} / {self.device.connerr}')
            if not self.device.connected:
                logging.debug('\t\tretry')
                self.device.connect()

            else:
                if self.request_sent:
                    time.sleep(1)
                    continue
                
                if self.pm.in_session():
                    def lf_callback(seqno, succeeded, response):
                        logging.debug(f'[{seqno}] Logfile Resp: {succeeded}/{response}')
                        if not succeeded:
                            logging.error(f'TODO Got a NAK for logfile request, but cannot clear cb')

                    def logbuf_cb(logbuf):
                        self.device_log += logbuf
                        self.device_log_file.write(logbuf)
                        if len(logbuf) == 2048:
                            seqno = self.pm.request_log_file(len(self.device_log),
                                                             2048,
                                                             lf_callback,
                                                             logbuf_cb)
                            logging.debug(f'[{seqno}] Getting next page: {len(self.device_log)}')
                        else:
                            self.finished = True
                            self.device_log_file.close()

                    seqno = self.pm.request_log_file(0, 2048, lf_callback, logbuf_cb)
                    self.request_sent = True
                    logging.debug(f'[{seqno}] Attempting to get stored logs')

            time.sleep(1)

    def stop(self):
        self.manager.stop()
        # self.gatt_thread.stop()


mac_address = None
if len(sys.argv) > 1:
    mac_address = sys.argv[1]

dumper = LogDumperDemoRunner(mac_address)
try:
    dumper.loop()
except KeyboardInterrupt:
    pass

dumper.stop()
sys.exit(1)

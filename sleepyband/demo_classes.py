import logging

import sys
import threading
import time

from sleepyband.band import Band
from sleepyband.protocol_machine import ProtocolMachine, SessionState

class BaseRunner:
    '''The most basic demo possible: connect, then do nothing.
    '''

    def __init__(self, mac_address=None, packet_log=None):
        self.device = None
        self.pm = ProtocolMachine(self.session_state_cb)

        manager, gatt_thread = Band.find_band(self.on_connect_success,
                                              self.pm.on_rx_buf)

        self.manager = manager
        self.gatt_thread = gatt_thread

        self._attach_packet_log(packet_log)

        self.finished = False  # Flag used to tell the runner to stop

    def one_loop(self):
        '''Override this: called in the loop when connected

        This is one cycle of the loop when the device is connected and
        the Protocol Machine thinks we're in a session.

        '''
        pass

    def _attach_packet_log(self, packet_log):
        if packet_log is None:
            packet_log = f'devlog_{int(time.time())}.dump'
        self.packet_log = open(packet_log, 'w')

    def on_connect_success(self, ble_device):
        self.device = ble_device
        self.device.attach_traffic_log(self.packet_log)
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
        while not self.finished:
            if not self.device:
                time.sleep(0.1)
                continue

            self.packet_log.flush()

            if not self.device.connected:
                logging.debug('\t\tretry')
                self.device.connect()

            else:
                if self.pm.in_session():
                    self.one_loop()

            time.sleep(1)

    def stop(self):
        self.manager.stop()
        # self.gatt_thread.stop()



class AcqRunner(BaseRunner):
    '''Encapsulates a Acquisition demo

    This connects to the band and tries to start an acquisition run

    TODO Add the 0x200 long header
    '''

    def __init__(self, data_log=None, **kwargs):
        super(AcqRunner, self).__init__(**kwargs)
        self.made_request = False
        self._attach_data_log(data_log)

    def _attach_data_log(self, data_log):
        if data_log is None:
            data_log = f"acq_session_{int(time.time())}.raw"

        self.data_captured_file = open(data_log, "wb")

    def one_loop(self):
        '''Acquisition loop: actually a single kick-off

        This only actually does anything once: when we are first
        connected, it submits the request to start collecting data,
        then it just goes into a busy loop while callbacks handle all
        the data acquisition.
        '''
        if not self.made_request:
            def ssd_callback(seqno, succeeded, response):
                logging.debug(f'[{seqno}] Start Acq response: {succeeded}/{response}')

            def chunk_callback(packet_buf):
                self.data_captured_file.write(packet_buf)
                self.data_captured_file.flush()
                logging.debug(f'Got raw packet buffer.')

            seqno = self.pm.request_acquisition_start(ssd_callback, chunk_callback)
            self.made_request = True
            logging.debug(f'[{seqno}] Attempting to start acquisition')


class DeviceLogRunner(BaseRunner):
    '''Encapsulates a Device Log downloader demo

    This connects to the band and downloads the on-device logfile
    '''

    def __init__(self, device_log=None, **kwargs):
        super(DeviceLogRunner, self).__init__(**kwargs)
        self.made_request = False
        self._attach_device_log(device_log)

    def _attach_device_log(self, device_log):
        if device_log is None:
            device_log = f"device_log_{int(time.time())}.raw"

        self.device_log_file = open(device_log, "wb")
        self.device_log = bytes()
        self.pending_seqnos = set()

    def one_loop(self):
        '''Acquisition loop: actually a single kick-off

        This only actually does anything once: when we are first
        connected, it submits the request to start collecting data,
        then it just goes into a busy loop while callbacks handle all
        the data acquisition.
        '''
        if not self.made_request:
            def lf_callback(seqno, succeeded, response):
                logging.debug(f'[{seqno}] Logfile Resp: {succeeded}/{response}')
                if not succeeded:
                    logging.error(f'TODO Got a NAK for logfile request, but cannot clear cb')
                if seqno not in self.pending_seqnos:
                    logging.err(f'Got duplicate callback for: {seqno}')
                self.pending_seqnos.remove(seqno)

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

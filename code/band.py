# Sleepyband BLE layer implementation
#
# Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>
# Released under the AGPL
#

# This file contains the packet framing and parsing implementation.
# The packet implementations are entirely self-contained, and the
# parser is designed to work via callbacks in and out.
#

import logging
import threading

import gatt

# Turns out these are actually the Nordic UART Service, go fig.
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
RX_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

class BandManager(gatt.DeviceManager):
    '''BLE Manager for discovering and connecting to bands
    '''

    def __init__(self, adapter_name, conn_cb, rx_cb, mac_address=None):
        super(BandManager, self).__init__(adapter_name)
        self.found = False

        self.conn_cb = conn_cb
        self.rx_cb = rx_cb
        self.mac_address = mac_address

    def device_discovered(self, device):
        if self.found:
            return

        if self.mac_address:
            if self.mac_address != device.mac_address:
                logging.debug(f'MAC miss: got {device.mac_address}, expected {self.mac_address}')
                return
        elif not device.alias().startswith("ITAMAR_"):
            logging.debug(f'Name filter miss: got {device.alias()}')
            return

        logging.debug("Discovered [%s] %s" % (device.mac_address, device.alias()))

        result = Band(device.mac_address, self, self.conn_cb, self.rx_cb)
        result.connect()
        self.found = True


class Band(gatt.Device):
    '''The GATT interface
    '''
    def __init__(self, mac_address, manager, conn_cb, rx_cb):
        super().__init__(mac_address=mac_address, manager=manager)

        self.connected = False
        self.connerr = False
        self.enumerated = False

        self.rx_char = None
        self.tx_char = None
        self.rx_service = None

        self.write_pending = False
        self.write_buf = list()   # List of (buf, seqno) tuples, buf <20B long

        self.conn_cb = conn_cb
        self.rx_cb = rx_cb

    @classmethod
    def find_band(cls, conn_cb, rx_cb, mac_address=None, adapter_name='hci0'):
        '''Find your band and connect to it with the given callbacks

        conn_cb: called with the Band instance as an argument when fully connected
        rx_cb: called with the characteristic and value when data comes in

        By "fully connected" for `conn_cb`, we mean that the service
        and characteristic have been fully enumerated and connected
        to, and we're ready to transfer data between the app and the
        device.

        NB: there is currently no callback when we get disconnected.
        This is probably a problem.

        The `rx_cb` is called every time an incoming frame is
        received.  It's the thinnest possible interface for this.  By
        using this, the code on the other side of the API should be
        general enough to work with other BLE implementations.

        To send data, call `enqueue(pkt)`, where `pkt` is a Packet
        object (which has a `to_bytes()` method and a `header`
        attribute that has a `seqno` attribute).  This gets enqueued
        as a sequence of 20 byte chunks (with the input seqno attached
        to each chunk).

        If a write error occurs, the remaining buffers for that seqno
        are popped off the queue, and then we blindly try to continue
        on.

        '''
        manager = BandManager(adapter_name, conn_cb, rx_cb)

        def _connect(mac):
            logging.debug(f'Connecting to {mac}')
            device = Band(mac_address=mac,
                          manager=manager,
                          conn_cb=conn_cb,
                          rx_cb=rx_cb)
            device.connect()

        def run_gatt_manager():
            logging.debug('thread entered')
            if mac_address is None:
                manager.sb_hit_cb = _connect
                logging.debug(f'No MAC, starting discovery')
                manager.start_discovery()
                manager.run()
            else:
                logging.debug(f'Skipping discovery, target MAC={mac_address}')
                manager.run()
                _connect(mac_address)
        logging.debug('find_band starting thread')
        thread = threading.Thread(target=run_gatt_manager)
        thread.start()
        return manager, thread

    def connect_succeeded(self):
        super().connect_succeeded()
        self.connected = True
        logging.debug("[%s] Connected" % (self.mac_address))

    def connect_failed(self, error):
        super().connect_failed(error)
        self.connected = False
        self.connerr = True
        logging.debug("[%s] Connection failed: %s" % (self.mac_address, str(error)))

    def disconnect_succeeded(self):
        '''A BLE disconnect succeeded

        Note that this tends to happen when reconnecting to the device
        without it shutting down.  It's a very normal transitory
        state, and not a real error per se.  We therefore treat it as
        a blip and a cue to poke the manager to reenable discovery.
        '''
        super().disconnect_succeeded()
        self.connected = False
        self.connerr = False
        logging.debug("[%s] Disconnected" % (self.mac_address))
        self.manager.found = False

    def characteristic_value_updated(self, characteristic, value):
        if self.rx_cb:
            self.rx_cb(characteristic, value)
        else:
            logging.debug('\t\tNotification %s: %s' % (characteristic.uuid, value.hex()))

    def _pop_queue(self):
        if self.write_pending:
            raise ValueError("Attempted to enqueue packets with an outstanding write")
        buf = self.write_buf[0][0]
        self.rx_char.write_value(buf)
        logging.debug(f'\tXMIT: {buf.hex()}')
        self.write_pending = True

    def enqueue(self, pkt):
        buf = pkt.to_bytes()
        seqno = pkt.header.seqno

        for i0 in range(0, len(buf), 20):
            i1 = min(i0 + 20, len(buf))
            self.write_buf.append( (buf[i0:i1], seqno) )

#        logging.debug(f'\t\tEnqueue, length now {len(self.write_buf)}: {self.write_buf.hex()}')
        if not self.write_pending:
            self._pop_queue()

    def characteristic_write_value_succeeded(self, characteristic):
        logging.debug(f'\tWrite succ: {characteristic.uuid}')
        self.write_pending = False
        self.write_buf.pop(0)

        if self.write_buf:
            self._pop_queue()

    def characteristic_write_value_failed(self, characteristic, error):
        seqno = self.write_buf[0][1]
        logging.debug(f'\tWrite fail: {characteristic.uuid}: {seqno}: {error} :: clearing packet')

        while self.write_buf and self.write_buf[0][1] == seqno:
            self.write_buf.pop(0)

        self.write_pending = False

        if self.write_buf:
            self._pop_queue()

    def services_resolved(self):
        super().services_resolved()

        logging.debug("[%s] Resolved services" % (self.mac_address))
        for service in self.services:
            if service.uuid == RX_SERVICE_UUID:
                self.rx_service = service

            logging.debug("[%s]  Service [%s]" % (self.mac_address, service.uuid))
            for characteristic in service.characteristics:
                logging.debug("[%s] Characteristic [%s]" % (self.mac_address, characteristic.uuid))
                if characteristic.uuid == RX_CHAR_UUID:
                    self.rx_char = characteristic
                elif characteristic.uuid == TX_CHAR_UUID:
                    self.tx_char = characteristic
                    self.tx_char.enable_notifications()

        self.enumerated = True
        self.connected = True
        if self.conn_cb:
            self.conn_cb(self)

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
                # logging.debug(f'MAC miss: got {device.mac_address}, expected {self.mac_address}')
                return
        elif not device.alias().startswith("ITAMAR_"):
            # logging.debug(f'Name filter miss: got {device.alias()}')
            return

        logging.debug("Discovered [%s] %s" % (device.mac_address, device.alias()))

        result = Band(device.mac_address, self, self.conn_cb, self.rx_cb)
        result.connect()
        self.found = True


class Band(gatt.Device):
    '''The GATT interface

    In general, you want to create these via `Band.find_band()`.  That
    function takes in callbacks for what to do when the connection is
    established and when a buffer is received.  To send packet, use
    `band.enqueue(pkt)` with a Packet object.

    For the details of these, please see the appropriate documentation
    below, or just look at the blink example.

    You can optionally attach a traffic logger via
    `attach_traffic_log`.  This takes a file-like object as an
    argument (it just needs a write method), and logs all data on the
    bus to that file.  This is helpful when working with new packet
    types.  To stop the log, call attach with None.
    '''
    def __init__(self, mac_address, manager, conn_cb, rx_cb):
        super().__init__(mac_address=mac_address, manager=manager)

        self.connected = False  # Are the characteristics connected?
        self.connerr = False    # Have we gotten an error state

        self.rx_char = None
        self.tx_char = None
        self.rx_service = None

        self.write_pending = False  # Do we have a write outstanding?
        self.write_buf = list()   # List of (buf, seqno) tuples

        self.conn_cb = conn_cb
        self.rx_cb = rx_cb

        self.logfile = None

    def attach_traffic_log(self, f):
        '''Dump all traffic to the given file-like object

        This dumps all data into the file passed in.  It will be
        hex-encoded, with a '>' (to band) or '<' (from band) at the
        beginning of each line.

        To stop logging, pass in None
        '''
        self.logfile = f

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

        Note that this doesn't use proper mutexes, and therefore may
        be racy in a few places.  For my purposes, this is fine, and
        it doesn't introduce new ways to hang the program.
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
            manager.start_discovery()  # Needed to find the thing
            manager.run()

            if mac_address is None:
                manager.sb_hit_cb = _connect
                logging.debug(f'No MAC, starting discovery')
            else:
                logging.debug(f'Skipping discovery, target MAC={mac_address}')
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

        self.manager.found = False  # Allow discovery to restart

    def characteristic_value_updated(self, characteristic, value):
        '''(Internal) Inbound buffer callback

        This is called when we have incoming BLE data.  We just
        blindly pass it on to the `rx_cb`, but also log the data.
        '''
        if self.logfile:
            self.logfile.write(f'< {value.hex()}\n')

        if self.rx_cb:
            self.rx_cb(characteristic, value)
        else:
            logging.debug('\t\tNotification %s: %s' % (characteristic.uuid, value.hex()))

    def _attempt_transmit(self):
        '''(Internal) Attempt a write

        This just tries to send the top buffer out to the BLE
        connection.  It also logs data as appropriate.
        '''
        if self.write_pending:
            raise ValueError("Attempted to transmit buffers with an outstanding write")

        buf = self.write_buf[0][0]
        self.rx_char.write_value(buf)

        if self.logfile:
            self.logfile.write(f'> {buf.hex()}\n')

        logging.debug(f'\tXMIT: {buf.hex()}')

        self.write_pending = True

    def enqueue(self, pkt) -> None:
        '''Enqueue a packet-like object

        pkt is a Packet instance, or at least something with a
        `to_bytes()` method and a `header` attribute (with a `seqno`
        attribute).
        '''
        buf = pkt.to_bytes()
        seqno = pkt.header.seqno

        for i0 in range(0, len(buf), 20):
            i1 = min(i0 + 20, len(buf))
            self.write_buf.append((buf[i0:i1], seqno))

        if not self.write_pending:
            self._attempt_transmit()

    def characteristic_write_value_succeeded(self, characteristic):
        '''(Internal) Called when a write succeeds

        This gets called whenever one of our BLE writes succeeds.  We
        use this to pop that buffer off the queue and trigger the
        attempt to write the next one out.

        # TODO Should this have a callback when it completes the write for a given seqno?
        '''
        logging.debug(f'\tWrite succ: {characteristic.uuid}')
        self.write_pending = False
        self.write_buf.pop(0)

        if self.write_buf:
            self._attempt_transmit()

    def characteristic_write_value_failed(self, characteristic, error):
        '''(Internal) Called when a write fails

        We just log this, and then clear the rest of that seqno's
        buffer.

        TODO Should we have a callback when a seqno fails to send?

        XXX TODO Should this retry the write?  I'm honestly not sure
        what "failed" means in this context.

        '''
        seqno = self.write_buf[0][1]
        logging.debug(f'\tWrite fail: {characteristic.uuid}: {seqno}: {error} :: clearing packet')

        while self.write_buf and self.write_buf[0][1] == seqno:
            self.write_buf.pop(0)

        self.write_pending = False

        if self.write_buf:
            # Attempt to keep on truckin'
            self._attempt_transmit()

    def services_resolved(self):
        '''(Internal) Called when services are resolved

        This is called when our BLE link is fully established and we
        can pull out the service and characteristics we care about.

        This is where we chain off to the "fully connected" callback.
        '''
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

        self.connected = True
        if self.conn_cb:
            self.conn_cb(self)

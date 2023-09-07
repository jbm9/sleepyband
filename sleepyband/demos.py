#!/usr/bin/env python3

import logging
import sys

import click

from sleepyband.demo_classes import AcqRunner, DeviceLogRunner


@click.group()
def cli():
    pass


@cli.command()
@click.option('--log-level', default="info")
@click.option('-m', '--mac-address', default=None)
@click.option('-p', '--packet-log', default=None)
@click.option('-o', '--data-log', default=None)
def acquire(log_level, mac_address, packet_log, data_log):
    logging.basicConfig(level=log_level.upper())

    dumper = AcqRunner(data_log, mac_address=mac_address, packet_log=packet_log)
    try:
        dumper.loop()
    except KeyboardInterrupt:
        dumper.stop()
        sys.exit(1)


@cli.command()
@click.option('--log-level', default="info")
@click.option('-m', '--mac-address', default=None)
@click.option('-p', '--packet-log', default=None)
@click.option('-o', '--device-log', default=None)
def device_log(log_level, mac_address, packet_log, device_log):
    logging.basicConfig(level=log_level.upper())

    dumper = DeviceLogRunner(device_log, mac_address=mac_address, packet_log=packet_log)
    try:
        dumper.loop()
    except KeyboardInterrupt:
        dumper.stop()
        sys.exit(1)


if __name__ == '__main__':
    cli()

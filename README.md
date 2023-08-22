# Sleepyband

Sleepyband is a very unofficial, linux-only tool to capture data from
the WatchPat-One disposable sleep study device.

Sleepyband is not affiliated with WatchPat-One or Itamar Medical in
any way, shape, or form.  WatchPat-One (or however they do their trade
dress) and Itamar and probably some other stuff is trademark Itamar
Medical.

Sleepyband is not a medical device, nor should it be used in any sort
of medical setting.  This is entirely for people who want to look at
their own data of themselves sleeping.

Sleepyband is also pretty minimalist, and only supports the things I
need it to.  PRs are welcome, but bear in mind that I don't intend to
offer all that much support for this.

Please note that this is a very pre-release version, and the structure
of the repository will be changing at some point.

## Goal

The goal of sleepyband is to be able to download data from the band
and then extract it out into the appropriate signals.

## Status/Roadmap

There are two main components of this project: data acquisition, then
data analysis.  We currently have data acquisition together, and are
now moving on to pulling apart the data that comes back from the
device.

## Development Tips

The protocol implementation allows you to log all traffic to and from
the device by calling `attach_traffic_log()` on the device interface
instance.  Pass it a file-like object, and it will log all traffic
there in human-readable hex.  This is pretty helpful when trying to
figure out what's going on.

You can use an Adafruit Feather nRF52 board to fake out the
characteristics used by the device, and then use it to play
query/response games with the app to suss out the protocol.  It's
going to look for a device with the name "ITAMAR_abcdN", where abcd is
a hex number.  You probably just want to copy the exact value for your
band, but you can change it and new installs of the app will work with
other numbers.

One helpful feature in the app is that you can tap on the top bar
(with the manufacturer's logo on it) a bunch and it will get into
debug modes.  There is a diagnostic mode available at 10 taps, with
the ultra-secure password "12345678".  From there, you can do lots of
fun poking at the device, and capture even more kinds of packets.  You
will also probably want to put your band onto a network where you can
sniff traffic to figure out what's going on upstream.  In particular,
there are config files that it may want to update from the cloud.
Finally, the app itself creates decent log files, and the device also
creates log files that can be downloaded to your phone in the
diagnostics menu.  From there, you can access the files on your
phone's filesystem via a file manager.

## Contents

sleepyband/ -- The implementation of the library
tests/ -- Unit tests for the library


## Administrivia

Copyright (c) 2023 Josh Myer <josh@joshisanerd.com>

Released under the AGPL

Code of Conduct

Sleepyband uses the "Contributor Covenant" code of conduct (currently
version 1.3.0).  Please review [CODE_OF_CONDUCT.md](Code of Conduct)
before submitting PRs or diffs to the repository.
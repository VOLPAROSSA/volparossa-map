#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Own the two offline Android services and publish the route observations after both exit."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


@contextmanager
def service(args, log):
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def ready(server):
    # Start the owned server before running a client that could fork another.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and server.poll() is None:
        try:
            with socket.create_connection(('127.0.0.1', 5037), timeout=.25):
                return
        except ConnectionRefusedError:
            time.sleep(.05)
    raise RuntimeError('owned ADB server did not start')


def run(trial, mode):
    assert os.getuid() != 0 and Path('/dev/kvm').is_char_device()
    assert [name for _, name in socket.if_nameindex()] == ['lo']
    emu, adb = trial / 'sdk/emulator/emulator', trial / 'sdk/platform-tools/adb'
    subprocess.run([emu, '-accel-check'], check=True, timeout=15)
    with (trial / 'emulator.log').open('xb') as log, service([adb, '-L', 'tcp:5037', 'nodaemon', 'server'], log) as server:
        ready(server)
        with service([emu, '-avd', 'offline-map', '-port', '5554', '-accel', 'on', '-no-window', '-no-audio',
                      '-no-boot-anim', '-no-snapshot', '-gpu', 'software', '-feature', '-Vulkan',
                      '-no-metrics', '-cores', '2', '-memory', '3072'], log):
            print('Starting the installed-app route trial', flush=True)
            subprocess.run([sys.executable, '-B', Path(__file__).with_name('android.py'), adb, trial, mode],
                           stdin=subprocess.DEVNULL, timeout=720, check=True)
            print('Both routes finished. Stopping the emulator and ADB.', flush=True)
    observations = json.loads((trial / 'observations.json').read_bytes())
    observations['emulator_and_adb_stopped'] = True
    with (trial / 'receipt.json').open('x') as receipt:
        receipt.write(json.dumps(observations, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    os.umask(0o077)
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, interrupted)
    run(Path(sys.argv[1]), sys.argv[2])

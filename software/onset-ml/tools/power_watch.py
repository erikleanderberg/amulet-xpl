#!/usr/bin/env python3
"""
power_watch.py -- does the board stay alive when USB goes away?

Scans continuously for the Amulet advertisement. Holds NO connection, so it
cannot itself be the reason the board stops advertising. Prints one line per
second: whether the board is visible on the air, and its RSSI.
"""
import asyncio, time
from bleak import BleakScanner

async def main():
    print("watching for 'Amulet-XPL' advertisements -- unplug USB when ready")
    print(f"{'time':>7}  {'seen':>5}  {'rssi':>5}   note")
    t0 = time.time(); last = None; gone_at = None
    while time.time() - t0 < 900:
        found = None
        try:
            ds = await BleakScanner.discover(timeout=2.0, return_adv=True)
            for _, (d, adv) in ds.items():
                if (adv.local_name or d.name or '').lower().startswith('amulet'):
                    found = adv.rssi
        except Exception as e:
            print(f"  scan error: {e}")
        el = time.time() - t0
        note = ''
        if found is not None and last is not True:
            note = '<<< APPEARED'; gone_at = None
        elif found is None and last is True:
            note = '<<< VANISHED'; gone_at = time.time()
        elif found is None and gone_at and time.time() - gone_at > 20:
            note = f'(gone {time.time()-gone_at:.0f}s)'
        print(f"{el:7.0f}  {'YES' if found is not None else 'no':>5}  "
              f"{found if found is not None else '--':>5}   {note}", flush=True)
        last = (found is not None)
asyncio.run(main())

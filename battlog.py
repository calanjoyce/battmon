#!/usr/bin/env python3
"""Background battery logger for the Cardputer Zero.

Appends one sample every INTERVAL seconds to ~/.local/share/battmon/log.csv
and keeps only the last KEEP_HOURS hours. BattMon reads this file to draw
its history graph.
"""
import os
import time

SUPPLY = "/sys/class/power_supply/bq27220-0"
LOG_DIR = os.path.expanduser("~/.local/share/battmon")
LOG = os.path.join(LOG_DIR, "log.csv")
INTERVAL = 30
KEEP_HOURS = 48
TRIM_EVERY = 120  # samples between trims (~1 h)

FIELDS = ("capacity", "voltage_now", "current_now", "temp", "status")


def read_sample():
    vals = []
    for f in FIELDS:
        try:
            with open(os.path.join(SUPPLY, f)) as fh:
                vals.append(fh.read().strip())
        except OSError:
            vals.append("")
    return vals


def trim():
    cutoff = time.time() - KEEP_HOURS * 3600
    try:
        with open(LOG) as fh:
            lines = fh.readlines()
    except OSError:
        return
    keep = [ln for ln in lines if ln.split(",", 1)[0].isdigit()
            and int(ln.split(",", 1)[0]) >= cutoff]
    tmp = LOG + ".tmp"
    with open(tmp, "w") as fh:
        fh.writelines(keep)
    os.replace(tmp, LOG)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    trim()
    n = 0
    while True:
        line = ",".join([str(int(time.time()))] + read_sample()) + "\n"
        with open(LOG, "a") as fh:
            fh.write(line)
        n += 1
        if n % TRIM_EVERY == 0:
            trim()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

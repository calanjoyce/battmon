#!/usr/bin/env python3
"""Background battery logger for the Cardputer Zero.

Every INTERVAL seconds it appends:

- one line to ~/.local/share/battmon/log.csv (charge, voltage, current,
  temperature, status), which BattMon graphs; and
- one JSON line to ~/.local/share/battmon/usage.jsonl with the average
  battery current over the interval and what the system was doing: screen
  brightness, Wi-Fi / Bluetooth / MeshZero on or off, CPU load and speed,
  and the CPU seconds each process used. BattMon's Usage screen estimates
  from this what the power went on (see usage.py).

Both files keep the last KEEP_HOURS hours.
"""
import json
import os
import subprocess
import time

SUPPLY = "/sys/class/power_supply/bq27220-0"
BACKLIGHT = "/sys/class/backlight/backlight"
CPUFREQ = "/sys/devices/system/cpu/cpufreq/policy0"
LOG_DIR = os.path.expanduser("~/.local/share/battmon")
LOG = os.path.join(LOG_DIR, "log.csv")
USAGE = os.path.join(LOG_DIR, "usage.jsonl")
INTERVAL = 30
CURRENT_EVERY = 5      # battery current is sampled this often and averaged
KEEP_HOURS = 48
TRIM_EVERY = 120       # samples between trims (~1 h)
BT_EVERY = 10          # samples between Bluetooth checks (~5 min)

FIELDS = ("capacity", "voltage_now", "current_now", "temp", "status")
INTERPRETERS = {"python3", "python", "sh", "bash", "perl", "node"}
# kernel threads rename themselves with their current job; group them
KERNEL_PREFIXES = ("kworker", "ksoftirqd", "rcu_", "irq/", "migration",
                   "kthreadd", "kswapd", "jbd2", "mmc", "card")
RENAME = {"M5CardputerZero": "launcher"}
CLK = os.sysconf("SC_CLK_TCK")


def rd(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def read_sample():
    return [rd(os.path.join(SUPPLY, f)) for f in FIELDS]


def drain_ma():
    """Battery current in mA, positive while discharging (None if unknown)."""
    try:
        ua = abs(int(rd(f"{SUPPLY}/current_now")))
    except ValueError:
        return None
    return ua / 1000.0 if rd(f"{SUPPLY}/status") == "Discharging" \
        else -ua / 1000.0


# ---------- system state ----------

def backlight_pct():
    try:
        cur = int(rd(f"{BACKLIGHT}/actual_brightness") or
                  rd(f"{BACKLIGHT}/brightness"))
        mx = int(rd(f"{BACKLIGHT}/max_brightness"))
        return round(cur * 100 / mx)
    except ValueError:
        return 0


def wifi_on():
    return rd("/sys/class/net/wlan0/operstate") == "up"


def bt_on():
    try:
        out = subprocess.run(["bluetoothctl", "show"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return "Powered: yes" in out


def cpu_ticks():
    """(busy, total) jiffies over all cores."""
    f = rd("/proc/stat").split("\n", 1)[0].split()[1:]
    v = [int(x) for x in f]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v) - idle, sum(v)


def freq_time():
    """{kHz: 10 ms units} from cpufreq stats, or None."""
    raw = rd(f"{CPUFREQ}/stats/time_in_state")
    if not raw:
        return None
    out = {}
    for ln in raw.splitlines():
        k, v = ln.split()
        out[int(k)] = int(v)
    return out


def proc_name(pid, comm):
    if comm.startswith(KERNEL_PREFIXES):
        return "kernel"
    if comm in RENAME:
        return RENAME[comm]
    if comm in INTERPRETERS:  # show the script, e.g. zsynth.py
        argv = rd(f"/proc/{pid}/cmdline").split("\0")
        script = next((a for a in argv[1:] if a and not a.startswith("-")),
                      "")
        if script:
            return os.path.basename(script)
    return comm


def proc_ticks():
    """{pid: (name, utime+stime)} for every process."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        st = rd(f"/proc/{pid}/stat")
        if not st:
            continue
        r = st.rfind(")")
        comm = st[st.find("(") + 1:r]
        f = st[r + 2:].split()
        try:
            out[int(pid)] = (proc_name(pid, comm), int(f[11]) + int(f[12]))
        except (IndexError, ValueError):
            continue
    return out


class UsageSampler:
    def __init__(self):
        self.t = time.time()
        self.cpu = cpu_ticks()
        self.freq = freq_time()
        self.procs = proc_ticks()
        self.ma = []
        self.n = 0
        self.bt = bt_on()

    def tick(self):
        """Called every CURRENT_EVERY seconds: sample the current."""
        ma = drain_ma()
        if ma is not None:
            self.ma.append(ma)

    def sample(self):
        """Called every INTERVAL: one usage record for the interval."""
        now = time.time()
        dt = now - self.t
        busy, total = cpu_ticks()
        b0, t0 = self.cpu
        cores = os.cpu_count() or 1
        cpu = (busy - b0) / (total - t0) * cores if total > t0 else 0.0
        freq = freq_time()
        mhz = None
        if freq and self.freq:
            d = {k: freq[k] - self.freq.get(k, 0) for k in freq}
            tot = sum(d.values())
            if tot > 0:
                mhz = sum(k * v for k, v in d.items()) / tot / 1000
        if mhz is None:
            try:
                mhz = int(rd(f"{CPUFREQ}/scaling_cur_freq")) / 1000
            except ValueError:
                mhz = 1000
        procs = proc_ticks()
        per = {}
        for pid, (name, ticks) in procs.items():
            old = self.procs.get(pid)
            # a pid new since last time: count its ticks only if it's young
            # (started during this interval), not its whole earlier life
            used = ticks - old[1] if old else \
                (ticks if ticks <= dt * CLK * (os.cpu_count() or 1) else 0)
            if used > 0:
                per[name] = per.get(name, 0.0) + used / CLK
        self.n += 1
        if self.n % BT_EVERY == 0:
            self.bt = bt_on()
        mesh = any(n in ("meshzerod", "meshtasticd")
                   for n, _ in procs.values())
        ma = self.ma
        rec = {
            "t": int(now), "dt": round(dt, 1),
            "ma": round(sum(ma) / len(ma), 1) if ma else None,
            "chg": int(bool(ma) and sum(ma) / len(ma) < 0),
            "bl": backlight_pct(), "wifi": int(wifi_on()),
            "bt": int(bool(self.bt)), "mesh": int(mesh),
            "cpu": round(cpu, 3), "mhz": round(mhz),
            "p": {k: round(v, 2) for k, v in
                  sorted(per.items(), key=lambda kv: -kv[1])[:12]
                  if v >= 0.05},
        }
        if rec["chg"]:
            rec["ma"] = abs(rec["ma"])
        self.t, self.cpu, self.freq, self.procs, self.ma = \
            now, (busy, total), freq, procs, []
        return rec


def trim(path, key):
    cutoff = time.time() - KEEP_HOURS * 3600
    try:
        with open(path) as fh:
            lines = fh.readlines()
    except OSError:
        return
    keep = [ln for ln in lines if key(ln) is not None and key(ln) >= cutoff]
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.writelines(keep)
    os.replace(tmp, path)


def csv_time(ln):
    head = ln.split(",", 1)[0]
    return int(head) if head.isdigit() else None


def json_time(ln):
    try:
        return json.loads(ln)["t"]
    except (ValueError, KeyError, TypeError):
        return None


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    trim(LOG, csv_time)
    trim(USAGE, json_time)
    sampler = UsageSampler()
    n = 0
    next_rec = time.time() + INTERVAL
    while True:
        sampler.tick()
        if time.time() >= next_rec:
            next_rec += INTERVAL
            if next_rec < time.time():          # woke from a long sleep
                next_rec = time.time() + INTERVAL
            line = ",".join([str(int(time.time()))] + read_sample()) + "\n"
            with open(LOG, "a") as fh:
                fh.write(line)
            rec = sampler.sample()
            if rec["ma"] is not None:
                with open(USAGE, "a") as fh:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            n += 1
            if n % TRIM_EVERY == 0:
                trim(LOG, csv_time)
                trim(USAGE, json_time)
        time.sleep(CURRENT_EVERY)


if __name__ == "__main__":
    main()

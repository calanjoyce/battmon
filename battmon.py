#!/usr/bin/env python3
"""BattMon: full-screen battery monitor for the M5 Cardputer Zero.

Draws straight to the 320x170 RGB565 LCD framebuffer (/dev/fb0) and reads
the built-in keyboard from evdev, the same way the stock APPLaunch apps do.

Two screens (Tab switches): the battery with a history graph, and Usage,
an estimate of what the power went on (screen, CPU and the processes using
it, radios, ...) worked out from battlog.py's usage log by usage.py.

Keys:  1-4  time range (10 min / 1 h / 6 h / 24 h)
       G    toggle graph: charge % / current mA
       Tab  battery / usage screen
       Esc, Q, Backspace  quit
"""
import os
import signal
import struct
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import numpy as np  # noqa: E402
import pygame  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage  # noqa: E402

W, H = 320, 170
FB = os.environ.get("BATTMON_FB", "/dev/fb0")
KBD = os.environ.get(
    "BATTMON_KBD", "/dev/input/by-path/platform-3f804000.i2c-event")
SUPPLY = "/sys/class/power_supply/bq27220-0"
LOG = os.path.expanduser("~/.local/share/battmon/log.csv")

RANGES = [("10m", 600), ("1h", 3600), ("6h", 6 * 3600), ("24h", 24 * 3600)]

# Linux input key codes
K_ESC, K_1, K_2, K_3, K_4, K_BKSP, K_Q, K_G = 1, 2, 3, 4, 5, 14, 16, 34
K_TAB = 15

BG = (12, 14, 18)
PANEL = (28, 32, 40)
GRID = (48, 54, 66)
TEXT = (232, 236, 242)
DIM = (140, 148, 162)
GREEN = (52, 211, 120)
AMBER = (245, 177, 52)
RED = (240, 82, 82)
BLUE = (80, 160, 255)

FONT_DIR = "/usr/share/fonts/truetype/dejavu/"


def font(name, size):
    try:
        return pygame.font.Font(FONT_DIR + name, size)
    except (OSError, FileNotFoundError):
        return pygame.font.Font(None, size + 4)


def read(name, default=None):
    try:
        with open(os.path.join(SUPPLY, name)) as fh:
            return fh.read().strip()
    except OSError:
        return default


def num(name):
    v = read(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def battery():
    b = {
        "pct": num("capacity"),
        "status": read("status", "Unknown"),
        "uv": num("voltage_now"),
        "ua": num("current_now"),
        "temp": num("temp"),
        "tte": num("time_to_empty_now"),
        "ttf": num("time_to_full_now"),
        "health": read("health", "?"),
        "now": num("charge_now"),
        "full": num("charge_full"),
        "cycles": num("cycle_count"),
    }
    # Normalise current sign: + charging, - discharging.
    if b["ua"] is not None:
        mag = abs(b["ua"])
        b["ua"] = -mag if b["status"] == "Discharging" else mag
    return b


def load_history(span):
    cutoff = time.time() - span
    pts = []
    try:
        with open(LOG) as fh:
            for ln in fh:
                p = ln.rstrip("\n").split(",")
                if len(p) < 6 or not p[0].isdigit():
                    continue
                t = int(p[0])
                if t < cutoff:
                    continue
                try:
                    pct = int(p[1])
                    ua = int(p[3])
                except ValueError:
                    continue
                if p[5] == "Discharging":
                    ua = -abs(ua)
                else:
                    ua = abs(ua)
                pts.append((t, pct, ua))
    except OSError:
        pass
    return pts


def fmt_dur(sec):
    if sec is None or sec <= 0:
        return None
    m = int(sec // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def usage_fmt(sec):
    m = int(sec // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m} min"


def level_color(pct):
    if pct is None:
        return DIM
    return GREEN if pct > 40 else AMBER if pct > 15 else RED


class App:
    def __init__(self):
        pygame.font.init()
        self.s = pygame.Surface((W, H))
        self.f_big = font("DejaVuSans-Bold.ttf", 30)
        self.f_mid = font("DejaVuSans-Bold.ttf", 13)
        self.f_sm = font("DejaVuSans.ttf", 10)
        self.f_lbl = font("DejaVuSansCondensed.ttf", 10)
        self.range_i = 1
        self.graph_current = False
        self.live = []  # (t, pct, ua) samples taken while running
        self.hist_cache = (0, None, [])
        self.screen = "battery"
        self.usage_cache = (0, None, None)
        self.fb = os.open(FB, os.O_RDWR)
        try:
            self.kbd = os.open(KBD, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"battmon: no keyboard ({e})", file=sys.stderr)
            self.kbd = None
        self.running = True

    # ---------- input ----------
    def poll_keys(self):
        if self.kbd is None:
            return
        while True:
            try:
                data = os.read(self.kbd, 24 * 16)
            except BlockingIOError:
                return
            except OSError:
                return
            if not data:
                return
            for i in range(0, len(data) - 23, 24):
                _, _, typ, code, val = struct.unpack_from("llHHi", data, i)
                if typ == 1 and val == 1:
                    self.on_key(code)

    def on_key(self, code):
        if code in (K_ESC, K_Q, K_BKSP):
            self.running = False
        elif code in (K_1, K_2, K_3, K_4):
            self.range_i = code - K_1
            self.hist_cache = (0, None, [])
            self.usage_cache = (0, None, None)
        elif code == K_TAB:
            self.screen = "usage" if self.screen == "battery" else "battery"
        elif code == K_G:
            self.graph_current = not self.graph_current

    # ---------- drawing ----------
    def text(self, f, s, color, pos, anchor="topleft"):
        img = f.render(s, True, color)
        r = img.get_rect(**{anchor: pos})
        self.s.blit(img, r)
        return r

    def draw_battery(self, b):
        x, y, w, h = 8, 8, 104, 42
        col = level_color(b["pct"])
        pygame.draw.rect(self.s, TEXT, (x, y, w, h), 2, border_radius=6)
        pygame.draw.rect(self.s, TEXT, (x + w, y + 13, 5, 16),
                         border_radius=2)
        if b["pct"] is not None:
            fw = int((w - 8) * max(0, min(100, b["pct"])) / 100)
            if fw > 0:
                pygame.draw.rect(self.s, col, (x + 4, y + 4, fw, h - 8),
                                 border_radius=3)
        label = f"{b['pct']}%" if b["pct"] is not None else "--%"
        self.text(self.f_big, label, TEXT, (x + w // 2 + 2, y + h // 2 + 1),
                  "center")
        if b["status"] == "Charging":
            bolt = [(0, 0), (-6, 9), (-1, 9), (-4, 18), (5, 6), (0, 6),
                    (3, 0)]
            bx, by = x + w - 2, y - 6
            pygame.draw.polygon(self.s, AMBER, [(bx + px, by + py)
                                                for px, py in bolt])
            pygame.draw.polygon(self.s, BG, [(bx + px, by + py)
                                             for px, py in bolt], 1)

        st = b["status"]
        if st == "Charging":
            sub = f"Charging · full in {fmt_dur(b['ttf'])}" \
                if fmt_dur(b["ttf"]) else "Charging"
        elif st == "Discharging":
            tte = b["tte"]
            if (tte is None or tte <= 0) and b["ua"] and b["now"]:
                tte = b["now"] / abs(b["ua"]) * 3600
            sub = f"{fmt_dur(tte)} left" if fmt_dur(tte) else "On battery"
        else:
            sub = st
        self.text(self.f_sm, sub, DIM, (x + w // 2 + 2, y + h + 4), "midtop")

    def draw_stats(self, b):
        x0, y0 = 128, 6
        cw, ch = 94, 22
        v = b["uv"] / 1e6 if b["uv"] else None
        ma = b["ua"] / 1000 if b["ua"] is not None else None
        watts = v * ma / 1000 if v is not None and ma is not None else None
        cur_col = GREEN if ma and ma > 0 else AMBER if ma else TEXT
        cells = [
            ("VOLTAGE", f"{v:.3f} V" if v else "--", TEXT),
            ("CURRENT", f"{ma:+.0f} mA" if ma is not None else "--",
             cur_col),
            ("POWER", f"{watts:+.2f} W" if watts is not None else "--",
             cur_col),
            ("TEMP", f"{b['temp'] / 10:.1f} °C" if b["temp"] else "--",
             RED if b["temp"] and b["temp"] > 450 else TEXT),
            ("CHARGE mAh", f"{b['now'] // 1000}/{b['full'] // 1000}"
             if b["now"] and b["full"] else "--", TEXT),
            ("HEALTH", f"{b['health']}", GREEN if b["health"] == "Good"
             else AMBER),
        ]
        for i, (lbl, val, col) in enumerate(cells):
            cx = x0 + (i % 2) * cw
            cy = y0 + (i // 2) * ch
            self.text(self.f_lbl, lbl, DIM, (cx, cy))
            self.text(self.f_mid, val, col, (cx, cy + 9))

    def history(self):
        name, span = RANGES[self.range_i]
        now = time.time()
        ts, key, pts = self.hist_cache
        if now - ts > 15 or key != span:
            pts = load_history(span)
            self.hist_cache = (now, span, pts)
        last = pts[-1][0] if pts else 0
        merged = pts + [p for p in self.live if p[0] > last]
        return [p for p in merged if p[0] >= now - span], span, name

    def draw_graph(self):
        gx, gy, gw, gh = 30, 76, 284, 76
        pygame.draw.rect(self.s, PANEL, (gx, gy, gw, gh), border_radius=4)
        pts, span, name = self.history()
        now = time.time()

        if self.graph_current:
            vals = [p[2] / 1000 for p in pts]
            hi = max([abs(v) for v in vals] + [100])
            hi = (int(hi / 100) + 1) * 100
            lo = -hi if any(v < 0 for v in vals) else 0
            ticks = [lo, (lo + hi) / 2, hi] if lo else [0, hi // 2, hi]
            unit = "mA"
            color = BLUE
        else:
            vals = [p[1] for p in pts]
            lo, hi = 0, 100
            ticks = [0, 50, 100]
            unit = "%"
            color = GREEN

        def ypos(v):
            return gy + gh - 3 - (v - lo) / (hi - lo) * (gh - 6)

        for t in ticks:
            yy = int(ypos(t))
            pygame.draw.line(self.s, GRID, (gx + 1, yy), (gx + gw - 2, yy))
            self.text(self.f_lbl, f"{t:.0f}", DIM, (gx - 3, yy), "midright")

        if len(pts) >= 2:
            line = [(gx + 2 + (p[0] - (now - span)) / span * (gw - 4),
                     ypos(v)) for p, v in zip(pts, vals)]
            if not self.graph_current:
                poly = line + [(line[-1][0], gy + gh - 2),
                               (line[0][0], gy + gh - 2)]
                fill = pygame.Surface((W, H), pygame.SRCALPHA)
                pygame.draw.polygon(fill, color + (50,), poly)
                self.s.blit(fill, (0, 0))
            pygame.draw.lines(self.s, color, False, line, 2)
        else:
            self.text(self.f_sm, "collecting history…", DIM,
                      (gx + gw // 2, gy + gh // 2), "center")

        self.text(self.f_lbl, f"{unit} · last {name}", DIM,
                  (gx + gw - 4, gy + 2), "topright")

    def draw_footer(self):
        hint = "1-4 range   G graph   Tab usage   Esc quit"
        self.text(self.f_lbl, hint, DIM, (W // 2, H - 2), "midbottom")
        self.text(self.f_lbl, time.strftime("%H:%M"), DIM, (W - 6, H - 2),
                  "bottomright")

    def render(self):
        b = battery()
        now = time.time()
        if b["pct"] is not None and b["ua"] is not None:
            if not self.live or now - self.live[-1][0] >= 5:
                self.live.append((now, b["pct"], b["ua"]))
                self.live = self.live[-2000:]
        self.s.fill(BG)
        if self.screen == "usage":
            self.draw_usage()
        else:
            self.draw_battery(b)
            self.draw_stats(b)
            self.draw_graph()
            self.draw_footer()
        self.flush()

    # ---------- usage screen ----------
    def usage_summary(self):
        name, span = RANGES[self.range_i]
        ts, key, sm = self.usage_cache
        if sm is None or key != span or time.time() - ts > 20:
            sm = usage.summary(span)
            self.usage_cache = (time.time(), span, sm)
        return sm

    def draw_usage(self):
        name, span = RANGES[self.range_i]
        sm = self.usage_summary()
        self.text(self.f_mid, f"Usage \u00b7 last {name}", TEXT, (8, 4))
        foot = "1-4 range   Tab battery   ~ typical value   Esc"
        if sm["total"] <= 0 or sm["hours"] <= 0:
            self.text(self.f_sm, "collecting usage data\u2026", DIM,
                      (W // 2, 70), "center")
            self.text(self.f_lbl, "The logger records every 30 s while on "
                      "battery.", DIM, (W // 2, 88), "center")
            self.text(self.f_lbl, foot, DIM, (W // 2, H - 2), "midbottom")
            return
        avg = sm["total"] / sm["hours"]
        cover = sm["hours"] * 3600
        info = f"{sm['total']:.0f} mAh \u00b7 avg {avg:.0f} mA"
        if cover < span * 0.9:
            info += f" \u00b7 {usage_fmt(cover)} logged"
        self.text(self.f_sm, info, DIM, (W - 6, 6), "topright")

        rows = []
        for key, label in sorted(usage.PARTS,
                                 key=lambda kv: -sm["parts"][kv[0]]):
            mah = sm["parts"][key]
            if mah <= 0.005 * sm["total"]:
                continue
            typical = key in usage.FEATURES and key not in sm["measured"]
            rows.append((label + (" ~" if typical else ""), mah, typical, 0))
            if key == "cpu":
                procs = sorted(sm["procs"].items(), key=lambda kv: -kv[1])
                for pname, pm in procs[:3]:
                    if pm >= 0.01 * sm["total"]:
                        rows.append((pname[:18], pm, typical, 1))
        top = max(r[1] for r in rows) if rows else 1
        y = 24
        for label, mah, typical, indent in rows[:9]:
            if y > H - 22:
                break
            pct = mah / sm["total"] * 100
            f = self.f_lbl if indent else self.f_sm
            col = DIM if indent else TEXT
            self.text(f, ("   " if indent else "") + label, col, (8, y))
            bx, bw = 132, 104
            w = max(2, int(bw * mah / top))
            bar = (80, 120, 190) if indent else (GRID if typical else BLUE)
            pygame.draw.rect(self.s, PANEL, (bx, y + 3, bw, 7),
                             border_radius=3)
            pygame.draw.rect(self.s, bar, (bx, y + 3, w, 7),
                             border_radius=3)
            self.text(f, f"{pct:.0f}%", col, (272, y), "topright")
            self.text(f, f"{mah:.1f}" if mah < 10 else f"{mah:.0f}", DIM,
                      (W - 6, y), "topright")
            y += 13 if indent else 15
        self.text(self.f_lbl, foot, DIM, (W // 2, H - 2), "midbottom")

    def flush(self):
        a = pygame.surfarray.pixels3d(self.s)  # (W, H, 3)
        r = a[:, :, 0].astype(np.uint16)
        g = a[:, :, 1].astype(np.uint16)
        bl = a[:, :, 2].astype(np.uint16)
        del a
        px = ((r >> 3) << 11) | ((g >> 2) << 5) | (bl >> 3)
        os.pwrite(self.fb, px.T.astype("<u2").tobytes(), 0)

    def run(self):
        next_draw = 0
        while self.running:
            self.poll_keys()
            if time.time() >= next_draw:
                self.render()
                next_draw = time.time() + 1
            time.sleep(0.05)
        # leave a clean screen for the launcher
        self.s.fill((0, 0, 0))
        self.flush()


def main():
    app = App()

    def stop(*_):
        app.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.run()


if __name__ == "__main__":
    main()

# BattMon

A full-screen battery monitor for the M5Stack **Cardputer Zero**, with live
readings and a history graph.

![BattMon](docs/screenshot.png)

## Features

- A large charge gauge that turns green, amber or red with the charge level,
  plus a charging indicator.
- Time to full while charging, or time left on battery.
- Live voltage, current, power, temperature, charge (mAh) and battery health,
  read from the BQ27220 fuel gauge.
- A history graph of charge % or current (mA) over the last 10 min, 1 h, 6 h
  or 24 h.
- A small background logger (`battlog.py`, a systemd user service) records a
  sample every 30 s and keeps 48 hours, so the graph has data even when the
  app isn't open.

## Keys

| Key | Action |
| --- | --- |
| `1`–`4` | graph range: 10 min / 1 h / 6 h / 24 h |
| `G` | switch the graph between charge % and current mA |
| `Esc`, `Q`, `Backspace` | quit |

## How it works

`battmon.py` draws with pygame (dummy video driver) straight to the LCD
framebuffer (`/dev/fb0`, 320×170 RGB565). It reads the keyboard from evdev and
the battery from `/sys/class/power_supply/bq27220-0`. It is launched from the
stock APPLaunch launcher through a `.desktop` entry, like the built-in apps.

The logger writes to `~/.local/share/battmon/log.csv`.

## Install

You need a Cardputer Zero on the official 64-bit Debian 13 image, reachable
over SSH with passwordless `sudo`. From this folder on your computer, run:

```sh
./install.sh user@cardputer-ip
```

This installs the app to `/opt/battmon`, adds **BattMon** to the launcher,
and enables the `battmon-logger` user service. It uses only the Python,
pygame and numpy that ship on the stock image.

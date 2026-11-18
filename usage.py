"""Estimate what used the battery, from battlog.py's usage log.

The Cardputer Zero can only measure the total battery current. The logger
records that every 30 s together with what the system was doing (screen
brightness, radios, CPU load, CPU time per process). This module fits a
simple linear model of the drain,

    mA = base + screen*brightness + wifi*on + bt*on + mesh*on + cpu*busy

and then splits each sample's *measured* current between those parts in
proportion to the model, so the totals always match what the battery saw.
The CPU part is divided between processes by their share of CPU time.

Coefficients that the data can't pin down (say Wi-Fi was never turned off)
are pulled towards typical values, and are marked as such.
"""
import json
import os
import time

import numpy as np

USAGE_LOG = os.path.expanduser("~/.local/share/battmon/usage.jsonl")

# (key, label) for the parts of the model, in display order
PARTS = [("screen", "Screen"), ("cpu", "CPU"), ("wifi", "Wi-Fi"),
         ("mesh", "MeshZero radio"), ("bt", "Bluetooth"),
         ("base", "Always on")]

# Typical values in mA, used where the data can't tell. Rough figures for a
# Pi CM0 board; replaced by measurements as data with variety comes in.
#   screen: per 100% backlight;  cpu: per fully busy core at 1 GHz
PRIOR = {"base": 250.0, "screen": 60.0, "wifi": 40.0, "bt": 5.0,
         "mesh": 30.0, "cpu": 70.0}
# pull towards the prior for a feature the data did vary, in samples' worth
# of evidence: small, so measurements win quickly
PRIOR_WEIGHT = 4.0
FEATURES = ["screen", "wifi", "bt", "mesh", "cpu"]
# a feature counts as "measured" once its value varied this much
MIN_SPREAD = {"screen": 0.2, "wifi": 0.5, "bt": 0.5, "mesh": 0.5, "cpu": 0.3}


def load(span, path=USAGE_LOG, now=None):
    """Samples from the last `span` seconds (dicts, oldest first)."""
    now = now or time.time()
    out = []
    try:
        with open(path) as fh:
            for ln in fh:
                try:
                    s = json.loads(ln)
                except ValueError:
                    continue
                if s.get("t", 0) >= now - span:
                    out.append(s)
    except OSError:
        pass
    return out


def feature_row(s):
    """Model inputs for one sample."""
    mhz = s.get("mhz") or 1000
    return {
        "screen": s.get("bl", 0) / 100.0,
        "wifi": float(bool(s.get("wifi"))),
        "bt": float(bool(s.get("bt"))),
        "mesh": float(bool(s.get("mesh"))),
        # busy cores, scaled by clock speed (power rises with frequency)
        "cpu": s.get("cpu", 0.0) * mhz / 1000.0,
    }


def fit(samples):
    """Fit the model on discharging samples.

    Returns (coef, measured) where coef maps part -> mA and measured is the
    set of parts the data actually determined (the rest lean on PRIOR).
    """
    rows = [(feature_row(s), s["ma"]) for s in samples
            if not s.get("chg") and s.get("ma", 0) > 0]
    if len(rows) < 10:
        return dict(PRIOR), set()
    names = ["base"] + FEATURES
    X = np.array([[1.0] + [r[f] for f in FEATURES] for r, _ in rows])
    y = np.array([ma for _, ma in rows])
    measured = {f for f in FEATURES
                if np.ptp(X[:, 1 + FEATURES.index(f)]) >= MIN_SPREAD[f]}

    # Ridge regression towards the priors:
    #   minimise |X c - y|^2 + sum_i w_i (c_i - prior_i)^2
    # A feature that never varied is held at its prior (huge w); one that
    # did varies freely (small w, worth a few samples of evidence). The
    # base is free, so it takes whatever the features don't explain.
    n = len(rows)
    prior = np.array([PRIOR[k] for k in names])
    w = np.array([1e-6] + [PRIOR_WEIGHT if f in measured else 1e4 * n
                           for f in FEATURES])
    free = list(range(len(names)))
    coef = prior.copy()
    for _ in range(len(names)):                  # no negative costs
        Xf = X[:, free]
        A = Xf.T @ Xf + np.diag(w[free])
        rhs = Xf.T @ (y - X @ np.where(np.isin(range(len(names)), free),
                                       0.0, coef)) + w[free] * prior[free]
        coef[free] = np.linalg.solve(A, rhs)
        neg = [i for i in free if i > 0 and coef[i] < 0]
        if not neg:
            break
        for i in neg:
            coef[i] = 0.0
            free.remove(i)
    coef[0] = max(coef[0], 0.0)
    return {k: float(c) for k, c in zip(names, coef)}, measured


def breakdown(samples, coef):
    """Split the measured drain of each sample into parts and processes.

    Returns (parts, procs, total_mah, hours) where parts maps part -> mAh and
    procs maps process name -> mAh (its share of the CPU part).
    """
    parts = {k: 0.0 for k, _ in PARTS}
    procs = {}
    total = 0.0
    secs = 0.0
    prev_t = None
    for s in samples:
        dt = s.get("dt") or (s["t"] - prev_t if prev_t else 30)
        prev_t = s["t"]
        dt = min(max(dt, 1), 120)  # gaps (device off) don't count
        if s.get("chg") or s.get("ma", 0) <= 0:
            continue
        f = feature_row(s)
        est = {"base": coef["base"]}
        for k in FEATURES:
            est[k] = coef[k] * f[k]
        tot_est = sum(est.values())
        if tot_est <= 0:
            continue
        scale = s["ma"] / tot_est        # make the parts add up to reality
        hours = dt / 3600.0
        for k, v in est.items():
            parts[k] += v * scale * hours
        total += s["ma"] * hours
        secs += dt
        # share the CPU part between processes by CPU seconds
        p = s.get("p") or {}
        busy = sum(p.values())
        cpu_mah = est["cpu"] * scale * hours
        if busy > 0 and cpu_mah > 0:
            for name, sec in p.items():
                procs[name] = procs.get(name, 0.0) + cpu_mah * sec / busy
    return parts, procs, total, secs / 3600.0


def summary(span, path=USAGE_LOG, now=None, fit_span=48 * 3600):
    """Everything the Usage screen needs, for the last `span` seconds.

    The model is fitted on the whole log (more variety, better estimates);
    the breakdown covers just the chosen span.
    """
    now = now or time.time()
    every = load(fit_span, path, now)
    coef, measured = fit(every)
    recent = [s for s in every if s["t"] >= now - span]
    parts, procs, total, hours = breakdown(recent, coef)
    return {"coef": coef, "measured": measured, "parts": parts,
            "procs": procs, "total": total, "hours": hours,
            "samples": len(recent), "fit_samples": len(every)}

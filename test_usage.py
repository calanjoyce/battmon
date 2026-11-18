#!/usr/bin/env python3
"""Check usage.py on synthetic data with known costs.

Runs anywhere with numpy:  python3 test_usage.py
"""
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage  # noqa: E402

TRUE = {"base": 230.0, "screen": 90.0, "wifi": 55.0, "bt": 8.0,
        "mesh": 35.0, "cpu": 110.0}
results = []


def check(name, ok):
    results.append((name, bool(ok)))


def make(n, vary, seed=1, t0=1_800_000_000):
    """n samples, 30 s apart; `vary` lists features that change."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        bl = rnd.choice([0, 20, 50, 100]) if "screen" in vary else 50
        wifi = rnd.random() < 0.7 if "wifi" in vary else True
        bt = rnd.random() < 0.5 if "bt" in vary else True
        mesh = rnd.random() < 0.5 if "mesh" in vary else True
        cpu = rnd.uniform(0.05, 2.5) if "cpu" in vary else 0.3
        mhz = rnd.choice([600, 1000]) if "cpu" in vary else 1000
        procs = {"zsynth.py": cpu * 0.6, "launcher": cpu * 0.3,
                 "pipewire": cpu * 0.1}
        ma = (TRUE["base"] + TRUE["screen"] * bl / 100 + TRUE["wifi"] * wifi
              + TRUE["bt"] * bt + TRUE["mesh"] * mesh
              + TRUE["cpu"] * cpu * mhz / 1000 + rnd.gauss(0, 15))
        out.append({"t": t0 + 30 * i, "dt": 30, "ma": round(ma, 1),
                    "chg": 0, "bl": bl, "wifi": int(wifi), "bt": int(bt),
                    "mesh": int(mesh), "cpu": round(cpu, 3), "mhz": mhz,
                    "p": {k: round(v * 30, 2) for k, v in procs.items()}})
    return out


# 1. everything varies: every cost should come back within ~15%
s = make(800, {"screen", "wifi", "bt", "mesh", "cpu"})
coef, measured = usage.fit(s)
print("fitted:", {k: round(v, 1) for k, v in coef.items()})
for k in ("base", "screen", "wifi", "mesh", "cpu"):
    check(f"recovers {k} ({coef[k]:.0f} vs {TRUE[k]:.0f})",
          abs(coef[k] - TRUE[k]) <= 0.15 * TRUE[k] + 5)
check("bt is small", coef["bt"] < 25)
check("all varied features count as measured",
      measured == {"screen", "wifi", "bt", "mesh", "cpu"})

# 2. Wi-Fi never changes: its cost stays at the prior, flagged unmeasured
s2 = make(400, {"screen", "cpu"}, seed=2)
coef2, measured2 = usage.fit(s2)
check(f"unvaried wifi keeps its prior ({coef2['wifi']:.0f})",
      abs(coef2["wifi"] - usage.PRIOR["wifi"]) < 1)
check("unvaried wifi is not 'measured'", "wifi" not in measured2)
check(f"screen still measured ({coef2['screen']:.0f})",
      abs(coef2["screen"] - TRUE["screen"]) <= 0.2 * TRUE["screen"] + 5)

# 3. breakdown adds up to the measured total, and splits CPU by process
parts, procs, total, hours = usage.breakdown(s, coef)
real = sum(x["ma"] * 30 / 3600 for x in s)
check(f"parts add up to the measured mAh ({sum(parts.values()):.1f} vs "
      f"{real:.1f})", abs(sum(parts.values()) - real) < 0.01 * real)
check("process shares follow CPU time",
      procs["zsynth.py"] > procs["launcher"] > procs["pipewire"]
      and abs(sum(procs.values()) - parts["cpu"]) < 0.01 * parts["cpu"])
check(f"covers {hours:.2f} h", abs(hours - 800 * 30 / 3600) < 0.01)

# 4. charging samples are left out; too little data falls back to priors
s3 = make(50, {"cpu"}, seed=3)
for x in s3[:25]:
    x["chg"] = 1
p3, _, tot3, h3 = usage.breakdown(s3, coef)
check("charging time isn't counted", abs(h3 - 25 * 30 / 3600) < 0.01)
c4, m4 = usage.fit(s3[:5])
check("too few samples -> priors", c4 == usage.PRIOR and not m4)

# 5. summary() reads the JSON-lines log and limits the span
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "usage.jsonl")
    with open(path, "w") as fh:
        for x in s:
            fh.write(json.dumps(x) + "\n")
        fh.write("not json\n")
    now = s[-1]["t"] + 1
    sm = usage.summary(3600, path, now)
    check(f"summary covers the last hour ({sm['samples']} samples)",
          sm["samples"] in (120, 121) and sm["fit_samples"] == len(s))

for name, ok in results:
    print("PASS" if ok else "FAIL", name)
print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok in results) else 1)

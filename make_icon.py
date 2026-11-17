#!/usr/bin/env python3
"""Draw the 90x90 BattMon launcher icon (flat style matching the stock icons)."""
import sys
from PIL import Image, ImageDraw

S = 4  # supersample
W = 90 * S
im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
green = (52, 211, 120, 255)
light = (214, 250, 228, 255)
# battery body and cap
d.rounded_rectangle((10 * S, 26 * S, 74 * S, 64 * S), radius=8 * S,
                    fill=light)
d.rounded_rectangle((74 * S, 37 * S, 82 * S, 53 * S), radius=3 * S,
                    fill=light)
# fill level
d.rounded_rectangle((16 * S, 32 * S, 56 * S, 58 * S), radius=4 * S,
                    fill=green)
# bolt
bolt = [(48, 20), (30, 48), (42, 48), (36, 70), (58, 40), (46, 40), (52, 20)]
d.polygon([(x * S, y * S) for x, y in bolt], fill=(245, 177, 52, 255),
          outline=(30, 34, 42, 255), width=2 * S)
im.resize((90, 90), Image.LANCZOS).save(sys.argv[1] if len(sys.argv) > 1
                                        else "battmon.png")

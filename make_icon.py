# -*- coding: utf-8 -*-
"""Generates assets/ssh_connect.ico (multi-size). Run only to change the icon: python make_icon.py"""
import os
from PIL import Image, ImageDraw

BG, EDGE, ACCENT, OK = (13, 17, 23), (59, 130, 246), (59, 130, 246), (34, 197, 94)
S = 1024  # draw large, downscale for smooth edges


def draw():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = int(S * 0.04)
    d.rounded_rectangle((pad, pad, S - pad, S - pad), radius=int(S * 0.22), fill=EDGE)
    d.rounded_rectangle((pad + 28, pad + 28, S - pad - 28, S - pad - 28), radius=int(S * 0.19), fill=BG)
    w = int(S * 0.075)
    # prompt ">"
    x0, y0, x1, y1, x2 = int(S * .24), int(S * .30), int(S * .47), int(S * .50), int(S * .24)
    d.line([(x0, y0), (x1, y1), (x2, int(S * .70))], fill=OK, width=w, joint="curve")
    for cx, cy in ((x0, y0), (x1, y1), (x2, int(S * .70))):
        d.ellipse((cx - w // 2, cy - w // 2, cx + w // 2, cy + w // 2), fill=OK)
    # cursor "_"
    d.rounded_rectangle((int(S * .52), int(S * .655), int(S * .78), int(S * .655) + w), radius=w // 2, fill=ACCENT)
    return im


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    os.makedirs(out, exist_ok=True)
    big = draw()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(out, "ssh_connect.ico"), format="ICO", sizes=[(s, s) for s in sizes])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(out, "ssh_connect.png"))
    print("ok", os.listdir(out))

"""Generate the Excephalon icon - the Chaosphere in the two-app family palette.

A brain in a spiked wire cage, drawn on transparency: cage and drill bits in the family
gray-green, the brain in the family light pink - the same palette Highdeas's leaf-and-mic
wears, so the two taskbar neighbors read as one designer's work.

The sphere carries the icon's visual size, so it is drawn large: an earlier cut kept the
sphere to 60% of the canvas and the icon sat beside full-bleed taskbar neighbors looking
half their size. Spike tips now graze the canvas edge and the cage fills most of it.

Three outputs:
- ``assets/excephalon.ico`` - what the window, the launcher shortcut and the taskbar use.
  Sizes 16-64 are packed as classic BMP entries (some shell paths render PNG-compressed
  small sizes poorly); only the 256 is PNG, per Windows convention.
- ``assets/excephalon.png`` - the 256px emblem for the README.
- ``assets/voice-log.ico`` - the Voice Log's shortcuts, packed the same way from the same
  microphone its tray icon draws (``excephalon.voice_log_icon``).

Each size is rendered at 16x supersampling and box-averaged down, so no Pillow is needed;
regenerate with::

    .venv\\Scripts\\python.exe tools\\make_icon.py
"""

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

GRAYGREEN = np.array([124.0, 156.0, 124.0])   # the split between his gray and the lurid green
GRAYGREEN_DIM = np.array([88.0, 112.0, 88.0])
LIGHTPINK = np.array([234.0, 182.0, 192.0])
PINK_DEEP = np.array([196.0, 118.0, 132.0])   # the brain's folds, a step into the same pink

SIZES = (16, 24, 32, 48, 64, 256)


def render(n, step=16):
    """The Chaosphere at n x n, drawn big and box-averaged down for smooth edges."""
    big = n * step
    ys, xs = np.mgrid[0:big, 0:big]
    c = (big - 1) / 2
    x, y = xs - c, ys - c
    r = np.hypot(x, y)

    R = big * 0.385           # the cage sphere: the mass that reads as the icon's size
    reach = big * 0.497       # drill tips graze the canvas edge
    line = big * 0.0135
    rgba = np.zeros((big, big, 4), np.float64)  # transparent ground, like the leaf's

    def paint(mask, color):
        for channel, value in enumerate(color):
            rgba[..., channel][mask] = value
        rgba[..., 3][mask] = 255

    core = r <= R * 0.62
    folds = np.sin(x / big * 46 + 2.6 * np.sin(y / big * 33)) > 0.35
    paint(core, LIGHTPINK)
    paint(core & folds, PINK_DEEP)
    paint(core & (np.abs(x) < line * 0.9), PINK_DEEP)

    shell = np.abs(r - R) <= line * 1.35
    paint(shell, GRAYGREEN)
    tilt = 0.30
    for phi in (-1.05, -0.55, 0.0, 0.55, 1.05):
        a = R * np.cos(phi)
        y0 = R * np.sin(phi) * 0.92
        b = max(a * tilt, line * 2.2)
        field = (x / a) ** 2 + ((y - y0) / b) ** 2
        band = (np.abs(field - 1) <= (line * 2.0) / b) & (r <= R + line)
        paint(band, GRAYGREEN_DIM if phi else GRAYGREEN)
    for meridian in (0.28, 0.62, 1.02, 1.45):
        a = max(R * np.cos(meridian), line * 2.2)
        field = (x / a) ** 2 + (y / R) ** 2
        band = (np.abs(field - 1) <= (line * 2.0) / a) & (r <= R + line)
        paint(band, GRAYGREEN_DIM)

    for k in range(16):
        angle = k * np.pi / 8 + np.pi / 16
        ux, uy = np.cos(angle), np.sin(angle)
        along = x * ux + y * uy
        aside = np.abs(-x * uy + y * ux)
        into = along - R * 0.98
        with np.errstate(invalid="ignore"):
            taper = (big * 0.030) * (1 - into / (reach - R * 0.98))
        paint((into >= 0) & (along <= reach) & (aside <= np.maximum(taper, 0)), GRAYGREEN)

    small = rgba.reshape(n, step, n, step, 4).mean(axis=(1, 3))
    return np.round(small).astype(np.uint8)


def png_bytes(rgba):
    height, width = rgba.shape[:2]

    def chunk(kind, payload):
        raw = kind + payload
        return struct.pack(">I", len(payload)) + raw + struct.pack(">I", zlib.crc32(raw))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def bmp_bytes(rgba):
    """A classic 32bpp ICO frame: BITMAPINFOHEADER, BGRA rows bottom-up, then the 1-bit AND
    mask (all zero - the alpha channel already says what is transparent)."""
    height, width = rgba.shape[:2]
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    bgra = rgba[::-1, :, [2, 1, 0, 3]].tobytes()
    mask_row = ((width + 31) // 32) * 4
    return header + bgra + b"\x00" * (mask_row * height)


def pack_ico(frames_by_size, out):
    sizes = sorted(frames_by_size)
    directory = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(directory) + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        frame = frames_by_size[size]
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(frame), offset)
        offset += len(frame)
        blobs += frame
    Path(out).write_bytes(directory + entries + blobs)


def write_icon(draw, out):
    frames = {size: png_bytes(draw(size)) if size == 256 else bmp_bytes(draw(size))
              for size in SIZES}
    pack_ico(frames, out)
    return frames


def main():
    root = Path(__file__).resolve().parents[1]
    frames = write_icon(render, root / "assets/excephalon.ico")
    (root / "assets/excephalon.png").write_bytes(frames[256])
    sys.path.insert(0, str(root / "src"))
    from excephalon.voice_log_icon import draw_icon

    write_icon(lambda size: draw_icon(size, recording=True), root / "assets/voice-log.ico")
    print("wrote assets/excephalon.ico, assets/excephalon.png and assets/voice-log.ico, sizes",
          SIZES)


if __name__ == "__main__":
    main()

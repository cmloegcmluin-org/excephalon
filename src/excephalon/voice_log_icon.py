import numpy as np

GRAYGREEN = (124, 156, 124)
LIGHTPINK = (234, 182, 192)
PINK_DEEP = (196, 118, 132)
IDLE_GRAY = (168, 168, 168)
RECORDING_LIGHT = (228, 48, 48)


def draw_icon(size, *, recording, supersample=8):
    big = size * supersample
    y, x = (np.mgrid[0:big, 0:big] + 0.5) / big
    rgba = np.zeros((big, big, 4))

    def paint(where, color):
        rgba[where, :3] = color
        rgba[where, 3] = 255

    def clear(where):
        rgba[where] = 0

    def stadium(left, right, top, bottom):
        radius = (right - left) / 2
        middle = (left + right) / 2
        nearest_y = np.clip(y, top + radius, bottom - radius)
        return np.hypot(x - middle, y - nearest_y) <= radius

    def ring(cx, cy, inner, outer):
        distance = np.hypot(x - cx, y - cy)
        return (distance >= inner) & (distance <= outer)

    paint(stadium(0.33, 0.67, 0.06, 0.60), LIGHTPINK if recording else IDLE_GRAY)
    paint(ring(0.5, 0.42, 0.22, 0.29) & (y >= 0.42), GRAYGREEN)
    paint((np.abs(x - 0.5) <= 0.035) & (y >= 0.70) & (y <= 0.86), GRAYGREEN)
    paint(stadium(0.28, 0.72, 0.84, 0.92) & (np.abs(y - 0.88) <= 0.04), GRAYGREEN)

    if recording:
        clear(np.hypot(x - 0.80, y - 0.20) <= 0.20)
        paint(np.hypot(x - 0.80, y - 0.20) <= 0.155, RECORDING_LIGHT)
    else:
        along = ((x - 0.16) * 0.64 + (y - 0.10) * 0.80) / np.hypot(0.64, 0.80)
        across = np.abs((x - 0.16) * 0.80 - (y - 0.10) * 0.64) / np.hypot(0.64, 0.80)
        on_the_line = (along >= 0) & (along <= np.hypot(0.64, 0.80))
        clear(on_the_line & (across <= 0.085))
        paint(on_the_line & (across <= 0.045), PINK_DEEP)

    small = rgba.reshape(size, supersample, size, supersample, 4).mean(axis=(1, 3))
    return np.round(small).astype(np.uint8)

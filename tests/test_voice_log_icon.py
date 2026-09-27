import numpy as np

from excephalon.voice_log_icon import RECORDING_LIGHT, draw_icon


def _has(icon, color):
    return bool(np.any(np.all(np.abs(icon[..., :3].astype(int) - color) < 12, axis=-1)
                       & (icon[..., 3] > 200)))


def test_the_icon_wears_the_red_recording_light_only_while_recording():
    assert _has(draw_icon(64, recording=True), RECORDING_LIGHT)
    assert not _has(draw_icon(64, recording=False), RECORDING_LIGHT)

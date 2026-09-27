import threading

import numpy as np
import pytest
import sounddevice as sd

from excephalon.mic import (BackgroundMicrophone, LiveMicrophone, MicrophoneStopped, choose_input_device,
                           microphone_gain, named_microphone, pick_input_device)


def _dev(name, in_ch=2, sr=44100):
    return {"name": name, "max_input_channels": in_ch, "default_samplerate": sr}


class ListSource:
    """A finite frame source for testing: read() hands back preloaded frames then signals end. The
    put(f1), put(f2)... into the queue all happen-before the end signal, so a test that waits on
    `exhausted` KNOWS every frame is already buffered - no sleeps, no flakiness."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.exhausted = threading.Event()
        self.closed = False

    def read(self):
        if not self._frames:
            self.exhausted.set()
            raise EOFError
        return self._frames.pop(0)

    def close(self):
        self.closed = True


def test_background_mic_delivers_every_captured_frame():
    src = ListSource(["a", "b", "c"])
    bg = BackgroundMicrophone(src)

    assert list(bg.frames()) == ["a", "b", "c"]  # the thread buffered them; frames() drains the buffer
    bg.close()


def test_flush_discards_audio_captured_before_listening_began():
    src = ListSource(["a", "b"])
    bg = BackgroundMicrophone(src)
    src.exhausted.wait(timeout=2)  # both frames are now buffered (their puts happen-before this signal)

    bg.flush()  # between-turn backlog - Excephalon's own reply - is thrown away

    assert list(bg.frames()) == []  # nothing replayed into their next turn
    bg.close()


def test_the_buffer_is_bounded_dropping_the_oldest_frames():
    src = ListSource(["a", "b", "c", "d", "e"])
    bg = BackgroundMicrophone(src, max_frames=2)
    src.exhausted.wait(timeout=2)

    assert list(bg.frames()) == ["d", "e"]  # a long between-turn stall can't grow the buffer without bound
    bg.close()


def test_close_stops_the_capture_thread_and_closes_the_source():
    src = ListSource(["a"])
    bg = BackgroundMicrophone(src)

    bg.close()

    assert bg._thread.is_alive() is False
    assert src.closed is True


def test_override_name_substring_wins_and_skips_probing():
    devices = [_dev("Microphone (Headset Adapter)"), _dev("Microphone (Onboard(R) Audio)")]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 1.0, override="onboard")

    assert (idx, name) == (1, "Microphone (Onboard(R) Audio)")
    assert probed == []  # an explicit choice doesn't need to listen to anything


def test_the_liveliest_input_is_chosen():
    devices = [_dev("Dead headset mic"), _dev("Real Mic", in_ch=1), _dev("Speakers", in_ch=0)]
    levels = {0: 0.00001, 1: 0.02}  # headset silent, real mic hears the room

    idx, name = choose_input_device(devices, lambda i: levels[i])

    assert (idx, name) == (1, "Real Mic")  # the silent default is passed over


def test_the_liveliest_is_taken_even_when_the_whole_room_is_quiet():
    # The bug this guards: an absolute threshold found NOTHING in a quiet room, returned None, and
    # the app fell back to the dead OS default (an idle headset) - all-zero audio. A real mic's self-noise
    # still beats a disconnected virtual device, so we always take the liveliest rather than default.
    devices = [_dev("Dead headset mic"), _dev("Real Mic", in_ch=1)]
    levels = {0: 0.00001, 1: 0.0003}  # both quiet, but the real mic is measurably alive

    idx, name = choose_input_device(devices, lambda i: levels[i])

    assert (idx, name) == (1, "Real Mic")


def test_a_device_that_fails_to_open_is_skipped():
    devices = [_dev("Broken"), _dev("Good", in_ch=1)]

    def probe(i):
        if i == 0:
            raise OSError("cannot open device")
        return 0.01

    idx, name = choose_input_device(devices, probe)

    assert (idx, name) == (1, "Good")


def test_each_physical_mic_is_probed_only_once():
    # Windows lists the same mic several times (one per host API); probing each is slow and noisy.
    devices = [_dev("Onboard", sr=44100), _dev("Onboard", sr=48000), _dev("Onboard", sr=16000)]
    probed = []

    choose_input_device(devices, lambda i: probed.append(i) or 0.01)

    assert probed == [0]


def test_output_only_devices_are_never_considered():
    devices = [_dev("Headphones", in_ch=0)]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 1.0)

    assert (idx, name) == (None, None)
    assert probed == []


def test_only_devices_on_the_requested_host_api_are_considered():
    # Windows lists the same mic under several host APIs; some (WDM-KS) can't be opened for blocking
    # reads, so we stick to the OS default's API.
    devices = [
        {"name": "Onboard (WDM-KS)", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 3},
        {"name": "Onboard (MME)", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 0},
    ]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 0.01, hostapi=0)

    assert (idx, name) == (1, "Onboard (MME)")
    assert probed == [1]  # the wrong-host-API entry isn't even opened


def test_a_device_returning_a_non_finite_level_is_ignored():
    devices = [_dev("Glitchy"), _dev("Good", in_ch=1)]

    def probe(i):
        return float("inf") if i == 0 else 0.01  # a garbage buffer can read as absurdly loud

    idx, name = choose_input_device(devices, probe)

    assert (idx, name) == (1, "Good")


def test_a_continuity_iphone_is_never_chosen_and_never_even_probed():
    # macOS lists his iPhone as an input (Continuity), and PROBING it is what makes the phone
    # ring itself awake trying to serve as a microphone - "that's annoying and I won't ever want
    # to do that." It is not a candidate: not probed, not picked, however lively. Only an
    # explicit mic.txt override naming it can reach it.
    devices = [
        {"name": "DOUGLAS's iPhone Microphone", "max_input_channels": 1},
        {"name": "MacBook Neo Microphone", "max_input_channels": 1},
    ]
    probed = []

    def probe(index):
        probed.append(index)
        return 0.5 if index == 0 else 0.1  # the phone reads livelier; it must still lose

    index, name = choose_input_device(devices, probe)

    assert (index, name) == (1, "MacBook Neo Microphone")
    assert probed == [1]  # the phone was never even touched

    index, name = choose_input_device(devices, probe, override="iphone")
    assert index == 0  # asked for BY NAME, it is still reachable


class HeldStream:
    def __init__(self, **settings):
        self.settings = settings
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def close(self):
        self.closed = True

    def hears(self, *samples):
        block = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        self.settings["callback"](block, len(samples), None, None)


def _held_microphone(**options):
    streams = []

    def open_stream(**settings):
        streams.append(HeldStream(**settings))
        return streams[-1]

    return LiveMicrophone(open_stream=open_stream, **options), streams


def test_each_block_arrives_with_the_moment_it_was_heard():
    moments = iter([1_790_000_000.25])
    microphone, [stream] = _held_microphone(clock=lambda: next(moments))

    stream.hears(0.1, -0.2)
    at, block = next(microphone.blocks(stall_after=1.0))

    assert at == 1_790_000_000.25
    assert block.tolist() == pytest.approx([0.1, -0.2])


def test_a_microphone_that_stops_sending_sound_says_so():
    microphone, [stream] = _held_microphone()

    stream.hears(0.1)
    blocks = microphone.blocks(stall_after=0.05)
    next(blocks)

    with pytest.raises(MicrophoneStopped):
        next(blocks)


def test_a_quiet_microphone_is_turned_up_without_clipping_past_full_scale():
    microphone, [stream] = _held_microphone(gain=3.0)

    stream.hears(0.1, -0.5)
    _, block = next(microphone.blocks(stall_after=1.0))

    assert block.tolist() == pytest.approx([0.3, -1.0])


def test_closing_lets_go_of_the_microphone():
    microphone, [stream] = _held_microphone()

    microphone.close()

    assert not stream.running and stream.closed


def test_the_mic_named_in_mic_txt_is_read_without_its_line_ending(tmp_path):
    (tmp_path / "mic.txt").write_text("Brio 101\n", encoding="utf-8")

    assert named_microphone(tmp_path / "mic.txt") == "Brio 101"
    assert named_microphone(tmp_path / "missing.txt") is None


def test_the_boost_in_mic_gain_txt_is_read_and_anything_unreadable_means_none(tmp_path):
    (tmp_path / "mic-gain.txt").write_text("5\n", encoding="utf-8")
    (tmp_path / "garbled.txt").write_text("louder please", encoding="utf-8")

    assert microphone_gain(tmp_path / "mic-gain.txt") == 5.0
    assert microphone_gain(tmp_path / "garbled.txt") == 1.0
    assert microphone_gain(tmp_path / "missing.txt") == 1.0


def test_the_named_mic_is_found_among_the_inputs_the_default_mic_s_system_offers(monkeypatch):
    devices = [dict(_dev("Speakers", in_ch=0), hostapi=0),
               dict(_dev("Microphone (Brio 101)"), hostapi=1),
               dict(_dev("Headset Microphone"), hostapi=0),
               dict(_dev("Microphone (Brio 101)"), hostapi=0)]
    monkeypatch.setattr(sd, "query_devices", lambda index=None: devices if index is None else devices[index])
    monkeypatch.setattr(sd.default, "device", [2, 0])

    assert pick_input_device(override="Brio 101") == (3, "Microphone (Brio 101)")

from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np
import sounddevice as sd

from .dsp import VoiceDSP
from .presets import PRESET_MAP, VoiceParams
from .vbcable import detect_virtual_cable


INTERNAL_SR = 48000
BLOCK_SIZE = 480  # 10.0 ms exact Windows WASAPI quantum
STREAM_LATENCY_SEC = 0.025  # 25 ms safe shared-mode WASAPI buffer (avoids 2ms crackling)


def _linear_resample(x: np.ndarray, out_len: int) -> np.ndarray:
    if len(x) == out_len:
        return x
    if len(x) == 0:
        return np.zeros(out_len, dtype=np.float32)
    xp = np.linspace(0.0, 1.0, len(x), endpoint=False)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
    return np.interp(x_new, xp, x).astype(np.float32)


class RingAudioFIFO:
    """Lock-protected sample-level ring buffer with pre-roll and gentle ASRC drift control."""

    def __init__(self, capacity: int = 16384, target_fill: int = 1440) -> None:
        self.cap = capacity
        self.target = target_fill
        self.buf = np.zeros(capacity, dtype=np.float32)
        self.w = 0
        self.r = 0
        self.avail = 0
        self.primed = False
        self.underruns = 0
        self.overruns = 0
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self.w = 0
            self.r = 0
            self.avail = 0
            self.primed = False

    def push(self, samples: np.ndarray) -> None:
        n = len(samples)
        if n == 0:
            return
        with self._lock:
            if n >= self.cap - 64:
                samples = samples[-(self.cap // 2) :]
                n = len(samples)
                self.w = 0
                self.r = 0
                self.avail = 0
            if self.avail + n > self.cap - 64:
                drop = self.avail + n - self.target
                self.r = (self.r + drop) % self.cap
                self.avail -= drop
                self.overruns += 1

            first = min(n, self.cap - self.w)
            self.buf[self.w : self.w + first] = samples[:first]
            if n > first:
                self.buf[0 : n - first] = samples[first:]
            self.w = (self.w + n) % self.cap
            self.avail += n
            if not self.primed and self.avail >= self.target:
                self.primed = True

    def pop_into(self, out: np.ndarray, gain: float = 1.0) -> bool:
        frames = len(out)
        with self._lock:
            if not self.primed:
                out.fill(0.0)
                return True

            err = self.avail - self.target
            if err > 360:
                consume = min(self.avail, frames + 2)
            elif err < -360 and self.avail >= frames - 2:
                consume = max(1, frames - 2)
            else:
                consume = frames

            if self.avail < consume:
                take = self.avail
                if take > 0:
                    first = min(take, self.cap - self.r)
                    tmp = np.empty(take, dtype=np.float32)
                    tmp[:first] = self.buf[self.r : self.r + first]
                    if take > first:
                        tmp[first:] = self.buf[0 : take - first]
                    fade = np.linspace(gain, 0.0, take, dtype=np.float32)
                    out[:take] = tmp * fade
                out[take:] = 0.0
                self.r = self.w
                self.avail = 0
                self.primed = False
                self.underruns += 1
                return False

            if consume == frames:
                first = min(frames, self.cap - self.r)
                if gain == 1.0:
                    out[:first] = self.buf[self.r : self.r + first]
                    if frames > first:
                        out[first:] = self.buf[0 : frames - first]
                else:
                    out[:first] = self.buf[self.r : self.r + first] * gain
                    if frames > first:
                        out[first:] = self.buf[0 : frames - first] * gain
            else:
                raw = np.empty(consume, dtype=np.float32)
                first = min(consume, self.cap - self.r)
                raw[:first] = self.buf[self.r : self.r + first]
                if consume > first:
                    raw[first:] = self.buf[0 : consume - first]
                out[:] = _linear_resample(raw, frames) * gain

            self.r = (self.r + consume) % self.cap
            self.avail -= consume
            return True


class AudioEngine:
    """Manages low-latency mic capture, DSP processing, virtual cable output, and headphone monitor."""

    def __init__(self) -> None:
        self.dsp = VoiceDSP(sr=INTERNAL_SR)
        self._lock = threading.RLock()

        self.running = False
        self.voice_enabled = True
        self.monitor_enabled = False
        self.active_preset_id = "deep"
        self.custom_params = VoiceParams.from_dict(PRESET_MAP["deep"].params.to_dict())
        self.dsp.set_params(self.custom_params)

        self.input_device_id: int | None = None
        self.output_device_id: int | None = None
        self.monitor_device_id: int | None = None

        self.input_gain_db: float = 0.0
        self.gate_threshold_db: float = -68.0
        self.monitor_volume: float = 0.85
        self.dsp.set_mic_controls(self.input_gain_db, self.gate_threshold_db)

        self._in_stream: sd.InputStream | None = None
        self._out_stream: sd.OutputStream | None = None
        self._mon_stream: sd.OutputStream | None = None

        self._in_sr = INTERNAL_SR
        self._out_sr = INTERNAL_SR
        self._mon_sr = INTERNAL_SR
        self._out_channels = 2
        self._mon_channels = 2

        self._out_fifo = RingAudioFIFO(capacity=16384, target_fill=1440)
        self._mon_fifo = RingAudioFIFO(capacity=16384, target_fill=1440)
        self._out_scratch = np.zeros(2048, dtype=np.float32)
        self._mon_scratch = np.zeros(2048, dtype=np.float32)

        self.last_error: str | None = None

        # Telemetry
        self.in_level = 0.0
        self.out_level = 0.0
        self.tuned_note = ""
        self.spectrum: list[float] = [0.0] * 24

        # 4-second loopback recording test
        self.record_state: str = "idle"  # idle | recording | playing
        self._is_recording = False
        self._record_chunks: list[np.ndarray] = []
        self._record_target_samples = INTERNAL_SR * 4
        self._record_collected = 0
        self._playback_audio: np.ndarray | None = None
        self._playback_pos: int = 0
        self._record_lock = threading.Lock()

        self.auto_select_devices()

    def list_devices(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if refresh:
                was_running = self.running
                if was_running:
                    self.stop()
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
                if was_running:
                    self.auto_select_devices(force_output=False)
                    self.start()

            apis = sd.query_hostapis()
            devices: list[dict[str, Any]] = []
            for idx, dev in enumerate(sd.query_devices()):
                api_name = apis[dev["hostapi"]]["name"]
                if api_name not in ("Windows WASAPI", "Windows DirectSound", "MME"):
                    continue
                devices.append(
                    {
                        "id": idx,
                        "name": dev["name"],
                        "hostapi": dev["hostapi"],
                        "hostapi_name": api_name,
                        "max_input_channels": int(dev["max_input_channels"]),
                        "max_output_channels": int(dev["max_output_channels"]),
                        "default_samplerate": float(dev["default_samplerate"]),
                    }
                )

            api_rank = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2}
            devices.sort(key=lambda d: (api_rank.get(d["hostapi_name"], 9), d["id"]))

            inputs = [d for d in devices if d["max_input_channels"] > 0]
            outputs = [d for d in devices if d["max_output_channels"] > 0]
            vbcable = detect_virtual_cable(devices)

            return {
                "inputs": inputs,
                "outputs": outputs,
                "vbcable": vbcable,
                "selected": {
                    "input_device_id": self.input_device_id,
                    "output_device_id": self.output_device_id,
                    "monitor_device_id": self.monitor_device_id,
                },
            }

    def get_device_key(self, dev_id: int | None) -> dict[str, str] | None:
        if dev_id is None:
            return None
        try:
            dev = sd.query_devices(dev_id)
            api = sd.query_hostapis(dev["hostapi"])["name"]
            return {"name": dev["name"], "hostapi_name": api}
        except Exception:
            return None

    def find_device_by_key(self, key: dict[str, Any] | None, is_input: bool) -> int | None:
        if not isinstance(key, dict) or not key.get("name"):
            return None
        info = self.list_devices(refresh=False)
        pool = info["inputs"] if is_input else info["outputs"]
        target_name = key["name"]
        target_api = key.get("hostapi_name", "Windows WASAPI")
        for d in pool:
            if d["name"] == target_name and d["hostapi_name"] == target_api:
                return d["id"]
        for d in pool:
            if d["name"] == target_name:
                return d["id"]
        return None

    def auto_select_devices(self, force_output: bool = True) -> None:
        info = self.list_devices(refresh=False)
        inputs = info["inputs"]
        outputs = info["outputs"]
        vbcable = info["vbcable"]

        if self.input_device_id is None or not any(d["id"] == self.input_device_id for d in inputs):
            non_cable = [
                d for d in inputs
                if (
                    "cable output" not in d["name"].lower()
                    and "sound mapper" not in d["name"].lower()
                    and "переназначение" not in d["name"].lower()
                )
            ]
            wasapi_mics = [d for d in non_cable if d["hostapi_name"] == "Windows WASAPI"]
            if wasapi_mics:
                usb = [d for d in wasapi_mics if "realtek" not in d["name"].lower()]
                self.input_device_id = (usb[0] if usb else wasapi_mics[0])["id"]
            elif non_cable:
                self.input_device_id = non_cable[0]["id"]
            elif inputs:
                self.input_device_id = inputs[0]["id"]

        if force_output or self.output_device_id is None:
            if vbcable["installed"] and vbcable["recommended_output_id"] is not None:
                self.output_device_id = vbcable["recommended_output_id"]

        if self.monitor_device_id is None or not any(d["id"] == self.monitor_device_id for d in outputs):
            wasapi_out = [
                d for d in outputs
                if d["hostapi_name"] == "Windows WASAPI"
                and "cable" not in d["name"].lower()
                and "voicemeeter" not in d["name"].lower()
            ]
            if wasapi_out:
                hp = [d for d in wasapi_out if "наушник" in d["name"].lower() or "headphone" in d["name"].lower()]
                self.monitor_device_id = (hp[0] if hp else wasapi_out[0])["id"]
            elif outputs:
                self.monitor_device_id = outputs[0]["id"]

    def _pick_samplerate(self, dev_id: int, is_input: bool) -> int:
        dev = sd.query_devices(dev_id)
        default_sr = int(round(dev.get("default_samplerate", INTERNAL_SR)))
        for sr in (INTERNAL_SR, default_sr, 44100):
            try:
                if is_input:
                    sd.check_input_settings(device=dev_id, samplerate=sr, channels=1)
                else:
                    ch = min(2, int(dev["max_output_channels"]))
                    sd.check_output_settings(device=dev_id, samplerate=sr, channels=ch)
                return sr
            except Exception:
                continue
        return default_sr

    def _input_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        mono = indata[:, 0].astype(np.float32, copy=False)
        if self._in_sr != INTERNAL_SR:
            target_len = max(64, int(round(len(mono) * INTERNAL_SR / self._in_sr)))
            mono = _linear_resample(mono, target_len)

        self.in_level = float(min(1.0, math.sqrt(float(np.mean(mono * mono)) + 1e-12) * 3.5))

        processed = self.dsp.process_block(mono)
        self.tuned_note = self.dsp.tuned_note
        self.out_level = float(min(1.0, math.sqrt(float(np.mean(processed * processed)) + 1e-12) * 3.5))

        # 24-band spectrum for reactive UI bars
        if len(processed) >= 128:
            win = processed * np.hanning(len(processed)).astype(np.float32)
            mag = np.abs(np.fft.rfft(win))[:144]
            step = max(1, len(mag) // 24)
            bands = []
            for b in range(24):
                val = float(np.mean(mag[b * step : (b + 1) * step])) / 18.0
                bands.append(min(1.0, val ** 0.65))
            self.spectrum = bands

        if self._is_recording:
            with self._record_lock:
                if self.record_state == "recording":
                    self._record_chunks.append(processed.copy())
                    self._record_collected += len(processed)
                    if self._record_collected >= self._record_target_samples:
                        self._is_recording = False
                        self.record_state = "playing"
                        threading.Thread(target=self._playback_recorded_audio, daemon=True).start()

        if self._out_stream is not None:
            out_blk = (
                processed
                if self._out_sr == INTERNAL_SR
                else _linear_resample(processed, max(32, int(round(len(processed) * self._out_sr / INTERNAL_SR))))
            )
            self._out_fifo.push(out_blk)

        if self._mon_stream is not None and self.monitor_enabled and self.record_state != "playing":
            mon_blk = (
                processed
                if self._mon_sr == INTERNAL_SR
                else _linear_resample(processed, max(32, int(round(len(processed) * self._mon_sr / INTERNAL_SR))))
            )
            self._mon_fifo.push(mon_blk)

    def _out_callback(self, outdata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        buf = self._out_scratch[:frames] if frames <= len(self._out_scratch) else np.empty(frames, dtype=np.float32)
        self._out_fifo.pop_into(buf, gain=1.0)
        for c in range(outdata.shape[1]):
            outdata[:, c] = buf

    def _mon_callback(self, outdata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        if self.record_state == "playing" and self._playback_audio is not None:
            buf = self._playback_audio
            pos = self._playback_pos
            remain = len(buf) - pos
            if remain <= 0:
                outdata.fill(0.0)
                self._playback_audio = None
                self.record_state = "idle"
                return
            take = min(frames, remain)
            seg = buf[pos : pos + take] * self.monitor_volume
            self._playback_pos = pos + take
            if take < frames:
                padded = np.zeros(frames, dtype=np.float32)
                padded[:take] = seg
                seg = padded
                self._playback_audio = None
                self.record_state = "idle"
            for c in range(outdata.shape[1]):
                outdata[:, c] = seg
            return

        if not self.monitor_enabled:
            outdata.fill(0.0)
            self._mon_fifo.clear()
            return

        buf = self._mon_scratch[:frames] if frames <= len(self._mon_scratch) else np.empty(frames, dtype=np.float32)
        self._mon_fifo.pop_into(buf, gain=self.monitor_volume)
        for c in range(outdata.shape[1]):
            outdata[:, c] = buf

    def _playback_recorded_audio(self) -> None:
        try:
            with self._record_lock:
                audio = np.concatenate(self._record_chunks) if self._record_chunks else np.zeros(0, dtype=np.float32)
                self._record_chunks = []
                self._record_collected = 0
            if len(audio) == 0:
                self.record_state = "idle"
                return
            if self._mon_stream is not None:
                if self._mon_sr != INTERNAL_SR:
                    audio = _linear_resample(audio, int(round(len(audio) * self._mon_sr / INTERNAL_SR)))
                self._mon_fifo.clear()
                self._playback_pos = 0
                self._playback_audio = audio
                return
            dev_id = self.monitor_device_id if self.monitor_device_id is not None else self.output_device_id
            if dev_id is not None:
                dev = sd.query_devices(dev_id)
                sr = self._pick_samplerate(dev_id, is_input=False)
                if sr != INTERNAL_SR:
                    audio = _linear_resample(audio, int(round(len(audio) * sr / INTERNAL_SR)))
                ch = min(2, int(dev["max_output_channels"]))
                stereo = np.column_stack([audio] * ch) if ch > 1 else audio[:, None]
                sd.play(stereo, samplerate=sr, device=dev_id, blocking=True)
            self.record_state = "idle"
        except Exception as exc:
            self.last_error = f"Playback test failed: {exc}"
            self.record_state = "idle"

    def start_test_record(self) -> dict[str, Any]:
        if not self.running:
            self.start()
        with self._record_lock:
            if self.record_state != "idle":
                return {"ok": False, "state": self.record_state}
            self._record_chunks = []
            self._record_collected = 0
            self.record_state = "recording"
            self._is_recording = True
        return {"ok": True, "state": "recording", "duration_sec": 4}

    def start(self) -> dict[str, Any]:
        with self._lock:
            self.stop()
            self.last_error = None

            if self.input_device_id is None:
                self.last_error = "No microphone selected."
                return {"ok": False, "error": self.last_error}

            try:
                self._in_sr = self._pick_samplerate(self.input_device_id, is_input=True)
                in_block = BLOCK_SIZE if self._in_sr == INTERNAL_SR else max(64, int(round(BLOCK_SIZE * self._in_sr / INTERNAL_SR)))

                if self.output_device_id is not None:
                    out_dev = sd.query_devices(self.output_device_id)
                    self._out_sr = self._pick_samplerate(self.output_device_id, is_input=False)
                    self._out_channels = min(2, max(1, int(out_dev["max_output_channels"])))
                    out_block = BLOCK_SIZE if self._out_sr == INTERNAL_SR else max(64, int(round(BLOCK_SIZE * self._out_sr / INTERNAL_SR)))
                    target_out = max(960, int(round(self._out_sr * 0.03)))
                    self._out_fifo = RingAudioFIFO(capacity=16384, target_fill=target_out)
                    self._out_stream = sd.OutputStream(
                        device=self.output_device_id,
                        samplerate=self._out_sr,
                        channels=self._out_channels,
                        blocksize=out_block,
                        dtype="float32",
                        latency=STREAM_LATENCY_SEC,
                        callback=self._out_callback,
                    )
                    self._out_stream.start()

                if self.monitor_device_id is not None and self.monitor_device_id != self.output_device_id:
                    mon_dev = sd.query_devices(self.monitor_device_id)
                    self._mon_sr = self._pick_samplerate(self.monitor_device_id, is_input=False)
                    self._mon_channels = min(2, max(1, int(mon_dev["max_output_channels"])))
                    mon_block = BLOCK_SIZE if self._mon_sr == INTERNAL_SR else max(64, int(round(BLOCK_SIZE * self._mon_sr / INTERNAL_SR)))
                    target_mon = max(960, int(round(self._mon_sr * 0.03)))
                    self._mon_fifo = RingAudioFIFO(capacity=16384, target_fill=target_mon)
                    self._mon_stream = sd.OutputStream(
                        device=self.monitor_device_id,
                        samplerate=self._mon_sr,
                        channels=self._mon_channels,
                        blocksize=mon_block,
                        dtype="float32",
                        latency=STREAM_LATENCY_SEC,
                        callback=self._mon_callback,
                    )
                    self._mon_stream.start()

                self._in_stream = sd.InputStream(
                    device=self.input_device_id,
                    samplerate=self._in_sr,
                    channels=1,
                    blocksize=in_block,
                    dtype="float32",
                    latency=STREAM_LATENCY_SEC,
                    callback=self._input_callback,
                )
                self._in_stream.start()
                self.running = True
                return {"ok": True}
            except Exception as exc:
                self.stop()
                self.last_error = f"Failed to start audio stream: {exc}"
                return {"ok": False, "error": self.last_error}

    def stop(self) -> None:
        with self._lock:
            self.running = False
            for attr in ("_in_stream", "_out_stream", "_mon_stream"):
                stream = getattr(self, attr)
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    setattr(self, attr, None)
            self._out_fifo.clear()
            self._mon_fifo.clear()
            self.in_level = 0.0
            self.out_level = 0.0
            self.tuned_note = ""
            self.spectrum = [0.0] * 24

    def set_preset(self, preset_id: str) -> dict[str, Any]:
        with self._lock:
            if preset_id in PRESET_MAP:
                self.active_preset_id = preset_id
                p = VoiceParams.from_dict(PRESET_MAP[preset_id].params.to_dict())
                self.custom_params = p
                self.dsp.set_params(p)
            elif preset_id == "custom":
                self.active_preset_id = "custom"
                self.dsp.set_params(self.custom_params)
            return self.get_state()

    def update_params(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = self.custom_params.to_dict()
            merged.update(patch)
            self.custom_params = VoiceParams.from_dict(merged)
            self.active_preset_id = "custom"
            self.dsp.set_params(self.custom_params)
            return self.get_state()

    def set_voice_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.voice_enabled = bool(enabled)
            self.dsp.set_bypass(not self.voice_enabled)

    def set_monitor_enabled(self, enabled: bool, volume: float | None = None) -> None:
        with self._lock:
            self.monitor_enabled = bool(enabled)
            if volume is not None:
                self.monitor_volume = float(np.clip(volume, 0.0, 1.5))

    def set_mic_controls(self, input_gain_db: float | None = None, gate_threshold_db: float | None = None) -> None:
        with self._lock:
            if input_gain_db is not None:
                self.input_gain_db = float(input_gain_db)
            if gate_threshold_db is not None:
                self.gate_threshold_db = float(gate_threshold_db)
            self.dsp.set_mic_controls(self.input_gain_db, self.gate_threshold_db)

    def set_devices(
        self,
        input_device_id: int | None = None,
        output_device_id: int | None = None,
        monitor_device_id: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if input_device_id is not None:
                self.input_device_id = input_device_id
            if output_device_id is not None:
                self.output_device_id = None if output_device_id < 0 else output_device_id
            if monitor_device_id is not None:
                self.monitor_device_id = None if monitor_device_id < 0 else monitor_device_id
            if self.running:
                return self.start()
            return {"ok": True}

    def get_state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "voice_enabled": self.voice_enabled,
            "monitor_enabled": self.monitor_enabled,
            "monitor_volume": self.monitor_volume,
            "active_preset_id": self.active_preset_id,
            "params": self.custom_params.to_dict(),
            "input_gain_db": self.input_gain_db,
            "gate_threshold_db": self.gate_threshold_db,
            "input_device_id": self.input_device_id,
            "output_device_id": self.output_device_id,
            "monitor_device_id": self.monitor_device_id,
            "record_state": self.record_state,
            "last_error": self.last_error,
        }

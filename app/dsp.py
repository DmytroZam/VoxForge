from __future__ import annotations

import math
import threading
import numpy as np
from scipy import signal
import pedalboard as pb

from .presets import VoiceParams


def _biquad_highpass(fc: float, sr: float, q: float = 0.7071) -> np.ndarray:
    fc = max(20.0, min(fc, sr * 0.45))
    w0 = 2.0 * math.pi * fc / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2.0 * q)
    b0 = (1.0 + cw) * 0.5
    b1 = -(1.0 + cw)
    b2 = (1.0 + cw) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cw
    a2 = 1.0 - alpha
    return np.array([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0], dtype=np.float64)


def _biquad_lowpass(fc: float, sr: float, q: float = 0.7071) -> np.ndarray:
    fc = max(100.0, min(fc, sr * 0.46))
    w0 = 2.0 * math.pi * fc / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2.0 * q)
    b0 = (1.0 - cw) * 0.5
    b1 = 1.0 - cw
    b2 = (1.0 - cw) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cw
    a2 = 1.0 - alpha
    return np.array([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0], dtype=np.float64)


def _biquad_peaking(fc: float, sr: float, gain_db: float, q: float = 1.6) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(40.0, min(fc, sr * 0.45)) / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2.0 * q)
    b0 = 1.0 + alpha * A
    b1 = -2.0 * cw
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cw
    a2 = 1.0 - alpha / A
    return np.array([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0], dtype=np.float64)


def _biquad_lowshelf(fc: float, sr: float, gain_db: float, slope: float = 0.9) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(30.0, min(fc, sr * 0.45)) / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = (sw / 2.0) * math.sqrt((A + 1.0 / A) * (1.0 / slope - 1.0) + 2.0)
    sqA2 = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1.0) - (A - 1.0) * cw + sqA2)
    b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cw)
    b2 = A * ((A + 1.0) - (A - 1.0) * cw - sqA2)
    a0 = (A + 1.0) + (A - 1.0) * cw + sqA2
    a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cw)
    a2 = (A + 1.0) + (A - 1.0) * cw - sqA2
    return np.array([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0], dtype=np.float64)


def _biquad_highshelf(fc: float, sr: float, gain_db: float, slope: float = 0.9) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return np.array([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float64)
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(100.0, min(fc, sr * 0.45)) / sr
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = (sw / 2.0) * math.sqrt((A + 1.0 / A) * (1.0 / slope - 1.0) + 2.0)
    sqA2 = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1.0) + (A - 1.0) * cw + sqA2)
    b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cw)
    b2 = A * ((A + 1.0) + (A - 1.0) * cw - sqA2)
    a0 = (A + 1.0) - (A - 1.0) * cw + sqA2
    a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cw)
    a2 = (A + 1.0) - (A - 1.0) * cw - sqA2
    return np.array([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0], dtype=np.float64)


class StreamingPitchShifter:
    """Ultra-low-latency phase-aligned dual-tap pitch shifter (~15-25 ms latency)."""

    def __init__(self, sr: int = 48000, window_len: int = 1200) -> None:
        self.sr = sr
        self.buf_len = 8192
        self.buf = np.zeros(self.buf_len, dtype=np.float32)
        self.w = 0
        self.win = window_len
        self.search = 300
        self.cmp_len = 192
        self.phase = 0.0
        self.delay0 = 900.0
        self.delay1 = 900.0

    def _read_interp(self, idx_arr: np.ndarray) -> np.ndarray:
        i0 = np.floor(idx_arr).astype(np.int32) % self.buf_len
        frac = (idx_arr - np.floor(idx_arr)).astype(np.float32)
        i1 = (i0 + 1) % self.buf_len
        return self.buf[i0] * (1.0 - frac) + self.buf[i1] * frac

    def _find_aligned_delay(self, ref_delay: float, base_delay: float) -> float:
        ref_delay = float(np.clip(ref_delay, self.cmp_len + 16.0, self.buf_len - 1024.0))
        base_delay = float(np.clip(base_delay, self.search + self.cmp_len + 32.0, self.buf_len - 1024.0))

        ref_idx = (int(self.w - ref_delay) + np.arange(self.cmp_len)) % self.buf_len
        ref_seg = self.buf[ref_idx]
        if float(np.dot(ref_seg, ref_seg)) < 1e-7:
            return base_delay

        start = int(round(self.w - base_delay - self.search))
        total_len = 2 * self.search + self.cmp_len
        cand_idx = (start + np.arange(total_len)) % self.buf_len
        cand = self.buf[cand_idx]
        corr = np.correlate(cand, ref_seg, mode="valid")
        best_offset = int(np.argmax(corr)) - self.search
        new_delay = base_delay - best_offset
        return float(np.clip(new_delay, self.cmp_len + 32.0, self.buf_len - 1024.0))

    def feed_silent(self, x: np.ndarray) -> None:
        n = len(x)
        idx = (self.w + np.arange(n)) % self.buf_len
        self.buf[idx] = x
        self.w = (self.w + n) % self.buf_len

    def process(self, x: np.ndarray, semitones: float, keep_delay: bool = False) -> np.ndarray:
        n = len(x)
        if not keep_delay and abs(semitones) < 0.05:
            self.feed_silent(x)
            return x

        semitones = float(np.clip(semitones, -16.0, 16.0))
        ratio = 1.0 if abs(semitones) < 0.01 else (2.0 ** (semitones / 12.0))
        drift = ratio - 1.0

        # Ensure causal bounds during full sweep window
        if drift > 0.02:
            start_d = 320.0 + abs(drift) * self.win + self.search + self.cmp_len
        elif drift < -0.02:
            start_d = self.search + self.cmp_len + 64.0
        else:
            start_d = 750.0

        out = np.empty(n, dtype=np.float32)
        pos = 0
        while pos < n:
            next_event = 0.5 if self.phase < 0.5 else 1.0
            samples_to_event = int(math.ceil((next_event - self.phase) * self.win))
            chunk = min(n - pos, max(1, samples_to_event))

            w_idx = (self.w + np.arange(chunk)) % self.buf_len
            self.buf[w_idx] = x[pos : pos + chunk]

            t = np.arange(chunk, dtype=np.float32)
            p0 = self.phase + t / self.win

            d0 = np.clip(self.delay0 - drift * t, 4.0, self.buf_len - 512.0)
            d1 = np.clip(self.delay1 - drift * t, 4.0, self.buf_len - 512.0)

            s0 = self._read_interp(self.w + t - d0)
            s1 = self._read_interp(self.w + t - d1)

            g0 = 0.5 * (1.0 - np.cos(2.0 * np.pi * p0))
            g1 = 1.0 - g0
            out[pos : pos + chunk] = s0 * g0 + s1 * g1

            prev_phase = self.phase
            self.w = (self.w + chunk) % self.buf_len
            self.delay0 = float(np.clip(self.delay0 - drift * chunk, 32.0, self.buf_len - 512.0))
            self.delay1 = float(np.clip(self.delay1 - drift * chunk, 32.0, self.buf_len - 512.0))
            self.phase += chunk / self.win

            if self.phase >= 1.0:
                self.phase -= 1.0
                base = self.delay1 if (abs(drift) < 0.02 and 420.0 <= self.delay1 <= 1500.0) else start_d
                self.delay0 = self._find_aligned_delay(self.delay1, base)
            elif prev_phase < 0.5 <= self.phase:
                base = self.delay0 if (abs(drift) < 0.02 and 420.0 <= self.delay0 <= 1500.0) else start_d
                self.delay1 = self._find_aligned_delay(self.delay0, base)

            pos += chunk
        return out


class DelayBuffer:
    """Simple stateful delay line for comb resonators and echo."""

    def __init__(self, max_samples: int) -> None:
        self.buf = np.zeros(max_samples, dtype=np.float32)
        self.size = max_samples
        self.w = 0

    def process_comb(self, x: np.ndarray, delay_samples: int, feedback: float, mix: float) -> np.ndarray:
        if mix <= 0.005:
            return x
        n = len(x)
        d = max(1, min(delay_samples, self.size - 1))
        fb = float(np.clip(feedback, 0.0, 0.92))
        y = np.empty(n, dtype=np.float32)
        # Sample-by-sample inner loop only if d < n; vectorized chunked when d >= n!
        if d >= n:
            r_idx = (self.w - d + np.arange(n)) % self.size
            delayed = self.buf[r_idx]
            wet = x + fb * delayed
            w_idx = (self.w + np.arange(n)) % self.size
            self.buf[w_idx] = np.clip(wet, -2.0, 2.0)
            self.w = (self.w + n) % self.size
            return (1.0 - mix) * x + mix * wet
        # When comb delay < block size (e.g. 5.8ms = 278 samples < 512), step in chunks of size d
        pos = 0
        while pos < n:
            step = min(d, n - pos)
            r_idx = (self.w - d + np.arange(step)) % self.size
            delayed = self.buf[r_idx]
            wet = x[pos : pos + step] + fb * delayed
            w_idx = (self.w + np.arange(step)) % self.size
            self.buf[w_idx] = np.clip(wet, -2.0, 2.0)
            y[pos : pos + step] = wet
            self.w = (self.w + step) % self.size
            pos += step
        return (1.0 - mix) * x + mix * y

    def process_echo(self, x: np.ndarray, delay_samples: int, feedback: float, mix: float) -> np.ndarray:
        if mix <= 0.005:
            return x
        n = len(x)
        d = max(64, min(delay_samples, self.size - 1))
        fb = float(np.clip(feedback, 0.0, 0.85))
        if d >= n:
            r_idx = (self.w - d + np.arange(n)) % self.size
            delayed = self.buf[r_idx]
            w_idx = (self.w + np.arange(n)) % self.size
            self.buf[w_idx] = np.clip(x + fb * delayed, -2.0, 2.0)
            self.w = (self.w + n) % self.size
            return x + mix * delayed
        return x


_AUTOTUNE_SCALES: dict[str, list[int]] = {
    "chromatic": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "major": [0, 2, 4, 5, 7, 9, 11],
    "pentatonic": [0, 3, 5, 7, 10],
}
_NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


class AutotuneAnalyzer:
    """Real-time MPM F0 tracker + hard scale quantizer (808s & Heartbreak style)."""

    def __init__(self, sr: int = 48000) -> None:
        self.sr = sr
        self.ana_len = 1536  # 32ms rolling analysis window
        self.ana_buf = np.zeros(self.ana_len, dtype=np.float32)
        self.curr_shift = 0.0
        self.locked_midi: int | None = None
        self.voiced_hold = 0
        self.tuned_note = ""
        self._lags = np.arange(40, 340)  # 70.6Hz .. 600Hz at 24kHz

    def feed_silent(self, x: np.ndarray) -> None:
        n = len(x)
        if n >= self.ana_len:
            self.ana_buf[:] = x[-self.ana_len :]
        else:
            self.ana_buf[:-n] = self.ana_buf[n:]
            self.ana_buf[-n:] = x
        self.curr_shift = 0.0
        self.locked_midi = None
        self.voiced_hold = 0
        self.tuned_note = ""

    def _detect_f0(self) -> tuple[float, float]:
        # 2x decimation -> 24 kHz for 4x faster autocorrelation
        x = 0.5 * (self.ana_buf[0::2] + self.ana_buf[1::2])
        x = x - float(np.mean(x))
        n = len(x)
        if float(np.dot(x, x)) < 1e-7:
            return 0.0, 0.0

        fft_len = 2048
        spec = np.fft.rfft(x, n=fft_len)
        acf = np.fft.irfft(spec * np.conj(spec), n=fft_len)[:n]

        sq = x * x
        csum = np.concatenate(([0.0], np.cumsum(sq)))
        lags = self._lags
        denom = csum[n - lags] + (csum[n] - csum[lags])
        nsdf = np.where(denom > 1e-8, 2.0 * acf[lags] / denom, 0.0)

        neg_idx = np.where(nsdf < 0.0)[0]
        if len(neg_idx) == 0:
            return 0.0, 0.0
        start = int(neg_idx[0])
        sub = nsdf[start:]
        if len(sub) < 3:
            return 0.0, 0.0
        global_max = float(np.max(sub))
        if global_max < 0.50:
            return 0.0, global_max

        # MPM octave-safe first key maximum >= 0.85 * global_max
        above = sub >= (0.85 * global_max)
        first_idx = int(np.argmax(above))
        end_idx = first_idx
        while end_idx < len(sub) and sub[end_idx] > 0.0:
            end_idx += 1
        pk_rel = first_idx + int(np.argmax(sub[first_idx:end_idx]))
        tau_idx = start + pk_rel

        if 0 < tau_idx < len(nsdf) - 1:
            y0, y1, y2 = float(nsdf[tau_idx - 1]), float(nsdf[tau_idx]), float(nsdf[tau_idx + 1])
            d = 0.5 * (y0 - y2) / (y0 - 2.0 * y1 + y2 + 1e-12)
            tau = float(lags[tau_idx]) + float(np.clip(d, -0.5, 0.5))
        else:
            tau = float(lags[tau_idx])
        return 24000.0 / tau, global_max

    def _quantize_midi(self, midi: float, scale_name: str) -> int:
        scale_notes = _AUTOTUNE_SCALES.get(scale_name, _AUTOTUNE_SCALES["minor"])
        best_note = int(round(midi))
        best_dist = 999.0
        octave_base = int(math.floor(midi / 12.0)) * 12
        for oct_off in (-12, 0, 12):
            for deg in scale_notes:
                cand = octave_base + oct_off + deg
                dist = abs(cand - midi)
                if dist < best_dist:
                    best_dist = dist
                    best_note = cand

        # Small hysteresis prevents boundary chattering on steady notes
        if self.locked_midi is not None and (self.locked_midi % 12) in scale_notes:
            if abs(self.locked_midi - midi) < best_dist + 0.16:
                return self.locked_midi
        return best_note

    def compute_shift(self, x: np.ndarray, amount: float, speed: float, scale_name: str) -> float:
        n = len(x)
        if n >= self.ana_len:
            self.ana_buf[:] = x[-self.ana_len :]
        else:
            self.ana_buf[:-n] = self.ana_buf[n:]
            self.ana_buf[-n:] = x

        f0, clarity = self._detect_f0()
        if f0 > 65.0 and clarity > 0.52:
            midi = 69.0 + 12.0 * math.log2(f0 / 440.0)
            target_midi = self._quantize_midi(midi, scale_name)
            self.locked_midi = target_midi
            corr = float(np.clip((target_midi - midi) * amount, -7.0, 7.0))
            self.voiced_hold = 8
            octave = (target_midi // 12) - 1
            self.tuned_note = f"{_NOTE_NAMES[target_midi % 12]}{octave}"
        else:
            if self.voiced_hold > 0:
                self.voiced_hold -= 1
                corr = self.curr_shift
            else:
                corr = 0.0
                self.locked_midi = None
                self.tuned_note = ""

        # speed=1.0 -> near-instant Kanye 808s stair-step snap
        alpha = 0.14 + 0.81 * (float(np.clip(speed, 0.0, 1.0)) ** 2)
        self.curr_shift = self.curr_shift + alpha * (corr - self.curr_shift)
        return self.curr_shift


class VoiceDSP:
    """Thread-safe real-time vocal DSP chain for Discord / low-latency streaming."""

    def __init__(self, sr: int = 48000) -> None:
        self.sr = sr
        self._lock = threading.Lock()
        self._params = VoiceParams()
        self._bypass = False
        self._input_gain_db = 0.0
        self._gate_threshold_db = -68.0

        # Pitch shifters & Autotune
        self._autotune = AutotuneAnalyzer(sr=sr)
        self._pitch_main = StreamingPitchShifter(sr=sr, window_len=1200)
        self._pitch_sub = StreamingPitchShifter(sr=sr, window_len=1400)
        self.tuned_note = ""

        # 6-stage fixed SOS cascade: [HP1, HP2, LP1, LP2, LowShelf, MidPeak, HighShelf] -> 7 sections
        self._eq_key: tuple[float, ...] | None = None
        self._sos = np.tile(np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]], dtype=np.float64), (7, 1))
        self._zi = np.zeros((7, 2), dtype=np.float64)

        # Modulation & delay state
        self._ring_phase = 0.0
        self._warble_phase = 0.0
        self._warble_buf = np.zeros(2048, dtype=np.float32)
        self._warble_w = 0

        self._comb = DelayBuffer(max_samples=sr // 2)
        self._echo = DelayBuffer(max_samples=sr * 2)

        # Pedalboard stateful FX
        self._chorus = pb.Chorus(rate_hz=1.4, depth=0.35, centre_delay_ms=7.0, feedback=0.15, mix=0.5)
        self._phaser = pb.Phaser(rate_hz=1.1, depth=0.65, centre_frequency_hz=900.0, feedback=0.45, mix=0.65)
        self._reverb = pb.Reverb(room_size=0.6, damping=0.5, wet_level=0.35, dry_level=0.85, width=0.5)

        # Lo-fi / ZOH / glitch / broken phone state
        self._zoh_hold = 0.0
        self._zoh_acc = 0.0
        self._rng = np.random.default_rng(1337)
        self._drop_remaining = 0
        self._drop_mode = 0  # 0=mute, 1=buzz/crackle
        self._glitch_buf = np.zeros(int(sr * 0.06), dtype=np.float32)
        self._glitch_capture_pos = 0
        self._glitch_repeat_left = 0
        self._glitch_play_pos = 0
        self._glitch_slice_len = int(sr * 0.036)

        # Walkie-talkie radio state
        self._radio_speaking = False
        self._radio_tail_samples = 0
        self._beep_samples_left = 0
        self._beep_phase = 0.0
        self._beep_total = int(sr * 0.095)

        # Noise gate envelope
        self._gate_gain = 1.0
        self._gate_hold = 0

        # Preset crossfade ramp to eliminate clicks when changing voices
        self._fade_samples = 0
        self._last_out_tail = 0.0

        self._rebuild_eq_if_needed(self._params)

    def set_params(self, params: VoiceParams) -> None:
        with self._lock:
            self._params = VoiceParams.from_dict(params.to_dict())
            self._fade_samples = 256

    def set_bypass(self, bypass: bool) -> None:
        with self._lock:
            self._bypass = bool(bypass)
            self._fade_samples = 256

    def set_mic_controls(self, input_gain_db: float, gate_threshold_db: float) -> None:
        with self._lock:
            self._input_gain_db = float(np.clip(input_gain_db, -18.0, 24.0))
            self._gate_threshold_db = float(np.clip(gate_threshold_db, -70.0, -12.0))

    def _rebuild_eq_if_needed(self, p: VoiceParams) -> None:
        key = (round(p.hp_hz, 1), round(p.lp_hz, 1), round(p.tone_tilt, 3), round(p.mid_boost_db, 2))
        if key == self._eq_key:
            return
        self._eq_key = key
        low_db = -p.tone_tilt * 7.5
        high_db = p.tone_tilt * 8.5
        self._sos = np.vstack([
            _biquad_highpass(p.hp_hz, self.sr, 0.7071),
            _biquad_highpass(p.hp_hz, self.sr, 0.7071),
            _biquad_lowpass(p.lp_hz, self.sr, 0.7071),
            _biquad_lowpass(p.lp_hz, self.sr, 0.7071),
            _biquad_lowshelf(240.0, self.sr, low_db),
            _biquad_peaking(1450.0, self.sr, p.mid_boost_db, q=1.8),
            _biquad_highshelf(2800.0, self.sr, high_db),
        ])

    def _apply_noise_gate(self, x: np.ndarray, threshold_db: float) -> tuple[np.ndarray, float]:
        rms = float(np.sqrt(np.mean(x * x) + 1e-12))
        rms_db = 20.0 * math.log10(rms + 1e-9)
        if threshold_db <= -68.0:
            self._gate_gain = 1.0
            return x, rms
        is_open = rms_db >= threshold_db
        if is_open:
            self._gate_hold = int(self.sr * 0.16)
            target = 1.0
        elif self._gate_hold > 0:
            self._gate_hold = max(0, self._gate_hold - len(x))
            target = 1.0
        else:
            target = 0.0
        # Smooth ramp across block
        alpha = 0.45 if target > self._gate_gain else 0.12
        next_gain = self._gate_gain + alpha * (target - self._gate_gain)
        ramp = np.linspace(self._gate_gain, next_gain, len(x), dtype=np.float32)
        self._gate_gain = next_gain
        return x * ramp, rms

    def _apply_warble(self, x: np.ndarray, depth: float, rate_hz: float) -> np.ndarray:
        if depth <= 0.01:
            return x
        n = len(x)
        buf_len = len(self._warble_buf)
        w_idx = (self._warble_w + np.arange(n)) % buf_len
        self._warble_buf[w_idx] = x
        t = np.arange(n, dtype=np.float32)
        phases = self._warble_phase + 2.0 * math.pi * rate_hz * t / self.sr
        self._warble_phase = float((phases[-1] + 2.0 * math.pi * rate_hz / self.sr) % (2.0 * math.pi))
        # Delay swings between 1ms and 1 + depth*4 ms
        delay = 48.0 + (0.5 + 0.5 * np.sin(phases)) * (depth * 180.0)
        r = (self._warble_w + t - delay) % buf_len
        i0 = np.floor(r).astype(np.int32)
        frac = r - i0
        i1 = (i0 + 1) % buf_len
        y = self._warble_buf[i0] * (1.0 - frac) + self._warble_buf[i1] * frac
        self._warble_w = (self._warble_w + n) % buf_len
        return y.astype(np.float32)

    def _apply_lofi_crusher(self, x: np.ndarray, rate_hz: float, bits: int) -> np.ndarray:
        y = x
        if rate_hz < self.sr - 1000.0:
            step = max(1.0, self.sr / max(2000.0, rate_hz))
            n = len(y)
            out = np.empty(n, dtype=np.float32)
            acc = self._zoh_acc
            hold = self._zoh_hold
            # Vectorized hold index calculation
            pos_arr = acc + np.arange(n, dtype=np.float64)
            buckets = np.floor(pos_arr / step).astype(np.int64)
            # First sample of each new bucket updates hold
            changes = np.nonzero(np.diff(buckets, prepend=buckets[0] - 1 if acc == 0.0 else -1))[0]
            # Wait: handle continuation cleanly:
            prev_b = int(math.floor((acc - 1.0) / step)) if acc > 0.0 else -1
            if buckets[0] == prev_b:
                out[:] = hold
                first_change = int(np.searchsorted(buckets, prev_b + 1))
            else:
                first_change = 0
            if first_change < n:
                sub_b = buckets[first_change:]
                _, idx = np.unique(sub_b, return_index=True)
                sample_pos = first_change + idx
                # Fill piecewise constant segments
                for k, sp in enumerate(sample_pos):
                    end = sample_pos[k + 1] if k + 1 < len(sample_pos) else n
                    hold = float(y[sp])
                    out[sp:end] = hold
            self._zoh_hold = hold
            self._zoh_acc = float((acc + n) % step)
            y = out
        if bits < 15:
            # Companded mu-law quantization for crisp phone crunch
            mu = 64.0
            comp = np.sign(y) * np.log1p(mu * np.abs(y)) / math.log1p(mu)
            levels = float(2 ** max(3, min(14, bits - 1)))
            q = np.round(comp * levels) / levels
            y = (np.sign(q) * (np.expm1(np.abs(q) * math.log1p(mu)) / mu)).astype(np.float32)
        return y

    def _apply_packet_loss(self, x: np.ndarray, amount: float, speech_rms: float) -> np.ndarray:
        if amount <= 0.01:
            return x
        n = len(x)
        y = x.copy()
        if self._drop_remaining > 0:
            take = min(n, self._drop_remaining)
            if self._drop_mode == 0:
                y[:take] *= 0.04
            else:
                crackle = self._rng.uniform(-0.18, 0.18, size=take).astype(np.float32)
                y[:take] = y[:take] * 0.15 + crackle * (0.4 + 0.6 * amount)
            self._drop_remaining -= take
        elif speech_rms > 0.008 and self._rng.random() < (0.045 * amount):
            self._drop_remaining = int(self._rng.integers(int(self.sr * 0.02), int(self.sr * 0.065)))
            self._drop_mode = 1 if self._rng.random() < 0.4 else 0
        return y

    def _apply_glitch(self, x: np.ndarray, amount: float, speech_rms: float) -> np.ndarray:
        if amount <= 0.01:
            return x
        n = len(x)
        s_len = self._glitch_slice_len
        if self._glitch_repeat_left > 0:
            y = np.empty(n, dtype=np.float32)
            for i in range(n):
                y[i] = self._glitch_buf[self._glitch_play_pos]
                self._glitch_play_pos = (self._glitch_play_pos + 1) % s_len
                self._glitch_repeat_left -= 1
                if self._glitch_repeat_left <= 0:
                    if i + 1 < n:
                        y[i + 1 :] = x[i + 1 :]
                    break
            return y
        # Record rolling slice
        for i in range(n):
            self._glitch_buf[self._glitch_capture_pos] = x[i]
            self._glitch_capture_pos = (self._glitch_capture_pos + 1) % s_len
        if speech_rms > 0.012 and self._rng.random() < (0.04 * amount):
            self._glitch_repeat_left = s_len * int(self._rng.integers(2, 5))
            self._glitch_play_pos = 0
        return x

    def _apply_radio_fx(self, x: np.ndarray, amount: float, speech_rms: float) -> np.ndarray:
        if amount <= 0.01:
            self._radio_speaking = False
            return x
        n = len(x)
        y = x.copy()
        speaking_now = speech_rms > 0.009
        if speaking_now:
            self._radio_speaking = True
            self._radio_tail_samples = int(self.sr * 0.14)
            # Subtle background radio hiss while talking
            hiss = self._rng.normal(0.0, 0.014 * amount, size=n).astype(np.float32)
            y += hiss
        elif self._radio_tail_samples > 0:
            take = min(n, self._radio_tail_samples)
            hiss = self._rng.normal(0.0, 0.032 * amount, size=take).astype(np.float32)
            y[:take] += hiss
            self._radio_tail_samples -= take
            if self._radio_tail_samples == 0 and self._radio_speaking:
                self._radio_speaking = False
                self._beep_samples_left = self._beep_total
                self._beep_phase = 0.0

        if self._beep_samples_left > 0:
            take = min(n, self._beep_samples_left)
            elapsed = self._beep_total - self._beep_samples_left
            t_idx = elapsed + np.arange(take, dtype=np.float32)
            # Two-tone roger beep: 1650 Hz then 2200 Hz
            freqs = np.where(t_idx < self._beep_total * 0.45, 1650.0, 2200.0)
            phases = self._beep_phase + np.cumsum(2.0 * math.pi * freqs / self.sr)
            self._beep_phase = float(phases[-1] % (2.0 * math.pi))
            env = np.sin(math.pi * np.clip(t_idx / self._beep_total, 0.0, 1.0))
            beep = 0.16 * amount * env * np.sin(phases)
            y[:take] += beep.astype(np.float32)
            self._beep_samples_left -= take
        return y

    def process_block(self, mono_in: np.ndarray) -> np.ndarray:
        with self._lock:
            p = self._params
            bypass = self._bypass
            in_gain_db = self._input_gain_db
            gate_db = self._gate_threshold_db
            fade_left = self._fade_samples
            self._fade_samples = 0

        x = mono_in.astype(np.float32, copy=True)
        if abs(in_gain_db) > 0.05:
            x *= 10.0 ** (in_gain_db / 20.0)

        x, speech_rms = self._apply_noise_gate(x, gate_db)

        if bypass:
            self._autotune.feed_silent(x)
            self._pitch_main.feed_silent(x)
            self._pitch_sub.feed_silent(x)
            self.tuned_note = ""
            y = np.tanh(x * 0.92).astype(np.float32)
            return y

        # 1. Autotune + Pitch shift (+ optional sub-octave layer for Demon / Giant)
        if p.autotune_amount > 0.01:
            at_shift = self._autotune.compute_shift(x, p.autotune_amount, p.autotune_speed, p.autotune_scale)
            self.tuned_note = self._autotune.tuned_note
        else:
            self._autotune.feed_silent(x)
            self.tuned_note = ""
            at_shift = 0.0

        main_shifted = self._pitch_main.process(
            x, p.pitch_semitones + at_shift, keep_delay=(p.autotune_amount > 0.01)
        )
        if p.sub_octave_mix > 0.01:
            sub_shifted = self._pitch_sub.process(x, -11.0)
            y = (1.0 - 0.35 * p.sub_octave_mix) * main_shifted + p.sub_octave_mix * 0.65 * sub_shifted
        else:
            self._pitch_sub.feed_silent(x)
            y = main_shifted

        # 2. Stateful SOS EQ / Bandpass / Tone Tilt
        self._rebuild_eq_if_needed(p)
        y64, self._zi = signal.sosfilt(self._sos, y.astype(np.float64), zi=self._zi)
        y = y64.astype(np.float32)

        # 3. Warble vibrato
        y = self._apply_warble(y, p.warble_depth, p.warble_rate_hz)

        # 4. Ring modulator (Robot / Alien)
        if p.ring_mod_mix > 0.01:
            n = len(y)
            t = np.arange(n, dtype=np.float32)
            ph = self._ring_phase + 2.0 * math.pi * p.ring_mod_hz * t / self.sr
            self._ring_phase = float((ph[-1] + 2.0 * math.pi * p.ring_mod_hz / self.sr) % (2.0 * math.pi))
            carrier = 0.72 * np.sin(ph) + 0.28 * np.sin(2.0 * ph)
            y = (1.0 - p.ring_mod_mix) * y + p.ring_mod_mix * (y * carrier * 1.45)

        # 5. Comb metallic resonator
        if p.comb_mix > 0.01:
            d_samples = int(round(p.comb_ms * self.sr / 1000.0))
            y = self._comb.process_comb(y, d_samples, p.comb_feedback, p.comb_mix)

        # 6. Chorus / Phaser via Pedalboard
        if p.chorus_mix > 0.01:
            self._chorus.mix = float(p.chorus_mix)
            y = self._chorus.process(y[None, :], self.sr, reset=False)[0]
        if p.phaser_mix > 0.01:
            self._phaser.mix = float(p.phaser_mix)
            y = self._phaser.process(y[None, :], self.sr, reset=False)[0]

        # 7. Overdrive / Saturation
        if p.drive_db > 0.2:
            drv = 10.0 ** (p.drive_db / 20.0)
            y = np.tanh(y * drv).astype(np.float32)

        # 8. Lo-fi downsampling & bitcrush
        y = self._apply_lofi_crusher(y, p.lofi_rate_hz, p.bit_depth)

        # 9. Broken phone packet loss & cyber glitch stutter
        y = self._apply_packet_loss(y, p.packet_loss, speech_rms)
        y = self._apply_glitch(y, p.glitch_amount, speech_rms)

        # 10. Walkie-talkie squelch & roger beep
        y = self._apply_radio_fx(y, p.radio_fx, speech_rms)

        # 11. Echo & Reverb
        if p.echo_mix > 0.01:
            d_echo = int(round(p.echo_ms * self.sr / 1000.0))
            y = self._echo.process_echo(y, d_echo, p.echo_feedback, p.echo_mix)
        if p.reverb_mix > 0.01:
            self._reverb.room_size = float(np.clip(p.reverb_room, 0.05, 0.98))
            self._reverb.wet_level = float(np.clip(p.reverb_mix * 0.7, 0.0, 0.9))
            self._reverb.dry_level = 1.0 - 0.25 * p.reverb_mix
            y = self._reverb.process(y[None, :], self.sr, reset=False)[0]

        # 12. Output gain + safety soft limiter
        if abs(p.output_gain_db) > 0.05:
            y *= 10.0 ** (p.output_gain_db / 20.0)
        y = (0.94 * np.tanh(y / 0.94)).astype(np.float32)

        if fade_left > 0:
            k = min(len(y), fade_left)
            ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
            y[:k] = self._last_out_tail * (1.0 - ramp) + y[:k] * ramp

        self._last_out_tail = float(y[-1]) if len(y) else 0.0
        return y

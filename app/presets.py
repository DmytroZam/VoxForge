from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VoiceParams:
    """DSP parameters controlling a real-time voice effect preset."""

    pitch_semitones: float = 0.0        # -12.0 .. +12.0
    autotune_amount: float = 0.0        # 0.0 .. 1.0 hard scale pitch lock
    autotune_speed: float = 1.0         # 0.0 .. 1.0 (1.0 = instant 808s snap)
    autotune_scale: str = "minor"       # minor | major | pentatonic | chromatic
    sub_octave_mix: float = 0.0         # 0.0 .. 1.0 (-11 st octave layer)
    tone_tilt: float = 0.0              # -1.0 (dark/bass) .. +1.0 (bright/formant)
    hp_hz: float = 55.0                 # High-pass cutoff Hz
    lp_hz: float = 18000.0              # Low-pass cutoff Hz
    mid_boost_db: float = 0.0           # Nasal/horn peak at ~1.45 kHz
    ring_mod_mix: float = 0.0           # 0.0 .. 1.0
    ring_mod_hz: float = 58.0           # Carrier frequency Hz
    comb_mix: float = 0.0               # Metallic resonator 0.0 .. 1.0
    comb_ms: float = 5.5                # Comb delay ms
    comb_feedback: float = 0.65         # Comb feedback
    warble_depth: float = 0.0           # 0.0 .. 1.0 alien/cartoon vibrato
    warble_rate_hz: float = 6.5         # Warble speed Hz
    chorus_mix: float = 0.0             # 0.0 .. 1.0
    phaser_mix: float = 0.0             # 0.0 .. 1.0
    drive_db: float = 0.0               # 0.0 .. 26.0 dB saturation/overdrive
    lofi_rate_hz: float = 48000.0       # Sample-rate reduction (4000 .. 48000)
    bit_depth: int = 16                 # Quantization bits (4 .. 16)
    packet_loss: float = 0.0            # 0.0 .. 1.0 GSM/phone stutter dropouts
    glitch_amount: float = 0.0          # 0.0 .. 1.0 cyber micro-repeat
    radio_fx: float = 0.0               # 0.0 .. 1.0 radio static + roger beep
    echo_mix: float = 0.0               # 0.0 .. 1.0
    echo_ms: float = 180.0              # Delay time ms
    echo_feedback: float = 0.35         # Delay feedback
    reverb_mix: float = 0.0             # 0.0 .. 1.0
    reverb_room: float = 0.5            # 0.0 .. 1.0
    output_gain_db: float = 0.0         # -12.0 .. +12.0 dB

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceParams":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class VoicePreset:
    id: str
    name: str
    category: str
    icon: str
    accent: str
    description: str
    hotkey: str
    params: VoiceParams = field(default_factory=VoiceParams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "icon": self.icon,
            "accent": self.accent,
            "description": self.description,
            "hotkey": self.hotkey,
            "params": self.params.to_dict(),
        }


PRESETS: list[VoicePreset] = [
    VoicePreset(
        id="clean",
        name="Clean Mic",
        category="Base",
        icon="mic",
        accent="#64748b",
        description="Unprocessed original microphone with broadcast safety limiter",
        hotkey="Ctrl+Shift+0",
        params=VoiceParams(),
    ),
    VoicePreset(
        id="studio",
        name="Studio / Podcast",
        category="Base",
        icon="sparkles",
        accent="#38bdf8",
        description="Warm broadcast tone, controlled lows, and crisp vocal clarity",
        hotkey="Ctrl+Shift+1",
        params=VoiceParams(
            pitch_semitones=-0.8,
            tone_tilt=-0.18,
            hp_hz=75.0,
            lp_hz=15500.0,
            mid_boost_db=1.8,
            drive_db=1.5,
            output_gain_db=1.5,
        ),
    ),
    VoicePreset(
        id="deep",
        name="Deep Bass",
        category="Pitch",
        icon="waves",
        accent="#6366f1",
        description="Heavy, cinematic deep voice with warm chest resonance",
        hotkey="Ctrl+Shift+2",
        params=VoiceParams(
            pitch_semitones=-4.6,
            tone_tilt=-0.35,
            hp_hz=60.0,
            lp_hz=11000.0,
            mid_boost_db=2.5,
            drive_db=2.0,
            output_gain_db=2.0,
        ),
    ),
    VoicePreset(
        id="squeaky",
        name="Squeaky / Helium",
        category="Pitch",
        icon="zap",
        accent="#f472b6",
        description="Ultra-high squeaky voice like inhaling a helium balloon",
        hotkey="Ctrl+Shift+3",
        params=VoiceParams(
            pitch_semitones=9.2,
            tone_tilt=0.65,
            hp_hz=180.0,
            lp_hz=14000.0,
            mid_boost_db=2.0,
            output_gain_db=1.0,
        ),
    ),
    VoicePreset(
        id="kid",
        name="Kid",
        category="Characters",
        icon="smile",
        accent="#fbbf24",
        description="Bright, playful high-pitched kid voice for voice chat",
        hotkey="Ctrl+Shift+4",
        params=VoiceParams(
            pitch_semitones=5.6,
            tone_tilt=0.45,
            hp_hz=150.0,
            lp_hz=13500.0,
            mid_boost_db=1.5,
            output_gain_db=1.0,
        ),
    ),
    VoicePreset(
        id="cartoon",
        name="Cartoon",
        category="Characters",
        icon="ghost",
        accent="#fb923c",
        description="Quirky animated character voice with subtle pitch vibrato",
        hotkey="Ctrl+Shift+5",
        params=VoiceParams(
            pitch_semitones=7.4,
            tone_tilt=0.55,
            hp_hz=190.0,
            lp_hz=12000.0,
            mid_boost_db=4.5,
            warble_depth=0.22,
            warble_rate_hz=8.5,
            output_gain_db=1.0,
        ),
    ),
    VoicePreset(
        id="robot",
        name="Robot / Cyborg",
        category="Synth",
        icon="bot",
        accent="#22d3ee",
        description="Metallic ring modulation and comb resonator droid synth",
        hotkey="Ctrl+Shift+6",
        params=VoiceParams(
            pitch_semitones=-1.5,
            tone_tilt=0.2,
            hp_hz=170.0,
            lp_hz=8500.0,
            ring_mod_mix=0.72,
            ring_mod_hz=58.0,
            comb_mix=0.68,
            comb_ms=5.8,
            comb_feedback=0.68,
            drive_db=5.5,
            lofi_rate_hz=18000.0,
            bit_depth=10,
            output_gain_db=2.0,
        ),
    ),
    VoicePreset(
        id="broken_phone",
        name="Broken Phone",
        category="Comms",
        icon="phone-off",
        accent="#ef4444",
        description="2007 cellar signal: 6.8 kHz crunch, 6-bit codec, and packet drops",
        hotkey="Ctrl+Shift+7",
        params=VoiceParams(
            pitch_semitones=0.0,
            hp_hz=440.0,
            lp_hz=2650.0,
            mid_boost_db=6.0,
            drive_db=11.0,
            lofi_rate_hz=6800.0,
            bit_depth=6,
            packet_loss=0.72,
            output_gain_db=2.5,
        ),
    ),
    VoicePreset(
        id="walkie",
        name="Walkie-Talkie",
        category="Comms",
        icon="radio",
        accent="#4ade80",
        description="Tactical radio bandpass, static squelch, and auto roger beep",
        hotkey="Ctrl+Shift+8",
        params=VoiceParams(
            pitch_semitones=0.0,
            hp_hz=540.0,
            lp_hz=2450.0,
            mid_boost_db=7.5,
            drive_db=9.5,
            lofi_rate_hz=11500.0,
            bit_depth=9,
            radio_fx=0.85,
            output_gain_db=2.0,
        ),
    ),
    VoicePreset(
        id="demon",
        name="Demon",
        category="Horror",
        icon="flame",
        accent="#dc2626",
        description="Layered infernal sub-octave voice with overdrive and dark hall",
        hotkey="Ctrl+Shift+9",
        params=VoiceParams(
            pitch_semitones=-4.8,
            sub_octave_mix=0.65,
            tone_tilt=-0.55,
            hp_hz=55.0,
            lp_hz=7500.0,
            drive_db=6.5,
            chorus_mix=0.35,
            reverb_mix=0.42,
            reverb_room=0.72,
            output_gain_db=2.5,
        ),
    ),
    VoicePreset(
        id="giant",
        name="Giant / Titan",
        category="Characters",
        icon="mountain",
        accent="#8b5cf6",
        description="Massive low-pitched titan boom with cavernous decay",
        hotkey="",
        params=VoiceParams(
            pitch_semitones=-7.6,
            sub_octave_mix=0.25,
            tone_tilt=-0.45,
            hp_hz=55.0,
            lp_hz=8500.0,
            mid_boost_db=2.0,
            reverb_mix=0.26,
            reverb_room=0.6,
            output_gain_db=2.5,
        ),
    ),
    VoicePreset(
        id="alien",
        name="Alien",
        category="Synth",
        icon="orbit",
        accent="#a855f7",
        description="Extraterrestrial phaser, warbling vibrato, and ring mod",
        hotkey="",
        params=VoiceParams(
            pitch_semitones=2.4,
            tone_tilt=0.25,
            hp_hz=120.0,
            lp_hz=12000.0,
            ring_mod_mix=0.38,
            ring_mod_hz=32.0,
            warble_depth=0.55,
            warble_rate_hz=7.2,
            phaser_mix=0.75,
            echo_mix=0.22,
            echo_ms=115.0,
            echo_feedback=0.38,
            output_gain_db=3.0,
        ),
    ),
    VoicePreset(
        id="megaphone",
        name="Megaphone",
        category="Comms",
        icon="megaphone",
        accent="#f59e0b",
        description="Loud street bullhorn with gritty overdrive and slapback echo",
        hotkey="",
        params=VoiceParams(
            pitch_semitones=0.0,
            hp_hz=640.0,
            lp_hz=3600.0,
            mid_boost_db=8.5,
            drive_db=14.0,
            echo_mix=0.26,
            echo_ms=92.0,
            echo_feedback=0.28,
            output_gain_db=2.0,
        ),
    ),
    VoicePreset(
        id="cave",
        name="Cave / Cathedral",
        category="Space",
        icon="sparkles",
        accent="#14b8a6",
        description="Deep atmospheric canyon echo and lush hall reverb",
        hotkey="",
        params=VoiceParams(
            pitch_semitones=-1.5,
            tone_tilt=-0.1,
            hp_hz=70.0,
            lp_hz=12000.0,
            echo_mix=0.45,
            echo_ms=260.0,
            echo_feedback=0.46,
            reverb_mix=0.58,
            reverb_room=0.88,
            output_gain_db=1.0,
        ),
    ),
    VoicePreset(
        id="glitch",
        name="Glitch Hacker",
        category="Synth",
        icon="cpu",
        accent="#ec4899",
        description="Deep anonymous pitch with random cyber micro-stutters",
        hotkey="",
        params=VoiceParams(
            pitch_semitones=-3.8,
            tone_tilt=0.1,
            hp_hz=90.0,
            lp_hz=10500.0,
            comb_mix=0.25,
            comb_ms=4.2,
            comb_feedback=0.5,
            drive_db=4.5,
            lofi_rate_hz=16000.0,
            bit_depth=9,
            glitch_amount=0.68,
            output_gain_db=2.0,
        ),
    ),
    VoicePreset(
        id="kanye_808",
        name="808s & Heartbreak",
        category="Autotune",
        icon="music",
        accent="#e11d48",
        description="Hard C-Minor autotune snap, glossy vocal sheen, and cold hall",
        hotkey="Ctrl+Shift+K",
        params=VoiceParams(
            pitch_semitones=0.0,
            autotune_amount=1.0,
            autotune_speed=1.0,
            autotune_scale="minor",
            tone_tilt=0.28,
            hp_hz=110.0,
            lp_hz=16000.0,
            mid_boost_db=2.8,
            drive_db=2.2,
            chorus_mix=0.16,
            reverb_mix=0.28,
            reverb_room=0.68,
            output_gain_db=1.8,
        ),
    ),
]


PRESET_MAP: dict[str, VoicePreset] = {p.id: p for p in PRESETS}

from __future__ import annotations

from typing import Callable
from pynput import keyboard


def _to_pynput_combo(combo: str) -> str:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mapped: list[str] = []
    for p in parts:
        if p in ("ctrl", "control"):
            mapped.append("<ctrl>")
        elif p == "shift":
            mapped.append("<shift>")
        elif p == "alt":
            mapped.append("<alt>")
        elif len(p) == 1:
            mapped.append(p)
        else:
            mapped.append(f"<{p}>")
    return "+".join(mapped)


class GlobalHotkeyManager:
    """Registers global Windows hotkeys so voices can be switched mid-game or in Discord."""

    def __init__(self, on_preset: Callable[[str], None], on_toggle_voice: Callable[[], None]) -> None:
        self._on_preset = on_preset
        self._on_toggle_voice = on_toggle_voice
        self._listener: keyboard.GlobalHotKeys | None = None

    def register(self, preset_hotkeys: dict[str, str], toggle_hotkey: str = "Ctrl+Shift+M") -> None:
        self.stop()
        mapping: dict[str, Callable[[], None]] = {}
        for pid, combo in preset_hotkeys.items():
            if not combo:
                continue
            key_str = _to_pynput_combo(combo)
            mapping[key_str] = lambda p=pid: self._on_preset(p)
        if toggle_hotkey:
            mapping[_to_pynput_combo(toggle_hotkey)] = self._on_toggle_voice
        if not mapping:
            return
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
        except Exception:
            self._listener = None

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

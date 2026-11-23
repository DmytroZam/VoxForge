from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audio_engine import AudioEngine
from .hotkeys import GlobalHotkeyManager
from .presets import PRESET_MAP, PRESETS, VoiceParams
from .vbcable import download_and_launch_vbcable_installer


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR.parent / "config.json"


class DeviceSelectBody(BaseModel):
    input_device_id: int | None = None
    output_device_id: int | None = None
    monitor_device_id: int | None = None


class ControlsBody(BaseModel):
    running: bool | None = None
    voice_enabled: bool | None = None
    monitor_enabled: bool | None = None
    monitor_volume: float | None = None
    input_gain_db: float | None = None
    gate_threshold_db: float | None = None


engine = AudioEngine()


def _save_config() -> None:
    try:
        data = {
            "active_preset_id": engine.active_preset_id,
            "voice_enabled": engine.voice_enabled,
            "monitor_volume": engine.monitor_volume,
            "input_gain_db": engine.input_gain_db,
            "gate_threshold_db": engine.gate_threshold_db,
            "input_device_key": engine.get_device_key(engine.input_device_id),
            "output_device_key": engine.get_device_key(engine.output_device_id),
            "monitor_device_key": engine.get_device_key(engine.monitor_device_id),
            "custom_params": engine.custom_params.to_dict(),
        }
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_config() -> None:
    if not CONFIG_PATH.exists():
        return
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        in_id = engine.find_device_by_key(data.get("input_device_key"), is_input=True)
        if in_id is not None:
            engine.input_device_id = in_id
        out_id = engine.find_device_by_key(data.get("output_device_key"), is_input=False)
        if out_id is not None:
            engine.output_device_id = out_id
        mon_id = engine.find_device_by_key(data.get("monitor_device_key"), is_input=False)
        if mon_id is not None:
            engine.monitor_device_id = mon_id

        if "input_gain_db" in data or "gate_threshold_db" in data:
            engine.set_mic_controls(
                input_gain_db=data.get("input_gain_db"),
                gate_threshold_db=data.get("gate_threshold_db"),
            )
        if "monitor_volume" in data:
            engine.set_monitor_enabled(False, volume=float(data["monitor_volume"]))
        if "voice_enabled" in data:
            engine.set_voice_enabled(bool(data["voice_enabled"]))

        pid = data.get("active_preset_id", "deep")
        if pid == "custom" and isinstance(data.get("custom_params"), dict):
            engine.custom_params = VoiceParams.from_dict(data["custom_params"])
            engine.set_preset("custom")
        elif pid in PRESET_MAP:
            engine.set_preset(pid)
    except Exception:
        pass


def _on_hotkey_preset(pid: str) -> None:
    engine.set_preset(pid)
    _save_config()


def _on_hotkey_toggle_voice() -> None:
    engine.set_voice_enabled(not engine.voice_enabled)
    _save_config()


hotkeys = GlobalHotkeyManager(_on_hotkey_preset, _on_hotkey_toggle_voice)


app = FastAPI(title="VoxForge — Discord Voice Changer")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ws_state = {"ever_connected": False, "active_count": 0, "last_disconnect": 0.0}


def init_runtime() -> None:
    _load_config()
    engine.start()
    hk_map = {p.id: p.hotkey for p in PRESETS if p.hotkey}
    hotkeys.register(hk_map, toggle_hotkey="Ctrl+Shift+M")


def shutdown_runtime() -> None:
    hotkeys.stop()
    engine.stop()
    _save_config()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/bootstrap")
def api_bootstrap() -> dict[str, Any]:
    return {
        "presets": [p.to_dict() for p in PRESETS],
        "devices": engine.list_devices(refresh=False),
        "state": engine.get_state(),
    }


@app.post("/api/devices/refresh")
def api_devices_refresh() -> dict[str, Any]:
    return {
        "devices": engine.list_devices(refresh=True),
        "state": engine.get_state(),
    }


@app.post("/api/devices/select")
def api_devices_select(body: DeviceSelectBody) -> dict[str, Any]:
    res = engine.set_devices(
        input_device_id=body.input_device_id,
        output_device_id=body.output_device_id,
        monitor_device_id=body.monitor_device_id,
    )
    _save_config()
    return {"result": res, "state": engine.get_state()}


@app.post("/api/preset/{preset_id}")
def api_set_preset(preset_id: str) -> dict[str, Any]:
    state = engine.set_preset(preset_id)
    _save_config()
    return {"state": state}


@app.post("/api/params")
def api_update_params(patch: dict[str, Any]) -> dict[str, Any]:
    state = engine.update_params(patch)
    _save_config()
    return {"state": state}


@app.post("/api/controls")
def api_controls(body: ControlsBody) -> dict[str, Any]:
    if body.running is True and not engine.running:
        engine.start()
    elif body.running is False and engine.running:
        engine.stop()
    if body.voice_enabled is not None:
        engine.set_voice_enabled(body.voice_enabled)
    if body.monitor_enabled is not None or body.monitor_volume is not None:
        enabled = body.monitor_enabled if body.monitor_enabled is not None else engine.monitor_enabled
        engine.set_monitor_enabled(enabled, volume=body.monitor_volume)
    if body.input_gain_db is not None or body.gate_threshold_db is not None:
        engine.set_mic_controls(body.input_gain_db, body.gate_threshold_db)
    _save_config()
    return {"state": engine.get_state()}


@app.post("/api/test-record")
def api_test_record() -> dict[str, Any]:
    return engine.start_test_record()


@app.post("/api/vbcable/install")
def api_vbcable_install() -> JSONResponse:
    try:
        res = download_and_launch_vbcable_installer()
        return JSONResponse(res)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"Failed to install VB-Cable: {exc}"}, status_code=500)


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket) -> None:
    await ws.accept()
    ws_state["ever_connected"] = True
    ws_state["active_count"] += 1
    try:
        while True:
            payload = {
                "in_level": round(engine.in_level, 4),
                "out_level": round(engine.out_level, 4),
                "tuned_note": engine.tuned_note,
                "spectrum": [round(v, 3) for v in engine.spectrum],
                "running": engine.running,
                "voice_enabled": engine.voice_enabled,
                "monitor_enabled": engine.monitor_enabled,
                "active_preset_id": engine.active_preset_id,
                "record_state": engine.record_state,
                "last_error": engine.last_error,
            }
            await ws.send_json(payload)
            await asyncio.sleep(0.033)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_state["active_count"] = max(0, ws_state["active_count"] - 1)
        ws_state["last_disconnect"] = time.monotonic()

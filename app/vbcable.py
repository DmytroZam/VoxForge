from __future__ import annotations

import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

VBCABLE_ZIP_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip"
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools" / "vbcable"


def detect_virtual_cable(devices: list[dict[str, Any]]) -> dict[str, Any]:
    """Check whether VB-Cable or another virtual audio cable is available."""
    keywords = ("cable input", "vb-audio", "voicemeeter", "virtual cable")
    out_matches = [
        d for d in devices
        if d.get("max_output_channels", 0) > 0
        and any(kw in d.get("name", "").lower() for kw in keywords)
    ]
    in_matches = [
        d for d in devices
        if d.get("max_input_channels", 0) > 0
        and any(kw in d.get("name", "").lower() for kw in ("cable output", "vb-audio", "voicemeeter"))
    ]
    preferred_out = None
    for d in out_matches:
        if "wasapi" in d.get("hostapi_name", "").lower():
            preferred_out = d
            break
    if not preferred_out and out_matches:
        preferred_out = out_matches[0]

    return {
        "installed": len(out_matches) > 0,
        "recommended_output_id": preferred_out["id"] if preferred_out else None,
        "recommended_output_name": preferred_out["name"] if preferred_out else None,
        "discord_input_hint": in_matches[0]["name"] if in_matches else "CABLE Output (VB-Audio Virtual Cable)",
    }


def download_and_launch_vbcable_installer() -> dict[str, Any]:
    """Download official VB-Cable Driver Pack and launch elevated setup exe."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = TOOLS_DIR / "VBCABLE_Driver_Pack46.zip"

    req = urllib.request.Request(
        VBCABLE_ZIP_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = resp.read()
    zip_path.write_bytes(data)

    extract_dir = TOOLS_DIR / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    exe_path = extract_dir / "VBCABLE_Setup_x64.exe"
    if not exe_path.exists():
        candidates = list(extract_dir.rglob("*Setup*x64*.exe")) + list(extract_dir.rglob("*Setup*.exe"))
        if not candidates:
            raise FileNotFoundError("VBCABLE_Setup_x64.exe was not found in the downloaded archive.")
        exe_path = candidates[0]

    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Start-Process -FilePath '{str(exe_path)}' -Verb RunAs",
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "ok": True,
        "message": "VB-Cable installer launched! Click 'Install Driver', then click 'Refresh' here.",
        "installer_path": str(exe_path),
    }

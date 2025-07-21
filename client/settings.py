"""
Load / save JSON settings for Silent Link client.
Gracefully handles first-run wizard prompts.
"""

import json
from typing import Any
from config import SETTINGS_FILE, DATA_DIR, ensure_data_dirs
from config import SERVER_PORT as DEFAULT_SERVER_PORT

# ── Default template if file doesn't exist yet ───────────────────────────────
DEFAULT_SETTINGS: dict[str, Any] = {
    "display_name": "",           # Display name shown to other users
    "server_ip":    "",           # Remote server IP address
    "server_port":  DEFAULT_SERVER_PORT,  # Server port number

    "audio_input":  "default",    # Selected audio input device
    "audio_output": "default",    # Selected audio output device

    "ptt_key":      "LeftAlt",    # Default push-to-talk hotkey
    "ptt":          False,        # Push-to-talk toggle state
    "vox":          False,        # Voice activation toggle state

    "mic_vol":      100,          # Microphone volume (0–100)
    "spk_vol":      100           # Speaker volume (0–100)
}

class Settings:
    def __init__(self) -> None:
        ensure_data_dirs()
        self.data = DEFAULT_SETTINGS | self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if SETTINGS_FILE.exists():
            try:
                return json.loads(SETTINGS_FILE.read_text())
            except json.JSONDecodeError:
                print("[WARN] corrupt settings.json, using defaults")
        return {}
    
    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(self.data, indent=2))

    # ── convenience getters/setters ─────────────────────────────────────────
    def __getitem__(self, k): return self.data.get(k)
    def __setitem__(self, k, v): 
        self.data[k] = v
        self.save()
    
    def get(self, k, default=None):  # ✅ ADD THIS
        return self.data.get(k, default)

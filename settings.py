"""
Load / save JSON settings for Silent Link client.
Gracefully handles first-run wizard prompts.
"""

import json
from typing import Any
from common import SETTINGS_FILE, DATA_DIR, ensure_data_dirs

# ── Default template if file doesn't exist yet ───────────────────────────────
DEFAULT_SETTINGS: dict[str, Any] = {
    "display_name": "",
    "server_ip":    "",
    "audio_input":  "default",
    "audio_output": "default",
    "ptt_key":      "LeftAlt"
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

"""Paths, settings and secrets."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000


def _xdg(env: str, default: str) -> Path:
    return Path(os.environ.get(env) or Path.home() / default)


def data_dir() -> Path:
    return Path(os.environ.get("AKU_DATA_DIR") or _xdg("XDG_DATA_HOME", ".local/share") / "aku")


def config_dir() -> Path:
    return Path(os.environ.get("AKU_CONFIG_DIR") or _xdg("XDG_CONFIG_HOME", ".config") / "aku")


def sessions_dir() -> Path:
    return Path(os.environ.get("AKU_SESSIONS_DIR") or data_dir() / "sessions")


def models_dir() -> Path:
    return data_dir() / "models"


def db_path() -> Path:
    return Path(os.environ.get("AKU_DB") or data_dir() / "aku.db")


def ensure_dirs() -> None:
    for d in (data_dir(), config_dir(), sessions_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)


DEFAULTS: dict[str, Any] = {
    "user_name": "Me",
    "output_folder": str(Path.home() / "Documents" / "Aku"),
    "backend": "local",  # local | cloud
    "local_model": "large-v3-turbo",
    "improve_model": "large-v3",
    "cloud_model": "whisper-1",
    "use_gpu": True,
    "language": "en",
    "diarizer": "auto",  # auto | pyannote | basic
    "port": 4587,
    "whisper_port": 4588,
    "notifications": True,
    "tray": True,
    "active_vocab_sets": [],
    "last_sources": {"input": None, "output": None, "scope": "all", "apps": []},
}

_lock = threading.Lock()


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    out = json.loads(json.dumps(DEFAULTS))
    try:
        stored = json.loads(settings_path().read_text())
    except (OSError, ValueError):
        stored = {}
    for k, v in stored.items():
        if k in out:
            out[k] = v
    return out


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = load_settings()
        for k, v in values.items():
            if k in DEFAULTS:
                current[k] = v
        config_dir().mkdir(parents=True, exist_ok=True)
        tmp = settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2))
        tmp.replace(settings_path())
        return current


# --- secrets (system keyring, with environment fallback) -------------------

SECRETS = {"openai_api_key": ("OPENAI_API_KEY",), "hf_token": ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")}
_KEYRING_SERVICE = "aku"


def get_secret(name: str) -> str | None:
    for env in SECRETS[name]:
        if os.environ.get(env):
            return os.environ[env]
    try:
        import keyring

        value = keyring.get_password(_KEYRING_SERVICE, name)
        if value:
            return value
    except Exception:
        pass
    if name == "hf_token":
        token_file = Path.home() / ".cache" / "huggingface" / "token"
        if token_file.exists():
            return token_file.read_text().strip() or None
    return None


def set_secret(name: str, value: str | None) -> None:
    if name not in SECRETS:
        raise KeyError(name)
    import keyring

    if value:
        keyring.set_password(_KEYRING_SERVICE, name, value)
    else:
        try:
            keyring.delete_password(_KEYRING_SERVICE, name)
        except Exception:
            pass


# --- whisper.cpp locations ----------------------------------------------------

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
VAD_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"


def model_path(name: str) -> Path:
    return models_dir() / f"ggml-{name}.bin"


def vad_model_path() -> Path:
    return models_dir() / "silero_vad.onnx"


def whisper_bin(tool: str = "whisper-server") -> Path | None:
    env = os.environ.get("AKU_WHISPER_BIN_DIR")
    candidates = [Path(env) / tool] if env else []
    candidates.append(data_dir() / "deps" / "whisper.cpp" / "build" / "bin" / tool)
    for c in candidates:
        if c.exists():
            return c
    found = shutil.which(tool)
    return Path(found) if found else None


def download(url: str, dest: Path, progress=None) -> Path:
    """Download a file to dest (atomic). progress(done_bytes, total_bytes)."""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "aku"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            done += len(block)
            if progress:
                progress(done, total)
    tmp.replace(dest)
    return dest


def ensure_vad_model() -> Path:
    p = vad_model_path()
    if not p.exists():
        download(VAD_URL, p)
    return p

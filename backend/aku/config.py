"""Paths, settings and secrets."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000
log = logging.getLogger(__name__)


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


# Languages offered in the UI (all are Whisper languages). "auto" lets Whisper detect per chunk.
AUTO = "auto"
LANGUAGES: dict[str, str] = {
    "auto": "Automatic", "en": "English", "da": "Danish", "el": "Greek",
    "ar": "Arabic", "bg": "Bulgarian", "ca": "Catalan", "zh": "Chinese", "hr": "Croatian", "cs": "Czech",
    "nl": "Dutch", "et": "Estonian", "fi": "Finnish", "fr": "French", "de": "German", "he": "Hebrew",
    "hi": "Hindi", "hu": "Hungarian", "is": "Icelandic", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "lv": "Latvian", "lt": "Lithuanian", "no": "Norwegian", "fa": "Persian", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sr": "Serbian", "sk": "Slovak", "sl": "Slovenian",
    "es": "Spanish", "sv": "Swedish", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese",
}
_CODE_BY_NAME = {name.lower(): code for code, name in LANGUAGES.items() if code != AUTO}


def language_code(name: str | None) -> str | None:
    """Whisper reports a detected language by its English name ("danish"): map it back to a code."""
    if not name:
        return None
    name = name.strip().lower()
    return name if name in LANGUAGES and name != AUTO else _CODE_BY_NAME.get(name)


DEFAULTS: dict[str, Any] = {
    "user_name": "Me",
    "output_folder": str(Path.home() / "Documents" / "Aku"),
    "backend": "local",  # local | cloud
    "local_model": "large-v3-turbo",
    "improve_model": "large-v3",
    "cloud_model": "whisper-1",
    "cloud_diarize_model": "gpt-4o-transcribe-diarize",  # Improve engine with its own speaker labels
    "use_gpu": True,
    "language": "en",
    "translate": True,  # with a language chosen: write other languages in it too (off = keep them as spoken)
    "diarizer": "auto",  # auto | pyannote | basic
    "live_diarization": True,  # show speakers during the call (built-in method)
    "port": 4587,
    "whisper_port": 4588,
    "notifications": True,
    "global_shortcut": False,  # hotkey through the desktop's GlobalShortcuts portal
    "shortcut_trigger": "CTRL+ALT+r",  # only a suggestion: the desktop's dialog has the last word
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
    except Exception as e:  # noqa: BLE001 - no keyring service running
        log.debug("keyring not readable for %s: %s", name, e)
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
    candidates.append(data_dir() / "deps" / "bin" / tool)  # from a release (get-aku.sh)
    candidates.append(data_dir() / "deps" / "whisper.cpp" / "build" / "bin" / tool)  # built here
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


def free_bytes(path: Path) -> int:
    """Free disk space where `path` lives (walks up to the nearest existing folder)."""
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def ensure_vad_model() -> Path:
    p = vad_model_path()
    if not p.exists():
        download(VAD_URL, p)
    return p


def die_with_parent() -> None:
    """preexec_fn for helper processes: if Aku is killed, they are stopped too (no orphans)."""
    import ctypes
    import signal

    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except OSError:
        pass

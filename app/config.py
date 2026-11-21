"""Read the sample's root .env; process environment takes precedence."""

import os
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BOT_NAME = "phonic demo"
load_dotenv(ROOT / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in .env before starting the demo.")
    return value


def tools_origin() -> str:
    value = require_env("PHONIC_TOOLS_ORIGIN").rstrip("/")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").rstrip(".")
        try:
            public_host = ip_address(hostname).is_global
        except ValueError:
            public_host = "." in hostname and not hostname.endswith((".local", ".localhost", ".internal"))
        valid = (
            parsed.scheme == "https" and public_host and parsed.port in (None, 443)
            and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
            and "\\" not in value and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("PHONIC_TOOLS_ORIGIN must be a public HTTPS origin without a path.")
    return value


def project_name() -> str:
    value = os.getenv("PHONIC_PROJECT", "main").strip()
    if not value or len(value.encode("utf-8")) > 256:
        raise ValueError("PHONIC_PROJECT must contain 1 to 256 UTF-8 bytes.")
    return value


def voice_id() -> str:
    value = os.getenv("PHONIC_VOICE_ID", "sabrina").strip()
    if not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise ValueError("PHONIC_VOICE_ID must be a voice available in your Phonic project.")
    return value


def session_timeout() -> int:
    try:
        value = int(os.getenv("SESSION_TIMEOUT_SECONDS", "600"))
        if 60 <= value <= 3600:
            return value
    except ValueError:
        pass
    raise ValueError("SESSION_TIMEOUT_SECONDS must be between 60 and 3600.")


def validate_runtime_settings() -> None:
    require_env("RECALL_API_KEY")
    require_env("RECALL_API_BASE_URL")
    tools_origin()
    project_name()
    voice_id()
    session_timeout()

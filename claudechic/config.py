"""Configuration management for claudechic via ~/.claude/.claudechic.yaml."""

import os
import tempfile
import uuid
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / ".claude" / ".claudechic.yaml"
_OLD_CONFIG_PATH = Path.home() / ".claude" / "claudechic.yaml"

_config: dict = {}
_new_install: bool = False


def _load_config() -> dict:
    """Load config from disk, creating file atomically if missing."""
    global _config, _new_install
    if _config:
        return _config

    # Migrate from old config path if it exists and new doesn't
    if not CONFIG_PATH.exists() and _OLD_CONFIG_PATH.exists():
        _OLD_CONFIG_PATH.rename(CONFIG_PATH)
    elif _OLD_CONFIG_PATH.exists():
        # Clean up old config file if new one already exists
        _OLD_CONFIG_PATH.unlink()

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
        # Provide defaults for missing keys (don't save - preserve user's file)
        _config.setdefault("analytics", {})
        _config["analytics"].setdefault("id", "anonymous")
        _config["analytics"].setdefault("enabled", True)
    else:
        # New install - create config with fresh ID and save
        _config = {"analytics": {"enabled": True, "id": str(uuid.uuid4())}}
        _new_install = True
        _save_config()

    return _config


def _save_config() -> None:
    """Write config to disk atomically."""
    if not _config:
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file, then rename
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(_config, f, default_flow_style=False)
        os.rename(tmp_path, CONFIG_PATH)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_analytics_enabled() -> bool:
    """Check if analytics collection is enabled."""
    return _load_config()["analytics"]["enabled"]


def get_analytics_id() -> str:
    """Get the anonymous analytics ID, generating if needed."""
    return _load_config()["analytics"]["id"]


def set_analytics_enabled(enabled: bool) -> None:
    """Enable or disable analytics collection."""
    _load_config()["analytics"]["enabled"] = enabled
    _save_config()


def get_theme() -> str | None:
    """Get saved theme preference, or None if not set."""
    return _load_config().get("theme")


def set_theme(theme: str) -> None:
    """Save theme preference."""
    _load_config()["theme"] = theme
    _save_config()


def is_new_install() -> bool:
    """Check if this is a new install (analytics ID was just created)."""
    _load_config()  # Ensure config is loaded
    return _new_install


def get_vim() -> bool:
    """Check if vim mode is enabled."""
    return _load_config().get("vim", False)


def set_vim(enabled: bool) -> None:
    """Enable or disable vim mode."""
    config = _load_config()
    config["vim"] = enabled
    _save_config()

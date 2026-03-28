import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

class Config:
    """Configuration manager for tidmon"""

    DEFAULT_CONFIG = {
        "version": "1.0.0",
        "user_id": None,
        "country_code": "US",
        "check_new_releases": True,
        "record_types": ["ALBUM", "EP", "SINGLE", "COMPILATION"],
        # quality_order: preferred download qualities, tried in order until one succeeds.
        # Available values: MAX, HI_RES_LOSSLESS, LOSSLESS, HIGH, LOW
        "quality_order": ["MAX", "HI_RES_LOSSLESS", "LOSSLESS", "HIGH", "LOW"],
        "save_cover": True,
        "save_lrc": False,
        "save_video": True,
        "embed_cover": True,
        "email_notifications": False,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "email_from": "",
        "email_to": "",
        "email_password": "",
        "download_location": {
            "default": str(Path.home() / "Music" / "tidmon"),
            "video": str(Path.home() / "Videos" / "tidmon")
        },
        "concurrent_downloads": 2,
        "requests_per_minute": 50,
        "debug_mode": False,
        "monitor_interval_hours": 24,
        "artist_separator": ", ",
        "templates": {
            "default": (
                "{artist_initials}/{album.artist}"
                "/({album.date:%Y-%m-%d}) {album.title} ({album.release})"
                "/{item.number}. {item.artists_with_features}"
                " - {item.title_version} {item.explicit:shortparens}"
            ),
            "video": (
                "{artist_initials}/{album.artist}"
                "/({item.releaseDate:%Y-%m-%d}) {item.artists_with_features}"
                " - {item.title_version} {item.explicit:shortparens}"
            ),
            "playlist": (
                "{playlist.title}/{item.artists_with_features}"
                " - {item.title_version} {item.explicit:shortparens}"
            ),
        },
    }

    # Legacy bitrate values -> quality_order equivalents (used during migration)
    _BITRATE_MIGRATION = {
        "MAX":             ["MAX", "HI_RES_LOSSLESS", "LOSSLESS", "HIGH", "LOW"],
        "HI_RES_LOSSLESS": ["HI_RES_LOSSLESS", "LOSSLESS", "HIGH", "LOW"],
        "LOSSLESS":        ["LOSSLESS", "HIGH", "LOW"],
        "HIGH":            ["HIGH", "LOW"],
        "LOW":             ["LOW"],
    }

    def __init__(self):
        from tidmon.core.utils.startup import get_appdata_dir, get_config_file
        self.appdata_dir = get_appdata_dir()
        self.config_file = get_config_file()
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if not self.config_file.exists():
            logger.info("Creating default configuration...")
            self._create_default_config()
            return self.DEFAULT_CONFIG.copy()

        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            dl_val = config.get("download_location")
            if isinstance(dl_val, str):
                logger.info("Migrating legacy 'download_location' setting...")
                default_dl_obj = self.DEFAULT_CONFIG["download_location"]
                config["download_location"] = {
                    "default": dl_val,
                    "video": default_dl_obj.get("video", dl_val)
                }

            # Migration 1: legacy 'bitrate' string -> 'quality_order' list
            if "bitrate" in config and "quality_order" not in config:
                legacy = config.pop("bitrate")
                migrated = self._BITRATE_MIGRATION.get(
                    str(legacy).upper(),
                    self.DEFAULT_CONFIG["quality_order"],
                )
                config["quality_order"] = migrated
                logger.info(
                    f"Migrated legacy 'bitrate={legacy}' to "
                    f"'quality_order={migrated}'"
                )
            elif "bitrate" in config:
                # quality_order already present — just drop the stale key
                config.pop("bitrate")

            # Migration 2: fill any missing top-level keys from DEFAULT_CONFIG
            for key, value in self.DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value

            # Deep merge for nested dicts (e.g. templates, download_location):
            # top-level merge above only adds missing *keys*, but if the key
            # exists as a dict the user may be missing sub-keys added in newer
            # versions.  We merge sub-keys without overwriting existing values.
            for key, default_value in self.DEFAULT_CONFIG.items():
                if isinstance(default_value, dict) and isinstance(config.get(key), dict):
                    for sub_key, sub_value in default_value.items():
                        if sub_key not in config[key]:
                            config[key][sub_key] = sub_value

            logger.debug(f"Configuration loaded from {self.config_file}")
            return config

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config file: {e}")
            self._create_default_config()
            return self.DEFAULT_CONFIG.copy()

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return self.DEFAULT_CONFIG.copy()

    def _create_default_config(self):
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.DEFAULT_CONFIG, f, indent=2)
            logger.info(f"Default configuration created at {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to create config file: {e}")

    def save(self) -> bool:
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.debug("Configuration saved")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        self.config[key] = value
        return self.save()

    def get_all(self) -> dict:
        return self.config.copy()

    def get_value(self, key: str) -> Any:
        return self.config.get(key)

    def set_value(self, key: str, value: str) -> bool:
        existing = self.config.get(key)
        if existing is not None:
            try:
                if isinstance(existing, bool):
                    value = value.lower() in ('true', '1', 'yes')
                elif isinstance(existing, int):
                    value = int(value)
                elif isinstance(existing, float):
                    value = float(value)
                elif isinstance(existing, list):
                    value = [v.strip() for v in value.split(',')]
            except (ValueError, AttributeError):
                pass
        self.config[key] = value
        return self.save()

    def show_config(self):
        print("\n" + "=" * 60)
        print("  TIDMON CONFIGURATION")
        print("=" * 60 + "\n")

        sensitive_keys = ['email_password']

        for key, value in sorted(self.config.items()):
            if key in sensitive_keys:
                display_value = "***HIDDEN***" if value else "Not set"
            else:
                display_value = value
            print(f"  {key:30} : {display_value}")

        print("\n" + "=" * 60 + "\n")
        print(f"  Config file: {self.config_file}\n")

    def user_id(self) -> Optional[str]:
        return self.get('user_id')

    def country_code(self) -> str:
        return self.get('country_code', 'US')

    def download_path(self, media_type: str = 'default') -> Path:
        paths = self.get('download_location', {})
        if isinstance(paths, str):
            return Path(paths)
        path_str = paths.get(media_type, paths.get('default', ''))
        return Path(path_str)

    def record_types(self) -> list:
        return self.get('record_types', ["ALBUM", "EP", "SINGLE", "COMPILATION"])

    def save_cover_enabled(self) -> bool:
        return self.get('save_cover', True)

    def embed_cover_enabled(self) -> bool:
        return self.get('embed_cover', True)

    def save_lrc_enabled(self) -> bool:
        return self.get('save_lrc', False)

    def save_video_enabled(self) -> bool:
        return self.get('save_video', True)

    def email_notifications_enabled(self) -> bool:
        return self.get('email_notifications', False)

    def concurrent_downloads(self) -> int:
        return int(self.get("concurrent_downloads", 2))

    def quality_order(self) -> list[str]:
        """
        Returns the preferred quality order list.
        Falls back to DEFAULT_CONFIG value if not set or invalid.
        """
        value = self.get('quality_order')
        if isinstance(value, list) and value:
            return value
        return self.DEFAULT_CONFIG["quality_order"]

    def get_config_file_path(self) -> str:
        return str(self.config_file)
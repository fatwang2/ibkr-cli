from __future__ import annotations

from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from platformdirs import user_config_dir
from pydantic import BaseModel, Field

CONFIG_DIR = Path(user_config_dir("ibkr-cli", "ibkr"))
CONFIG_FILE = CONFIG_DIR / "config.toml"


class ProfileConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    mode: Literal["paper", "live"] = "paper"


class AppConfig(BaseModel):
    default_profile: str = "paper"
    profiles: Dict[str, ProfileConfig] = Field(default_factory=dict)


def default_profiles() -> Dict[str, ProfileConfig]:
    return {
        "gateway-live": ProfileConfig(host="127.0.0.1", port=4001, client_id=1, mode="live"),
        "gateway-paper": ProfileConfig(host="127.0.0.1", port=4002, client_id=1, mode="paper"),
        "paper": ProfileConfig(host="127.0.0.1", port=7497, client_id=1, mode="paper"),
        "live": ProfileConfig(host="127.0.0.1", port=7496, client_id=1, mode="live"),
    }


def default_config() -> AppConfig:
    return AppConfig(default_profile="paper", profiles=default_profiles())


def load_config(path: Optional[Path] = None) -> Tuple[AppConfig, bool]:
    target = path or CONFIG_FILE
    if not target.exists():
        config = default_config()
        save_config(config, path=target, force=True)
        return config, True

    raw = target.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    config = AppConfig.model_validate(data)
    if config.default_profile not in config.profiles:
        raise ValueError(f"Default profile '{config.default_profile}' is not defined in {target}.")
    return config, True


def save_config(config: AppConfig, path: Optional[Path] = None, force: bool = False) -> Path:
    target = path or CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        raise FileExistsError(f"Config file already exists: {target}")
    target.write_text(serialize_config(config), encoding="utf-8")
    return target


def get_profile(config: AppConfig, name: Optional[str] = None) -> Tuple[str, ProfileConfig]:
    selected_name = name or config.default_profile
    if selected_name not in config.profiles:
        raise KeyError(selected_name)
    return selected_name, config.profiles[selected_name]


def serialize_config(config: AppConfig) -> str:
    lines = [f'default_profile = "{config.default_profile}"', ""]
    for name in sorted(config.profiles):
        profile = config.profiles[name]
        lines.extend(
            [
                f"[profiles.{name}]",
                f'host = "{profile.host}"',
                f"port = {profile.port}",
                f"client_id = {profile.client_id}",
                f'mode = "{profile.mode}"',
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def profile_to_dict(name: str, profile: ProfileConfig, is_default: bool = False) -> Dict[str, object]:
    return {
        "name": name,
        "host": profile.host,
        "port": profile.port,
        "client_id": profile.client_id,
        "mode": profile.mode,
        "default": is_default,
    }

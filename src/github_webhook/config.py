"""YAML-based configuration with dataclass schemas."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class ServerConfig:
    """Server and worker pool settings."""

    host: str = "0.0.0.0"
    port: int = 5000
    worker_count: int = 4
    processing_timeout: int = 30
    max_payload_bytes: int = 25 * 1024 * 1024
    db_path: str = "events.db"


@dataclass
class RetryConfig:
    """Retry behaviour for transient handler failures."""

    max_attempts: int = 3
    backoff_base: float = 2.0


@dataclass
class RetentionConfig:
    """How long processed events are kept before pruning."""

    success_hours: float = 4.0
    failed_days: float = 4.0
    prune_interval_minutes: float = 60.0


@dataclass
class GitHubConfig:
    """GitHub provider settings."""

    webhook_secret: str = ""

    def read_secret(self) -> bytes:
        """Read the webhook secret. If the value is a file path, read from it."""
        value = self.webhook_secret
        if not value:
            raise RuntimeError("github.webhook_secret is not configured")
        path = Path(value)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("github.webhook_secret file is empty")
        return value.encode()


@dataclass
class Config:
    """Top-level configuration container."""

    server: ServerConfig = field(default_factory=ServerConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)


def load_config(path: Path = _DEFAULT_CONFIG_PATH) -> Config:
    """Load configuration from a YAML file, falling back to defaults."""
    if not path.exists():
        print(f"Warning: {path} not found, using defaults", file=sys.stderr)
        return Config()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    return Config(
        server=ServerConfig(**raw.get("server", {})),
        retry=RetryConfig(**raw.get("retry", {})),
        retention=RetentionConfig(**raw.get("retention", {})),
        github=GitHubConfig(**raw.get("github", {})),
    )


cfg: Config = Config()


def init_config(path: Path = _DEFAULT_CONFIG_PATH) -> None:
    """Load config from *path* into the module-level ``cfg`` singleton.

    Must be called before the app starts. Updates ``cfg`` in place so that
    all modules that imported it see the updated values.
    """
    loaded = load_config(path)
    for f in fields(Config):
        setattr(cfg, f.name, getattr(loaded, f.name))

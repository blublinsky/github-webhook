"""CLI entrypoint — ``python -m github_webhook`` or ``github-webhook``."""

import argparse
from pathlib import Path

import uvicorn

from .config import cfg, init_config


def main() -> None:
    """Parse CLI args, load config, and start the server."""
    parser = argparse.ArgumentParser(description="GitHub Webhook Handler")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="path to config.yaml (default: ./config.yaml)",
    )
    args = parser.parse_args()

    init_config(Path(args.config))
    cfg.github.read_secret()

    uvicorn.run(
        "github_webhook.app:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

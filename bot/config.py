from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Credentials:
    api_key: str
    api_secret: str
    paper: bool


def load_credentials() -> Credentials:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_API_SECRET")
    if not key or not secret:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_API_SECRET missing. Copy .env.example to .env "
            "and fill in the keys from your AGGRESSIVE-BOT paper account."
        )
    paper = os.getenv("ALPACA_PAPER", "true").lower() != "false"
    if not paper:
        raise RuntimeError(
            "ALPACA_PAPER is false. This bot is paper-only by design — "
            "set ALPACA_PAPER=true or remove the env var."
        )
    return Credentials(api_key=key, api_secret=secret, paper=paper)


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r") as f:
        return yaml.safe_load(f)

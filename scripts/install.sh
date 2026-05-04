#!/usr/bin/env bash
# One-shot setup. Run from the project root after cloning.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Creating virtualenv at .venv"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Upgrading pip"
pip install --upgrade pip

echo "==> Installing requirements"
pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example (edit it with your AGGRESSIVE-BOT paper keys)"
  cp .env.example .env
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env with your second Alpaca PAPER account keys."
echo "  2. Run: source .venv/bin/activate && python -m bot.main status"
echo "  3. To install cron jobs: ./scripts/setup_cron.sh"

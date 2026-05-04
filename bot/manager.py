"""Exit management: per-position stop / target / trailing-stop on premium.

Runs every `exit_check_interval_seconds` while any position is open.
"""
from __future__ import annotations

import json
from pathlib import Path

from .alpaca_client import AlpacaClient
from .logger import get_logger
from .risk import record_exit

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
POSITIONS_FILE = STATE_DIR / "open_positions.json"


def _load() -> dict:
    if POSITIONS_FILE.exists():
        return json.loads(POSITIONS_FILE.read_text())
    return {}


def _save(d: dict):
    POSITIONS_FILE.write_text(json.dumps(d, indent=2, default=str))


def manage_exits(client: AlpacaClient, r: dict):
    """Walk every tracked position and decide whether to close it."""
    positions = _load()
    if not positions:
        return

    # Cross-check against broker — if a position no longer exists at the broker,
    # remove it from our tracker (it may have been closed manually or by close-all).
    broker_symbols = {p.symbol for p in client.option_positions()}
    stale_locally = [s for s in positions.keys() if s not in broker_symbols]
    for s in stale_locally:
        log.info(f"{s}: not at broker anymore, removing from tracker")
        positions.pop(s, None)

    for symbol, data in list(positions.items()):
        quote = client.option_mid(symbol)
        if quote is None:
            log.debug(f"{symbol}: no quote, skipping check")
            continue
        bid, ask, mid = quote

        entry = data["entry_price"]
        peak = data.get("peak_price", entry)
        if mid > peak:
            data["peak_price"] = mid
            peak = mid

        change_pct = (mid - entry) / entry * 100 if entry > 0 else 0
        peak_change_pct = (peak - entry) / entry * 100 if entry > 0 else 0

        exit_reason = None

        # Hard stop on premium loss.
        if change_pct <= -r["stop_loss_pct"]:
            exit_reason = f"stop_loss ({change_pct:.1f}%)"
        # Hard target.
        elif change_pct >= r["take_profit_pct"]:
            exit_reason = f"take_profit ({change_pct:.1f}%)"
        # Trailing stop.
        else:
            if not data.get("trail_active", False) and peak_change_pct >= r["trail_activate_pct"]:
                data["trail_active"] = True
                log.info(f"{symbol}: trailing stop ARMED at +{peak_change_pct:.1f}% peak")
            if data.get("trail_active", False):
                drop_from_peak = (peak - mid) / peak * 100 if peak > 0 else 0
                if drop_from_peak >= r["trail_distance_pct"]:
                    exit_reason = f"trailing_stop (peak={peak_change_pct:.1f}%, dropped {drop_from_peak:.1f}% from peak)"

        if exit_reason:
            try:
                client.close_position(symbol)
                pnl_per_contract = (bid - entry) * 100
                pnl_total = pnl_per_contract * data["qty"]
                log.info(
                    f"EXIT {symbol} reason={exit_reason} bid={bid:.2f} entry={entry:.2f} "
                    f"qty={data['qty']} pnl=${pnl_total:+.2f}"
                )
                record_exit(symbol, bid, exit_reason, pnl_total)
                positions.pop(symbol, None)
            except Exception as e:
                log.error(f"Failed to close {symbol}: {e}")
        else:
            log.debug(
                f"{symbol}: bid={bid:.2f} mid={mid:.2f} entry={entry:.2f} "
                f"chg={change_pct:+.1f}% peak={peak_change_pct:+.1f}% "
                f"trail={'armed' if data.get('trail_active') else 'off'}"
            )

    _save(positions)

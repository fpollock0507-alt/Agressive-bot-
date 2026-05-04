"""Submit option entries with limit-then-market fallback.

Pure market orders on options bleed the spread (sometimes 5%+ on 0DTE) so we
try a limit at (ask + offset_cents/100) for `limit_timeout_seconds`, then fall
back to market if unfilled.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from alpaca.trading.enums import OrderSide

from .alpaca_client import AlpacaClient
from .logger import get_logger
from .risk import Sizing, record_trade
from .strategy import Signal

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
POSITIONS_FILE = STATE_DIR / "open_positions.json"


def _load_positions() -> dict:
    if POSITIONS_FILE.exists():
        return json.loads(POSITIONS_FILE.read_text())
    return {}


def _save_positions(d: dict):
    POSITIONS_FILE.write_text(json.dumps(d, indent=2, default=str))


def track_position(contract_symbol: str, qty: int, entry_price: float, signal: Signal):
    positions = _load_positions()
    positions[contract_symbol] = {
        "qty": qty,
        "entry_price": entry_price,
        "peak_price": entry_price,
        "trail_active": False,
        "underlying": signal.underlying,
        "direction": signal.direction,
        "opened_at": date.today().isoformat(),
    }
    _save_positions(positions)


def untrack_position(contract_symbol: str):
    positions = _load_positions()
    positions.pop(contract_symbol, None)
    _save_positions(positions)


def execute_signal(client: AlpacaClient, signal: Signal, sizing: Sizing, ex: dict) -> str | None:
    """Submit the option entry. Returns order_id on success."""
    limit_price = signal.entry_ask + (ex["limit_offset_cents"] / 100)

    try:
        order = client.submit_option_limit(
            symbol=signal.contract_symbol,
            qty=sizing.qty,
            side=OrderSide.BUY,
            limit_price=limit_price,
        )
        oid = str(order.id)
        log.info(
            f"LIMIT BUY {sizing.qty} {signal.contract_symbol} @ {limit_price:.2f} "
            f"(ask={signal.entry_ask:.2f}, mid={signal.entry_mid:.2f}, cost=${sizing.total_cost:,.0f}, "
            f"target={sizing.target_pct:.1f}% actual={sizing.actual_pct:.2f}%)"
        )
    except Exception as e:
        log.error(f"Limit submission failed for {signal.contract_symbol}: {e}")
        return None

    # Wait for fill, then fall back to market.
    waited = 0
    fill_price = None
    while waited < ex["limit_timeout_seconds"]:
        time.sleep(1)
        waited += 1
        try:
            fresh = client.trading.get_order_by_id(oid)
            if str(fresh.status) in ("OrderStatus.FILLED", "filled"):
                fill_price = float(fresh.filled_avg_price or limit_price)
                log.info(f"FILLED {signal.contract_symbol} @ {fill_price:.2f}")
                break
        except Exception as e:
            log.debug(f"Order status check failed: {e}")

    if fill_price is None:
        # Cancel + market.
        try:
            client.trading.cancel_order_by_id(oid)
            log.info(f"Limit unfilled after {ex['limit_timeout_seconds']}s, cancelled. Falling back to market.")
        except Exception as e:
            log.warning(f"Cancel failed (may already be filled): {e}")
            try:
                fresh = client.trading.get_order_by_id(oid)
                if str(fresh.status) in ("OrderStatus.FILLED", "filled"):
                    fill_price = float(fresh.filled_avg_price or limit_price)
            except Exception:
                pass

        if fill_price is None:
            try:
                mkt = client.submit_option_market(
                    symbol=signal.contract_symbol,
                    qty=sizing.qty,
                    side=OrderSide.BUY,
                )
                oid = str(mkt.id)
                # Brief wait for market fill.
                for _ in range(5):
                    time.sleep(1)
                    fresh = client.trading.get_order_by_id(oid)
                    if str(fresh.status) in ("OrderStatus.FILLED", "filled"):
                        fill_price = float(fresh.filled_avg_price or signal.entry_ask)
                        log.info(f"MARKET FILLED {signal.contract_symbol} @ {fill_price:.2f}")
                        break
                if fill_price is None:
                    log.warning(f"Market order {oid} not yet filled. Tracking at ask as estimate.")
                    fill_price = signal.entry_ask
            except Exception as e:
                log.error(f"Market fallback failed for {signal.contract_symbol}: {e}")
                return None

    record_trade(signal, sizing, oid, fill_price=fill_price)
    track_position(signal.contract_symbol, sizing.qty, fill_price, signal)
    return oid


def flatten_all(client: AlpacaClient):
    log.info("Flattening all positions and cancelling open orders.")
    try:
        client.cancel_all_orders()
        client.close_all_positions()
        # Clear local tracker — positions are gone.
        _save_positions({})
    except Exception as e:
        log.error(f"Flatten failed: {e}")

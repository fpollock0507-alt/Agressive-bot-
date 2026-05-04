"""Risk management: position sizing + kill switches.

Sizing model:
  - Spend `premium_pct_per_trade` % of equity on each contract.
  - qty = floor(target_premium / (mid * 100))   # 100 = options multiplier.

Kill switches:
  - Daily loss cap: stop + flatten if equity drops > daily_loss_cap_pct from open.
  - Daily profit lock: stop + flatten if equity is up > daily_profit_target_pct.
  - Max trades/day: hard stop on count.
  - Max concurrent positions: gates new entries.
  - WEEKLY loss cap: stop the bot for the rest of the week.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .alpaca_client import AlpacaClient
from .logger import get_logger
from .strategy import Signal

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(exist_ok=True)


@dataclass
class Sizing:
    qty: int
    premium_per_contract: float  # mid in dollars per share
    total_cost: float            # qty * mid * 100
    target_pct: float            # what we asked for (e.g. 7.0)
    actual_pct: float            # what we got after rounding


def size_position(signal: Signal, equity: float, cash: float, r: dict) -> Sizing | None:
    target_pct = r["premium_pct_per_trade"]
    target_dollars = equity * (target_pct / 100)

    # Use the ASK as the conservative cost estimate (we cross spread on entry).
    cost_per_contract = signal.entry_ask * 100
    if cost_per_contract <= 0:
        return None

    qty = int(target_dollars // cost_per_contract)
    if qty < 1:
        log.info(f"{signal.underlying}: qty=0 ({signal.contract_symbol} ask {signal.entry_ask:.2f} too rich for {target_pct}% sizing)")
        return None

    total_cost = qty * cost_per_contract
    if total_cost > cash * 0.98:  # 2% safety buffer
        # Try shrinking by one
        qty -= 1
        if qty < 1:
            log.info(f"{signal.underlying}: insufficient cash for one contract")
            return None
        total_cost = qty * cost_per_contract

    actual_pct = total_cost / equity * 100
    return Sizing(
        qty=qty,
        premium_per_contract=signal.entry_mid,
        total_cost=total_cost,
        target_pct=target_pct,
        actual_pct=actual_pct,
    )


def can_trade_today(client: AlpacaClient, r: dict, starting_equity: float, weekly_start_equity: float) -> tuple[bool, str]:
    current = client.equity()

    # Weekly kill — strongest gate.
    weekly_pnl_pct = (current - weekly_start_equity) / weekly_start_equity * 100
    if weekly_pnl_pct <= -r["weekly_loss_cap_pct"]:
        return False, f"WEEKLY loss cap hit ({weekly_pnl_pct:.2f}%). Bot halted for the week."

    # Daily kill.
    daily_pnl_pct = (current - starting_equity) / starting_equity * 100
    if daily_pnl_pct <= -r["daily_loss_cap_pct"]:
        return False, f"Daily loss cap hit ({daily_pnl_pct:.2f}%). Trading halted, flattening."
    if daily_pnl_pct >= r["daily_profit_target_pct"]:
        return False, f"Daily profit target hit ({daily_pnl_pct:.2f}%). Locking in gains."

    trades_today = count_trades_today()
    if trades_today >= r["max_trades_per_day"]:
        return False, f"Max trades/day reached ({trades_today}/{r['max_trades_per_day']})."

    open_positions = len(client.option_positions())
    if open_positions >= r["max_concurrent_positions"]:
        return False, f"Max concurrent positions ({open_positions}) reached."

    return True, f"OK ({trades_today} trades, daily {daily_pnl_pct:+.2f}%, weekly {weekly_pnl_pct:+.2f}%)"


def record_trade(signal: Signal, sizing: Sizing, order_id: str, fill_price: float | None = None):
    path = STATE_DIR / f"trades_{date.today().isoformat()}.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow([
                "timestamp", "underlying", "direction", "contract_symbol",
                "contract_type", "strike", "expiry", "qty",
                "entry_mid", "entry_ask", "fill_price", "total_cost",
                "target_pct", "actual_pct", "spread_pct", "reason", "order_id",
            ])
        w.writerow([
            datetime.now().isoformat(), signal.underlying, signal.direction,
            signal.contract_symbol, signal.contract_type, signal.contract_strike,
            signal.contract_expiry.isoformat(), sizing.qty,
            f"{signal.entry_mid:.2f}", f"{signal.entry_ask:.2f}",
            f"{fill_price:.2f}" if fill_price else "",
            f"{sizing.total_cost:.2f}",
            f"{sizing.target_pct:.2f}", f"{sizing.actual_pct:.2f}",
            f"{signal.spread_pct:.2f}", signal.reason, order_id,
        ])


def record_exit(contract_symbol: str, exit_price: float, reason: str, pnl: float):
    path = STATE_DIR / f"exits_{date.today().isoformat()}.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "contract_symbol", "exit_price", "reason", "pnl"])
        w.writerow([
            datetime.now().isoformat(), contract_symbol,
            f"{exit_price:.2f}", reason, f"{pnl:.2f}",
        ])


def count_trades_today() -> int:
    path = STATE_DIR / f"trades_{date.today().isoformat()}.csv"
    if not path.exists():
        return 0
    with path.open() as f:
        return max(0, sum(1 for _ in f) - 1)


def get_starting_equity(client: AlpacaClient) -> float:
    """Today's open equity, cached so a mid-session restart sees the same value."""
    path = STATE_DIR / f"equity_{date.today().isoformat()}.txt"
    if path.exists():
        return float(path.read_text().strip())
    eq = client.equity()
    path.write_text(str(eq))
    return eq


def get_weekly_start_equity(client: AlpacaClient) -> float:
    """Monday's open equity. Cached as `equity_week_<MondayISO>.txt`."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    path = STATE_DIR / f"equity_week_{monday.isoformat()}.txt"
    if path.exists():
        return float(path.read_text().strip())
    # First time we see this week: snapshot today's open as the weekly start.
    # (If the bot first runs on a Wed, that's the cleanest baseline available.)
    eq = get_starting_equity(client)
    path.write_text(str(eq))
    return eq

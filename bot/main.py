"""Main orchestrator for the aggressive options bot.

    python -m bot.main session   # market hours: scan + manage exits
    python -m bot.main eod       # after close: write report + dashboard + push
    python -m bot.main status    # one-shot account dump
    python -m bot.main flatten   # emergency: close everything
    python -m bot.main dashboard # rebuild the 30-day dashboard from history

Driven by cron — see scripts/setup_cron.sh.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from .alpaca_client import AlpacaClient
from .config import load_config, load_credentials
from .executor import execute_signal, flatten_all
from .logger import get_logger
from .manager import manage_exits
from .reporter import git_push_reports, write_dashboard, write_eod_report
from .risk import (
    can_trade_today,
    get_starting_equity,
    get_weekly_start_equity,
    size_position,
)
from .strategy import scan_for_signals

ET = ZoneInfo("America/New_York")
log = get_logger("main")

KILL_SWITCH_REASON = ""  # populated by run_session if a switch fires


def _client() -> AlpacaClient:
    return AlpacaClient(load_credentials())


def run_session():
    global KILL_SWITCH_REASON
    cfg = load_config()
    client = _client()

    if not client.is_market_open():
        log.info("Market closed. Exiting session loop.")
        return

    starting_equity = get_starting_equity(client)
    weekly_start = get_weekly_start_equity(client)
    log.info(
        f"Session start. Equity: ${client.equity():,.2f} (day-open ${starting_equity:,.2f}, "
        f"week-open ${weekly_start:,.2f})"
    )

    # Nothing should carry over (force-flat at 15:45 + 0DTE expires).
    # But if the Mac slept yesterday and missed flatten, clean up now.
    stale = client.option_positions()
    if stale:
        log.warning(f"Carryover detected: {len(stale)} stale option position(s). Flattening.")
        for p in stale:
            log.warning(f"  - {p.symbol} qty={p.qty} avg={p.avg_entry_price} uPnL={p.unrealized_pl}")
        flatten_all(client)
        time.sleep(5)

    s = cfg["strategy"]
    r = cfg["risk"]
    ex = cfg["execution"]

    force_flat = dtime.fromisoformat(s["force_flat_time"])
    entry_end = dtime.fromisoformat(s["entry_window_end"])
    scan_interval = ex["scan_interval_seconds"]
    exit_interval = ex["exit_check_interval_seconds"]
    last_scan = 0.0
    already_taken: set[str] = set()  # contract_symbols already submitted

    while True:
        now_et = datetime.now(ET).time()

        if now_et >= force_flat:
            log.info("Force-flat time reached. Closing positions.")
            flatten_all(client)
            break

        if not client.is_market_open():
            log.info("Market closed mid-session. Flattening and exiting.")
            flatten_all(client)
            break

        # Always manage open positions, regardless of trading-gate state.
        manage_exits(client, r)

        ok, reason = can_trade_today(client, r, starting_equity, weekly_start)
        if not ok:
            log.info(f"Trading halted: {reason}")
            if "loss cap" in reason or "profit target" in reason:
                KILL_SWITCH_REASON = reason
                flatten_all(client)
                break
            # Max trades or positions: keep managing exits, just don't open new.
            time.sleep(exit_interval)
            continue

        # Scan only inside entry window and only every scan_interval seconds.
        in_window = now_et <= entry_end
        if in_window and (time.time() - last_scan >= scan_interval):
            last_scan = time.time()
            try:
                signals = scan_for_signals(client, cfg)
            except Exception as e:
                log.error(f"Scan failed: {e}")
                signals = []

            held = {p.symbol for p in client.option_positions()}
            for sig in signals:
                if sig.contract_symbol in held or sig.contract_symbol in already_taken:
                    continue
                ok, reason = can_trade_today(client, r, starting_equity, weekly_start)
                if not ok:
                    log.info(f"Risk gate closed mid-loop: {reason}")
                    break

                equity = client.equity()
                cash = client.cash()
                sizing = size_position(sig, equity, cash, r)
                if sizing is None:
                    continue

                oid = execute_signal(client, sig, sizing, ex)
                if oid:
                    already_taken.add(sig.contract_symbol)

        time.sleep(exit_interval)

    log.info("Session loop ended.")


def run_eod():
    cfg = load_config()
    client = _client()
    starting_equity = get_starting_equity(client)
    write_eod_report(client, starting_equity, kill_switch_reason=KILL_SWITCH_REASON)
    write_dashboard(window_days=cfg["reporting"]["dashboard_window_days"])
    if cfg["reporting"]["git_push_on_eod"]:
        git_push_reports(cfg["reporting"]["git_branch"])


def print_status():
    client = _client()
    acct = client.account()
    print(f"Account:          {acct.account_number}")
    print(f"Status:           {acct.status}")
    print(f"Equity:           ${float(acct.equity):,.2f}")
    print(f"Cash:             ${float(acct.cash):,.2f}")
    print(f"Buying power:     ${float(acct.buying_power):,.2f}")
    print(f"Options enabled:  {getattr(acct, 'options_trading_level', 'unknown')}")
    print(f"Market open:      {client.is_market_open()}")
    positions = client.positions()
    print(f"Open positions:   {len(positions)}")
    for p in positions:
        print(f"  {p.symbol:25} {p.qty:>4} @ {p.avg_entry_price}  uPnL {p.unrealized_pl}")


def rebuild_dashboard():
    cfg = load_config()
    write_dashboard(window_days=cfg["reporting"]["dashboard_window_days"])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "session":
        run_session()
    elif cmd == "eod":
        run_eod()
    elif cmd == "status":
        print_status()
    elif cmd == "flatten":
        flatten_all(_client())
    elif cmd == "dashboard":
        rebuild_dashboard()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

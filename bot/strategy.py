"""Strategy: opening-range momentum on SPY/QQQ → short-dated option.

Flow per scan:
  1. For each underlying (SPY, QQQ):
     - Compute opening-range high/low from the first N min after 9:30 ET.
     - Filter on range size (not too dead, not already exploded).
     - Confirm breakout volume.
     - Determine direction (long if > OR high, short if < OR low).
  2. If `require_index_confirmation`, require the OTHER index agrees on side.
  3. Pick the option contract:
     - Same-day expiry (0DTE) when available; otherwise next trading day.
     - Calls for long, puts for short.
     - `strikes_otm` controls how far OTM (0 = ATM).
  4. Validate the contract: spread tight enough, premium above the floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.trading.enums import ContractType

from .alpaca_client import AlpacaClient
from .logger import get_logger

ET = ZoneInfo("America/New_York")
log = get_logger(__name__)


@dataclass
class UnderlyingState:
    symbol: str
    or_high: float
    or_low: float
    or_volume: float
    last_price: float
    direction: str | None  # "long", "short", or None


@dataclass
class Signal:
    underlying: str
    direction: str  # "long" (call) or "short" (put)
    underlying_price: float
    or_high: float
    or_low: float
    contract_symbol: str
    contract_strike: float
    contract_expiry: date
    contract_type: str  # "call" or "put"
    entry_bid: float
    entry_ask: float
    entry_mid: float
    spread_pct: float
    reason: str


def _opening_range(bars: pd.DataFrame, minutes: int, session_date) -> tuple[float, float, float] | None:
    if bars.empty:
        return None
    bars = bars.copy()
    if bars.index.tz is None:
        bars.index = bars.index.tz_localize("UTC")
    bars_et = bars.tz_convert(ET)
    rth_open = datetime.combine(session_date, time(9, 30), tzinfo=ET)
    rth_end = datetime.combine(session_date, time(9, 30 + minutes), tzinfo=ET)
    window = bars_et[(bars_et.index >= rth_open) & (bars_et.index < rth_end)]
    if window.empty:
        return None
    return float(window["high"].max()), float(window["low"].min()), float(window["volume"].sum())


def _read_underlying(client: AlpacaClient, sym: str, s: dict, session_date) -> UnderlyingState | None:
    bars = client.minute_bars(sym, lookback_minutes=480)
    if bars.empty:
        log.debug(f"{sym}: no minute bars")
        return None
    or_result = _opening_range(bars, s["opening_range_minutes"], session_date)
    if or_result is None:
        log.debug(f"{sym}: opening range not yet formed")
        return None
    or_high, or_low, or_volume = or_result
    last = float(bars["close"].iloc[-1])

    range_pct = (or_high - or_low) / or_low * 100
    if range_pct < s["min_range_pct"] or range_pct > s["max_range_pct"]:
        log.debug(f"{sym}: range {range_pct:.2f}% outside band")
        return UnderlyingState(sym, or_high, or_low, or_volume, last, None)

    # Volume confirmation against avg-OR-window proxy from 20 daily bars.
    daily = client.daily_bars(sym, days=25)
    if not daily.empty and len(daily) >= 20:
        avg_or_volume = daily["volume"].tail(20).mean() * (s["opening_range_minutes"] / 390)
        vol_ratio = or_volume / avg_or_volume if avg_or_volume > 0 else 0
        if vol_ratio < s["volume_confirm_multiplier"]:
            log.debug(f"{sym}: volume {vol_ratio:.2f}x < {s['volume_confirm_multiplier']}x")
            return UnderlyingState(sym, or_high, or_low, or_volume, last, None)

    direction = None
    if last > or_high:
        direction = "long"
    elif last < or_low:
        direction = "short"

    return UnderlyingState(sym, or_high, or_low, or_volume, last, direction)


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _pick_expiry(today: date, preferred_dte: int) -> date:
    if preferred_dte == 0:
        return today
    target = today
    for _ in range(preferred_dte):
        target = _next_trading_day(target)
    return target


def _pick_contract(
    client: AlpacaClient,
    underlying: str,
    direction: str,
    underlying_price: float,
    expiration: date,
    strikes_otm: int,
):
    """Pick the contract `strikes_otm` strikes out of the money on the right side."""
    contract_type = ContractType.CALL if direction == "long" else ContractType.PUT
    chain = client.get_option_chain(underlying, expiration, contract_type)
    if not chain:
        return None

    # Sort by strike ascending and find the index of the ATM strike.
    chain_sorted = sorted(chain, key=lambda c: float(c.strike_price))
    strikes = [float(c.strike_price) for c in chain_sorted]

    # Find the closest strike to the underlying price.
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_price))

    # OTM offset: calls move up, puts move down.
    if direction == "long":
        target_idx = min(atm_idx + strikes_otm, len(chain_sorted) - 1)
    else:
        target_idx = max(atm_idx - strikes_otm, 0)

    return chain_sorted[target_idx]


def scan_for_signals(client: AlpacaClient, cfg: dict) -> list[Signal]:
    s = cfg["strategy"]
    now_et = datetime.now(ET)
    session_date = now_et.date()

    entry_start = time.fromisoformat(s["entry_window_start"])
    entry_end = time.fromisoformat(s["entry_window_end"])
    if not (entry_start <= now_et.time() <= entry_end):
        log.debug(f"Outside entry window {s['entry_window_start']}–{s['entry_window_end']}")
        return []

    states: dict[str, UnderlyingState] = {}
    for sym in s["underlyings"]:
        st = _read_underlying(client, sym, s, session_date)
        if st is not None:
            states[sym] = st

    signals: list[Signal] = []
    for sym, st in states.items():
        if st.direction is None:
            continue

        # Index correlation filter: the other index must NOT be against us.
        if s.get("require_index_confirmation", True) and len(states) > 1:
            other = next((o for k, o in states.items() if k != sym), None)
            if other is not None and other.direction is not None and other.direction != st.direction:
                log.info(f"{sym}: skipping — other index says {other.direction}, we say {st.direction}")
                continue

        expiry = _pick_expiry(session_date, s["preferred_dte"])
        contract = _pick_contract(client, sym, st.direction, st.last_price, expiry, s["strikes_otm"])

        # If 0DTE not available (e.g. QQQ on Tue/Thu), bump to 1DTE.
        if contract is None and s["preferred_dte"] == 0:
            expiry = _next_trading_day(session_date)
            contract = _pick_contract(client, sym, st.direction, st.last_price, expiry, s["strikes_otm"])

        if contract is None:
            log.warning(f"{sym}: no option contract found for {st.direction} expiring {expiry}")
            continue

        quote = client.option_mid(contract.symbol)
        if quote is None:
            log.warning(f"{sym}: no quote for {contract.symbol}")
            continue
        bid, ask, mid = quote

        if mid < s["min_premium"]:
            log.info(f"{sym}: premium {mid:.2f} below floor {s['min_premium']}")
            continue

        spread_pct = (ask - bid) / mid * 100 if mid > 0 else 100
        if spread_pct > s["max_spread_pct"]:
            log.info(f"{sym}: spread {spread_pct:.2f}% > {s['max_spread_pct']}%")
            continue

        signals.append(Signal(
            underlying=sym,
            direction=st.direction,
            underlying_price=st.last_price,
            or_high=st.or_high,
            or_low=st.or_low,
            contract_symbol=contract.symbol,
            contract_strike=float(contract.strike_price),
            contract_expiry=expiry,
            contract_type="call" if st.direction == "long" else "put",
            entry_bid=bid,
            entry_ask=ask,
            entry_mid=mid,
            spread_pct=spread_pct,
            reason=(
                f"{sym} ORB-{st.direction} px={st.last_price:.2f} "
                f"OR={st.or_low:.2f}-{st.or_high:.2f} "
                f"contract={contract.symbol} mid={mid:.2f} spr={spread_pct:.1f}%"
            ),
        ))
    return signals

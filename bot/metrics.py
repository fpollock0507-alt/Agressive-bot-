"""Compute performance metrics across the history of daily summaries + trades."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
SUMMARY_FILE = STATE_DIR / "daily_summary.csv"


@dataclass
class DailyRow:
    day: date
    starting_equity: float
    ending_equity: float
    pnl: float
    pnl_pct: float
    trades: int
    wins: int
    losses: int
    kill_switch: str  # "" if none, else reason


@dataclass
class Stats:
    days: int
    cum_return_pct: float
    avg_daily_return_pct: float
    median_daily_return_pct: float
    best_day_pct: float
    worst_day_pct: float
    days_above_5: int
    days_below_neg5: int
    days_kill_switch: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_annualized: float
    sortino_annualized: float
    calmar: float
    total_trades: int
    total_wins: int
    total_losses: int


def append_daily_summary(row: DailyRow):
    SUMMARY_FILE.parent.mkdir(exist_ok=True)
    new = not SUMMARY_FILE.exists()
    # If today already has a row, replace it (allows re-running EOD).
    rows = read_daily_summary()
    rows = [r for r in rows if r.day != row.day]
    rows.append(row)
    rows.sort(key=lambda r: r.day)
    with SUMMARY_FILE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "starting_equity", "ending_equity", "pnl", "pnl_pct", "trades", "wins", "losses", "kill_switch"])
        for r in rows:
            w.writerow([
                r.day.isoformat(), f"{r.starting_equity:.2f}", f"{r.ending_equity:.2f}",
                f"{r.pnl:.2f}", f"{r.pnl_pct:.4f}", r.trades, r.wins, r.losses, r.kill_switch,
            ])


def read_daily_summary() -> list[DailyRow]:
    if not SUMMARY_FILE.exists():
        return []
    out = []
    with SUMMARY_FILE.open() as f:
        for r in csv.DictReader(f):
            out.append(DailyRow(
                day=date.fromisoformat(r["day"]),
                starting_equity=float(r["starting_equity"]),
                ending_equity=float(r["ending_equity"]),
                pnl=float(r["pnl"]),
                pnl_pct=float(r["pnl_pct"]),
                trades=int(r["trades"]),
                wins=int(r["wins"]),
                losses=int(r["losses"]),
                kill_switch=r.get("kill_switch", ""),
            ))
    return out


def read_exits_for_day(day: date) -> list[dict]:
    path = STATE_DIR / f"exits_{day.isoformat()}.csv"
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def read_trades_for_day(day: date) -> list[dict]:
    path = STATE_DIR / f"trades_{day.isoformat()}.csv"
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def compute_stats(window_days: int = 30) -> Stats:
    rows = read_daily_summary()
    if not rows:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    cutoff = date.today() - timedelta(days=window_days)
    rows = [r for r in rows if r.day >= cutoff]
    if not rows:
        return Stats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    daily_pcts = [r.pnl_pct for r in rows]
    n = len(daily_pcts)
    avg = sum(daily_pcts) / n
    sorted_pcts = sorted(daily_pcts)
    median = sorted_pcts[n // 2] if n % 2 else (sorted_pcts[n // 2 - 1] + sorted_pcts[n // 2]) / 2

    best = max(daily_pcts)
    worst = min(daily_pcts)
    days_above_5 = sum(1 for p in daily_pcts if p >= 5)
    days_below_neg5 = sum(1 for p in daily_pcts if p <= -5)
    days_kill = sum(1 for r in rows if r.kill_switch)

    # Cumulative compound return.
    cum = 1.0
    for p in daily_pcts:
        cum *= (1 + p / 100)
    cum_return_pct = (cum - 1) * 100

    # Win/loss stats from per-trade exits.
    all_exits = []
    for r in rows:
        for e in read_exits_for_day(r.day):
            try:
                all_exits.append(float(e["pnl"]))
            except Exception:
                pass
    wins = [p for p in all_exits if p > 0]
    losses = [p for p in all_exits if p < 0]
    total_trades = len(all_exits)
    total_wins = len(wins)
    total_losses = len(losses)
    win_rate = total_wins / total_trades * 100 if total_trades else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0  # negative
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    expectancy = (avg_win * (total_wins / total_trades) +
                  avg_loss * (total_losses / total_trades)) if total_trades else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0
    )

    # Max drawdown on the cumulative equity curve.
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in daily_pcts:
        eq *= (1 + p / 100)
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100  # negative or zero
        max_dd = min(max_dd, dd)

    # Sharpe / Sortino (annualized, assume 252 trading days).
    if n > 1:
        mean_d = avg
        var = sum((p - mean_d) ** 2 for p in daily_pcts) / (n - 1)
        sd = math.sqrt(var)
        sharpe = (mean_d / sd) * math.sqrt(252) if sd > 0 else 0
        downside = [min(p, 0) for p in daily_pcts]
        ds_var = sum(d * d for d in downside) / n
        ds = math.sqrt(ds_var)
        sortino = (mean_d / ds) * math.sqrt(252) if ds > 0 else 0
    else:
        sharpe = 0
        sortino = 0

    # Calmar: annualized return / max DD.
    annualized = ((cum) ** (252 / n) - 1) * 100 if n > 0 else 0
    calmar = (annualized / abs(max_dd)) if max_dd < 0 else 0

    return Stats(
        days=n,
        cum_return_pct=cum_return_pct,
        avg_daily_return_pct=avg,
        median_daily_return_pct=median,
        best_day_pct=best,
        worst_day_pct=worst,
        days_above_5=days_above_5,
        days_below_neg5=days_below_neg5,
        days_kill_switch=days_kill,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        sharpe_annualized=sharpe,
        sortino_annualized=sortino,
        calmar=calmar,
        total_trades=total_trades,
        total_wins=total_wins,
        total_losses=total_losses,
    )

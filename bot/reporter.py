"""EOD report writer + 30-day rolling dashboard + git push."""
from __future__ import annotations

import csv
import subprocess
from datetime import date, datetime
from pathlib import Path

from .alpaca_client import AlpacaClient
from .logger import get_logger
from .metrics import (
    DailyRow,
    append_daily_summary,
    compute_stats,
    read_daily_summary,
    read_exits_for_day,
    read_trades_for_day,
)

log = get_logger(__name__)
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)


def write_eod_report(client: AlpacaClient, starting_equity: float, kill_switch_reason: str = "") -> Path:
    today = date.today()
    trades = read_trades_for_day(today)
    exits = read_exits_for_day(today)

    account = client.account()
    equity = float(account.equity)
    cash = float(account.cash)
    pnl = equity - starting_equity
    pnl_pct = (pnl / starting_equity * 100) if starting_equity else 0.0

    wins = sum(1 for e in exits if float(e.get("pnl", 0)) > 0)
    losses = sum(1 for e in exits if float(e.get("pnl", 0)) < 0)

    append_daily_summary(DailyRow(
        day=today,
        starting_equity=starting_equity,
        ending_equity=equity,
        pnl=pnl,
        pnl_pct=pnl_pct,
        trades=len(trades),
        wins=wins,
        losses=losses,
        kill_switch=kill_switch_reason,
    ))

    lines = []
    lines.append(f"# Aggressive Bot EOD — {today.isoformat()}")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Account")
    lines.append(f"- Starting equity: **${starting_equity:,.2f}**")
    lines.append(f"- Ending equity:   **${equity:,.2f}**")
    lines.append(f"- Day P&L:         **${pnl:+,.2f} ({pnl_pct:+.2f}%)**")
    lines.append(f"- Cash:            ${cash:,.2f}")
    if kill_switch_reason:
        lines.append(f"- **Kill switch fired:** {kill_switch_reason}")
    lines.append("")
    lines.append(f"## Entries: {len(trades)}")
    if trades:
        lines.append("")
        lines.append("| Time | Underlying | Dir | Contract | Strike | Exp | Qty | EntryMid | Fill | Cost$ | Spread% |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for t in trades:
            ts = t["timestamp"].split("T")[1][:8] if "T" in t["timestamp"] else t["timestamp"]
            lines.append(
                f"| {ts} | {t['underlying']} | {t['direction']} | {t['contract_symbol']} | "
                f"{t['strike']} | {t['expiry']} | {t['qty']} | {t['entry_mid']} | {t.get('fill_price','')} | "
                f"{t['total_cost']} | {t['spread_pct']} |"
            )
    else:
        lines.append("No entries today — filters did not trigger.")

    lines.append("")
    lines.append(f"## Exits: {len(exits)}")
    if exits:
        lines.append("")
        lines.append("| Time | Contract | Exit | Reason | P&L $ |")
        lines.append("|---|---|---|---|---|")
        for e in exits:
            ts = e["timestamp"].split("T")[1][:8] if "T" in e["timestamp"] else e["timestamp"]
            lines.append(
                f"| {ts} | {e['contract_symbol']} | {e['exit_price']} | {e['reason']} | {e['pnl']} |"
            )
    else:
        lines.append("No exits recorded today.")

    lines.append("")
    lines.append("## Open positions at close")
    positions = client.option_positions()
    if positions:
        lines.append("")
        lines.append("| Symbol | Qty | Avg Entry | Current | Unrealized P&L |")
        lines.append("|---|---|---|---|---|")
        for p in positions:
            lines.append(
                f"| {p.symbol} | {p.qty} | {p.avg_entry_price} | "
                f"{p.current_price} | {p.unrealized_pl} |"
            )
    else:
        lines.append("Flat at close.")

    content = "\n".join(lines) + "\n"
    path = REPORT_DIR / f"{today.isoformat()}.md"
    path.write_text(content)
    log.info(f"Wrote EOD report: {path}")
    return path


def write_dashboard(window_days: int = 30) -> Path:
    """30-day rolling performance dashboard. Overwrites each EOD."""
    s = compute_stats(window_days=window_days)
    rows = read_daily_summary()[-window_days:] if read_daily_summary() else []

    lines = []
    lines.append(f"# Aggressive Bot — {window_days}-Day Dashboard")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}  ")
    lines.append(f"Window: last {window_days} days  ")
    lines.append(f"Days recorded: {s.days}")
    lines.append("")

    lines.append("## Headline")
    lines.append(f"- **Cumulative return:** {s.cum_return_pct:+.2f}%")
    lines.append(f"- **Avg daily return:** {s.avg_daily_return_pct:+.2f}%   median {s.median_daily_return_pct:+.2f}%")
    lines.append(f"- **Best day:** {s.best_day_pct:+.2f}%   **Worst day:** {s.worst_day_pct:+.2f}%")
    lines.append(f"- **Max drawdown:** {s.max_drawdown_pct:.2f}%")
    lines.append("")

    lines.append("## Hitting the goal")
    lines.append(f"- Days ≥ +5%: **{s.days_above_5}** / {s.days}")
    lines.append(f"- Days ≤ -5%: **{s.days_below_neg5}** / {s.days}")
    lines.append(f"- Days kill-switch fired: **{s.days_kill_switch}** / {s.days}")
    lines.append("")

    lines.append("## Trade quality")
    lines.append(f"- Total trades: **{s.total_trades}**   wins {s.total_wins}   losses {s.total_losses}")
    lines.append(f"- Win rate: **{s.win_rate:.1f}%**")
    lines.append(f"- Avg win: ${s.avg_win:+,.2f}   Avg loss: ${s.avg_loss:+,.2f}")
    lines.append(f"- Expectancy/trade: **${s.expectancy:+,.2f}**")
    lines.append(f"- Profit factor: **{s.profit_factor:.2f}**")
    lines.append("")

    lines.append("## Risk-adjusted")
    lines.append(f"- Sharpe (annualized): **{s.sharpe_annualized:.2f}**")
    lines.append(f"- Sortino (annualized): **{s.sortino_annualized:.2f}**")
    lines.append(f"- Calmar: **{s.calmar:.2f}**")
    lines.append("")

    if rows:
        lines.append("## Daily breakdown")
        lines.append("")
        lines.append("| Day | Start $ | End $ | P&L $ | P&L % | Trades | W | L | Kill switch |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            ks = r.kill_switch or ""
            lines.append(
                f"| {r.day.isoformat()} | {r.starting_equity:,.0f} | {r.ending_equity:,.0f} | "
                f"{r.pnl:+,.0f} | {r.pnl_pct:+.2f}% | {r.trades} | {r.wins} | {r.losses} | {ks} |"
            )

    content = "\n".join(lines) + "\n"
    path = REPORT_DIR / "DASHBOARD.md"
    path.write_text(content)
    log.info(f"Wrote dashboard: {path}")
    return path


def git_push_reports(branch: str = "main") -> bool:
    try:
        if not (ROOT / ".git").exists():
            log.warning("Not a git repo; run `git init` and add a remote first. See README.")
            return False

        subprocess.run(["git", "add", "reports/", "state/", "logs/"], cwd=ROOT, check=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
        )
        if not status.stdout.strip():
            log.info("No changes to commit.")
            return True
        msg = f"Aggressive bot EOD {date.today().isoformat()}"
        subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        subprocess.run(["git", "push", "origin", branch], cwd=ROOT, check=True)
        log.info(f"Pushed EOD report to origin/{branch}.")
        return True
    except subprocess.CalledProcessError as e:
        log.error(f"Git push failed: {e}")
        return False

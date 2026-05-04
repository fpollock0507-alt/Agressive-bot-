# Aggressive Bot — 0DTE Options Momentum (Paper)

Sister-bot to the ORB stock bot in the parent directory. This one trades
**short-dated SPY/QQQ options** on opening-range momentum, sized to chase
6–10% days with hard kill-switches at -15% daily / -25% weekly.

**Paper trading only.** Refuses to start if `ALPACA_PAPER=false`.

---

## Reality check

The target (6–10% per day) is wildly aggressive. Renaissance's Medallion
Fund — the best fund ever — does that *per year*. The point of this bot is
to **measure honestly** what aggressive really costs:

- Realistic outcome over 30 days: a few huge green days, more red days
  than green, ending anywhere between -50% and +200%.
- The kill switches exist to keep the bot alive long enough to learn —
  not to guarantee profit.
- All metrics (win rate, expectancy, Sharpe, drawdown, days hit ≥ +5%)
  are tracked in `reports/DASHBOARD.md`, refreshed every EOD.

---

## Strategy

1. **Read SPY and QQQ:** compute the first 10-min opening range.
2. **Filter:** range size in `[0.15%, 1.5%]`, breakout volume ≥ 1.5× the
   per-window historical average.
3. **Trigger:** price closes above OR-high (long) or below OR-low (short).
4. **Cross-confirm:** if the *other* index is moving the opposite direction,
   skip — don't fade the broader market.
5. **Pick contract:** today's expiry (0DTE) when available, else next
   trading day; `strikes_otm` controls strike (default 1 OTM).
6. **Validate:** spread ≤ 5% of mid, premium ≥ $0.30.
7. **Enter:** limit at ask + 2¢ for 8s, market fallback if unfilled.
8. **Manage exits** every 10s: -40% stop / +75% target / trailing stop
   armed at +50%, then exit on -25% from peak.
9. **Force flat at 15:45 ET** — 0DTE goes to zero at 16:00.

## Risk controls

| Control | Value | Rationale |
|---|---|---|
| Premium per trade | 7% of equity | Single-trade max loss capped here |
| Stop / Target | -40% / +75% premium | Wide enough for gamma, tight enough to control |
| Trailing stop | armed +50%, trail 25% | Lets winners run, locks gains |
| Max trades/day | 4 | Avoids overtrading |
| Max concurrent | 2 | Limits gamma exposure |
| Daily kill | -15% equity | Flatten + halt for the day |
| Daily lock | +20% equity | Flatten + halt for the day |
| Weekly kill | -25% equity | Halt for the rest of the week |

All values live in `config.yaml`.

---

## Setup

### 1. Create a SECOND Alpaca paper account

You need a separate paper account from the ORB bot so equity, kill switches,
and metrics don't get tangled.

- Log in at https://app.alpaca.markets
- Account dropdown (top right) → Paper Accounts → **Create New Paper Account**
- Name it something like `aggressive-options`
- Switch into it, then **Account → Configurations → Options Trading**
  → enable **Level 2** (long calls/puts)
- API Keys → Generate New Key → copy Key ID + Secret immediately

If you only see one paper account slot, sign up with a `+alias` email
(e.g. `you+aggressive@gmail.com`) and use that account instead.

### 2. Install

```bash
cd "/Users/Finpollock/Trading routine/aggressive_bot"
./scripts/install.sh
```

This creates `.venv/`, installs requirements, and copies `.env.example` → `.env`.

### 3. Add the keys

Edit `.env`:
```
ALPACA_API_KEY=PK...      # from the NEW paper account
ALPACA_API_SECRET=...
ALPACA_PAPER=true
```

### 4. Smoke test

```bash
source .venv/bin/activate
python -m bot.main status
```

You should see:
- A different account number than the ORB bot
- $100,000 equity
- Options trading level 2+

### 5. (Optional) Hook to GitHub

If you want EOD reports + dashboard pushed to GitHub, init a repo here:

```bash
git init -b main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<your-user>/<repo>.git
git push -u origin main
```

If you skip this, set `reporting.git_push_on_eod: false` in `config.yaml`.

### 6. Install cron

```bash
./scripts/setup_cron.sh
```

This adds two entries (separate from the ORB bot's, identified by `# aggressive-bot:`):
- `23:30 AEST Mon–Fri` → start session loop
- `06:05 AEST Tue–Sat` → write EOD + dashboard + push

Both bots can coexist; cron runs them in parallel.

**macOS:** cron needs Full Disk Access — you should already have this set
for the ORB bot. If not, System Settings → Privacy & Security → Full Disk
Access → add `/usr/sbin/cron`.

---

## Manual commands

```bash
source .venv/bin/activate

python -m bot.main status        # account dump
python -m bot.main session       # run session loop (gated by market hours)
python -m bot.main eod           # write EOD report + dashboard
python -m bot.main dashboard     # rebuild dashboard from history only
python -m bot.main flatten       # EMERGENCY: close everything
```

---

## Files

```
bot/
├── main.py          # Entry point
├── config.py        # .env + config.yaml loader
├── alpaca_client.py # Trading + Stock + Options API wrapper
├── strategy.py      # ORB on SPY/QQQ → option contract selection
├── risk.py          # Sizing + daily/weekly kill switches
├── executor.py      # Limit-then-market option entries
├── manager.py       # Per-position stop/target/trailing exits
├── metrics.py       # 30-day stats (Sharpe, expectancy, drawdown, ...)
├── reporter.py      # EOD markdown + dashboard + git push
└── logger.py

scripts/
├── install.sh
├── run_session.sh   # cron entry: session loop wrapped in caffeinate
├── run_eod.sh       # cron entry: EOD + dashboard
└── setup_cron.sh

config.yaml          # All tunable parameters
state/               # Daily trade/exit CSVs, equity snapshots, daily summary
reports/             # Per-day .md + DASHBOARD.md (rolling 30-day)
logs/                # Per-day log files
```

---

## How to read DASHBOARD.md

After 30 days you'll have enough data to judge:

- **Hit rate on the goal:** `Days ≥ +5%` is the headline. If this is < ~5/30,
  the strategy isn't compounding the way the spec wants.
- **Cumulative return** vs **max drawdown:** the *real* answer to "is it
  working." A bot that returns +50% with -60% drawdown on the way is worse
  than one that returns +10% with -8%.
- **Expectancy + profit factor:** must be positive. Negative expectancy
  means each trade has negative EV — no amount of frequency saves it.
- **Days kill-switch fired:** if this is > 5/30, the strategy is too
  aggressive even by its own standards — tune `daily_loss_cap_pct` or
  `premium_pct_per_trade` down.

---

## Tuning knobs (in priority order)

1. `risk.premium_pct_per_trade` — biggest lever. 7% → 5% halves blow-up risk
   and roughly halves the upside tail.
2. `strategy.opening_range_minutes` — 10 = early/noisy. 15 = cleaner, fewer
   signals. 5 = max signals, max chop.
3. `strategy.strikes_otm` — 0 = ATM (highest delta, lowest leverage).
   1 = mild OTM (default). 2+ = pure lottery tickets.
4. `strategy.preferred_dte` — 0 = max gamma + max theta. 1 = a little more
   cushion, less explosive.
5. `risk.stop_loss_pct` / `take_profit_pct` — defines your R:R on premium.
   Current 40/75 ≈ 1.9R; raising target to 100 makes it 2.5R but lowers hit rate.

---

## Going live? Don't.

Not yet — and not with these settings. If after 30 days the dashboard
shows positive expectancy AND max drawdown ≤ 25% AND Sortino > 1.5:

- Halve every size knob (`premium_pct_per_trade: 3.0`, `max_trades_per_day: 2`).
- Rerun for another 30 days on paper.
- Only then consider live with TINY size (e.g. start at 1% per trade on
  $1k of risk capital — not the full account).

You can always scale up. You can't un-blow an account.

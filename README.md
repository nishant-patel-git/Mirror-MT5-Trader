# MT5-Trader

A **spread price-ladder trading terminal for MetaTrader 5** — a manual tool,
with an optional per-ladder **Algo**: in dry run it only signals; in LIVE,
switched on and confirmed by a person, it trades that ladder by its rules and
the ladder takes no manual orders while it does. A human looks at a ladder
of spread prices, clicks a price, and an order exists at that price.

Each ladder trades one pair of instruments across **two MT5 accounts** (Leg A on
account 1, Leg B on account 2) and displays the spread between them as a single
instrument, the way a CQG or TT inter-product ladder does.

## The specification

The full build specification is held privately, outside this repository.
Ask the repository owner for a copy — it is required reading before
writing any code.

## What is built so far

The spec's build order, in order. Steps 1-8 are in:

| Module | What it does |
|---|---|
| `mt5trader/config.py` | Accounts, pairs, settings; the three clash refusals; atomic saves; `.env` secrets |
| `mt5trader/broker.py` | The ONLY module that imports MetaTrader5 — every MT5 quirk lives here |
| `mt5trader/leg_runner.py`, `ipc.py`, `legs.py` | One process per account, JSON-lines over localhost, `LocalLeg`/`RemoteLeg` behind one interface |
| `mt5trader/spread.py` | The spread, the executable touches, the staleness and jump guards |
| `mt5trader/sizing.py` | `L_B = L_A x C_A / (beta x C_B)` and the one `k` |
| `mt5trader/hedgeratio.py` | Beta stamped with the pair it was computed for |
| `mt5trader/costs.py` | The round trip, split into crossing (already in the prices) and commission (not) |
| `mt5trader/executor.py` | MARKET entry both legs, the 2.0s escalation, unwind by ticket, ticket-based closes |
| `mt5trader/book.py` | Synthetic orders one-per-click, positions, net and average |
| `mt5trader/coordinator.py` | The poll loop, the guards, the sweeps, one status snapshot for every panel |
| `mt5trader/quoter.py` | The synthetic LIMIT path: quote one leg, re-peg off the OTHER leg by MODIFY, cross on fill |
| `mt5trader/reconcile.py` | Orphans and ghosts, three strikes, contract-size-correct P&L |
| `mt5trader/session.py` | The one cutoff: DAY orders die, each ladder's overnight rule decides |
| `mt5trader/slippage.py` | The session window on the broker's clock, and the slippage report over it |
| `mt5trader/database.py` | SQLite (WAL, 30s busy timeout): crash-safe positions, the fill journal, the audit trail |
| `mt5trader/shutdown.py` | An unanswered prompt means NO |
| `mt5trader/commands.py` | The web↔coordinator bridge, primed at startup so a restart never replays a command |
| `mt5trader/bands.py` | Bollinger bands on the spread: candles, EMA middle, population sigma, MT5 bars matched across two brokers' clocks |
| `mt5trader/algo.py` | Fair spread, and the Algo signal: entry, gates, exits — decides, never acts |
| `mt5trader/algodesk.py` | The per-ladder Algo switch (off at every start), history backfill, and the sink that records intents |
| `mt5trader/webapp.py` | The Flask process: it renders and it asks; it never trades |
| `mt5trader/static/`, `templates/` | The ladder, the Market Grid, the positions monitor and the settings page — self-hosted, no CDN |

The UI is the reference TT screen: window chrome and a bottom taskbar,
the quote strip, the left control rail, and the five-column grid
(`Work | Bids | Price | Asks | LTQ`) with the black inside-market rule,
bid blue and ask red. MARKET mode re-tints the click columns and
confirms through the one shared modal — there are no native dialogs
anywhere, and a test fails the build if they come back.

Settings (accounts and pairs) is a page in the browser: it saves each
account with the clash refusals on the row, tests a terminal (naming
Algo Trading being off, or a netting account, in words), searches each
broker's symbols, and derives β, the increment, the matched-minimum
clip and the pair's minimum notional from what MT5 says — each shown
with its derivation and offered as a one-click correction, never
applied silently. It talks to the leg runners directly, so it works
with the coordinator down.

**Restart recovery.** Open positions are written to the database on
every change and recovered at startup with their own ids, fills and
tickets. Until recovery has completed against both accounts the book is
marked incomplete and the reconciler auto-closes **nothing** — an
orphan is only an orphan if we are sure it is not ours. Anything at the
broker carrying our magic that recovery cannot explain is listed as
UNCLAIMED on a red banner, never closed automatically, with *adopt* and
*close it* for a person to choose.

**Fills — the trade journal.** Every fill the broker reports, read back
from MT5's own deal history rather than from our intentions, keyed by
(account, deal id) so two brokers' identical deal numbers cannot
overwrite each other. It carries the trader's own terminal clicks too,
marked as not ours, with the broker's commission, swap and P&L and the
broker's own clock beside the offset from ours. Exportable as CSV.

**Exchanges** is the setup page: each account with **Connect** (is the
runner there and the terminal logged in), **Test** (can it trade — Algo
Trading, permissions, hedging) and **Diagnose** (everything, including
whether the two legs fit each other: symbols, contract sizes, expiry,
and a beta stamped for the pair it is actually used on). Every failure
carries the step that fixes it. At the top, one line says whether the
system is **CONNECTED** or names the single thing standing in the way,
and it is said out loud once when it becomes true.

**Times are the broker's.** The session cutoff runs on the broker's
clock, measured from the terminal rather than configured, and the page
shows the broker's time and its offset from the machine. Unmeasured is
not zero: with no measurement the cutoff does not fire, and says so.

**Accounts** is the fifth monitor tab: equity, balance, credit, open
P&L, margin used and free, margin level against the broker's own call
and stop-out levels, and this system's own lots and units on each
account. With two brokers there is no combined margin — each posts its
own and the pair can only be carried by the **weaker** of the two, so
that account is named rather than averaged into a total that reads
comfortable.

**Slippage — the report over a real session.** The sixth monitor tab
reports the session you are in, cut at the cutoff on the **broker's**
clock so it lines up with the day MT5 stamps its deals in. Entries are
measured against the touch the click was taken at, exits against the
touch the close was sent at — and the exit is now anchored on the
CLOSING fills rather than on the touch it aimed at, which is what makes
an exit's slippage measurable at all. Positive is a cost at both ends,
in spread points and in money through the same `k`. MARKET and LIMIT
are reported side by side, because that is the split the peg has to
justify itself against, and the worst entries are ranked in money
rather than points. A fill that could not be priced is counted as
**unmeasured**, never averaged in as zero, and the journal is counted
over the same window as a check on coverage. Exportable as CSV, with
empty cells rather than zeros where nothing was measured.

## The Algo — a signal, not a trader

Per ladder, picked in the ladder's settings (**Algo: Off / Fair spread / Dry run /
LIVE**). It is OFF after every restart. In dry run the ladder trades exactly
as before — it adds a reading, not a lock; LIVE takes the ladder over (below).

- **The band.** Candles of the spread `B - beta x A` from the mid (15-minute,
  N = 20 by default). Middle = EMA(N), Pine-style; sigma = population stdev of
  the last N closes; the forming candle counts. Turning the Algo on loads both
  legs' MT5 bars (moved from bid to mid with each bar's own spread, matched in
  UTC on each broker's measured clock), so the band is there at once. If the
  terminals will not give history, candles are built from the live price and
  the window says `candles 7/20`. Closed candles are saved, so a restart keeps
  them.
- **Warm-up.** History fills the band at once, but no entry is taken until the
  Algo has watched `Warm-up (min)` of LIVE prices since it was turned on — 90 by
  default, 0 = off. Time with no price does not count; dry-run time does, so a
  ladder can warm up in DRY and go LIVE without waiting again. Turning the Algo
  off starts it over. A restart does not: switched back on within 5 minutes of
  the last live price it watched (an update, a quick restart), it carries on
  where it was; after a longer gap it starts again. The window shows `Live ... warming up 23/90 min`.
- **Staying out of a trend.** A mean-reversion Algo that sells every touch of
  the upper band is run over by a spread climbing along it. Two protections,
  on by default, each switchable per ladder:
  - *Re-entry* — a side is ARMED when its stretch reaches Entry z and enters
    only when the spread comes back inside by `Re-entry back by` (2.0 armed,
    1.5 enters). A spread riding the band never comes back, so it gives no
    entry; disarmed if the spread reaches the mean first.
  - *Trend filter* — if the band's middle moved more than `Trend filter (σ)`
    over `Trend lookback (min)` (1σ over 120 min), no entry against it: no
    H to L while it rises, no L to H while it falls. The panel's Trend badge
    shows ↑ / ↓ / –.
- **Backtest.** *Run backtest* in the Algo window replays the last 3–10 days of
  MT5's own 15-minute bars through this ladder's Algo — the same decision
  code, filters, costs, break-even, target and stop — and lists what it would
  have traded, beside the same run without re-entry and the trend filter. It
  sends nothing. It sees one price per candle (its close) and today's bid-ask.
- **Direction.** Both (default), H to L only, or L to H only — entries only;
  an open position is always managed to its exit.
- **Entry.** SELL when the z of the bid-side spread is >= +2.5, BUY when the z
  of the offer-side spread is <= -2.5, held for 3 fresh quotes, only on a flat
  ladder. Held back — and the reason shown — by a stale or jumping price, too
  few candles, a 5-minute cooldown after an exit, the last 20 minutes before
  the session cutoff, or a |z| past 3.5.
- **Exit.** For each REAL position, from its own fill: the closing side
  reaching break-even after every cost plus the take-profit % of margin — the
  same TP the Exit panel shows — or falling to the **stop loss**, break-even
  less 2% of margin (on by default, per ladder). Optional and OFF by default: a
  z-stop, back to the mean in profit, and a time stop in candles. No gate ever
  holds an exit.
- **In position.** The window names it — `in BUY @ 59.11 — TP 59.31 · SL
  58.93` — and draws an SL ◄ entry ► TP bar with the closing price on it.
- **The filters** (from the stat-arb system's algo), every one judged before an
  entry in dry run and LIVE alike — and one that cannot be priced blocks:
  - *Edge*: expected capture (0.5 x |z| x sigma, in money) at least 1.5x the
    round-trip cost — the spread's bid-ask crossed both ways, commission both
    legs both ways, and the slippage budget.
  - *Regime*: no entry while the spread is TRENDING (Kaufman efficiency ratio
    >= 0.6 and <= 4 crossings of its mean).
  - *Half-life band*: off until bounds (minutes) are typed.
  - *Ready*: collecting candles, or a warm-up not yet done, always blocks.
- **The switch** is on each ladder's title bar — ALGO OFF / ALGO DRY / ALGO
  LIVE; click it for the menu. LIVE still asks to confirm.
- **The Algo window** opens while the Algo is on, with the stat-arb dashboard's
  three panels: *Signal & Position* (sell/buy spread tiles with their z and
  entry line, FLAT/LONG/SHORT, and in a position the entry, each leg's fill and
  close-now price, BE/TP/SL and the SL-entry-TP bar), *Statistics* (EMA mean,
  sigma, half-life, regime, candles, band, data progress) and *Filters* (Edge /
  Regime / Ready badges, capture / cost against the requirement, round
  trip, the day's counts, and the last signal blocked and why).
  Closing it turns the Algo off.
- **Record.** Every signal goes to the audit trail; *Algo signals CSV* on the
  Fills tab exports them, with what an exit would have made after costs.
- **Dry run or LIVE.** *Algo: Dry run* records signals and sends nothing.
  *Algo: LIVE* (confirmed every time) trades: MARKET both legs in, closes by
  ticket, through the same executor a click uses, at its own **Algo qty**
  (spreads; one spread = the ladder's Leg A / Leg B lots — 0.01 for testing).
  One position at a time.
- **Algo or Manual, never both.** While a ladder is LIVE a new manual order on
  it is refused on the engine, in words; CLOSE ALL and the positions list still
  close. LIVE is refused while the ladder holds a manual position or a working
  order. Turning LIVE off with a position asks: close it now, or hand it to
  manual. A restart comes back OFF; the Algo's position is shown as not
  managed until LIVE adopts it again.
- **The day's limits** stop entries (never exits) for the rest of the day: max
  trades (10), losses in a row (3), and a daily loss limit (off until set). A
  refused entry waits out the cooldown; a failed exit is retried every 5s.

## One click is one order

That is the product, so it is the default and it is fast:

- A **market click crosses both accounts immediately** — no confirmation
  dialog. The arming carries the weight instead: the mode badge, the
  tinted click columns, the crosshair cursor. A desk that wants the
  extra gesture turns on *ask before crossing* in Settings → Trading.
- The clicked price is still the **slippage guard**: a fill worse than
  it by more than `MARKET_PROTECTION_TICKS` is refused, with the reason
  on the ladder.
- Clicks are drained on their **own thread** (~20 ms), not on the price
  poll — a click never waits for the next 300 ms pass.
- The order appears on the ladder **before the engine answers**: the row
  flashes and a hatched ghost sits in the Work column until the real
  order arrives. A trader who cannot see their click clicks again.
- **The Asks column BUYS the spread** (buy leg B, sell leg A); the Bids
  column sells it. The rail's BUY button is red and SELL is blue for
  the same reason — a button takes the colour of the column it acts on.
- **A click away from the touch RESTS.** A buy under the offer cannot
  cross at any price, so it becomes a working order at that level and
  the toast says so. `CLICK_AWAY_RESTS` turns that off and the click is
  refused instead.
- **BUY / SELL / FLATTEN** on the rail hit the touch and get flat
  without hunting for a row.
- **The ladder centres on the mid**, re-centres every `RECENTRE_SEC`
  (5s by default; 0 = only when the market leaves the window), and
  leaves a hand-scrolled ladder alone for four seconds so an order can
  be placed twenty rows away. Lock stops it entirely.
- **Sound**: one blip when an order is placed, two rising notes when
  one fills, a low note when one is cancelled — generated in the page
  with WebAudio, so nothing can be silenced by a blocked network. Off
  is one click in the taskbar, and so is turning the **keyboard** off
  entirely: B and S are orders.
- **One line says whether it is working**, always on screen: green only
  when both accounts answered AND every enabled ladder is quoting.
- **The Bids and Asks columns carry the implied size** — how many
  spreads the two order books can actually do, matched leg against leg
  and counted once. Where a broker publishes no depth the columns stay
  empty rather than inventing a size from one leg.
- **The exit price on the rail**: break-even (the entry price plus
  commission, both legs, both ends) and break-even plus a target that
  is a percentage of the MARGIN one spread ties up — priced by the two
  terminals with `order_calc_margin`, not derived from notional. Both
  directions, because a long leaves on the bid and a short buys back on
  the offer. A position that is on gets its own line, anchored on the
  price it was ENTERED at. Nothing is sent to the broker: it is a price
  on the screen, and no leg ever carries a broker-side stop.
- **Fair spread on the rail**: `swap per day × days to expiry`, with
  the market named rich or cheap against it. Set the futures leg's
  expiry and swap on the Exchanges page; without both there is no fair
  value and the box shows an em dash.
- **The mid carries a heavy rule** and is what the ladder centres on;
  the inside-market rule stays where it was, between the two touches.
- **Keyboard**: `B` buy at the offer, `S` sell at the bid, `F` flatten,
  `X` cancel this ladder, `1`–`5` arm 1/5/10/50/100, `0` clear, `L`
  lock the scroll, `M` switch LIMIT↔MARKET, `Tab` next ladder, `?` the
  list. Keys never fire while a field has focus.

Flatten and the global kill still ask once — they are irreversible and
they are the buttons pressed in a hurry.

## More than one spread

Every pair is its own ladder, and they trade side by side on one
desktop: Spot silver vs the silver future, WTI vs Brent, gold basis —
as many as the two accounts carry symbols for.

Add one on **Exchanges → Pairs → New pair**: pick the account and
symbol for each leg (Find lists what each broker actually offers),
press **Read both legs from MT5** to derive β, the increment, the
matched-minimum clip and the minimum notional from the contract specs,
then Save. The key is built from the two symbols. The launcher restarts
the engine within a few seconds and the ladder appears.

A pair saved on the Exchanges page gets its ladder **by itself**, beside
the ones already open, as soon as the engine picks it up — no reload. A
ladder you close stays closed. The **+** button in the taskbar lists
every ladder, the Market Grid and the Positions window, to open one
again. Windows move by their title bar and resize from
the corner grip, and where you put them is where they are after a
reload; **Tidy** puts them back in a row.

## Running it — one file

```
python start.py
```

Or `py -3.11 start.py` where the python.org launcher is installed — a
conda or venv prompt has `python` and usually no launcher at all, so
use whichever of the two answers on your box. (`python -3.11` is not a
thing: `-3.11` is the LAUNCHER's argument, and `python` reads it as an
option and exits.)

That is the whole thing. No arguments, nothing to copy, nothing to
edit: it creates `config.json` and `.env` on a first run, brings up the
web UI **first** (the accounts are entered on that screen, so it has to
be reachable before there are any), starts a leg runner per account and
the coordinator, opens the browser, and restarts a crashed child with
backoff.

Save an account on the Exchanges page and the launcher notices within a
few seconds and restarts the engine to pick it up — accounts, symbols
and β are read at startup, so it has to come back to see them, and that
should not be the operator's job to remember. It watches only those
fields: changing a display setting or a per-ladder knob does not
interrupt trading.

**Credentials are entered in the UI**, never in a file. The password
goes to `.env` under a sanitised key; `config.json` holds only the name
of that key.

On the trading box, double-clicking **START-TRADING** does the same
thing with a few guard rails first (Python present, a terminal running,
the test suite passing — it refuses to start the engine on a failing
suite). `deploy/bootstrap.ps1` prepares a fresh Windows box and puts
that shortcut on the desktop; `deploy/RUNBOOK.md` is the page to hand
to whoever runs it day to day.

## Running it (the pieces)

The terminals and the leg runners live on the Windows box; everything
else runs anywhere.

```
cp config.example.json config.json     # then edit accounts and pairs
cp .env.example .env                   # passwords go here, nowhere else
python start.py --config config.json   # spawns the runners + coordinator
```

Or run the pieces separately while setting up:

```
python run_leg.py --config config.json --account leg_a
python run_leg.py --config config.json --account leg_b
python run_coordinator.py --config config.json
```

## Logs, and "why did the Algo not trade?"

Every process writes to `logs/` beside `config.json`: `coordinator.log`,
`web.log`, `leg-<account>.log`, `launcher.log`. Each also has a
**`<name>.problems.log`** with warnings and errors only — open that one
first. The screen's own polling is not logged (it was a line three times a
second); button presses and failed requests still are.

For the Algo, the journal answers it directly:

```
python tools/algo_report.py              # the last 24 hours
python tools/algo_report.py --hours 72
```

Per ladder: when it was switched, every signal it acted on and what became
of the order (done, or refused in the refusal's own words), and every
signal it held back with a count per reason — the cutoff, the warm-up, a
filter, an Algo qty the broker cannot trade.

## Tests

```
pytest tests/ -q
```

262 of them, including an end-to-end suite that runs two leg runners on
real sockets, a coordinator with a real database, the Flask process and
Chromium — a click crosses every boundary except MT5 itself, survives a
coordinator restart, and lands in the journal. Twenty-one more drive
the UI under Playwright and read `pageerror` — Python tests cannot see a temporal-dead-zone
`ReferenceError` that aborts a script block and silently unregisters a
handler, and that is exactly what happened in the system this is ported
from. They skip cleanly where no browser is installed — and nothing else
skips or fails with them: a box with no Chromium runs 231 and skips 31,
because every test about what happens to a live position clicks through
the command bridge when there is no browser to click with. (To get the
browser ones too: `python -m playwright install chromium`.)

They must pass before any commit, and LIVE mode must never be run
without them. Everything is faked — `FakeBroker` keeps a real
hedging-mode book, so an opposite market order opens a SECOND position
exactly as it does on the real accounts. No MT5, no network, no clock.

## Reference screen

The UI target is a TT inter-product ladder screen supplied by the operator.
Drop it at `docs/reference/tt_ladder.png` — §3.3 transcribes it, and the
Playwright layout test checks against it.

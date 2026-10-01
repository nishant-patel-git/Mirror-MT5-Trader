# Working on MT5-Trader

The build specification is held privately, outside this repository — get
it from the repository owner and read it first. Most of it is a rule that
was paid for on a live account in the stat-arb system this code is ported
from.

## Hard rules

- **`pytest tests/ -q` must pass before any commit**, and LIVE mode must
  never be run without it.
- **The spread is `Leg B - beta x Leg A`**, built from the MID OF THE
  BOOK, never `tick.last`. Levels and triggers read the EXECUTABLE side
  for their own direction; a position reads the OPPOSITE executable side
  to close.
- **`L_B = L_A x C_A / (beta x C_B)`**, and `k = L_B x C_B` is the one
  multiplier every spread-to-money conversion uses.
- **Closes target position TICKETS.** These accounts are hedging mode;
  an opposite market order opens a SECOND position.
- **Never attach broker-side stops to individual legs.** One leg
  stopping alone converts the hedge into a naked position.
- **Credentials live only in `.env`** — never in code, config, chat or a
  log line.
- **Sweep our pendings at shutdown AND at startup**, magic-scoped.
- **The book is persisted and recovered.** An empty book at startup
  makes every live position look like an orphan; the reconciler
  auto-closes nothing until recovery says the book is complete, and
  never touches a position it cannot explain.
- **Signals, never automatic orders.** A signal may be computed and
  shown (the Algo: per ladder, OFF at every start, DRY_RUN only).
  Nothing places, modifies or cancels an order by itself — no
  automatic entries or exits, nothing that re-enters by itself. The
  Algo's intents go to a sink that only records them; letting one
  trade is a separate step that has to be asked for, and
  `tests/test_algo.py` fails the build if the Algo's modules can reach
  an order.

## Conventions that are easy to lose in a refactor

- `positions()` and `pending_orders()` return **None for "unknown"**
  (the leg could not be read), which is NOT "flat"/"no orders". Code
  that treats None as empty will sweep a live account clean in its own
  report while the money sits at the broker.
- **Unmeasured is not zero.** Return None and render "—".
- Guards may withhold an ORDER. **A guard must never prevent a close.**
- A refusal carries the broker's own words (`10027 AutoTrading disabled
  by client`), never "check the log".
- `mt5trader/broker.py` is the only module allowed to import
  MetaTrader5.
- Every test that asserts a guard withholds something needs a
  **control** that turns the guard off and asserts the opposite.

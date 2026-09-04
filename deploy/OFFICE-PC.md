# Putting MT5-Trader on an office PC

One trader, one PC, their own two MT5 accounts. `RUNBOOK.md` is the
shared EC2 box; this is the other shape.

What the trader sees:

1. Double-click **SETUP.bat** — installs everything, about ten minutes
2. A window asks **six questions** — two logins, two passwords, two servers, and which pair
3. Double-click **START TRADING** on the Desktop — the ladder opens

They never open MetaTrader 5, never edit a file, never see a port
number. `terminal_path` is set for both accounts, so the engine opens
each terminal and signs it in itself (`mt5trader/broker.py`).

Because every trader is on their own machine, **everyone uses the same
ports** — 9101, 9102, web 8000. Nothing to allocate, nothing to clash.
That is only true one-trader-per-PC; on a shared box read `## Ports` in
RUNBOOK.md instead.

---

## Before the first PC: build the golden terminal

Once, on your own machine, and again whenever the broker ships a new
build. This is the step that makes every PC identical.

Prepare one terminal by hand:

1. Install MetaTrader 5 once, into its own folder.
2. Log in so the servers list knows your broker, then **log out**.
3. Market Watch: keep only the symbols this desk trades.
4. **Close every chart.** Charts are the bulk of the zip, and each one
   is a live subscription the terminal re-opens on startup.
5. Tools → Options → Server: News off. Community and Signals too.
6. Tools → Options → Expert Advisors: tick **Allow Algo Trading**, so
   nobody has to be told to press the button.
7. Close the terminal — a zip taken while it runs is a snapshot of
   half-written files.

Then:

```powershell
.\deploy\make-golden-terminal.ps1 -Source 'C:\MT5-A'
```

It strips logs, price history, chart profiles **and the accounts
files**, then refuses to build if an accounts file survived — a
template that carries a login would ship one trader's account to every
PC in the office. Result: `deploy\MT5-golden.zip`.

## The rollout kit

Copy these three into one folder — a USB stick or a network share:

```
SETUP.bat
setup.ps1
MT5-golden.zip
```

`SETUP.bat` asks for Administrator itself and hands over to
`setup.ps1`, which:

- installs Git and Python 3.11 64-bit if they are missing
- clones the repo to `C:\MT5-Trader`
- unpacks the golden zip **twice**, to `C:\MT5-A` and `C:\MT5-B`
- installs the dependencies
- **runs the test suite, and stops if it fails** — before anything is
  configured and before a shortcut exists
- opens the six-question wizard
- puts START TRADING on the Desktop

Private repo, so each PC needs read access:

```
SETUP.bat -Token github_pat_xxx
```

Use a **fine-grained, read-only token scoped to this one repository**.
It goes into the Windows Credential Manager, never into the clone URL —
a token in the URL is written to `.git\config` in plain text and is
then in every screenshot of that file forever. A leaked read-only token
reads code; it cannot push.

## The six questions

`deploy\configure.py`, opened by SETUP or run again any time:

| | |
|---|---|
| Account A | login, password, server |
| Account B | login, password, server |
| Pair | pick a preset, or type both symbols |

The two terminal folders are **not** among the questions. SETUP made
them and passes them in — a trader asked to type a path is a trader who
can type the same path twice, and two accounts on one installation is
one account hedging against itself.

What it writes, and nothing else:

- `config.json` — the two accounts and the pair
- `.env` — the two passwords, quoted, and nowhere else

The `.env` key comes from the app's own `env_key_for()`. Nothing guesses
it: a key the loader does not read is a password that is silently never
there.

It **refuses to overwrite a machine that already has accounts.**
`--force` replaces it and keeps `config.json.bak`. A `config.json` that
is still the shipped example is replaced without asking, because
`start.py` copies the example on a first run — the file existing is not
evidence anyone configured anything.

Tick size, contract size and lot steps are left unset on purpose. The
app reads them from MT5 on the first connect; a number typed at setup
is a number that can be wrong, and every money figure on the ladder
runs through it.

## Every morning

START TRADING does the whole start:

1. finds a Python
2. `git pull --ff-only` — the free auto-update
3. installs any new dependencies
4. **preflight** — can this machine actually connect?
5. **the safety tests** — and it will not start the engine if they fail
6. starts the engine and opens the ladders

Steps 2 and 3 are allowed to fail. No internet means a warning and the
copy already on the machine; a trader must never arrive to a black
window and no ladder. Step 5 is not allowed to fail — that rule is what
keeps a bad build away from a live account.

`deploy\preflight.py` refuses only what would trade **wrong**:

- one login, one port or one MT5 folder on both accounts — that is one
  account entered twice, and the pair would hedge against itself
- a `terminal_path` pointing at a file that is not there
- an account with a blank `terminal_path` when no terminal is open (it
  attaches to whatever is running, so something has to be running)

A configured machine with no terminal open is **fine** and starts — the
engine opens them. The old check refused exactly this case.

## Still to decide

- Which symbols are the office standard? The wizard ships three
  presets in `PRESETS` at the top of `configure.py`; add or cut there.
- Who owns `MT5-golden.zip`, and re-strips it when the broker updates
  MetaTrader 5?

## One thing this shape is not for

This is the "give them the DVD" model: the code lands on the PC and can
be copied. Fine for your own office, your staff, your machines. Do not
use it for paying subscribers — keep those hosted.

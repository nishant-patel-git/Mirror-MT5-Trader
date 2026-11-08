"""Signals: armed, entered, and what became of them.

The zone track in `zones.py` answers "what did the spread DO". This
answers the two questions a trader actually asks of it:

- **how often did it signal** — counted the way the Algo counts, not
  the way a naive crossing check does;
- **would it have paid** — did the spread come back, and would the
  take-profit or the stop have been hit first, on the settings in
  force at the time.

Both are measured whether a ladder's Algo is on, off, dry or live. The
whole point is to be able to answer "is this worth switching on?"
BEFORE switching it on.

**A second poke at the band is not a second signal.** This is the fault
the module exists for. A spread that reaches +2.5, eases to +2.1 and
pushes back to +2.6 has stretched ONCE: it armed on the way out and
never came back inside, so there is nothing new to act on. Counting
that as two signals inflates every count on the page and makes a
quiet day look busy. So a side ARMS on the way out, stays armed, and
only re-arms after it has come back through the floor.

ONE rule, and it is the ladder's own. A fixed +-2 / +-3 sigma study
was tracked beside it for a while and taken back out: two sets of
counts on one page answered a question nobody was asking and made the
one that mattered harder to read. What a desk wants to know is whether
THESE settings would have made money, so `entry_z` and its re-entry
machine are the only threshold here - armed on the stretch out,
ENTERED only on the way back inside the window, disarmed if it falls
through the floor first. A trend rides the band and never comes back,
so it arms and never enters, which is the Algo working and worth
seeing.

Nothing here reads a price, a broker or a clock, and nothing here can
reach an order. It takes numbers and returns numbers.
"""

import math

#: How far back inside a stretch must come before the same side can
#: arm again. 1.0 is the mean zone `zones.MEAN`, so "re-armed" and
#: "back to the mean" mean one thing across this page.
REARM_INSIDE = 1.0

#: Stamped on every row. One rule now, but the column stays: rows
#: written when there were three must still read back.
ALGO = 'ALGO'

#: WHERE A ROW CAME FROM, and it matters enough to be on the screen.
#:
#: LIVE is measured forward tick by tick, as it happened. REPLAY is
#: derived afterwards by running the ladder's own settings back over
#: MT5's bars, which is the only way to answer for a day that has
#: already gone - and it sees one look per candle, so a stop touched
#: and recovered inside fifteen minutes is invisible to it. Mixing the
#: two silently would let a trader read a candle-resolution guess as a
#: measurement.
LIVE, REPLAY = 'LIVE', 'REPLAY'
SOURCES = (LIVE, REPLAY)

#: The Algo's own exit reasons, and what each one settles as. Only the
#: take-profit and the stop say a level was REACHED; the rest closed
#: the trade for another reason entirely and are neither.
EXIT_OUTCOME = {'PROFIT_TARGET': 'TP', 'STOP_LOSS': 'SL'}

#: Where a signal is in its life.
ARMED = 'ARMED'          # stretched out; waiting (ALGO) or entered at once
OPEN = 'OPEN'            # hypothetically on, being watched forward
CLOSED = 'CLOSED'        # it is done; `outcome` says how

#: How it ended. REVERTED is NOT one of these: coming back to the mean
#: and taking a profit are different questions, and a trade can do
#: either without the other, so reversion is recorded beside the
#: outcome rather than instead of it.
TP = 'TP'                # the take-profit would have been reached
SL = 'SL'                # the stop would have been reached
EXPIRED = 'EXPIRED'      # neither, inside the watch window
NO_ENTRY = 'NO_ENTRY'    # armed, then disarmed without ever entering
OUTCOMES = (TP, SL, EXPIRED, NO_ENTRY)


def reentry_window(entry_z, reentry_back, window_pct):
    """(enters at, window floor) as |z| — the Algo's own arithmetic.

    Kept here rather than imported so this module stays arithmetic on
    numbers it is handed; `tests/test_signals.py` asserts it agrees
    with `algo.reentry_window` so the two can never drift.
    """
    back_at = float(entry_z) - float(reentry_back)
    return back_at, back_at * (1.0 - float(window_pct) / 100.0)


class AlgoArming:
    """The ladder's own arm-then-enter machine, mirroring
    `algo.AlgoSignal._count`.

    Armed when the stretch reaches `entry_z`; ENTERS on the way back
    into `(floor, back_at]`; disarmed if it falls through the floor
    first. With re-entry off it enters at the stretch itself.
    """

    def __init__(self, state=None):
        state = dict(state or {})
        self.armed = {'SELL': bool(state.get('SELL')),
                      'BUY': bool(state.get('BUY'))}
        self.armed_z = dict(state.get('armed_z') or {})

    def to_dict(self):
        return dict(self.armed, armed_z=dict(self.armed_z))

    def observe(self, z, params):
        """Returns (armed_side, entered_side); either may be None."""
        if z is None or not math.isfinite(z):
            return None, None
        entry_z = float(params.get('entry_z') or 0.0)
        if entry_z <= 0:
            return None, None
        if not params.get('reentry_on', True):
            # No window: the stretch itself is the entry, and the same
            # re-arm discipline keeps a second poke from counting twice.
            side = None
            if z >= entry_z and not self.armed['SELL']:
                self.armed['SELL'], side = True, 'SELL'
            elif z <= -entry_z and not self.armed['BUY']:
                self.armed['BUY'], side = True, 'BUY'
            if abs(z) < REARM_INSIDE:
                self.armed = {'SELL': False, 'BUY': False}
            return side, side
        back_at, floor = reentry_window(entry_z,
                                        params.get('reentry_back') or 0.0,
                                        params.get('reentry_window_pct') or 0)
        armed_side = entered = None
        if z >= entry_z:
            if not self.armed['SELL']:
                self.armed['SELL'], armed_side = True, 'SELL'
                self.armed_z['SELL'] = round(z, 3)
        elif z <= floor:
            self.armed['SELL'] = False
        if z <= -entry_z:
            if not self.armed['BUY']:
                self.armed['BUY'], armed_side = True, 'BUY'
                self.armed_z['BUY'] = round(z, 3)
        elif z >= -floor:
            self.armed['BUY'] = False
        if self.armed['SELL'] and floor < z <= back_at:
            entered = 'SELL'
        elif self.armed['BUY'] and -back_at <= z < -floor:
            entered = 'BUY'
        return armed_side, entered


def new_signal(pair_key, basis, side, at, z, params, broker_day=None,
               threshold=None):
    """A signal the moment it arms. Nothing hypothetical priced yet —
    that happens when (and if) it enters."""
    return {
        'pair_key': pair_key, 'basis': basis, 'side': side,
        'broker_day': broker_day,
        'threshold': threshold,
        'state': ARMED, 'outcome': None,
        'armed_at': float(at), 'armed_z': round(float(z), 3),
        'entered_at': None, 'entered_z': None,
        'entry': None, 'break_even': None, 'tp': None, 'sl': None,
        'units': None,
        'closed_at': None, 'closed_z': None,
        'peak_net': None, 'trough_net': None,
        #: What the trade would have MADE, in money, at its close. The
        #: page's "would have won / would have lost" is this, not which
        #: level was touched: an exit on the mean reversion in profit
        #: is a win however it got out.
        'net_pnl': None,
        'source': LIVE, 'exit_reason': None,
        'reverted': False, 'revert_min': None,
        'held_min': None,
        'entry_z': params.get('entry_z'),
        'max_entry_z': params.get('max_entry_z'),
        'reentry_back': params.get('reentry_back'),
        'reentry_window_pct': params.get('reentry_window_pct'),
        'algo_mode': None,
    }


def enter(signal, at, z, prices, units):
    """Freeze the hypothetical trade onto a signal that has entered."""
    signal['state'] = OPEN
    signal['entered_at'] = float(at)
    signal['entered_z'] = round(float(z), 3)
    signal['entry'] = (prices or {}).get('entry')
    signal['break_even'] = (prices or {}).get('break_even')
    signal['tp'] = (prices or {}).get('tp')
    signal['sl'] = (prices or {}).get('sl')
    signal['units'] = units
    return signal


def abandon(signal, at):
    """Armed, then disarmed without ever entering. Not a failure — a
    trend rides the band and never comes back, and the Algo declining
    to chase it is the filter working."""
    signal['state'] = CLOSED
    signal['outcome'] = NO_ENTRY
    signal['closed_at'] = float(at)
    return signal


def net_at(signal, closing_spread):
    """What the hypothetical trade would be making, in money, closing
    at this spread. None where it cannot be measured."""
    if closing_spread is None or signal.get('break_even') is None \
            or not signal.get('units'):
        return None
    move = float(closing_spread) - float(signal['break_even'])
    if signal['side'] == 'SELL':
        move = -move
    return move * float(signal['units'])


def _hit_target(signal, spread):
    """Reached the take-profit — the direction the trade PROFITS in.

    A long is in profit as the spread rises, a short as it falls.
    """
    level = signal.get('tp')
    if spread is None or level is None:
        return False
    return (spread >= level) if signal['side'] == 'BUY' else (spread <= level)


def _hit_stop(signal, spread):
    """Reached the stop — the OTHER direction, and the one that is easy
    to get backwards.

    A long's stop sits BELOW its break-even and a short's sits ABOVE,
    so this is not the target's test with another level passed in. It
    was written that way first, and every short closed as a stop on
    its first poll.
    """
    level = signal.get('sl')
    if spread is None or level is None:
        return False
    return (spread <= level) if signal['side'] == 'BUY' else (spread >= level)


def watch(signal, closing_spread, z, at, window_sec):
    """Mark one open signal against the price it would close at.

    The ORDER matters: the stop is checked before the target. Inside
    one poll both can look reached, and booking the happier of the two
    is how a simulation flatters itself — a desk reading this would
    size up on a number that never happened.

    Returns True when anything changed.
    """
    if signal.get('state') != OPEN:
        return False
    changed = False
    minutes = round(max(0.0, at - signal['entered_at']) / 60.0, 1)
    net = net_at(signal, closing_spread)
    if net is not None:
        if signal['peak_net'] is None or net > signal['peak_net']:
            signal['peak_net'] = round(net, 2)
            changed = True
        if signal['trough_net'] is None or net < signal['trough_net']:
            signal['trough_net'] = round(net, 2)
            changed = True
    if z is not None and math.isfinite(z) and not signal['reverted']:
        # "It came back": the stretch crossed the mean. A separate
        # question from whether it paid, so it is recorded beside the
        # outcome rather than as one.
        if (signal['side'] == 'SELL' and z <= 0) or \
                (signal['side'] == 'BUY' and z >= 0):
            signal['reverted'] = True
            signal['revert_min'] = minutes
            changed = True
    if _hit_stop(signal, closing_spread):
        return _close(signal, SL, at, z, minutes, net)
    if _hit_target(signal, closing_spread):
        return _close(signal, TP, at, z, minutes, net)
    if window_sec and at - signal['entered_at'] >= float(window_sec):
        return _close(signal, EXPIRED, at, z, minutes, net) or True
    return changed


def _close(signal, outcome, at, z, minutes, net=None):
    signal['state'] = CLOSED
    signal['outcome'] = outcome
    signal['closed_at'] = float(at)
    signal['closed_z'] = None if z is None else round(float(z), 3)
    signal['held_min'] = minutes
    if net is not None:
        signal['net_pnl'] = round(float(net), 2)
    return True


def from_replay(trade, pair_key, params, broker_day=None, units=None):
    """One trade from `backtest.run` as a signal row.

    The backtest replays the SAME `algo.AlgoSignal` the live Algo runs,
    so its arming, its filters and its exits are not a second model of
    the Algo - they are the Algo, fed candles instead of ticks. What
    differs is resolution, and that is why the row is stamped REPLAY:
    one look per candle cannot see a stop touched and recovered inside
    one.
    """
    params = dict(params or {})
    opened = trade.get('opened_at')
    closed = trade.get('closed_at')
    reason = trade.get('reason')
    z_in = trade.get('entry_z')
    row = new_signal(pair_key, ALGO, trade.get('side'), opened,
                     0.0 if z_in is None else z_in, params,
                     broker_day=broker_day,
                     threshold=params.get('entry_z'))
    row['source'] = REPLAY
    row['exit_reason'] = reason
    row['entered_at'] = opened
    row['entered_z'] = None if z_in is None else round(float(z_in), 3)
    row['entry'] = trade.get('entry')
    row['units'] = units
    if closed is None:
        # Still open when the replay ran out of candles. Not a result.
        row['state'] = OPEN
        return row
    row['state'] = CLOSED
    row['outcome'] = EXIT_OUTCOME.get(reason, EXPIRED)
    row['closed_at'] = closed
    row['held_min'] = round(max(0.0, closed - opened) / 60.0, 1)
    pnl = trade.get('pnl')
    row['net_pnl'] = None if pnl is None else round(float(pnl), 2)
    return row


def tally(rows):
    """The three numbers the page exists to answer, and their workings.

    How often did it signal, how many of those would have made money,
    and how many would have lost. Rates are over SETTLED signals only
    — a day with three still running is not a day with three losses,
    and dividing by them would read as one.
    """
    mine = list(rows or ())
    entered = [r for r in mine if r.get('entered_at') is not None]
    settled = [r for r in entered if r.get('outcome') in (TP, SL, EXPIRED)]
    # WON and LOST are measured in money, not by which level was
    # touched. An exit on the mean reversion in profit is a win however
    # it got out, and a page that called it "neither" would under-report
    # exactly the trades the Algo's own exits are there to take. A row
    # whose P&L was never measured is in neither count.
    priced = [r for r in settled if r.get('net_pnl') is not None]
    wins = [r for r in priced if r['net_pnl'] > 0]
    losses = [r for r in priced if r['net_pnl'] < 0]
    reverted = [r for r in settled if r.get('reverted')]
    nets = [r['peak_net'] for r in settled if r.get('peak_net') is not None]
    return {
        'armed': len(mine),
        'entered': len(entered),
        'no_entry': sum(1 for r in mine if r.get('outcome') == NO_ENTRY),
        'open': sum(1 for r in mine if r.get('state') == OPEN),
        'settled': len(settled),
        'priced': len(priced),
        'tp': len(wins), 'sl': len(losses),
        'net_pnl': (round(sum(r['net_pnl'] for r in priced), 2)
                    if priced else None),
        'live': sum(1 for r in mine if r.get('source') != REPLAY),
        'replayed': sum(1 for r in mine if r.get('source') == REPLAY),
        'expired': sum(1 for r in settled if r.get('outcome') == EXPIRED),
        'reverted': len(reverted),
        # None, not 0, where nothing has settled: a win rate of 0% on no
        # trades is a statement nobody measured.
        'win_rate': (round(100.0 * len(wins) / len(priced), 1)
                     if priced else None),
        'revert_rate': (round(100.0 * len(reverted) / len(settled), 1)
                        if settled else None),
        'best_peak': round(max(nets), 2) if nets else None,
    }

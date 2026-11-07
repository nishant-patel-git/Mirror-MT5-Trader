"""Sigma zones: one row when the zone CHANGES, and nothing while it sits.

The Z-score excursion counters in `analysis.py` answer "how often did it
stretch?". This answers a different question — "what did it DO?" — as a
path a person can read:

    Mean -> +2s -> +3s -> +2s -> Mean -> -2s

and the rule that makes it readable is that a zone is recorded ONCE, on
the pass it is entered. A spread parked at +2.3 for an hour is one row,
not a row per poll; a spread that walks out to +3 and back is three.

Three faults this replaces, each measured on the live desk:

1. **The arming state was never saved.** `ZExcursions.to_dict()` kept
   the counts and dropped `_arm`, so every restart re-armed all four
   bands and a spread that had not moved logged a fresh "+2s touch" on
   the next poll. Six restarts, six touches, one stretch.
2. **"Re-armed under |z| < 1" is not "back to the mean."** A spread
   oscillating 2.2 <-> 0.8 logged a touch per cycle and no reversion at
   all.
3. **Nothing was recorded with the Algo off**, which is every ladder
   after every restart. The zone track is ANALYSIS: it measures with
   the Algo on, off, dry or live, and it can no more place an order
   than `analysis.py` can.

**The numbers on a row are frozen at the event.** Entry, take-profit,
stop and the edge ratio are what the settings in force AT THE TIME
would have produced; editing a ladder tomorrow must not rewrite what
yesterday said. They are HYPOTHETICAL — most zone changes never become
a trade — so an unpriceable one is None and renders as a dash, never 0.

Nothing here reads a price, a broker or a clock. It takes numbers and
returns numbers, so the same code answers for a live poll, a restart
and a test.
"""

import math

#: The zone a spread is in when it is not stretched. "Back to the mean"
#: is entering THIS zone - |z| falling under 1.0 - and nothing else.
MEAN = 'MEAN'

#: Zone boundaries in |z|. Below the first is the mean; at or above the
#: last is the outer zone. Signed, so +2s and -2s are different zones
#: and a swing through the mean is two rows, not none.
BOUNDARIES = (1.0, 2.0, 3.0)

#: Every zone, inner to outer, for a UI that wants them in order.
ZONES = ('-3', '-2', '-1', MEAN, '+1', '+2', '+3')

# -- what a row's Status can say ---------------------------------------------
#
# Taken from the Algo's own gate order (`algodesk.observe` ->
# `algo.AlgoSignal.evaluate`), so a row says the same thing the desk
# would have said standing at that z.

#: The band has fewer than `length` candles: it cannot measure yet.
COLLECTING = 'COLLECTING'
#: The band is full but the live tape has not been watched long enough.
WARMUP = 'WARMUP'
#: Inside `entry_z`. The Algo wants no entry here, and that is not a
#: fault - most zone changes are this.
NO_SIGNAL = 'NO_SIGNAL'
#: Past `entry_z` with nothing visible holding it back. NOT "it would
#: have traded": the Algo also wants its re-entry arming, its direction
#: and its confirm ticks, and none of those are modelled here. It says
#: only that the threshold was cleared and no filter or gate refused.
ELIGIBLE = 'ELIGIBLE'
#: Past `max_entry_z`: a blow-out, not a stretch. No entry.
BEYOND_MAX = 'BEYOND_MAX'
#: An entry filter priced it and said no (edge, regime, half-life).
FILTERED = 'FILTERED'
#: Held back by something other than a filter's verdict - a gate
#: (session, cutoff, cooldown, day limit, direction), or a filter that
#: could NOT be priced, which the Algo treats as a block.
BLOCKED = 'BLOCKED'
#: The Algo signalled an entry on this pass.
SIGNALLED = 'SIGNALLED'
#: A position was actually opened from it (LIVE only).
ENTERED = 'ENTERED'

STATUSES = (COLLECTING, WARMUP, NO_SIGNAL, ELIGIBLE, BEYOND_MAX, FILTERED,
            BLOCKED, SIGNALLED, ENTERED)


def zone_of(z):
    """Which zone `z` is in, signed. None when z is not a number.

    None is "unmeasured", which is not the mean: a pass with no band
    records nothing rather than a walk back to zero.
    """
    if z is None:
        return None
    try:
        value = float(z)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    size = abs(value)
    if size < BOUNDARIES[0]:
        return MEAN
    sign = '+' if value > 0 else '-'
    for index, edge in enumerate(BOUNDARIES[1:], start=2):
        if size < edge:
            return f'{sign}{index - 1}'
    return f'{sign}{len(BOUNDARIES)}'


def zone_label(zone):
    """How a zone reads on screen."""
    if zone is None:
        return None
    if zone == MEAN:
        return 'Mean'
    return f'{zone}σ'


def is_mean(zone):
    return zone == MEAN


def changed(previous, zone):
    """Should this zone be recorded?

    Only a CHANGE, and only from a zone we have actually seen: an
    unmeasured pass (None) neither records nor forgets, so a gap in the
    band does not manufacture a row when the price comes back.
    """
    if zone is None:
        return False
    return previous != zone


def direction(previous, zone):
    """'OUT' further from the mean, 'IN' back towards it, 'CROSS' through
    it, or None when there is nothing to compare."""
    if previous is None or zone is None or previous == zone:
        return None
    if previous == MEAN:
        return 'OUT'
    if zone == MEAN:
        return 'IN'
    if previous[0] != zone[0]:
        # Different sides without passing through the mean on a poll we
        # saw: still a crossing, and worth saying so.
        return 'CROSS'
    return 'OUT' if abs(int(zone[1:])) > abs(int(previous[1:])) else 'IN'


def round_to(value, step):
    """`value` on the spread's own grid. No step means no rounding -
    never a price invented to a tidier number than the pair quotes."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not step:
        return value
    step = float(step)
    if step <= 0:
        return value
    return round(round(value / step) * step, 10)


def entry_side(z):
    """Which way the Algo would enter at this z.

    It sells a spread that is stretched high and buys one stretched
    low: `SpreadSide.SELL` above the mean, `BUY` below. None at the
    mean, where it would not enter at all.
    """
    if z is None:
        return None
    if z > 0:
        return 'SELL'
    if z < 0:
        return 'BUY'
    return None


def levels(side, entry_spread, fee_points, target_points, stop_points,
           step=None):
    """The hypothetical entry, take-profit and stop, on the spread grid.

    The same arithmetic `takeprofit.for_position` uses for a real
    position, with the fill it WOULD have had in place of the one it
    did: break-even is the entry plus the costs to recover, and the
    target and the stop are measured from break-even - never from the
    entry, and never from the current touch.

    Each is None where its input was not measured. A stop of None is a
    stop that could not be priced, which is not a stop at zero.
    """
    body = {'entry': None, 'break_even': None, 'tp': None, 'sl': None}
    if side not in ('BUY', 'SELL') or entry_spread is None:
        return body
    try:
        entry = float(entry_spread)
    except (TypeError, ValueError):
        return body
    if not math.isfinite(entry):
        return body
    body['entry'] = round_to(entry, step)
    if fee_points is None:
        return body
    sign = 1.0 if side == 'BUY' else -1.0
    break_even = entry + sign * float(fee_points)
    body['break_even'] = round_to(break_even, step)
    if target_points is not None:
        body['tp'] = round_to(break_even + sign * float(target_points), step)
    if stop_points is not None:
        body['sl'] = round_to(break_even - sign * float(stop_points), step)
    return body


def status_of(z, params, filters=None, block_reason=None,
              warmup_done=True, signalled=False, entered=False):
    """What the Algo would have said, standing at this z.

    In the Algo's own order, because the first thing that stops an
    entry is the thing worth reporting: a band that cannot measure is
    not "filtered", and a filter that could not be PRICED is a block,
    not a pass.
    """
    if entered:
        return ENTERED
    if signalled:
        return SIGNALLED
    filters = dict(filters or {})
    params = dict(params or {})
    if not filters.get('ready'):
        # The band itself cannot measure. Saying FILTERED here would
        # claim a filter looked at a number that does not exist.
        return COLLECTING
    if warmup_done is False:
        return WARMUP
    size = None if z is None else abs(float(z))
    entry_z = params.get('entry_z')
    max_entry_z = params.get('max_entry_z')
    if size is not None and max_entry_z and size > float(max_entry_z):
        return BEYOND_MAX
    if size is not None and entry_z and size < float(entry_z):
        return NO_SIGNAL
    if block_reason:
        return BLOCKED if _unpriced(block_reason, filters) else FILTERED
    if size is None or not entry_z:
        return NO_SIGNAL
    return ELIGIBLE


#: Phrases the Algo's own entry check uses when it could not PRICE a
#: filter, as opposed to pricing one and refusing. Both stop an entry;
#: only one of them is a measurement.
_UNPRICED = ('not priced', 'not enough', 'not measure', 'not mean-reverting')


def _unpriced(reason, filters=None):
    """Did the entry check stop because something could not be measured?

    The edge filter's own verdict settles it where it has one: `ok` of
    None is the Algo's way of saying "unpriceable", and it blocks.
    """
    edge_verdict = (filters or {}).get('edge')
    if isinstance(edge_verdict, dict) and edge_verdict.get('on') \
            and edge_verdict.get('ok') is None \
            and 'edge filter' in str(reason).lower():
        return True
    text = str(reason or '').lower()
    return any(phrase in text for phrase in _UNPRICED)


def event_row(pair_key, at, z, zone, previous, mean, sigma, params,
              prices=None, edge=None, filters=None, block_reason=None,
              warmup_done=True, timeframe_sec=None, signalled=False,
              entered=False, algo_mode=None, step=None):
    """One recorded zone change, with everything it is judged on frozen
    into it. What goes to the database and what the table renders."""
    prices = dict(prices or {})
    edge = dict(edge or {})
    params = dict(params or {})
    return {
        'pair_key': pair_key,
        'at': float(at),
        'timeframe_sec': timeframe_sec,
        'zone': zone,
        'prev_zone': previous,
        'direction': direction(previous, zone),
        'z': None if z is None else round(float(z), 3),
        'mean': mean,
        'sigma': sigma,
        'side': entry_side(z),
        'entry': prices.get('entry'),
        'break_even': prices.get('break_even'),
        'tp': prices.get('tp'),
        'sl': prices.get('sl'),
        'edge_ratio': edge.get('ratio'),
        'edge_capture': edge.get('capture'),
        'edge_cost': edge.get('cost'),
        'edge_required': edge.get('required'),
        'edge_ok': edge.get('ok'),
        'status': status_of(z, params, filters, block_reason,
                            warmup_done, signalled, entered),
        'blocked_by': block_reason,
        'algo_mode': algo_mode,
        # The settings in force AT THE EVENT, so an Algo-relative zone
        # can be re-derived later without reading today's config.
        'entry_z': params.get('entry_z'),
        'max_entry_z': params.get('max_entry_z'),
        'stop_z': params.get('stop_z'),
        'length': params.get('length'),
        'increment': step,
    }

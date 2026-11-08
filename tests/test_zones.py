"""Sigma zones: the boundaries, the de-duplication, and the frozen row.

The faults these are written against were all measured on the desk:
a spread parked beyond a band logging a touch per restart, an
oscillation logging a touch per cycle, and a row whose numbers moved
when the ladder was edited afterwards.
"""

from mt5trader import zones


# -- the boundaries ----------------------------------------------------------

def test_the_zone_boundaries_are_signed_and_closed_at_the_bottom():
    """|z| < 1 is the mean; each band owns its own lower edge."""
    assert zones.zone_of(0.0) == zones.MEAN
    assert zones.zone_of(0.999) == zones.MEAN
    assert zones.zone_of(-0.999) == zones.MEAN
    assert zones.zone_of(1.0) == '+1'
    assert zones.zone_of(1.999) == '+1'
    assert zones.zone_of(2.0) == '+2'
    assert zones.zone_of(2.999) == '+2'
    assert zones.zone_of(3.0) == '+3'
    assert zones.zone_of(9.9) == '+3'
    assert zones.zone_of(-1.0) == '-1'
    assert zones.zone_of(-2.5) == '-2'
    assert zones.zone_of(-3.1) == '-3'


def test_an_unmeasured_z_is_not_the_mean():
    """None, NaN and infinity are UNKNOWN. Calling them the mean would
    record a walk back to zero on every gap in the band."""
    for value in (None, float('nan'), float('inf'), float('-inf'), 'x'):
        assert zones.zone_of(value) is None, value


def test_the_labels_carry_the_sigma_sign():
    assert zones.zone_label(zones.MEAN) == 'Mean'
    assert zones.zone_label('+2') == '+2σ'
    assert zones.zone_label('-3') == '-3σ'
    assert zones.zone_label(None) is None


# -- recording only on a change ----------------------------------------------

def test_the_same_zone_is_never_recorded_twice_in_a_row():
    """The whole point: a spread parked at +2.3 is ONE row."""
    previous = None
    recorded = []
    for z in (2.3, 2.31, 2.29, 2.4, 2.35):
        zone = zones.zone_of(z)
        if zones.changed(previous, zone):
            recorded.append(zone)
            previous = zone
    assert recorded == ['+2']


def test_control_a_real_path_records_every_step_of_it():
    """0 -> +2 -> +3 -> +2 -> mean -> -2, exactly as asked for."""
    previous = None
    recorded = []
    for z in (0.2, 0.5, 2.4, 2.6, 3.2, 3.4, 2.7, 0.4, -0.2, -2.2, -2.4):
        zone = zones.zone_of(z)
        if zones.changed(previous, zone):
            recorded.append(zone)
            previous = zone
    assert recorded == [zones.MEAN, '+2', '+3', '+2', zones.MEAN, '-2']


def test_an_unmeasured_pass_neither_records_nor_forgets():
    """A gap in the band must not manufacture a row when it comes back
    to the zone it was already in."""
    previous = '+2'
    assert not zones.changed(previous, zones.zone_of(None))
    assert not zones.changed(previous, zones.zone_of(2.5))


def test_crossing_one_sigma_is_not_a_zone_change_on_its_own():
    """2.2 <-> 0.8 logged a touch per cycle under the old counter. The
    zone changes - +1 and mean are real zones - but +2 is recorded once
    per genuine excursion, not once per dip."""
    previous = None
    recorded = []
    for z in (2.2, 1.4, 2.2, 1.4, 2.2):
        zone = zones.zone_of(z)
        if zones.changed(previous, zone):
            recorded.append(zone)
            previous = zone
    assert recorded.count('+2') == 3      # three genuine re-entries
    assert recorded == ['+2', '+1', '+2', '+1', '+2']
    # and no two consecutive rows are ever the same zone
    assert all(a != b for a, b in zip(recorded, recorded[1:]))


def test_the_direction_says_which_way_it_went():
    assert zones.direction(zones.MEAN, '+2') == 'OUT'
    assert zones.direction('+2', '+3') == 'OUT'
    assert zones.direction('+3', '+2') == 'IN'
    assert zones.direction('+2', zones.MEAN) == 'IN'
    assert zones.direction('+1', '-1') == 'CROSS'
    assert zones.direction('+2', '+2') is None
    assert zones.direction(None, '+2') is None


# -- the hypothetical prices -------------------------------------------------

def test_the_entry_side_is_the_one_the_algo_would_take():
    """It sells a spread stretched high and buys one stretched low."""
    assert zones.entry_side(2.5) == 'SELL'
    assert zones.entry_side(-2.5) == 'BUY'
    assert zones.entry_side(0.0) is None
    assert zones.entry_side(None) is None


def test_a_buy_puts_the_target_above_break_even_and_the_stop_below():
    body = zones.levels('BUY', entry_spread=10.0, fee_points=0.20,
                        target_points=1.0, stop_points=0.5)
    assert body['entry'] == 10.0
    assert body['break_even'] == 10.20          # costs are recovered UP
    assert body['tp'] == 11.20
    assert body['sl'] == 9.70


def test_a_sell_mirrors_it():
    body = zones.levels('SELL', entry_spread=10.0, fee_points=0.20,
                        target_points=1.0, stop_points=0.5)
    assert body['entry'] == 10.0
    assert body['break_even'] == 9.80           # costs are recovered DOWN
    assert body['tp'] == 8.80
    assert body['sl'] == 10.30


def test_prices_are_rounded_to_the_spreads_own_step():
    body = zones.levels('BUY', entry_spread=10.004, fee_points=0.203,
                        target_points=1.0, stop_points=0.5, step=0.01)
    assert body['entry'] == 10.0
    assert body['break_even'] == 10.21
    assert body['tp'] == 11.21
    assert body['sl'] == 9.71


def test_an_unpriceable_level_is_none_and_never_zero():
    """A stop of 0.00 reads as 'get out at break-even', which is a
    different instruction from 'this could not be priced'."""
    body = zones.levels('BUY', 10.0, fee_points=0.2,
                        target_points=None, stop_points=None)
    assert body['break_even'] == 10.2
    assert body['tp'] is None
    assert body['sl'] is None
    no_costs = zones.levels('BUY', 10.0, fee_points=None,
                            target_points=1.0, stop_points=0.5)
    assert no_costs['entry'] == 10.0
    assert no_costs['break_even'] is None
    assert no_costs['tp'] is None and no_costs['sl'] is None


def test_no_side_and_no_entry_price_nothing():
    assert zones.levels(None, 10.0, 0.2, 1.0, 0.5)['entry'] is None
    assert zones.levels('BUY', None, 0.2, 1.0, 0.5)['entry'] is None


# -- status ------------------------------------------------------------------

PARAMS = {'entry_z': 2.5, 'max_entry_z': 3.5, 'stop_z': 4.0, 'length': 20}
READY = {'ready': True, 'edge': {'on': True, 'ok': True, 'ratio': 2.0}}


def test_a_band_that_cannot_measure_is_collecting_not_filtered():
    """FILTERED would claim a filter looked at a number that does not
    exist yet."""
    assert zones.status_of(2.6, PARAMS, {'ready': False}) == zones.COLLECTING


def test_a_full_band_on_an_unwatched_tape_is_warming_up():
    assert zones.status_of(2.6, PARAMS, READY,
                           warmup_done=False) == zones.WARMUP


def test_inside_the_entry_threshold_there_is_simply_no_signal():
    assert zones.status_of(1.2, PARAMS, READY) == zones.NO_SIGNAL


def test_past_the_cap_is_a_blow_out_not_an_entry():
    assert zones.status_of(3.9, PARAMS, READY) == zones.BEYOND_MAX


def test_a_filter_that_priced_it_and_said_no_is_filtered():
    """The Algo's own refusal text, verbatim."""
    reason = 'edge filter: capture 0.40x the cost, under the 1.5x required'
    body = dict(READY, edge={'on': True, 'ok': False, 'ratio': 0.4})
    assert zones.status_of(2.6, PARAMS, body, reason) == zones.FILTERED
    trending = 'regime: the spread is TRENDING (efficiency 0.80, 2 crossings)'
    assert zones.status_of(2.6, PARAMS, READY, trending) == zones.FILTERED


def test_a_filter_that_could_not_be_priced_blocks():
    """The Algo treats an unpriceable filter as a block, and so does
    this - a row that said FILTERED would claim it was measured."""
    reason = 'edge filter: the round-trip cost is not priced yet'
    body = dict(READY, edge={'on': True, 'ok': None, 'ratio': None})
    assert zones.status_of(2.6, PARAMS, body, reason) == zones.BLOCKED
    short = 'trend filter: not enough candles to measure it yet'
    assert zones.status_of(2.6, PARAMS, READY, short) == zones.BLOCKED


def test_clearing_the_threshold_with_nothing_in_the_way_is_eligible():
    """ELIGIBLE, never SIGNALLED: the re-entry arming, the direction and
    the confirm ticks are the Algo's and are not modelled here."""
    assert zones.status_of(2.6, PARAMS, READY) == zones.ELIGIBLE
    assert zones.status_of(2.4, PARAMS, READY) == zones.NO_SIGNAL


def test_a_signal_and_a_fill_outrank_everything():
    assert zones.status_of(2.6, PARAMS, {'ready': False},
                           signalled=True) == zones.SIGNALLED
    assert zones.status_of(2.6, PARAMS, READY, 'edge filter: no',
                           entered=True) == zones.ENTERED


# -- the frozen row ----------------------------------------------------------

def test_the_row_freezes_the_settings_it_was_judged_on():
    """History must not change when a ladder is edited tomorrow."""
    row = zones.event_row(
        'XAUUSD_|GC1226', at=1000.0, z=2.6, zone='+2', previous=zones.MEAN,
        mean=10.0, sigma=0.5, params=PARAMS,
        prices={'entry': 11.3, 'break_even': 11.1, 'tp': 10.1, 'sl': 11.6},
        edge={'ratio': 2.1, 'capture': 42.0, 'cost': 20.0,
              'required': 1.5, 'ok': True},
        filters=READY, timeframe_sec=900.0, algo_mode='DRY_RUN', step=0.01)
    assert row['zone'] == '+2' and row['prev_zone'] == zones.MEAN
    assert row['direction'] == 'OUT'
    assert row['side'] == 'SELL'
    assert row['entry_z'] == 2.5 and row['max_entry_z'] == 3.5
    assert row['stop_z'] == 4.0 and row['length'] == 20
    assert row['edge_ratio'] == 2.1 and row['edge_ok'] is True
    assert row['status'] == zones.ELIGIBLE   # 2.6 clears entry_z, no gates
    assert row['algo_mode'] == 'DRY_RUN'
    assert row['timeframe_sec'] == 900.0
    assert row['z'] == 2.6

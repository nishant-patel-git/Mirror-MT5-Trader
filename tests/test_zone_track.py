"""The signal track in the engine: what the Algo would really have
traded, with its Algo on or off.

The faults these are written against were measured on a live desk. The
worst of them put 147 trades on the page in one day that the Algo would
never have taken, every one stopped out on its first mark, for -$53,245
of P&L that never existed.
"""

import pytest

from mt5trader import algo as algo_module, zones
from mt5trader.coordinator import Coordinator
from mt5trader.database import Store

from test_algo_desk import Clock


def engine(config, legs, tmp_path):
    store = Store(str(tmp_path / 'trader.db'))
    clock = Clock()
    coordinator = Coordinator(config, legs, sleep=lambda s: None,
                              clock=clock, store=store)
    coordinator.start()
    coordinator.poll_once()
    return coordinator, store, clock


def fill_the_band(coordinator, pair, clock, centre=13.0, step=0.0):
    """Give the analysis band a full set of closed candles.

    Written straight into the candle store the band seeds from, which
    is the same table the Algo's band fills and keyed the same way — so
    this is the state a restart finds, not a fixture shortcut.
    """
    params = algo_module.clean_params(getattr(pair, 'algo_params', None))
    timeframe = params['timeframe_min'] * 60.0
    rows = [(clock.now - (params['length'] - i) * timeframe,
             centre + i * step) for i in range(params['length'] * 2)]
    coordinator.store.save_candles(pair.key, timeframe,
                                   pair.hedge_ratio or 1.0, rows, 'mt5')
    coordinator._bands.clear()
    return timeframe


def drive(coordinator, pair, clock, zs, settings=None):
    """Feed the zone track a series of z readings and collect the rows
    it chose to record. The reading is built the way `_band_reading`
    builds it, so the de-duplication under test is the real one."""
    settings = settings or pair.exit_settings(coordinator.config.settings)
    md = dict(coordinator.market[pair.key])
    candles = coordinator._band(pair)
    recorded = []
    for z in zs:
        reading = {'z': z, 'mean': 13.0, 'sigma': 0.1,
                   'stats': candles.stats(), 'source': 'analysis',
                   'candles': candles}
        clock.now += 1.0
        row = coordinator._track_zones(pair, md, None, reading, settings,
                                       clock.now)
        if row is not None:
            recorded.append(row)
    return recorded


# -- signals: armed, entered, and what became of them ------------------------

def signal_drive(coordinator, pair, clock, zs, settings=None):
    """Feed the signal track a series of z readings, as `_band_reading`
    would build them."""
    settings = settings or pair.exit_settings(coordinator.config.settings)
    md = dict(coordinator.market[pair.key])
    candles = coordinator._band(pair)
    for z in zs:
        clock.now += 60.0
        coordinator._track_signals(
            pair, md, None,
            {'z': z, 'mean': 13.0, 'sigma': 0.1, 'stats': candles.stats(),
             'source': 'analysis', 'candles': candles},
            settings, clock.now)
    return coordinator.store.signals(pair.key)


def test_a_second_poke_at_the_band_is_not_a_second_signal(config, pair,
                                                          legs, tmp_path):
    """The complaint this track was rewritten for: 2.6 -> 2.55 -> 2.9
    has stretched ONCE. It never came back inside, so there is nothing
    new to act on."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = signal_drive(coordinator, pair, clock,
                        [2.6, 2.55, 2.9, 2.7, 3.1])
    assert len(rows) == 1


def test_control_a_stretch_that_came_back_signals_again(config, pair, legs,
                                                        tmp_path):
    """Back through the window's floor and out again IS a second
    signal."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = signal_drive(coordinator, pair, clock, [2.6, 0.5, 2.7])
    assert len(rows) == 2


def test_only_the_ladders_own_threshold_is_tracked(config, pair, legs,
                                                   tmp_path):
    """The fixed +-2 / +-3 sigma study was taken back out: two sets of
    counts on one page made the one that mattered harder to read. A
    stretch under `entry_z` is no longer a signal of any kind."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = signal_drive(coordinator, pair, clock, [2.1, 2.3, 2.4])
    assert rows == []
    # CONTROL: past entry_z (2.5) it signals.
    rows = signal_drive(coordinator, pair, clock, [2.6])
    assert len(rows) == 1 and rows[0]['basis'] == 'ALGO'


def test_a_signal_carries_its_hypothetical_trade(config, pair, legs,
                                                 tmp_path):
    """Priced when it ENTERS, not when it arms: until the pull-back
    there is no trade to price."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    coordinator._entry_refusal = lambda *a, **k: (None, {})
    signal_drive(coordinator, pair, clock, [2.6])
    [row] = store.signals(pair.key)
    assert row['entry'] is None            # armed only
    signal_drive(coordinator, pair, clock, [1.8])
    [row] = store.signals(pair.key)
    assert row['side'] == 'SELL'
    assert row['entry'] is not None and row['break_even'] is not None
    assert row['units']
    # A sell's target is BELOW break-even and its stop ABOVE.
    if row['tp'] is not None and row['sl'] is not None:
        assert row['tp'] < row['break_even'] < row['sl']


def test_a_stretch_the_algo_would_refuse_is_not_a_trade(config, pair, legs,
                                                        tmp_path):
    """The fault this was rewritten for.

    Arming and entering on z ALONE put 147 trades on a live desk's page
    in one day that the Algo would never have taken - its edge filter
    was reading 0.02x against the 1.5x it requires - and every one was
    then stopped out on its first mark. A refusal is recorded as a
    refusal, with the Algo's own words.
    """
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    signal_drive(coordinator, pair, clock, [2.6])
    [row] = store.signals(pair.key)
    assert row['state'] == 'ARMED' and row['entered_at'] is None
    signal_drive(coordinator, pair, clock, [1.8])
    [row] = store.signals(pair.key)
    assert row['outcome'] == 'BLOCKED'
    assert row['entered_at'] is None          # never a trade
    assert row['block_reason']                # in the Algo's own words
    assert row['net_pnl'] is None             # and never a loss


def test_CONTROL_with_the_gates_open_it_arms_then_enters(config, pair, legs,
                                                         tmp_path):
    """Exactly the Algo's own machine: armed at entry_z, entered on the
    pull-back into the window - when nothing refuses it."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    # Nothing refuses: no filters, and a stop well outside the bid-ask.
    coordinator._entry_refusal = lambda *a, **k: (None, {'ratio': 9.0,
                                                         'required': 1.5})
    signal_drive(coordinator, pair, clock, [2.6])
    [row] = store.signals(pair.key)
    assert row['state'] == 'ARMED' and row['entered_at'] is None
    signal_drive(coordinator, pair, clock, [1.8])
    [row] = store.signals(pair.key)
    assert row['entered_at'] is not None
    assert row['entered_z'] == 1.8
    assert row['outcome'] is None             # on, and being watched
    assert row['edge_ratio'] == 9.0


def test_a_trend_that_never_comes_back_is_recorded_as_never_entered(
        config, pair, legs, tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    signal_drive(coordinator, pair, clock, [2.6, 3.1, 3.6, 4.2, 0.4])
    algo = [r for r in store.signals(pair.key) if r['basis'] == 'ALGO']
    assert algo and algo[-1]['outcome'] == 'NO_ENTRY'


def test_the_signals_are_recorded_with_the_algo_off(config, pair, legs,
                                                    tmp_path):
    """The whole point: answer 'is this worth switching on?' BEFORE
    switching it on."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    assert not coordinator.algos.is_on(pair.key)
    fill_the_band(coordinator, pair, clock)
    rows = signal_drive(coordinator, pair, clock, [2.6])
    assert rows and all(r['algo_mode'] == 'OFF' for r in rows)


def test_the_arming_is_not_lost_on_a_restart(config, pair, legs, tmp_path):
    """Arming that lives only in memory re-arms on every restart, and a
    spread that has not moved then signals again."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    signal_drive(coordinator, pair, clock, [2.6])
    for _ in range(3):
        coordinator._signals = None          # a restart
        coordinator._arming_live = {}
        signal_drive(coordinator, pair, clock, [2.6])
    assert len(store.signals(pair.key)) == 1


def test_every_row_is_filed_under_the_brokers_day(config, pair, legs,
                                                  tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = signal_drive(coordinator, pair, clock, [2.6])
    day = coordinator.broker_day()
    assert day is None or all(r['broker_day'] == day for r in rows)


def test_a_stop_inside_the_bid_ask_is_refused_not_entered(config, pair, legs,
                                                          tmp_path):
    """Why a live desk's whole day closed at 0.0 min.

    A trade is entered at one executable side and marked against the
    other, so the round turn is against it immediately. A stop nearer
    than the bid-ask is therefore reached on the FIRST mark - every
    time, for -$360 a go. `backtest._side_levels` already refused this
    for the Algo's own replay; the live track did not.
    """
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    md = dict(coordinator.market[pair.key])
    width = md['long_spread'] - md['short_spread']
    assert width > 0
    params = algo_module.clean_params(getattr(pair, 'algo_params', None))
    settings = pair.exit_settings(coordinator.config.settings)
    candles = coordinator._band(pair)
    reading = {'z': 1.8, 'mean': 13.0, 'sigma': 0.1,
               'stats': candles.stats(), 'candles': candles}

    # No filter refuses; the LEVELS must still be looked at.
    import mt5trader.coordinator as mod
    real = mod.judge_filters
    mod.judge_filters = lambda *a, **k: ({'cost': {'total': 1.0}, 'k': 10.0},
                                         lambda side, z: None)
    try:
        # A stop a tenth of the bid-ask away is not a stop.
        coordinator.hypothetical_levels = lambda *a, **k: {
            'prices': {'entry': 13.0, 'break_even': 13.0,
                       'tp': 13.0 + width * 5,
                       'sl': 13.0 - width / 10.0},
            'units': 50.0}
        reason, _edge = coordinator._entry_refusal(
            pair, md, None, reading, params, 'BUY', 1.8, settings)
        assert reason and 'inside the bid-ask' in reason

        # CONTROL: the same trade with the stop well OUTSIDE it is taken.
        coordinator.hypothetical_levels = lambda *a, **k: {
            'prices': {'entry': 13.0, 'break_even': 13.0,
                       'tp': 13.0 + width * 5,
                       'sl': 13.0 - width * 5},
            'units': 50.0}
        reason, _edge = coordinator._entry_refusal(
            pair, md, None, reading, params, 'BUY', 1.8, settings)
        assert reason is None
    finally:
        mod.judge_filters = real


def test_a_blocked_signal_is_never_counted_as_a_loss():
    """147 refusals read as 147 losses and -$53,245 of P&L that never
    happened. A refusal is not a trade."""
    from mt5trader import signals as sig
    rows = []
    for _ in range(3):
        row = sig.new_signal('A|B', sig.ALGO, 'SELL', 0.0, 2.6, {})
        sig.block(row, 10.0, 'edge filter: capture 0.02x the cost',
                  {'ratio': 0.02, 'required': 1.5})
        rows.append(row)
    body = sig.tally(rows)
    assert body['armed'] == 3 and body['blocked'] == 3
    assert body['entered'] == 0 and body['settled'] == 0
    assert body['sl'] == 0 and body['tp'] == 0
    assert body['net_pnl'] is None            # never measured, never zero
    assert body['win_rate'] is None

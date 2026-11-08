"""The sigma-zone track in the engine: recorded on a CHANGE, recorded
whether the Algo is on or off, and never recorded twice in a row.

The three faults these are written against were all measured on the
desk, and the third is the one that cost the most: the tab meant to
tell a trader whether the Algo is worth switching on recorded nothing
until they had switched it on.
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


# -- recorded on a change, and only on a change ------------------------------

def test_a_spread_parked_in_one_zone_is_recorded_once(config, pair, legs,
                                                      tmp_path):
    """Twelve polls beyond +2 sigma, one row. This is the complaint the
    whole track was rewritten for."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = drive(coordinator, pair, clock, [2.3] * 12)
    assert [r['zone'] for r in rows] == ['+2']
    assert len(store.zone_events(pair.key)) == 1


def test_control_a_real_path_records_every_step(config, pair, legs, tmp_path):
    """0 -> +2 -> +3 -> +2 -> mean -> -2, the path asked for."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    rows = drive(coordinator, pair, clock,
                 [0.2, 0.3, 2.4, 2.5, 3.2, 3.3, 2.7, 0.4, -0.3, -2.2, -2.3])
    assert [r['zone'] for r in rows] == [
        zones.MEAN, '+2', '+3', '+2', zones.MEAN, '-2']
    assert [r['direction'] for r in rows][1:] == [
        'OUT', 'OUT', 'IN', 'IN', 'OUT']


def test_no_two_consecutive_rows_are_ever_the_same_zone(config, pair, legs,
                                                        tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    drive(coordinator, pair, clock,
          [0.1, 2.2, 2.3, 2.9, 3.4, 3.5, 2.8, 2.2, 0.9, 0.1, -1.2, -2.6])
    saved = list(reversed(store.zone_events(pair.key)))   # oldest first
    assert len(saved) > 2
    assert all(a['zone'] != b['zone'] for a, b in zip(saved, saved[1:]))


def test_an_unmeasured_pass_records_nothing_and_forgets_nothing(
        config, pair, legs, tmp_path):
    """A gap in the band must not manufacture a walk back to the mean,
    nor a second row for the zone it was already in."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    drive(coordinator, pair, clock, [2.4])
    settings = pair.exit_settings(coordinator.config.settings)
    md = dict(coordinator.market[pair.key])
    assert coordinator._track_zones(pair, md, None, None, settings,
                                    clock.now) is None
    assert coordinator._track_zones(pair, md, None, {'z': None}, settings,
                                    clock.now) is None
    drive(coordinator, pair, clock, [2.45])
    assert len(store.zone_events(pair.key)) == 1


def test_a_restart_does_not_re_record_the_zone_it_is_already_in(
        config, pair, legs, tmp_path):
    """The zone last recorded is read back from the database. Keeping it
    only in memory is what made a parked spread log a fresh touch after
    every restart."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    drive(coordinator, pair, clock, [2.4])
    for _ in range(4):
        coordinator._zone_last = None        # a restart
        coordinator._bands.clear()
        drive(coordinator, pair, clock, [2.4])
    assert len(store.zone_events(pair.key)) == 1
    # CONTROL: a genuine move after the restart is still recorded.
    coordinator._zone_last = None
    drive(coordinator, pair, clock, [3.2])
    assert [r['zone'] for r in store.zone_events(pair.key)] == ['+3', '+2']


# -- the Algo's switch must not gate the MEASURING ---------------------------

def test_the_zone_track_records_with_the_algo_off(config, pair, legs,
                                                  tmp_path):
    """LIVE is off after every restart, so 'only while the Algo is on'
    meant 'almost never'."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    assert not coordinator.algos.is_on(pair.key)        # off, as at start
    fill_the_band(coordinator, pair, clock)
    rows = drive(coordinator, pair, clock, [0.2, 2.4, 3.3])
    assert [r['zone'] for r in rows] == [zones.MEAN, '+2', '+3']
    assert all(r['algo_mode'] == 'OFF' for r in rows)
    assert len(store.zone_events(pair.key)) == 3


def test_control_with_the_algo_on_the_rows_say_which_mode(config, pair, legs,
                                                          tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    coordinator.algos.turn_on(pair, algo_module.DRY_RUN)
    rows = drive(coordinator, pair, clock, [0.2, 2.4])
    assert rows and all(r['algo_mode'] == algo_module.DRY_RUN for r in rows)


def test_the_band_measures_without_the_algo_ever_being_turned_on(
        config, pair, legs, tmp_path):
    """The analysis band is the Algo's own candles and arithmetic, kept
    whatever the switch says."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock, centre=13.0, step=0.01)
    md = dict(coordinator.market[pair.key])
    reading = coordinator._band_reading(pair, md, None, clock.now)
    assert reading is not None
    assert reading['source'] == 'analysis'
    assert reading['stats']['ready'] is True
    assert reading['mean'] is not None and reading['sigma']
    assert reading['z'] is not None


def test_the_algos_own_band_wins_when_it_is_on(config, pair, legs, tmp_path):
    """So the tab and the desk can never quote two numbers for one
    moment."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock, centre=13.0, step=0.01)
    md = dict(coordinator.market[pair.key])
    body = {'mean': 99.0, 'sigma': 2.0}
    reading = coordinator._band_reading(pair, md, body, clock.now)
    assert reading['source'] == 'algo'
    assert reading['mean'] == 99.0 and reading['sigma'] == 2.0


# -- the row's own numbers ---------------------------------------------------

def test_a_row_carries_the_settings_it_was_judged_on(config, pair, legs,
                                                     tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    [row] = drive(coordinator, pair, clock, [2.6])
    params = algo_module.clean_params(getattr(pair, 'algo_params', None))
    assert row['entry_z'] == params['entry_z']
    assert row['max_entry_z'] == params['max_entry_z']
    assert row['stop_z'] == params['stop_z']
    assert row['length'] == params['length']
    assert row['status'] in zones.STATUSES
    assert row['side'] == 'SELL'          # stretched HIGH: it sells


def test_a_stretch_below_the_mean_is_a_buy(config, pair, legs, tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    [row] = drive(coordinator, pair, clock, [-2.6])
    assert row['side'] == 'BUY' and row['zone'] == '-2'


def test_history_does_not_change_when_the_ladder_is_edited_afterwards(
        config, pair, legs, tmp_path):
    """The whole reason the numbers are stored and not recomputed."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    drive(coordinator, pair, clock, [2.6])
    before = store.zone_events(pair.key)[0]
    pair.algo_params = dict(getattr(pair, 'algo_params', None) or {},
                            entry_z=1.0, max_entry_z=9.0)
    coordinator.poll_once()
    # THAT row, by id — the poll may append others, which is the table
    # growing and not the row changing.
    [after] = [r for r in store.zone_events(pair.key)
               if r['id'] == before['id']]
    assert after['entry_z'] == before['entry_z'] == 2.5
    assert after['max_entry_z'] == 3.5
    assert after['status'] == before['status']
    assert after['tp'] == before['tp'] and after['sl'] == before['sl']


def test_an_unpriceable_level_is_a_dash_and_never_a_zero(config, pair, legs,
                                                         tmp_path):
    """A stop of 0.00 reads as 'get out at break-even'."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    pair.algo_params = dict(getattr(pair, 'algo_params', None) or {},
                            stop_loss_on=False)
    [row] = drive(coordinator, pair, clock, [2.6])
    assert row['sl'] is None


# -- the date range is filtered in the QUERY ---------------------------------

def test_the_range_is_filtered_in_the_query_not_in_the_browser(
        config, pair, legs, tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    day = 86400.0
    for age, zone in ((40 * day, '+2'), (20 * day, '+3'),
                      (3 * day, '-2'), (0.5 * day, '-3')):
        store.save_zone_event(zones.event_row(
            pair.key, clock.now - age, 2.5, zone, zones.MEAN, 13.0, 0.1,
            {'entry_z': 2.5}, timeframe_sec=900.0))
    since = lambda days: clock.now - days * day
    assert len(store.zone_events(pair.key, since=since(1))) == 1
    assert len(store.zone_events(pair.key, since=since(7))) == 2
    assert len(store.zone_events(pair.key, since=since(30))) == 3
    assert len(store.zone_events(pair.key)) == 4
    span = store.zone_span()[pair.key]
    assert span['rows'] == 4
    assert span['first_at'] == pytest.approx(clock.now - 40 * day)
    assert span['last_at'] == pytest.approx(clock.now - 0.5 * day)


# -- the edge filter, the entry, the take-profit and the stop ----------------

def test_the_target_is_a_percentage_of_the_margin_the_trade_ties_up(
        config, pair, legs, tmp_path):
    """MARGIN mode: `tp_pct` of margin for `qty` spreads, through the
    one `k` every spread-to-money conversion in this system uses."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    params = dict(algo_module.clean_params(None),
                  target_mode='MARGIN', algo_qty=2.0)
    settings = {'TP_TARGET_PCT_OF_MARGIN': 3.0}
    points = coordinator._zone_target_points(
        params, settings, margin=500.0, qty=2.0, units=50.0, atr=None)
    # 3% of 500 x 2 spreads = $30, over k x qty = 50 x 2 = 100
    assert points == pytest.approx(0.30)


def test_the_target_in_atr_mode_is_a_multiple_of_the_atr(config, pair, legs,
                                                         tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    params = dict(algo_module.clean_params(None), target_mode='ATR',
                  atr_target_mult=1.5)
    points = coordinator._zone_target_points(
        params, {'TP_TARGET_PCT_OF_MARGIN': 3.0}, margin=500.0, qty=1.0,
        units=50.0, atr=0.4)
    assert points == pytest.approx(0.6)
    # Unmeasured ATR is not a zero target.
    assert coordinator._zone_target_points(
        params, {}, 500.0, 1.0, 50.0, None) is None


def test_an_unpriced_margin_is_no_target_rather_than_break_even(
        config, pair, legs, tmp_path):
    """Break-even wearing the target's name is a different
    instruction."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    params = dict(algo_module.clean_params(None), target_mode='MARGIN')
    assert coordinator._zone_target_points(
        params, {'TP_TARGET_PCT_OF_MARGIN': 3.0}, margin=None, qty=1.0,
        units=50.0, atr=None) is None
    assert coordinator._zone_target_points(
        params, {'TP_TARGET_PCT_OF_MARGIN': 0}, margin=500.0, qty=1.0,
        units=50.0, atr=None) is None


@pytest.mark.parametrize('z,side', [(2.8, 'SELL'), (-2.8, 'BUY')])
def test_the_target_and_the_stop_straddle_break_even_on_both_sides(
        config, pair, legs, tmp_path, z, side):
    """+sigma sells and -sigma buys, and each one's target is the way it
    profits while its stop is the way it loses."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    [row] = drive(coordinator, pair, clock, [z])
    assert row['side'] == side
    assert row['entry'] is not None and row['break_even'] is not None
    if row['tp'] is not None and row['sl'] is not None:
        if side == 'BUY':
            assert row['tp'] > row['break_even'] > row['sl']
        else:
            assert row['tp'] < row['break_even'] < row['sl']


@pytest.mark.parametrize('side,expected_be', [('BUY', 10.2), ('SELL', 9.8)])
def test_break_even_recovers_the_costs_in_the_direction_of_the_trade(
        side, expected_be):
    """A long recovers its costs UP, a short DOWN. The same four terms
    `takeprofit` uses, so the row and the Exit panel cannot disagree."""
    body = zones.levels(side, entry_spread=10.0, fee_points=0.2,
                        target_points=1.0, stop_points=0.5)
    assert body['break_even'] == pytest.approx(expected_be)


def test_the_edge_value_on_a_row_is_capture_over_cost(config, pair, legs,
                                                      tmp_path):
    """The edge filter's own number: expected capture against the
    round-trip cost, with the multiple it had to clear."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock, centre=13.0, step=0.01)
    [row] = drive(coordinator, pair, clock, [2.8])
    if row['edge_ratio'] is not None:
        assert row['edge_capture'] is not None and row['edge_cost'] is not None
        assert row['edge_ratio'] == pytest.approx(
            row['edge_capture'] / row['edge_cost'])
        assert row['edge_ok'] == (row['edge_ratio'] >= row['edge_required'])
    else:
        # Unpriceable is a BLOCK and a dash, never a 0.00x edge.
        assert row['edge_ok'] is None
        assert row['status'] in (zones.BLOCKED, zones.COLLECTING)


def test_the_counters_also_count_with_the_algo_off(config, pair, legs,
                                                   tmp_path):
    """The same reading feeds the +-2/+-3 tiles, so they stopped being
    blank on a ladder whose Algo has never been switched on."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    assert not coordinator.algos.is_on(pair.key)
    fill_the_band(coordinator, pair, clock)
    md = dict(coordinator.market[pair.key])
    candles = coordinator._band(pair)
    for z in (0.2, 2.4):
        coordinator._track_excursions(
            pair, md, {'z': z, 'mean': 13.0, 'sigma': 0.1,
                       'stats': candles.stats(), 'candles': candles},
            clock.now)
    assert store.excursions()[pair.key]['counts']['touch_2_up'] == 1



# -- the Analysis side must not be able to change what the Algo does --------

def test_the_analysis_band_never_writes_to_the_algos_candles(config, pair,
                                                             legs, tmp_path):
    """A reading must not be able to change a decision.

    The analysis band runs whether a ladder's Algo is on or off. If it
    wrote into `algo_candles`, a later `turn_on` would seed the Algo's
    band from candles the ANALYSIS side collected — which is a reading
    reaching into signal generation by the back door.
    """
    coordinator, store, clock = engine(config, legs, tmp_path)
    params = algo_module.clean_params(getattr(pair, 'algo_params', None))
    timeframe = params['timeframe_min'] * 60.0
    beta = pair.hedge_ratio or 1.0

    def algo_rows():
        with store._connect() as c:
            return c.execute('SELECT COUNT(*) AS n FROM algo_candles'
                             ).fetchone()['n']

    before = algo_rows()
    md = dict(coordinator.market[pair.key])
    candles = coordinator._band(pair)
    # Walk the clock over several candle boundaries so closes are
    # written, then check WHERE they went.
    for step in range(1, 6):
        clock.now += timeframe
        coordinator._band_reading(pair, md, None, clock.now)
    assert algo_rows() == before, 'the analysis band wrote to algo_candles'
    assert store.analysis_candles(pair.key, timeframe, beta), \
        'the analysis band saved nothing of its own'


def test_the_analysis_band_still_starts_warm_from_the_algos_candles(
        config, pair, legs, tmp_path):
    """Reading them is safe in the direction that matters: the band
    starts warm without ever writing there."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    timeframe = fill_the_band(coordinator, pair, clock)
    md = dict(coordinator.market[pair.key])
    reading = coordinator._band_reading(pair, md, None, clock.now)
    assert reading['stats']['ready'] is True
    assert reading['stats']['count'] >= reading['stats']['needed']


def test_the_algo_decides_before_the_analysis_observes(config, pair, legs,
                                                       tmp_path):
    """Order matters: `_work_algo` then `_track`. Reversed, this pass's
    reading would be built from a band the Algo had not seen."""
    import inspect
    from mt5trader.coordinator import Coordinator
    source = inspect.getsource(Coordinator._poll_once)
    assert '_work_algo' in source and 'self._track(' in source
    assert source.index('_work_algo') < source.index('self._track(')


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


def test_the_algo_basis_arms_first_and_enters_on_the_way_back(config, pair,
                                                              legs, tmp_path):
    """Exactly the Algo's own machine: armed at entry_z, entered on the
    pull-back into the window."""
    coordinator, store, clock = engine(config, legs, tmp_path)
    fill_the_band(coordinator, pair, clock)
    signal_drive(coordinator, pair, clock, [2.6])
    [algo] = [r for r in store.signals(pair.key) if r['basis'] == 'ALGO']
    assert algo['state'] == 'ARMED' and algo['entered_at'] is None
    signal_drive(coordinator, pair, clock, [1.8])
    [algo] = [r for r in store.signals(pair.key) if r['basis'] == 'ALGO']
    assert algo['entered_at'] is not None
    assert algo['entered_z'] == 1.8


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

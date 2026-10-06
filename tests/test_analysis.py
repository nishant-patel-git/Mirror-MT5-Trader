"""The Analysis tab: the arithmetic, the trackers the engine feeds, and
that none of it can reach an order."""

import ast

import pytest

from mt5trader import analysis
from mt5trader.coordinator import Coordinator
from mt5trader.database import Store
from mt5trader.models import SpreadSide

from test_algo_desk import Clock


def closed(pnl, at, peak=None, trough=None, peak_min=None, **kw):
    return dict({'position_id': f'P{at}', 'pair_key': 'A|B', 'side': 'SELL',
                 'opened_at': at - 600, 'closed_at': at, 'realized_pnl': pnl,
                 'peak_pnl': peak, 'trough_pnl': trough, 'peak_min': peak_min,
                 'close_reason': 'manual', 'source': 'ALGO'}, **kw)


# -- the arithmetic ----------------------------------------------------------


def test_the_drawdown_is_the_worst_fall_from_a_peak_in_closing_order():
    rows = [closed(10, 1), closed(-4, 2), closed(-8, 3), closed(5, 4)]
    d = analysis.drawdown(rows)
    assert d['peak_equity'] == 10 and d['max'] == 12
    assert d['current'] == 7 and d['max_pct'] == 120.0


def test_no_closed_trade_is_no_drawdown_not_a_zero():
    d = analysis.drawdown([])
    assert d['max'] is None and d['peak_equity'] is None
    s = analysis.summary([])
    assert s['net_pnl'] is None and s['win_rate'] is None


def test_the_summary_leaves_an_unpriced_close_out_of_the_win_rate():
    rows = [closed(5, 1), closed(-2, 2), closed(None, 3),
            dict(closed(0, 4), closed_at=None)]
    s = analysis.summary(rows)
    assert s['count'] == 4 and s['closed'] == 2 and s['open'] == 1
    assert s['win_rate'] == 50.0 and s['net_pnl'] == 3


def test_a_trade_keeps_its_best_and_worst_point_and_when():
    class P:
        opened_at = 0.0
        peak_pnl = peak_min = trough_pnl = trough_min = None
    p = P()
    assert analysis.observe_extremes(p, -3.0, 60.0)
    assert analysis.observe_extremes(p, 7.5, 300.0)
    assert not analysis.observe_extremes(p, 2.0, 400.0)       # inside
    assert (p.peak_pnl, p.peak_min) == (7.5, 5.0)
    assert (p.trough_pnl, p.trough_min) == (-3.0, 1.0)
    # Unmeasured changes nothing.
    assert not analysis.observe_extremes(p, None, 500.0)


def test_mae_mfe_and_how_much_of_its_best_a_trade_kept():
    [row] = analysis.excursion_rows([closed(4.0, 1, peak=10.0, trough=-6.0,
                                            peak_min=12.0)])
    assert row['mae'] == 6.0 and row['mfe'] == 10.0
    assert row['utilisation_pct'] == 40.0 and row['peak_min'] == 12.0


def test_a_loser_has_no_kept_percentage():
    [row] = analysis.excursion_rows([closed(-4.0, 1, peak=10.0, trough=-6.0)])
    assert row['utilisation_pct'] is None


def test_a_trade_from_before_extremes_were_kept_is_left_out():
    assert analysis.excursion_rows([closed(4.0, 1)]) == []
    assert analysis.calibration([closed(4.0, 1)])['n'] == 0


def test_calibration_reads_the_peaks():
    rows = [closed(1, i, peak=float(i), peak_min=float(i)) for i in
            range(1, 11)]
    c = analysis.calibration(rows)
    assert c['n'] == 10 and c['peak_pctile']['50'] == pytest.approx(5.5)
    assert c['suggested_take'] == pytest.approx(analysis.percentile(
        [float(i) for i in range(1, 11)], 65))
    assert c['suggested_max_hold_min'] == pytest.approx(5.5)


def test_the_journal_runs_its_total_in_closing_order_newest_first():
    rows = [closed(5, 1), closed(-2, 2), closed(3, 3)]
    trips = analysis.journal(rows)
    assert [t['cum_pnl'] for t in trips] == [6, 3, 5]
    assert trips[0]['held_sec'] == 600


def test_the_algos_z_is_matched_to_its_position_at_both_ends():
    events = [
        {'kind': 'algo_signal', 'at': 100.0, 'pair_key': 'A|B',
         'detail': {'action': 'ENTER', 'z': 1.49}},
        {'kind': 'algo_order', 'at': 101.0, 'pair_key': 'A|B',
         'detail': {'action': 'ENTER', 'ok': True, 'position_id': 'P9'}},
        {'kind': 'algo_signal', 'at': 900.0, 'pair_key': 'A|B',
         'detail': {'action': 'EXIT', 'z': -1.58, 'position_id': 'P9'}},
    ]
    entry, exit_ = analysis.algo_z(events)
    assert entry == {'P9': 1.49} and exit_ == {'P9': -1.58}


def test_an_entry_order_long_after_the_signal_is_not_matched_to_it():
    events = [
        {'kind': 'algo_signal', 'at': 100.0, 'pair_key': 'A|B',
         'detail': {'action': 'ENTER', 'z': 1.49}},
        {'kind': 'algo_order', 'at': 500.0, 'pair_key': 'A|B',
         'detail': {'action': 'ENTER', 'ok': True, 'position_id': 'P9'}},
    ]
    assert analysis.algo_z(events)[0] == {}


# -- the z-score excursions --------------------------------------------------


def test_a_spread_riding_the_band_is_one_touch_not_a_hundred():
    ex = analysis.ZExcursions(now=0.0)
    for i in range(50):
        ex.tally(2.3, float(i))
    assert ex.counts['touch_2_up'] == 1


def test_the_CONTROL_back_inside_one_sigma_it_counts_again():
    ex = analysis.ZExcursions(now=0.0)
    for z in (2.3, 0.5, 2.1):
        ex.tally(z, 0.0)
    assert ex.counts['touch_2_up'] == 2


def test_a_two_sigma_stretch_that_comes_back_through_zero_is_a_reversion():
    ex = analysis.ZExcursions(now=0.0)
    for z in (1.0, 2.2, 3.1, 1.5, 0.2, -0.1):
        ex.tally(z, 0.0)
    assert ex.counts == {'touch_2_up': 1, 'touch_2_down': 0,
                         'touch_3_up': 1, 'touch_3_down': 0, 'reversions': 1}
    assert ex.max_z == 3.1
    # A small wobble through zero, never stretched, is not one.
    ex2 = analysis.ZExcursions(now=0.0)
    for z in (0.5, -0.5, 0.5):
        ex2.tally(z, 0.0)
    assert ex2.counts['reversions'] == 0


# -- the what-if-held shadow -------------------------------------------------


class Pos:
    position_id = 'P1'
    pair_key = 'A|B'
    side = SpreadSide.SELL
    entry_spread = 13.196
    spread_units = 50.0
    close_reason = 'Algo: stop loss'
    realized_pnl = -9.5


def test_a_shadow_marks_the_closed_trade_on_its_own_entry():
    w = analysis.arm_shadow(Pos(), fees=0.0, target_net=3.83, now=0.0)
    assert analysis.update_shadow(w, 13.30, 60.0)            # still losing
    assert not w['reverted_be']
    analysis.update_shadow(w, 13.19, 600.0)                  # back past BE
    assert w['reverted_be'] and w['be_min'] == 10.0
    analysis.update_shadow(w, 13.10, 900.0)                  # +4.80: target
    assert w['reverted_target'] and w['peak_net'] == pytest.approx(4.8)
    analysis.update_shadow(w, None, 3600.0)
    assert w['done']
    s = analysis.shadow_summary([w])
    assert s['revert_target_rate'] == 100.0 and s['active'] == 0


def test_a_clean_target_is_not_shadowed_and_the_CONTROL_a_stop_is():
    clean = Pos()
    clean.close_reason = 'Algo: profit target (after costs)'
    assert analysis.arm_shadow(clean, 0.0, None, 0.0) is None
    assert analysis.arm_shadow(Pos(), 0.0, None, 0.0) is not None


# -- the engine feeds it -----------------------------------------------------


def engine(config, legs, tmp_path):
    store = Store(str(tmp_path / 'trader.db'))
    clock = Clock()
    coordinator = Coordinator(config, legs, sleep=lambda s: None,
                              clock=clock, store=store)
    coordinator.start()
    coordinator.poll_once()
    return coordinator, store, clock


def opened(coordinator, pair, side=SpreadSide.BUY):
    md = coordinator.market[pair.key]
    position = coordinator.executor.market_entry(pair, side, md, 1.0).position
    coordinator.book.add_position(position)
    return position


def move_b(legs, pair, by):
    symbol = legs['acct_b'].broker.symbols[pair.symbol_b]
    symbol.quote(symbol.bid + by, symbol.ask + by)


def test_the_engine_keeps_each_open_trades_best_and_worst(config, pair, legs,
                                                         tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    position = opened(coordinator, pair)                 # long the spread
    coordinator.poll_once()
    move_b(legs, pair, +2.0)                             # B up: it gains
    clock.now += 60
    coordinator.poll_once()
    move_b(legs, pair, -4.0)
    clock.now += 60
    coordinator.poll_once()
    assert position.peak_pnl > 0 > position.trough_pnl
    assert position.peak_min == 1.0 and position.trough_min == 2.0
    # Saved, so a restart and the tab both have them.
    [row] = store.open_positions()
    assert row['trough_pnl'] == position.trough_pnl


def test_a_close_that_is_not_the_target_arms_a_shadow(config, pair, legs,
                                                       tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    position = opened(coordinator, pair)
    coordinator.poll_once()
    coordinator.executor.close_position(pair, position,
                                        coordinator.market[pair.key],
                                        reason='manual')
    clock.now += 5
    coordinator.poll_once()
    [watch] = store.shadows()
    assert watch['position_id'] == position.position_id
    assert watch['done'] is False


def test_the_CONTROL_a_clean_target_arms_none(config, pair, legs, tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    position = opened(coordinator, pair)
    coordinator.poll_once()
    coordinator.executor.close_position(
        pair, position, coordinator.market[pair.key],
        reason='Algo: profit target (after costs)')
    coordinator.poll_once()
    assert store.shadows() == []


def test_excursions_are_counted_on_the_algos_band_and_reset(config, pair, legs,
                                                            tmp_path):
    coordinator, store, clock = engine(config, legs, tmp_path)
    md = dict(coordinator.market[pair.key])
    mean = md['mid_spread']
    coordinator._track_excursions(pair, md, {'mean': mean - 0.25,
                                             'sigma': 0.1}, clock.now)
    saved = store.excursions()[pair.key]
    assert saved['counts']['touch_2_up'] == 1
    # No band (the Algo off): nothing is counted.
    coordinator._track_excursions(pair, md, None, clock.now)
    coordinator.reset_excursions(pair.key)
    assert store.excursions()[pair.key]['counts']['touch_2_up'] == 0


def test_the_analysis_cannot_reach_an_order():
    path = analysis.__file__
    tree = ast.parse(open(path, encoding='utf-8').read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add((node.module or '') + ':' + ','.join(
                a.name for a in node.names))
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert imported <= {'math'}, imported


def test_the_journal_splits_each_trip_into_gross_and_fees():
    row = closed(4.0, 1, entry_spread=13.20, exit_spread=13.10,
                 spread_units=50.0)                    # SELL: +0.10 x 50
    [trip] = analysis.journal([row])
    assert trip['gross_pnl'] == 5.0 and trip['fees'] == 1.0
    # Unmeasured stays unmeasured, not zero.
    [trip] = analysis.journal([closed(4.0, 1)])
    assert trip['gross_pnl'] is None and trip['fees'] is None

"""Signals: how many, and would they have paid.

The fault these are written against: a spread that pokes the band
twice without coming back was being counted as two signals, which
inflates every number on the page and makes a quiet day look busy.
"""

import pytest

from mt5trader import algo as algo_module, signals


PARAMS = dict(algo_module.DEFAULT_PARAMS)      # entry_z 2.5, back 0.5, 50%


# -- the arming discipline ---------------------------------------------------

# -- the Algo's own arm-then-enter machine -----------------------------------

def test_the_window_arithmetic_agrees_with_the_algos_own():
    """Kept in both places; they must never drift."""
    for entry_z, back, pct in ((2.5, 0.5, 50.0), (3.0, 0.75, 40.0),
                               (2.0, 0.2, 100.0)):
        params = dict(PARAMS, entry_z=entry_z, reentry_back=back,
                      reentry_window_pct=pct)
        assert signals.reentry_window(entry_z, back, pct) == \
            algo_module.reentry_window(params)


def test_a_second_poke_at_the_band_is_not_a_second_signal():
    """2.6 -> 2.55 -> 2.9 has stretched ONCE: it never came back
    inside, so there is nothing new to act on."""
    arming = signals.AlgoArming()
    fired = [arming.observe(z, PARAMS)[0]
             for z in (2.6, 2.55, 2.9, 2.7, 3.1)]
    assert fired == ['SELL', None, None, None, None]


def test_control_a_stretch_that_came_back_arms_again():
    arming = signals.AlgoArming()
    fired = [arming.observe(z, PARAMS)[0] for z in (2.6, 0.5, 2.7)]
    assert fired == ['SELL', None, 'SELL']


def test_the_arming_survives_being_written_down_and_read_back():
    """Arming that lives only in memory re-arms on every restart."""
    arming = signals.AlgoArming()
    assert arming.observe(2.6, PARAMS)[0] == 'SELL'
    for _ in range(4):
        arming = signals.AlgoArming(arming.to_dict())      # a restart
        assert arming.observe(2.6, PARAMS)[0] is None


def test_an_unmeasured_z_arms_nothing_and_disarms_nothing():
    arming = signals.AlgoArming()
    assert arming.observe(2.6, PARAMS)[0] == 'SELL'
    for bad in (None, float('nan'), float('inf')):
        assert arming.observe(bad, PARAMS) == (None, None)
    assert arming.observe(2.6, PARAMS)[0] is None          # still armed


def test_the_algo_arms_on_the_stretch_and_enters_on_the_way_back():
    """Defaults: arms at 2.5, enters between 1.0 and 2.0."""
    arming = signals.AlgoArming()
    assert arming.observe(2.6, PARAMS) == ('SELL', None)      # armed
    assert arming.observe(2.9, PARAMS) == (None, None)        # still out
    assert arming.observe(2.2, PARAMS) == (None, None)        # above window
    assert arming.observe(1.8, PARAMS) == (None, 'SELL')      # ENTERS


def test_a_trend_that_rides_the_band_arms_and_never_enters():
    """Which is the Algo declining to chase it — worth seeing, not a
    failure."""
    arming = signals.AlgoArming()
    armed, entered = arming.observe(2.6, PARAMS)
    assert (armed, entered) == ('SELL', None)
    for z in (2.8, 3.1, 3.4, 3.9, 4.2):
        assert arming.observe(z, PARAMS) == (None, None)


def test_a_spread_that_comes_back_too_far_too_fast_disarms():
    """Through the window's floor leaves nothing worth trading."""
    arming = signals.AlgoArming()
    assert arming.observe(2.6, PARAMS) == ('SELL', None)
    assert arming.observe(0.6, PARAMS) == (None, None)        # under the floor
    assert arming.observe(1.5, PARAMS) == (None, None)        # NOT an entry


def test_the_buy_side_mirrors_it():
    arming = signals.AlgoArming()
    assert arming.observe(-2.6, PARAMS) == ('BUY', None)
    assert arming.observe(-1.8, PARAMS) == (None, 'BUY')


def test_with_re_entry_off_the_stretch_itself_is_the_entry():
    params = dict(PARAMS, reentry_on=False)
    arming = signals.AlgoArming()
    assert arming.observe(2.6, params) == ('SELL', 'SELL')
    assert arming.observe(2.7, params) == (None, None)        # not again


# -- the hypothetical trade --------------------------------------------------

def armed_then_entered(side, tp, sl, units=50.0, be=13.0):
    sig = signals.new_signal('A|B', signals.ALGO, side, 0.0,
                             2.6 if side == 'SELL' else -2.6, PARAMS)
    return signals.enter(sig, 0.0, 2.6, {'entry': be, 'break_even': be,
                                         'tp': tp, 'sl': sl}, units)


def test_a_sell_takes_profit_when_the_spread_falls_to_its_target():
    sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
    signals.watch(sig, 12.9, 2.0, 60.0, 86400)
    assert sig['state'] == signals.OPEN
    signals.watch(sig, 12.4, 0.2, 600.0, 86400)
    assert sig['outcome'] == signals.TP
    assert sig['held_min'] == 10.0


def test_a_buy_takes_profit_in_the_other_direction():
    sig = armed_then_entered('BUY', tp=13.5, sl=12.6)
    signals.watch(sig, 13.6, -0.1, 300.0, 86400)
    assert sig['outcome'] == signals.TP


def test_the_stop_is_checked_before_the_target():
    """Inside one poll both can look reached. Booking the happier of
    the two is how a simulation flatters itself."""
    sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
    signals.watch(sig, 13.5, 3.0, 60.0, 86400)     # past the stop
    assert sig['outcome'] == signals.SL
    # CONTROL: the same move short of the stop takes the target.
    other = armed_then_entered('SELL', tp=12.5, sl=13.4)
    signals.watch(other, 12.4, 0.1, 60.0, 86400)
    assert other['outcome'] == signals.TP


def test_coming_back_to_the_mean_is_recorded_beside_the_outcome():
    """A trade can revert without paying, and pay without reverting.
    Two questions, two fields."""
    sig = armed_then_entered('SELL', tp=11.0, sl=14.0)    # a far target
    signals.watch(sig, 12.95, 0.4, 60.0, 86400)
    assert sig['reverted'] is False
    signals.watch(sig, 12.9, -0.1, 120.0, 86400)          # through the mean
    assert sig['reverted'] is True and sig['revert_min'] == 2.0
    assert sig['outcome'] is None                          # still open
    signals.watch(sig, 12.9, -0.1, 90000.0, 86400)
    assert sig['outcome'] == signals.EXPIRED and sig['reverted'] is True


def test_a_signal_that_never_entered_is_recorded_as_such():
    sig = signals.new_signal('A|B', signals.ALGO, 'SELL', 0.0, 2.6, PARAMS)
    signals.abandon(sig, 300.0)
    assert sig['state'] == signals.CLOSED
    assert sig['outcome'] == signals.NO_ENTRY
    assert sig['entered_at'] is None


def test_the_best_and_worst_point_are_both_kept():
    sig = armed_then_entered('SELL', tp=11.0, sl=15.0)
    for spread, at in ((13.2, 60.0), (12.6, 120.0), (13.1, 180.0)):
        signals.watch(sig, spread, 1.0, at, 86400)
    assert sig['trough_net'] == -10.0      # 13.2 against a 13.0 break-even
    assert sig['peak_net'] == 20.0         # 12.6, sold


def test_an_unpriced_signal_is_watched_without_inventing_a_number():
    """No break-even or no k means no net — never a zero."""
    sig = signals.new_signal('A|B', signals.ALGO, 'SELL', 0.0, 2.6, PARAMS)
    signals.enter(sig, 0.0, 2.6, {'entry': 13.0, 'break_even': None,
                                  'tp': None, 'sl': None}, None)
    signals.watch(sig, 12.0, 0.1, 600.0, 86400)
    assert signals.net_at(sig, 12.0) is None
    assert sig['peak_net'] is None
    assert sig['outcome'] is None          # nothing to reach


# -- the counts above the table ----------------------------------------------

def test_the_tally_counts_armed_entered_and_how_they_ended():
    rows = []
    for outcome, net in ((signals.TP, 30.0), (signals.TP, 18.0),
                         (signals.SL, -44.0), (signals.EXPIRED, -3.0)):
        sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
        sig['state'], sig['outcome'] = signals.CLOSED, outcome
        sig['net_pnl'] = net
        sig['reverted'] = outcome != signals.SL
        rows.append(sig)
    never = signals.new_signal('A|B', signals.ALGO, 'SELL', 0.0, 2.6,
                               PARAMS)
    signals.abandon(never, 10.0)
    rows.append(never)
    body = signals.tally(rows)
    assert body['armed'] == 5 and body['entered'] == 4
    assert body['no_entry'] == 1 and body['settled'] == 4
    # Won and lost are by MONEY: the expired trade closed at -$3.
    assert body['tp'] == 2 and body['sl'] == 2 and body['expired'] == 1
    assert body['win_rate'] == 50.0
    assert body['reverted'] == 3 and body['revert_rate'] == 75.0


def test_nothing_settled_is_a_dash_and_never_a_zero_per_cent():
    """A win rate of 0% on no trades is a statement nobody measured."""
    sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
    body = signals.tally([sig])
    assert body['entered'] == 1 and body['settled'] == 0
    assert body['win_rate'] is None and body['revert_rate'] is None


def test_nothing_at_all_still_reports_every_figure():
    body = signals.tally([])
    assert body['armed'] == 0 and body['entered'] == 0
    assert body['win_rate'] is None and body['revert_rate'] is None


# -- derived from history ----------------------------------------------------

def replay_trade(**kw):
    return dict({'side': 'SELL', 'opened_at': 1000.0, 'entry': 13.2,
                 'entry_z': 2.61, 'closed_at': 4600.0, 'exit': 12.7,
                 'reason': 'PROFIT_TARGET', 'pnl': 24.5}, **kw)


def test_a_replayed_trade_becomes_a_signal_that_says_it_is_derived():
    row = signals.from_replay(replay_trade(), 'A|B', PARAMS,
                              broker_day='2026-11-05', units=50.0)
    assert row['source'] == signals.REPLAY
    assert row['basis'] == signals.ALGO
    assert row['broker_day'] == '2026-11-05'
    assert row['state'] == signals.CLOSED
    assert row['outcome'] == signals.TP
    assert row['net_pnl'] == 24.5
    assert row['held_min'] == 60.0
    assert row['armed_z'] == 2.61 and row['entered_z'] == 2.61


def test_only_the_target_and_the_stop_say_a_level_was_reached():
    """The Algo's other exits closed the trade for another reason
    entirely; calling them a take-profit would overstate the page."""
    assert signals.from_replay(replay_trade(reason='PROFIT_TARGET'),
                               'A|B', PARAMS)['outcome'] == signals.TP
    assert signals.from_replay(replay_trade(reason='STOP_LOSS'),
                               'A|B', PARAMS)['outcome'] == signals.SL
    for other in ('Z_STOP', 'MEAN_REVERSION', 'TIME_STOP', 'session cutoff'):
        assert signals.from_replay(replay_trade(reason=other), 'A|B',
                                   PARAMS)['outcome'] == signals.EXPIRED


def test_a_replay_that_ran_out_of_candles_is_open_not_a_result():
    row = signals.from_replay(
        replay_trade(closed_at=None, pnl=None, reason='still open'),
        'A|B', PARAMS)
    assert row['state'] == signals.OPEN
    assert row['outcome'] is None and row['net_pnl'] is None


def test_won_and_lost_are_measured_in_money_not_by_the_level_touched():
    """An exit on the mean reversion in profit is a win however it got
    out. Counting only PROFIT_TARGET would under-report exactly the
    trades the Algo's own exits exist to take."""
    rows = [signals.from_replay(replay_trade(reason=r, pnl=p), 'A|B', PARAMS)
            for r, p in (('PROFIT_TARGET', 30.0), ('MEAN_REVERSION', 12.0),
                         ('STOP_LOSS', -40.0), ('TIME_STOP', -5.0))]
    body = signals.tally(rows)
    assert body['tp'] == 2 and body['sl'] == 2
    assert body['win_rate'] == 50.0
    assert body['net_pnl'] == -3.0


def test_a_trade_whose_pnl_was_never_measured_is_in_neither_count():
    rows = [signals.from_replay(replay_trade(pnl=None), 'A|B', PARAMS),
            signals.from_replay(replay_trade(pnl=10.0), 'A|B', PARAMS)]
    body = signals.tally(rows)
    assert body['settled'] == 2 and body['priced'] == 1
    assert body['tp'] == 1 and body['sl'] == 0
    assert body['win_rate'] == 100.0


def test_the_tally_says_how_many_rows_were_measured_and_how_many_derived():
    live = armed_then_entered('SELL', tp=12.5, sl=13.4)
    live['state'], live['outcome'], live['net_pnl'] = 'CLOSED', 'TP', 20.0
    rows = [live, signals.from_replay(replay_trade(), 'A|B', PARAMS)]
    body = signals.tally(rows)
    assert body['live'] == 1 and body['replayed'] == 1


def test_a_live_close_records_what_it_would_have_made():
    sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
    signals.watch(sig, 12.4, 0.1, 600.0, 86400)
    assert sig['outcome'] == signals.TP
    # break-even 13.0, sold, closed at 12.4, k of 50
    assert sig['net_pnl'] == 30.0


# -- the signal journal ------------------------------------------------------

def a_signal(**kw):
    row = {'pair_key': 'A|B', 'side': 'SELL', 'source': signals.LIVE,
           'state': signals.CLOSED, 'outcome': signals.SL,
           'armed_at': 100.0, 'armed_z': 2.61, 'entered_at': 110.0,
           'closed_at': 200.0,
           'closed_z': 3.12, 'entry': 71.0, 'exit_spread': 71.46,
           'break_even': 71.20, 'tp': 70.55, 'sl': 71.40,
           'net_pnl': -205.0, 'reverted': False}
    row.update(kw)
    return row


def test_the_journal_runs_a_total_in_the_order_things_closed():
    rows = [a_signal(closed_at=300.0, net_pnl=50.0),
            a_signal(closed_at=100.0, net_pnl=-20.0),
            a_signal(closed_at=200.0, net_pnl=10.0)]
    out = signals.journal(rows)
    # Newest first on the screen, but the total runs in CLOSING order.
    assert [r['closed_at'] for r in out] == [300.0, 200.0, 100.0]
    assert [r['cum_pnl'] for r in out] == [40.0, -10.0, -20.0]


def test_a_refusal_carries_no_money_and_does_not_move_the_total():
    """A signal the Algo declined is not a loss, and a page that let it
    move the running total would report money nobody could have lost."""
    rows = [a_signal(closed_at=100.0, net_pnl=-20.0),
            a_signal(closed_at=150.0, outcome=signals.BLOCKED, net_pnl=None,
                     block_reason='edge filter: capture is 0.02x',
                     edge_ratio=0.02, edge_required=1.5)]
    out = signals.journal(rows)
    assert [r['cum_pnl'] for r in out] == [-20.0, -20.0]


def test_the_levels_are_columns_on_the_row_itself():
    """They were a prose line under the row first. The signal already
    carries its three levels, so the journal prints them as columns
    and the WHY column keeps only what is not a number."""
    [row] = signals.journal([a_signal()])
    assert row['break_even'] == 71.20
    assert row['tp'] == 70.55 and row['sl'] == 71.40
    assert row['why'] == 'never came back to the mean'


def test_a_signal_that_came_back_says_when():
    [row] = signals.journal([a_signal(reverted=True, revert_min=22.0)])
    assert row['why'] == 'came back to the mean after 22.0 min'


def test_a_refusal_says_the_filter_and_the_reading_behind_it():
    [row] = signals.journal([a_signal(
        outcome=signals.BLOCKED, net_pnl=None, entered_at=None,
        block_reason='edge filter: capture is 0.02x the round trip',
        edge_ratio=0.02, edge_required=1.5)])
    assert 'edge filter' in row['why']
    assert 'edge 0.02x of 1.50x' in row['why']


def test_a_stretch_that_never_came_back_names_the_window():
    [row] = signals.journal([a_signal(
        outcome=signals.NO_ENTRY, net_pnl=None, entered_at=None,
        entry_z=2.0, reentry_back=0.5)])
    assert 'never back inside the window' in row['why']
    assert 'enters 2.00' in row['why'] and 'back 0.50' in row['why']


def test_a_signal_still_running_has_nothing_to_explain():
    """CONTROL: the WHY column is for how it ENDED. One that has not
    ended yet is a dash, not a sentence invented for it."""
    [row] = signals.journal([a_signal(state=signals.OPEN, outcome=None,
                                      entered_at=None, net_pnl=None)])
    assert row['why'] is None


def test_a_close_records_the_spread_it_came_off_at():
    """The journal prints 'spread in -> out'. Without this the out half
    was a dash on every row."""
    signal = signals.new_signal('A|B', signals.ALGO, 'SELL', 10.0, 2.6,
                                {'entry_z': 2.0})
    signals.enter(signal, 11.0, 2.6, {'entry': 71.0, 'break_even': 70.9,
                                      'tp': 70.0, 'sl': 71.5}, 10.0,
                  {'leg_a_side': 'BUY', 'leg_a_entry': 101.118,
                   'leg_b_side': 'SELL', 'leg_b_entry': 115.554}, 1.0)
    assert signal['leg_a_entry'] == 101.118 and signal['quantity'] == 1.0
    signals.watch(signal, 71.6, 3.1, 12.0, 0)
    assert signal['outcome'] == signals.SL
    assert signal['exit_spread'] == 71.6


# -- how often the z was triggered -------------------------------------------

def test_the_z_count_is_split_by_side_and_names_its_threshold():
    """'How many times did it trigger' is the question the page is for.
    Above and below are different trades, so they are counted apart."""
    rows = [a_signal(armed_z=2.61, entry_z=2.5, side='SELL'),
            a_signal(armed_z=3.12, entry_z=2.5, side='SELL'),
            a_signal(armed_z=-2.63, entry_z=2.5, side='BUY')]
    t = signals.tally(rows)
    assert t['triggered'] == 3
    assert t['triggered_up'] == 2 and t['triggered_down'] == 1
    assert t['threshold_z'] == 2.5
    assert t['peak_z'] == 3.12 and t['trough_z'] == -2.63


def test_a_refusal_and_a_stretch_that_never_entered_still_triggered():
    """The z reached the band: that is the trigger. What the Algo did
    about it afterwards is a different column, and leaving these out
    would under-count exactly the stretches worth knowing about."""
    rows = [a_signal(armed_z=2.61, entry_z=2.5, outcome=signals.BLOCKED,
                     net_pnl=None, entered_at=None),
            a_signal(armed_z=2.70, entry_z=2.5, outcome=signals.NO_ENTRY,
                     net_pnl=None, entered_at=None)]
    t = signals.tally(rows)
    assert t['triggered'] == 2 and t['entered'] == 0


def test_two_ladders_that_disagree_quote_no_threshold():
    """Printing either one would be wrong on half the rows."""
    rows = [a_signal(armed_z=2.61, entry_z=2.5),
            a_signal(armed_z=2.10, entry_z=2.0)]
    assert signals.tally(rows)['threshold_z'] is None
    # The COUNT still works: it is the threshold that cannot be named.
    assert signals.tally(rows)['triggered'] == 2


def test_nothing_triggered_is_no_furthest_z_rather_than_zero():
    t = signals.tally([])
    assert t['triggered'] == 0
    assert t['peak_z'] is None and t['trough_z'] is None


def test_a_second_poke_at_the_band_is_not_a_second_trigger():
    """The whole reason the arming machine exists, now visible as a
    count: one stretch out and back is ONE trigger, however many times
    z pushes at the band on the way."""
    arming = signals.AlgoArming()
    params = {'entry_z': 2.5, 'reentry_back': 0.5, 'max_entry_z': 0}
    rows = []
    for z in (2.6, 2.1, 2.7, 2.55, 3.0):
        armed, _entered = arming.observe(z, params)
        if armed:
            rows.append(a_signal(armed_z=z, entry_z=2.5, side='SELL'))
    assert signals.tally(rows)['triggered'] == 1
    # CONTROL: back through the floor, then out again, IS a second one.
    arming.observe(0.2, params)
    armed, _entered = arming.observe(2.8, params)
    assert armed == 'SELL'
    rows.append(a_signal(armed_z=2.8, entry_z=2.5, side='SELL'))
    assert signals.tally(rows)['triggered'] == 2


def test_a_page_of_refusals_has_no_running_total_rather_than_zero():
    """UNMEASURED IS NOT ZERO. Three signals, none of them entered:
    $0.00 down the Running column reads as 'it broke even', which is a
    measurement nobody made. Seen live on a desk whose edge filter
    refused everything."""
    rows = [a_signal(outcome=signals.BLOCKED, net_pnl=None,
                     entered_at=None, closed_at=100.0),
            a_signal(outcome=signals.NO_ENTRY, net_pnl=None,
                     entered_at=None, closed_at=200.0)]
    assert [r['cum_pnl'] for r in signals.journal(rows)] == [None, None]


def test_the_total_is_real_from_the_first_priced_row_on():
    """CONTROL. Once something has been priced the total is a
    measurement, and a later unpriced row leaves it where it was
    rather than blanking it again."""
    rows = [a_signal(net_pnl=None, outcome=signals.BLOCKED,
                     entered_at=None, closed_at=100.0),
            a_signal(net_pnl=-205.0, closed_at=200.0),
            a_signal(net_pnl=None, outcome=signals.BLOCKED,
                     entered_at=None, closed_at=300.0)]
    # Newest first on the screen.
    assert [r['cum_pnl'] for r in signals.journal(rows)] == \
        [-205.0, -205.0, None]

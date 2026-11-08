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
    for outcome in (signals.TP, signals.TP, signals.SL, signals.EXPIRED):
        sig = armed_then_entered('SELL', tp=12.5, sl=13.4)
        sig['state'], sig['outcome'] = signals.CLOSED, outcome
        sig['reverted'] = outcome != signals.SL
        rows.append(sig)
    never = signals.new_signal('A|B', signals.ALGO, 'SELL', 0.0, 2.6,
                               PARAMS)
    signals.abandon(never, 10.0)
    rows.append(never)
    body = signals.tally(rows)
    assert body['armed'] == 5 and body['entered'] == 4
    assert body['no_entry'] == 1 and body['settled'] == 4
    assert body['tp'] == 2 and body['sl'] == 1 and body['expired'] == 1
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

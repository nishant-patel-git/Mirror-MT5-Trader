"""TP and SL for a position that is not one spread, and the margin they
are a % of, frozen when the position is first priced.

The fault: a position's `spread_units` is its WHOLE size (0.05 lots x
1,000 = 50 for half a spread of 0.1 lots), and the TP/SL maths divided
by the quantity again. At 0.5 spreads the levels sat twice as far away
as the % asked for; at 3 spreads, a third as far. (The fake future's
minimum is 0.1 lots, so these tests use 2 and 3 spreads.)
"""

import pytest

from mt5trader import takeprofit
from mt5trader.coordinator import Coordinator
from mt5trader.database import Store
from mt5trader.models import SpreadPosition, SpreadSide

SETTINGS = {'TP_TARGET_PCT_OF_MARGIN': 2.0}
MARGIN = 500.0                       # per ONE spread


def engine(config, legs, store=None):
    legs['acct_a'].broker.margin_per_lot = 3000.0
    legs['acct_b'].broker.margin_per_lot = 2000.0       # 0.1 lots: 300+200
    coordinator = Coordinator(config, legs, sleep=lambda s: None, store=store)
    coordinator.start()
    coordinator.poll_once()
    return coordinator


def opened(coordinator, pair, quantity, side=SpreadSide.SELL):
    md = coordinator.market[pair.key]
    position = coordinator.executor.market_entry(pair, side, md,
                                                 quantity).position
    coordinator.book.add_position(position)
    return position


@pytest.mark.parametrize('quantity', [1.0, 2.0, 3.0])
def test_the_target_is_the_percent_of_margin_THIS_position_ties_up(
        config, pair, legs, quantity):
    """In money, the TP is TP% of the margin the position ties up —
    what a trader works out from MT5 by hand. 1.0 is the control: it
    was right before, and must stay right."""
    coordinator = engine(config, legs)
    position = opened(coordinator, pair, quantity)
    body = takeprofit.for_position(position, coordinator.market[pair.key],
                                   pair, SETTINGS, MARGIN)
    money_at_tp = body['target_points'] * position.spread_units
    assert money_at_tp == pytest.approx(0.02 * MARGIN * quantity)


@pytest.mark.parametrize('quantity', [1.0, 2.0, 3.0])
def test_the_algo_stop_is_the_percent_of_margin_THIS_position_ties_up(
        config, pair, legs, quantity):
    coordinator = engine(config, legs)
    pair.algo_params = {'stop_loss_on': True, 'stop_loss_pct': 5.0}
    position = opened(coordinator, pair, quantity)
    [row] = coordinator._algo_positions(pair, coordinator.market[pair.key])
    money_at_sl = (row['sl'] - row['break_even']) * position.spread_units
    assert money_at_sl == pytest.approx(0.05 * MARGIN * quantity)


def test_the_margin_is_frozen_when_the_position_is_first_priced(
        config, pair, legs):
    coordinator = engine(config, legs)
    position = opened(coordinator, pair, 2.0)
    md = coordinator.market[pair.key]
    [before] = coordinator._algo_positions(pair, md)
    assert position.entry_margin == pytest.approx(MARGIN)

    # The terminals' margin moves with the price...
    legs['acct_a'].broker.margin_per_lot = 6000.0
    coordinator._margin_cache.clear()
    [after] = coordinator._algo_positions(pair, md)
    # ...the open position's TP and SL do not.
    assert after['tp'] == pytest.approx(before['tp'])
    assert after['sl'] == pytest.approx(before['sl'])

    # CONTROL: a position opened now is priced on the margin now.
    later = opened(coordinator, pair, 2.0)
    coordinator._algo_positions(pair, md)
    assert later.entry_margin == pytest.approx(800.0)        # 600 + 200


def test_the_frozen_margin_survives_a_restart(config, pair, legs, tmp_path):
    store = Store(str(tmp_path / 'trader.db'))
    coordinator = engine(config, legs, store=store)
    position = opened(coordinator, pair, 2.0)
    coordinator._algo_positions(pair, coordinator.market[pair.key])

    [row] = store.open_positions()
    again = SpreadPosition.from_dict(row)
    assert again.entry_margin == pytest.approx(MARGIN)
    assert again.position_id == position.position_id


def test_a_zero_margin_is_never_frozen(config, pair, legs):
    """A terminal answering 0 has not priced it: nothing is frozen, so
    the first real figure is the one kept — never a 0 that would put
    the TP and SL at break-even."""
    coordinator = engine(config, legs)
    position = opened(coordinator, pair, 2.0)
    legs['acct_a'].broker.margin_per_lot = 0.0
    legs['acct_b'].broker.margin_per_lot = 0.0
    coordinator._margin_cache.clear()
    assert coordinator.entry_margin(pair, position) is None
    assert position.entry_margin is None
    # CONTROL: once the terminals price it, that is what is frozen.
    legs['acct_a'].broker.margin_per_lot = 3000.0
    legs['acct_b'].broker.margin_per_lot = 2000.0
    coordinator._margin_cache.clear()
    assert coordinator.entry_margin(pair, position) == pytest.approx(MARGIN)

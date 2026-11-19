"""An order from the phone may only REST.

The Telegram desk places working orders when nobody is at the screen. A
level at or through the market would fill at once, unseen, so the
engine itself refuses it - on the price it holds when the order lands,
not on the copy the phone read seconds earlier. Every refusal has a
control that sends the same order without the flag.
"""

import pytest

from mt5trader.coordinator import Coordinator
from mt5trader.models import OrderType


@pytest.fixture
def engine(config, pair, legs):
    co = Coordinator(config, legs, sleep=lambda s: None)
    co.start()
    co.poll_once()
    return co


def market(engine, pair):
    md = engine.market[pair.key]
    return md['short_spread'], md['long_spread']


def test_a_remote_buy_at_or_through_the_offer_is_refused(engine, pair):
    bid, offer = market(engine, pair)
    for level in (offer, offer + 0.5):
        answer = engine.click(pair.key, 'BUY', level, 1, rest_only=True)
        assert answer['refused'] and 'would cross the market' in \
            answer['reason'], answer
        assert f'{offer:g}' in answer['reason']     # the market, in words
    assert engine.book.orders(pair.key) == []


def test_a_remote_sell_at_or_through_the_bid_is_refused(engine, pair):
    bid, offer = market(engine, pair)
    answer = engine.click(pair.key, 'SELL', bid, 1, rest_only=True)
    assert answer['refused'] and 'would cross' in answer['reason']
    assert engine.book.orders(pair.key) == []


def test_CONTROL_a_remote_order_away_from_the_market_rests(engine, pair):
    bid, offer = market(engine, pair)
    buy = engine.click(pair.key, 'BUY', round(bid - 0.5, 2), 1,
                       rest_only=True)
    assert buy['ok'], buy
    engine.book.cancel_where(pair.key)        # one at a time: clear it
    sell = engine.click(pair.key, 'SELL', round(offer + 0.5, 2), 1,
                        rest_only=True)
    assert sell['ok'], sell
    assert [o.side.value for o in engine.book.orders(pair.key)] == ['SELL']


def test_CONTROL_the_same_crossing_click_from_the_screen_is_not_refused(
        engine, pair):
    """The flag is what refuses it: the ladder's own click is unchanged."""
    bid, offer = market(engine, pair)
    answer = engine.click(pair.key, 'BUY', offer, 1)
    assert 'would cross the market' not in (answer.get('reason') or '')


def test_no_price_is_no_remote_order(engine, pair):
    engine.market[pair.key] = None
    answer = engine.click(pair.key, 'BUY', 1.0, 1, rest_only=True)
    assert answer['refused'] and 'no live price' in answer['reason']


def test_the_command_carries_the_flag_through(engine, pair, tmp_path):
    from mt5trader.commands import CommandRunner
    runner = CommandRunner.__new__(CommandRunner)
    runner.coordinator = engine
    bid, offer = market(engine, pair)
    answer = runner._do_click({'pair': pair.key, 'side': 'BUY',
                               'level': offer, 'quantity': 1,
                               'rest_only': True})
    assert answer['refused']


# -- one position at a time, from the phone -----------------------------------

def away(engine, pair, side):
    bid, offer = market(engine, pair)
    return round(bid - 0.5, 2) if side == 'BUY' else round(offer + 0.5, 2)


def open_position(engine, pair, side, quantity):
    """A position put on at the SCREEN, at market."""
    bid, offer = market(engine, pair)
    pair.order_type = OrderType.MARKET
    answer = engine.click(pair.key, side, offer if side == 'BUY' else bid,
                          quantity)
    pair.order_type = OrderType.LIMIT
    assert answer.get('ok'), answer
    [position] = engine.book.positions(pair.key)
    assert position.side.value == side and position.quantity == quantity
    return position


def test_flat_a_second_remote_opening_order_is_refused(engine, pair):
    first = engine.click(pair.key, 'BUY', away(engine, pair, 'BUY'), 1,
                         rest_only=True)
    assert first['ok'], first
    for side in ('BUY', 'SELL'):
        answer = engine.click(pair.key, side, away(engine, pair, side), 1,
                              rest_only=True)
        assert answer['refused'] and 'one at a time' in answer['reason']
        assert 'BUY 1 at' in answer['reason']     # names the one working
    assert len(engine.book.orders(pair.key)) == 1


def test_CONTROL_the_screen_may_still_stack_working_orders(engine, pair):
    for side in ('BUY', 'SELL', 'BUY'):
        answer = engine.click(pair.key, side, away(engine, pair, side), 1)
        assert answer['ok'], answer
    assert len(engine.book.orders(pair.key)) == 3


def test_a_position_on_is_never_added_to_from_the_phone(engine, pair):
    open_position(engine, pair, 'BUY', 2)
    answer = engine.click(pair.key, 'BUY', away(engine, pair, 'BUY'), 1,
                          rest_only=True)
    assert answer['refused'] and 'never added to' in answer['reason']
    assert engine.book.orders(pair.key) == []


def test_CONTROL_a_close_up_to_the_position_rests_from_the_phone(engine,
                                                                 pair):
    position = open_position(engine, pair, 'BUY', 2)
    answer = engine.click(pair.key, 'SELL', away(engine, pair, 'SELL'), 2,
                          rest_only=True)
    assert answer['ok'], answer
    [order] = engine.book.orders(pair.key)
    assert order.position_id == position.position_id     # a CLOSE
    assert len(engine.book.positions(pair.key)) == 1     # nothing opened


def test_a_close_never_flips_the_position_from_the_phone(engine, pair):
    open_position(engine, pair, 'BUY', 2)
    answer = engine.click(pair.key, 'SELL', away(engine, pair, 'SELL'), 3,
                          rest_only=True)
    assert answer['refused'] and 'never opens the other way' in \
        answer['reason']
    assert 'at most the 2 left' in answer['reason']
    assert engine.book.orders(pair.key) == []


def test_closes_already_working_count_against_the_room_left(engine, pair):
    open_position(engine, pair, 'BUY', 2)
    sell = away(engine, pair, 'SELL')
    assert engine.click(pair.key, 'SELL', sell, 1, rest_only=True)['ok']
    over = engine.click(pair.key, 'SELL', sell + 0.1, 2, rest_only=True)
    assert over['refused'] and '1 already working' in over['reason']
    # Control: the one spread still uncovered may rest.
    rest = engine.click(pair.key, 'SELL', sell + 0.1, 1, rest_only=True)
    assert rest['ok'], rest
    assert len(engine.book.orders(pair.key)) == 2


def test_part_lots_from_the_phone_count_to_the_hundredth(engine, pair):
    """1.5 typed on the phone against a 2: the room left is 0.5, counted
    exactly - a 1 is refused and the reason names the 0.5."""
    open_position(engine, pair, 'BUY', 2)
    sell = away(engine, pair, 'SELL')
    assert engine.click(pair.key, 'SELL', sell, 1.5, rest_only=True)['ok']
    over = engine.click(pair.key, 'SELL', sell + 0.1, 1, rest_only=True)
    assert over['refused'] and 'at most the 0.5 left' in over['reason']
    assert [o.quantity for o in engine.book.orders(pair.key)] == [1.5]

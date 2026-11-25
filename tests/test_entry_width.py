"""The bid-ask a trade paid: the round turn of both legs' bid-ask
(long - short) frozen on a position the first time it is priced, and
saved. The Analysis tab's bid-ask cost is this times k. Display only."""

from mt5trader.coordinator import Coordinator
from mt5trader.models import OrderType, SpreadPosition


def _engine(config, legs):
    co = Coordinator(config, legs, sleep=lambda s: None)
    co.start()
    co.poll_once()
    return co


def _open(co, pair):
    md = co.market[pair.key]
    pair.order_type = OrderType.MARKET
    assert co.click(pair.key, 'SELL', md['short_spread'], 1)['ok']
    pair.order_type = OrderType.LIMIT
    [position] = co.book.positions(pair.key)
    return position


def test_the_width_at_entry_is_frozen_and_saved(config, pair, legs):
    co = _engine(config, legs)
    position = _open(co, pair)
    md = dict(co.market[pair.key])
    co.snapshot()
    width = md['long_spread'] - md['short_spread']
    assert position.entry_width is not None
    assert abs(position.entry_width - width) < 1e-9
    # Frozen: a wider market later does not move it.
    co.market[pair.key] = dict(md, long_spread=md['long_spread'] + 1.0)
    co.snapshot()
    assert abs(position.entry_width - width) < 1e-9
    # Saved with the position, and read back.
    again = SpreadPosition.from_dict(position.to_dict())
    assert again.entry_width == position.entry_width


def test_CONTROL_without_a_market_it_stays_unmeasured(config, pair, legs):
    co = _engine(config, legs)
    position = _open(co, pair)
    position.entry_width = None
    assert co.entry_width(position, None) is None
    assert co.entry_width(position, {'long_spread': None,
                                     'short_spread': 1.0}) is None
    assert position.entry_width is None


def test_the_database_keeps_it(config, pair, legs, tmp_path):
    from mt5trader.database import Store
    co = _engine(config, legs)
    position = _open(co, pair)
    position.entry_width = 0.04
    db = Store(str(tmp_path / 'w.db'))
    db.save_position(position)
    [row] = db.positions_between(None, None)
    assert row['entry_width'] == 0.04

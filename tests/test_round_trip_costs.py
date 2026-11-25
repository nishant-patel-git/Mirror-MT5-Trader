"""What each round trip cost, beside its P&L - for MT5: the bid-ask the
trade paid (already inside the gross), and the commission, swap and any
fee MT5 booked on the trade's own deals. No maker / taker on MT5."""

import pytest

from mt5trader import analysis


def _position(pid='P1', closed=2000.0, width=0.04, units=1000.0,
              tickets_a=('101',), tickets_b=('201',), pnl=50.0):
    leg = lambda tickets: {'side': 'BUY', 'volume': 1.0,
                           'contract_size': 1000.0, 'price': 80.0,
                           'exit_price': 80.1,
                           'position_tickets': list(tickets)}
    return {'position_id': pid, 'pair_key': 'OIL', 'side': 'SELL',
            'quantity': 1.0, 'opened_at': closed - 600.0,
            'closed_at': closed, 'entry_spread': 9.40, 'exit_spread': 9.30,
            'spread_units': units, 'realized_pnl': pnl,
            'entry_width': width, 'entry_margin': 1000.0,
            'leg_a': leg(tickets_a), 'leg_b': leg(tickets_b)}


def _deal(deal, ticket, commission=-3.0, swap=0.0, fee=0.0, account='A'):
    return {'deal_id': deal, 'account': account, 'position_ticket': ticket,
            'commission': commission, 'swap': swap, 'fee': fee}


def _journal(positions):
    return analysis.journal(positions)


def test_mt5s_own_charges_are_summed_from_the_trades_deals():
    positions = [_position()]
    journal = _journal(positions)
    fills = [_deal('d1', '101'), _deal('d2', '101', swap=-1.25),
             _deal('d3', '201', commission=-3.0, fee=-0.5),
             _deal('d4', '201', commission=-3.0),
             _deal('dX', '999', commission=-50.0)]     # another trade's
    tally = analysis.attach_costs(journal, positions, fills)
    [row] = journal
    assert row['costs_source'] == 'MT5' and row['deals'] == 4
    assert row['commission'] == pytest.approx(12.0)
    assert row['swap'] == pytest.approx(1.25)
    assert row['fee'] == pytest.approx(0.5)
    assert row['charges'] == pytest.approx(13.75)
    # Bid-ask: 0.04 of spread x k 1,000 = $40, ALREADY in the gross.
    assert row['bid_ask'] == pytest.approx(40.0)
    assert row['round_trip'] == pytest.approx(53.75)
    # Net P&L is NOT re-charged: the costs sit beside it.
    assert row['pnl'] == pytest.approx(50.0)
    assert tally['measured'] == 1 and tally['estimated'] == 0
    assert tally['round_trip'] == pytest.approx(53.75)


def test_a_deal_is_counted_once():
    positions = [_position(tickets_a=('101',), tickets_b=('101',))]
    journal = _journal(positions)
    analysis.attach_costs(journal, positions, [_deal('d1', '101')])
    assert journal[0]['deals'] == 1
    assert journal[0]['commission'] == pytest.approx(3.0)


def test_without_its_deals_a_trade_keeps_the_estimate_and_swap_unmeasured():
    positions = [_position()]
    journal = _journal(positions)
    journal[0]['fees'] = 6.0                # the desk's own: gross - net
    tally = analysis.attach_costs(journal, positions, [])
    [row] = journal
    assert row['costs_source'] == 'estimate'
    assert row['commission'] == 6.0
    assert row['swap'] is None and row['fee'] is None     # not 0
    assert tally['estimated'] == 1 and tally['measured'] == 0
    # Control: with its deals, MT5's figure replaces the estimate.
    journal = _journal(positions)
    journal[0]['fees'] = 6.0
    analysis.attach_costs(journal, positions, [_deal('d1', '101',
                                                     commission=-4.0)])
    assert journal[0]['costs_source'] == 'MT5'
    assert journal[0]['commission'] == pytest.approx(4.0)


def test_a_trade_priced_before_the_width_was_kept_has_no_bid_ask():
    positions = [_position(width=None)]
    journal = _journal(positions)
    tally = analysis.attach_costs(journal, positions, [_deal('d1', '101')])
    assert journal[0]['bid_ask'] is None and journal[0]['round_trip'] is None
    assert tally['bid_ask_trades'] == 0
    # Control: with the width kept, it is priced.
    positions = [_position(width=0.02)]
    journal = _journal(positions)
    analysis.attach_costs(journal, positions, [])
    assert journal[0]['bid_ask'] == pytest.approx(20.0)


def test_trade_numbers_are_over_the_whole_record():
    record = [_position('P1', 1000.0), _position('P2', 3000.0),
              _position('P3', 2000.0), dict(_position('P4'), closed_at=None)]
    numbers = analysis.trade_numbers(record)
    assert numbers == {'P1': 1, 'P3': 2, 'P2': 3}
    # A window holding only the latest still calls it #3.
    body = {'journal': _journal([record[1]]),
            'excursion': [{'position_id': 'P2'}]}
    analysis.number_trades(body, numbers)
    assert body['journal'][0]['trade_no'] == 3
    assert body['excursion'][0]['trade_no'] == 3


def test_an_excursion_carries_the_margin_it_is_read_on():
    row = analysis.excursion_row(dict(_position(), peak_pnl=80.0,
                                      trough_pnl=-30.0))
    assert row['margin'] == pytest.approx(1000.0)


def _app(tmp_path, settings):
    import json
    import time
    from mt5trader.webapp import create_app
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({
        'at': time.time(),
        'accounts': {'AC': {'equity': 60000.0}, 'FX': {'equity': 40000.0},
                     'OTHER': {'equity': 999999.0}},
        'pairs': {'OIL': {'account_a': 'AC', 'account_b': 'FX'}}}),
        encoding='utf-8')
    (tmp_path / 'config.json').write_text(json.dumps(
        {'accounts': {}, 'pairs': {}, 'settings': settings}),
        encoding='utf-8')
    return create_app(str(status), str(tmp_path / 'c.jsonl'),
                      str(tmp_path / 'r.json'),
                      str(tmp_path / 'config.json'),
                      db_path=str(tmp_path / 'trader.db'))


def test_nothing_typed_the_ladders_accounts_balance_is_the_capital(tmp_path):
    body = _app(tmp_path, {}).test_client().get('/api/analysis').get_json()
    # The accounts the LADDERS trade - not every account on the desk.
    assert body['summary']['capital'] == pytest.approx(100000.0)
    assert body['summary']['capital_source'] == 'balance'


def test_CONTROL_a_typed_capital_wins(tmp_path):
    body = _app(tmp_path, {'CAPITAL_MANAGED': 25000}).test_client().get(
        '/api/analysis').get_json()
    assert body['summary']['capital'] == pytest.approx(25000.0)
    assert body['summary']['capital_source'] == 'set'

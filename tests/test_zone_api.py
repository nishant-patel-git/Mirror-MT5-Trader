"""The sigma-zone track over the API: the date range, and that it is
filtered in the QUERY rather than by handing the browser everything.
"""

import json
import time

import pytest

from mt5trader import zones
from mt5trader.database import Store
from mt5trader.webapp import create_app

DAY = 86400.0


@pytest.fixture
def desk(tmp_path):
    paths = {'status': str(tmp_path / 'status.json'),
             'commands': str(tmp_path / 'commands.jsonl'),
             'results': str(tmp_path / 'results.json'),
             'config': str(tmp_path / 'config.json'),
             'db': str(tmp_path / 'trader.db')}
    with open(paths['config'], 'w', encoding='utf-8') as handle:
        json.dump({'pairs': {'A|B': {'name': 'A vs B'},
                             'C|D': {'name': 'C vs D'}}}, handle)
    store = Store(paths['db'])
    app = create_app(paths['status'], paths['commands'], paths['results'],
                     paths['config'], paths['db'])
    app.config.update(TESTING=True)
    return app.test_client(), store


def write(store, pair_key, age_days, zone, now, **kw):
    store.save_zone_event(zones.event_row(
        pair_key, now - age_days * DAY, kw.pop('z', 2.6), zone,
        kw.pop('previous', zones.MEAN), 13.0, 0.1,
        {'entry_z': 2.5, 'max_entry_z': 3.5, 'stop_z': 4.0, 'length': 20},
        timeframe_sec=900.0, **kw))


def test_the_range_dropdown_filters_the_zone_rows(desk):
    client, store = desk
    now = time.time()
    for age, zone in ((40, '+2'), (20, '+3'), (3, '-2'), (0.4, '-3')):
        write(store, 'A|B', age, zone, now)

    def rows(query):
        body = client.get('/api/analysis' + query).get_json()
        assert body['ok'], body
        return body

    assert len(rows('?zone_days=1')['zones']) == 1
    assert len(rows('?zone_days=7')['zones']) == 2
    assert len(rows('?zone_days=30')['zones']) == 3
    assert len(rows('?zone_days=0')['zones']) == 4     # all records


def test_a_day_is_the_default_without_being_asked_for(desk):
    client, store = desk
    now = time.time()
    write(store, 'A|B', 0.4, '+2', now)
    write(store, 'A|B', 9, '+3', now)
    body = client.get('/api/analysis').get_json()
    assert body['zone_days'] == 1
    assert [r['zone'] for r in body['zones']] == ['+2']


def test_the_rows_come_back_newest_first(desk):
    client, store = desk
    now = time.time()
    for age, zone in ((0.9, '+2'), (0.1, '-2'), (0.5, '+3')):
        write(store, 'A|B', age, zone, now)
    body = client.get('/api/analysis?zone_days=1').get_json()
    ats = [r['at'] for r in body['zones']]
    assert ats == sorted(ats, reverse=True)
    assert [r['zone'] for r in body['zones']] == ['-2', '+3', '+2']


def test_one_ladder_can_be_asked_for_on_its_own(desk):
    client, store = desk
    now = time.time()
    write(store, 'A|B', 0.2, '+2', now)
    write(store, 'C|D', 0.2, '-3', now)
    body = client.get('/api/analysis?zone_days=1&pair=A%7CB').get_json()
    assert [r['pair_key'] for r in body['zones']] == ['A|B']
    both = client.get('/api/analysis?zone_days=1').get_json()
    assert sorted(r['pair_key'] for r in both['zones']) == ['A|B', 'C|D']


def test_the_span_says_what_history_there_is_to_filter(desk):
    """The data-availability question, answered by the API itself."""
    client, store = desk
    now = time.time()
    for age in (40, 20, 3, 0.4):
        write(store, 'A|B', age, '+2', now)
    write(store, 'C|D', 1.0, '-2', now)
    body = client.get('/api/analysis?zone_days=0').get_json()
    span = body['zone_span']
    assert span['A|B']['rows'] == 4
    assert span['C|D']['rows'] == 1
    assert span['A|B']['first_at'] == pytest.approx(now - 40 * DAY, abs=2)
    assert span['A|B']['last_at'] == pytest.approx(now - 0.4 * DAY, abs=2)


def test_the_row_carries_every_column_the_table_renders(desk):
    client, store = desk
    now = time.time()
    write(store, 'A|B', 0.1, '+2', now,
          prices={'entry': 13.25, 'break_even': 13.05, 'tp': 12.55,
                  'sl': 13.55},
          edge={'ratio': 2.1, 'capture': 42.0, 'cost': 20.0,
                'required': 1.5, 'ok': True},
          filters={'ready': True}, algo_mode='DRY_RUN', step=0.01)
    [row] = client.get('/api/analysis?zone_days=1').get_json()['zones']
    assert row['zone'] == '+2' and row['prev_zone'] == 'MEAN'
    assert row['direction'] == 'OUT' and row['side'] == 'SELL'
    assert row['entry'] == 13.25 and row['tp'] == 12.55 and row['sl'] == 13.55
    assert row['break_even'] == 13.05
    assert row['edge_ratio'] == 2.1 and row['edge_ok'] is True
    assert row['status'] == zones.ELIGIBLE
    assert row['algo_mode'] == 'DRY_RUN'
    assert row['entry_z'] == 2.5 and row['length'] == 20


def test_an_unpriced_edge_comes_back_as_none_not_zero(desk):
    """A 0.00x edge reads as 'no edge'; this one was never measured."""
    client, store = desk
    write(store, 'A|B', 0.1, '+2', time.time(),
          edge={'ratio': None, 'capture': None, 'cost': None,
                'required': 1.5, 'ok': None},
          filters={'ready': True},
          block_reason='edge filter: the round-trip cost is not priced yet')
    [row] = client.get('/api/analysis?zone_days=1').get_json()['zones']
    assert row['edge_ratio'] is None
    assert row['edge_ok'] is None
    assert row['status'] == zones.BLOCKED


def test_the_statuses_are_published_for_the_screen(desk):
    client, _store = desk
    body = client.get('/api/analysis').get_json()
    assert set(body['zone_statuses']) == set(zones.STATUSES)


def test_an_empty_track_is_an_empty_list_not_an_error(desk):
    client, _store = desk
    body = client.get('/api/analysis?zone_days=7').get_json()
    assert body['ok'] and body['zones'] == [] and body['zone_span'] == {}


# -- the date picker, and the signals ----------------------------------------

def write_signal(store, pair_key, day, basis='ALGO', outcome='TP',
                 armed_at=None, **kw):
    row = dict({
        'pair_key': pair_key, 'basis': basis, 'side': 'SELL',
        'broker_day': day, 'threshold': 2.0, 'state': 'CLOSED',
        'outcome': outcome, 'armed_at': armed_at or time.time(),
        'armed_z': 2.6, 'entered_at': armed_at or time.time(),
        'entered_z': 2.6, 'entry': 13.25, 'break_even': 13.05,
        'tp': 12.55, 'sl': 13.55, 'units': 50.0, 'reverted': True,
        'revert_min': 22.0, 'held_min': 40.0, 'entry_z': 2.5}, **kw)
    return store.save_signal(row)


def test_a_particular_day_can_be_asked_for_on_its_own(desk):
    """'How many signals on 5 Nov for Gold' - the question asked."""
    client, store = desk
    write_signal(store, 'A|B', '2026-11-05')
    write_signal(store, 'A|B', '2026-11-05')
    write_signal(store, 'A|B', '2026-11-04')
    body = client.get('/api/analysis?date=2026-11-05').get_json()
    assert body['ok'] and body['date'] == '2026-11-05'
    assert len(body['signals']) == 2


def test_a_day_and_an_instrument_together(desk):
    client, store = desk
    write_signal(store, 'A|B', '2026-11-05')
    write_signal(store, 'C|D', '2026-11-05')
    body = client.get('/api/analysis?date=2026-11-05&pair=A%7CB').get_json()
    assert [r['pair_key'] for r in body['signals']] == ['A|B']


def test_the_day_beats_the_rolling_window_when_both_are_given(desk):
    """A day and 'the last 24 hours' are different questions, and
    different answers near a boundary."""
    client, store = desk
    write_signal(store, 'A|B', '2020-01-02',
                 armed_at=time.time() - 900 * DAY)
    body = client.get('/api/analysis?zone_days=1&date=2020-01-02').get_json()
    assert len(body['signals']) == 1       # the rolling day would show none


def test_a_date_that_is_not_a_date_is_refused_in_words(desk):
    client, _store = desk
    body = client.get('/api/analysis?date=5-Nov').get_json()
    assert body['ok'] is False and 'YYYY-MM-DD' in body['error']


def test_the_days_that_have_signals_are_published_for_the_picker(desk):
    client, store = desk
    write_signal(store, 'A|B', '2026-11-05')
    write_signal(store, 'A|B', '2026-11-05')
    write_signal(store, 'A|B', '2026-11-03')
    days = client.get('/api/analysis').get_json()['signal_days']
    assert days == [{'day': '2026-11-05', 'rows': 2},
                    {'day': '2026-11-03', 'rows': 1}]


def test_the_tally_counts_what_the_tiles_show(desk):
    client, store = desk
    for outcome in ('TP', 'TP', 'SL'):
        write_signal(store, 'A|B', '2026-11-05', outcome=outcome,
                     reverted=outcome == 'TP')
    write_signal(store, 'A|B', '2026-11-05', outcome='NO_ENTRY',
                 state='CLOSED', entered_at=None, entered_z=None)
    body = client.get('/api/analysis?date=2026-11-05').get_json()
    tally = body['signal_tally']
    assert tally['armed'] == 4 and tally['entered'] == 3
    assert tally['no_entry'] == 1
    assert tally['tp'] == 2 and tally['sl'] == 1
    assert tally['win_rate'] == pytest.approx(66.7)


def test_a_day_with_nothing_is_an_empty_list_not_an_error(desk):
    client, _store = desk
    body = client.get('/api/analysis?date=1999-01-01').get_json()
    assert body['ok'] and body['signals'] == [] and body['zones'] == []

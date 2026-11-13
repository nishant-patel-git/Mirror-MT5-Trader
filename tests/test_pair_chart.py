"""The pair chart: each ladder's spread and the Algo's own z, over time.

The chart reads; it never decides. Its z is the Algo's own - the number
it armed and entered on - so the line crosses the entry exactly where
the Algo did.
"""

import json

import pytest

from mt5trader import chart
from mt5trader.coordinator import Coordinator
from mt5trader.webapp import create_app

from test_signal_journal import ARM, HOME, REENTRY, Desk


def md(spread, jump=None):
    return {'mid_spread': spread, 'jump_reason': jump}


BODY = {'z_mid': 1.25, 'mean': 59.1, 'sigma': 0.1, 'ready': True,
        'count': 20, 'needed': 20}
PARAMS = {'entry_z': 2.0, 'stop_z': 4.0, 'stop_z_on': True}


def test_one_point_a_second_however_fast_the_poll():
    tape = chart.SpreadTape()
    for i in range(10):
        tape.observe('P', 100.0 + i * 0.3, md(59.0 + i), BODY, PARAMS)
    points = tape.series('P')['points']
    assert [p['t'] for p in points] == [100.0, 101.2, 102.4]


def test_only_the_last_two_hours_are_kept():
    tape = chart.SpreadTape(keep_sec=60)
    for i in range(200):
        tape.observe('P', float(i), md(59.0), BODY, PARAMS)
    points = tape.series('P')['points']
    assert points[0]['t'] >= 199 - 60 and points[-1]['t'] == 199.0


def test_a_price_the_jump_guard_holds_back_is_not_drawn():
    tape = chart.SpreadTape()
    tape.observe('P', 1.0, md(59.0, jump='jumped 8 sigma'), BODY, PARAMS)
    assert tape.series('P')['points'] == []
    # Control: the same price, not held back, is drawn.
    tape.observe('P', 2.0, md(59.0), BODY, PARAMS)
    assert len(tape.series('P')['points']) == 1


def test_the_series_is_thinned_and_ends_at_now():
    tape = chart.SpreadTape()
    for i in range(1000):
        tape.observe('P', float(i), md(59.0 + i * 0.001), BODY, PARAMS)
    series = tape.series('P', max_points=100)
    assert len(series['points']) <= 101
    assert series['points'][-1]['t'] == 999.0
    # Min and max over EVERY point, not just the ones drawn.
    assert series['spread_min'] == pytest.approx(59.0)
    assert series['spread_max'] == pytest.approx(59.999)


def test_the_stop_line_is_drawn_only_while_the_z_stop_is_on():
    tape = chart.SpreadTape()
    tape.observe('P', 1.0, md(59.0), BODY, PARAMS)
    assert tape.series('P')['stop_z'] == 4.0
    tape.observe('P', 3.0, md(59.0), BODY, dict(PARAMS, stop_z_on=False))
    assert tape.series('P')['stop_z'] is None
    assert tape.series('P')['entry_z'] == 2.0


# -- the engine ---------------------------------------------------------------

def test_the_engine_charts_the_z_the_algo_decided_on(config, pair, legs,
                                                     tmp_path):
    desk = Desk(config, pair, legs, tmp_path, **REENTRY)
    bodies = desk.push(HOME, ARM, HOME)
    points = desk.co.tape.series(pair.key)['points']
    assert len(points) >= 3
    # The chart's z IS the Algo's: the same number, poll for poll.
    assert [p['z'] for p in points[-3:]] == pytest.approx(
        [b['z_mid'] for b in bodies], abs=1e-3)
    assert desk.co.tape.series(pair.key)['entry_z'] == 2.5


def test_the_chart_file_is_written_beside_the_status_file(config, pair, legs,
                                                         tmp_path):
    status = tmp_path / 'status.json'
    co = Coordinator(config, legs, status_path=str(status),
                     sleep=lambda s: None)
    co.start()
    co.poll_once()
    co.publish()
    body = json.loads((tmp_path / 'chart.json').read_text(encoding='utf-8'))
    assert pair.key in body['pairs']


# -- the web route ------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / 'status.json'),
                     str(tmp_path / 'commands.jsonl'),
                     str(tmp_path / 'results.json'),
                     str(tmp_path / 'config.json'))
    app.config.update(TESTING=True)
    return app.test_client()


def test_the_route_draws_only_the_minutes_asked_for(client, tmp_path):
    tape = chart.SpreadTape()
    for i in range(0, 3600, 60):
        tape.observe('A|B', 10_000.0 + i, md(59.0 + i / 3600.0), BODY,
                     PARAMS)
    tape.write(str(tmp_path / 'chart.json'), 10_000.0 + 3540, force=True)
    body = client.get('/api/chart/A|B?minutes=15').get_json()
    assert body['ok'] and body['entry_z'] == 2.0
    assert len(body['points']) == 16
    assert body['spread_min'] == pytest.approx(59.0 + 2640 / 3600.0)
    # Control: the whole hour.
    assert len(client.get('/api/chart/A|B?minutes=60')
               .get_json()['points']) == 60


def test_a_ladder_with_nothing_yet_says_so(client):
    body = client.get('/api/chart/A|B').get_json()
    assert body['ok'] and body['points'] == []
    assert 'fills while the engine runs' in body['note']

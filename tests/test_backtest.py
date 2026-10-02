"""The backtest: history through the Algo's own decision code.

It has to show what the protections are for — a climbing spread sold
into by the plain Algo and left alone by the protected one — and that a
range still trades. And it can never reach an order.
"""

import ast
import random

import pytest

from mt5trader import backtest

TF = 900.0
COST = {'k': 10.0, 'commission': 0.0, 'slippage': 0.0}
LEVELS = {'fee_points': 0.0, 'target_points': 0.05, 'stop_points': 0.30}
BASE = {'entry_z': 2.0, 'reentry_back': 0.5, 'edge_on': False,
        'regime_on': False, 'cutoff_buffer_min': 0, 'cooldown_min': 0,
        'max_trades_day': 0, 'max_losses_row': 0, 'max_entry_z': 0,
        'trend_sigma': 1.0, 'trend_lookback_min': 120}
PROTECTED = dict(BASE, reentry_on=True, trend_on=True)
PLAIN = dict(BASE, reentry_on=False, trend_on=False)


def climbing(n=200, seed=7):
    rng = random.Random(seed)
    return [(i * TF, 10.0 + 0.02 * i + rng.uniform(-0.06, 0.06))
            for i in range(n)]


def ranging(n=200, seed=3):
    """Quiet around 10.00, with a stretch every 25 candles that comes
    back over the next three."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        value = 10.0 + rng.uniform(-0.02, 0.02)
        phase = i % 25
        if i > 30 and phase == 0:
            value += 0.20
        elif i > 30 and phase == 1:
            value += 0.06
        rows.append((i * TF, value))
    return rows


def sells(result):
    return [t for t in result['trades'] if t['side'] == 'SELL']


def test_a_climbing_spread_is_sold_into_without_the_protections():
    result = backtest.run(climbing(), PLAIN, 0.01, COST, LEVELS)
    assert len(sells(result)) >= 3


def test_and_left_alone_with_them():
    result = backtest.run(climbing(), PROTECTED, 0.01, COST, LEVELS)
    assert sells(result) == []
    assert any('trend' in reason for reason in result['held'])


def test_a_range_still_trades_with_the_protections_on():
    result = backtest.run(ranging(), PROTECTED, 0.01, COST, LEVELS)
    assert result['summary']['trades'] >= 3
    assert result['summary']['wins'] >= 1


def test_the_summary_adds_up():
    result = backtest.run(ranging(), PLAIN, 0.01, COST, LEVELS)
    s = result['summary']
    assert s['wins'] + s['losses'] == s['closed']
    closed = [t for t in result['trades'] if t['pnl'] is not None]
    assert s['net'] == pytest.approx(sum(t['pnl'] for t in closed))
    assert s['max_drawdown'] <= 0
    assert result['caveats']


def test_no_history_is_no_trades_not_an_error():
    result = backtest.run([], PROTECTED, 0.01, COST, LEVELS)
    assert result['trades'] == [] and result['summary']['net'] is None


def test_the_backtest_cannot_reach_an_order():
    tree = ast.parse(open(backtest.__file__, encoding='utf-8').read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add((node.module or '') + ':' + ','.join(
                a.name for a in node.names))
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert imported <= {'datetime', 're', ':algo', ':bands',
                        'algodesk:judge_filters'}, imported


# -- through the engine ------------------------------------------------------------


def test_the_engine_backtests_a_ladder_from_mt5_history(config, pair, legs):
    from mt5trader.coordinator import Coordinator
    from test_algo_desk import Clock, give_history
    give_history(legs, count=80)
    coordinator = Coordinator(config, legs, sleep=lambda s: None,
                              clock=Clock())
    coordinator.start()
    coordinator.poll_once()
    answer = coordinator.algo_backtest(pair.key, days=1)
    assert answer['ok'], answer
    assert answer['summary']['candles'] > 20
    assert 'without_protections' in answer
    assert answer['width'] > 0
    # Nothing was sent: the brokers saw no order.
    assert legs['acct_a'].broker.sent == [] and legs['acct_b'].broker.sent == []


def test_the_engine_says_why_it_cannot_backtest(config, pair, legs):
    from mt5trader.coordinator import Coordinator
    from test_algo_desk import Clock
    coordinator = Coordinator(config, legs, sleep=lambda s: None,
                              clock=Clock())
    coordinator.start()
    coordinator.poll_once()
    answer = coordinator.algo_backtest(pair.key, days=1)    # no bars
    assert answer['ok'] is False and 'history' in answer['reason']

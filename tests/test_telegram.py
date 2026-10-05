"""The Telegram bot: who it answers, what it may do, and what it tells.

A fake Telegram records what the bot sends; a fake desk records what it
asks the engine to do. Every test that shows something WITHHELD has a
control beside it showing the same thing go through.
"""

import json
import logging
import urllib.error

import pytest

from mt5trader import telegram as tg

ME, STRANGER = 111, 999
KEY = 'USOILZ6.c|UKOILZ26.p'


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.updates = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'getUpdates':
            updates, self.updates = self.updates, []
            return updates
        return {'message_id': len(self.calls)}

    def texts(self):
        return [p.get('text') for m, p in self.calls
                if m in ('sendMessage', 'editMessageText')]

    def last(self):
        return [p for m, p in self.calls
                if m in ('sendMessage', 'editMessageText')][-1]

    def buttons(self):
        markup = self.last().get('reply_markup') or {}
        return {b['text']: b['callback_data']
                for row in markup.get('inline_keyboard', []) for b in row}


def row(**extra):
    base = {'name': 'Oil', 'algo_on': True, 'algo_mode': 'DRY_RUN',
            'net_position': 0.0, 'open_pnl': None, 'positions': [],
            'short_spread': 12.275, 'long_spread': 12.36,
            'overnight': 'ALLOW',
            'algo_params': {'entry_z': 2.0, 'direction': 'BOTH',
                            'algo_qty': 1.0, 'max_trades_day': 6},
            'algo_block': {'state': 'WATCHING', 'z_sell': 1.87,
                           'z_buy': 2.37, 'upper': 12.298, 'lower': 11.623,
                           'warmup': {'sec': 600, 'need_sec': 5400,
                                      'done': False},
                           'filters': {'ready': False,
                                       'edge': {'on': True, 'ok': True,
                                                'ratio': 1.99,
                                                'required': 1.5},
                                       'regime': {'on': False}},
                           'day': {'trades': 0, 'losses_row': 0,
                                   'pnl': 0.0}},
            'market': {}}
    base.update(extra)
    return base


class FakeDesk:
    def __init__(self):
        self.snapshot = {'engine': 'up', 'pairs': {KEY: row()},
                         'accounts': {}, 'dark_accounts': [],
                         'broker_clock': {}}
        self.saved_config = {'algo_params': {'entry_z': 2.0}}
        self.commands = []
        self.saves = []
        self.answer = {'ok': True, 'data': {'ok': True}}
        self.event_rows = []
        self.records = {}
        self.report = {'ok': True, 'journal': [], 'drawdown': {},
                       'names': {KEY: 'Oil'}}

    def analysis(self, days=0):
        return json.loads(json.dumps(self.report))

    def position(self, position_id):
        return self.records.get(position_id) or {'ok': False}

    def status(self):
        return json.loads(json.dumps(self.snapshot))

    def config_pair(self, key):
        return json.loads(json.dumps(self.saved_config))

    def events(self, limit=50):
        return list(self.event_rows)

    def save_pair(self, key, payload):
        self.saves.append((key, payload))
        return {'ok': True, 'notes': []}

    def command(self, kind, payload, wait=10.0):
        assert kind in tg.ALLOWED_COMMANDS, kind
        self.commands.append((kind, payload))
        answer, self.answer = self.answer, {'ok': True, 'data': {'ok': True}}
        return answer


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def bot():
    api, desk, clock = FakeAPI(), FakeDesk(), Clock()
    b = tg.Bot(api, desk, {ME}, clock=clock)
    b.clock_ = clock
    return b


def say(bot, text, user=ME):
    bot.handle({'update_id': 1, 'message': {
        'message_id': 5, 'from': {'id': user}, 'chat': {'id': user},
        'text': text}})


def press(bot, label, user=ME):
    data = bot.api.buttons()[label]
    bot.handle({'update_id': 2, 'callback_query': {
        'id': 'q', 'from': {'id': user}, 'data': data,
        'message': {'message_id': 7, 'chat': {'id': user}}}})


# -- who it answers -----------------------------------------------------------


def test_a_stranger_gets_no_answer_at_all(bot, caplog):
    with caplog.at_level(logging.WARNING):
        say(bot, '/start', user=STRANGER)
    assert bot.api.calls == []
    assert str(STRANGER) in caplog.text      # so it can be allowed later


def test_the_CONTROL_an_allowed_user_gets_the_menu(bot):
    say(bot, '/start')
    assert 'NEXUS SPREAD DESK' in bot.api.texts()[-1]
    assert {'Status', 'Positions', 'Trades', 'P&L', 'Balance', 'Settings',
            'Alerts: ON', 'KILL ALL'} <= \
        set(bot.api.buttons())


def test_rows_are_a_bold_label_and_a_value_in_code_type():
    """Stat_Arb_W3's layout: every number in its own box."""
    assert tg.table([('Net P&L', '+$5.27'), None, ('Hold', '23m')]) == (
        '<b>Net P&amp;L</b>  <code>+$5.27</code>\n\n'
        '<b>Hold</b>  <code>23m</code>')
    assert tg.table([('Peak', None)]) == '<b>Peak</b>  <code>—</code>'


def test_no_monospace_block_anywhere():
    """Bold labels and boxed values only: a <pre> block was asked
    against."""
    source = open(tg.__file__, encoding='utf-8').read()
    assert '<pre>' not in source


def test_the_menu_lists_the_commands_as_tappable_text(bot):
    say(bot, '/start')
    text = bot.api.texts()[-1]
    assert '/positions  -  Open positions: live P&amp;L + exit levels' \
        in text and '<pre>' not in text


def test_nothing_the_bot_says_carries_an_emoji(bot):
    """A desk tool, not a chat toy: plain words on every screen."""
    source = open(tg.__file__, encoding='utf-8').read()
    assert not [c for c in source if ord(c) >= 0x2190 and c not in
                '\u2265\u2264\u2212\u2192\u221e'], 'an icon crept in'


def test_a_stranger_cannot_press_an_allowed_users_button(bot):
    say(bot, '/start')
    press(bot, 'KILL ALL', user=STRANGER)
    assert bot.desk.commands == []


def test_allowed_users_are_read_from_the_env_list():
    assert tg.allowed_users('111, 222 x 333') == {111, 222, 333}
    assert tg.allowed_users('') == set()


def test_no_token_means_no_bot_and_it_says_so(caplog):
    class App:
        config = {}
    with caplog.at_level('INFO'):
        assert tg.start(App(), env={}) is None
    assert 'TELEGRAM_BOT_TOKEN is not set' in caplog.text


# -- LIVE asks twice ------------------------------------------------------------


def open_algo(bot, mode='Dry run'):
    say(bot, '/start')
    press(bot, 'Settings')
    press(bot, 'Oil')
    press(bot, 'Algo mode: ' + mode)


def test_LIVE_needs_two_confirms_and_only_then_is_sent(bot):
    open_algo(bot)
    press(bot, 'LIVE')
    assert bot.desk.commands == []
    assert 'REAL' in bot.api.texts()[-1]
    press(bot, 'Continue')
    assert bot.desk.commands == []          # still not sent
    press(bot, 'Confirm LIVE')
    assert bot.desk.commands == [('set_algo', {
        'pair': KEY, 'algo': 'ALGO', 'mode': 'LIVE', 'confirmed': True})]


def test_the_CONTROL_dry_run_takes_one_confirm(bot):
    bot.desk.snapshot['pairs'][KEY]['algo_on'] = False
    open_algo(bot, 'Off')
    press(bot, 'Dry run')
    press(bot, 'Confirm')
    assert bot.desk.commands == [('set_algo', {
        'pair': KEY, 'algo': 'ALGO', 'mode': 'DRY_RUN'})]


def test_cancel_sends_nothing(bot):
    open_algo(bot)
    press(bot, 'LIVE')
    press(bot, 'Cancel')
    assert bot.desk.commands == []


def test_an_expired_button_does_nothing(bot):
    open_algo(bot)
    press(bot, 'Off')
    bot.clock_.now += tg.TOKEN_TTL_SEC + 1
    press(bot, 'Confirm')
    assert bot.desk.commands == []
    assert 'expired' in bot.api.texts()[-1]


def test_leaving_LIVE_with_a_position_asks_what_to_do(bot):
    bot.desk.snapshot['pairs'][KEY]['algo_mode'] = 'LIVE'
    open_algo(bot, 'LIVE')
    press(bot, 'Off')
    bot.desk.answer = {'ok': False, 'data': {'ok': False, 'choose':
                                             ['close', 'manual'],
                                             'positions': ['P1']}}
    press(bot, 'Confirm')
    assert 'Close now' in bot.api.buttons()
    press(bot, 'Hand to manual')
    assert bot.desk.commands[-1] == ('set_algo', {
        'pair': KEY, 'algo': 'NONE', 'mode': 'DRY_RUN',
        'off_action': 'manual'})


def test_a_refusal_comes_back_in_the_engines_words(bot):
    open_algo(bot)
    press(bot, 'LIVE')
    press(bot, 'Continue')
    bot.desk.answer = {'ok': False, 'reason': 'the ladder holds a manual '
                                              'position'}
    press(bot, 'Confirm LIVE')
    assert any('holds a manual position' in t for t in bot.api.texts())


# -- closing ---------------------------------------------------------------------


def test_close_all_on_a_ladder_is_confirmed_first(bot):
    bot.desk.snapshot['pairs'][KEY].update(
        net_position=1.0, open_pnl=3.2,
        positions=[{'side': 'SELL', 'quantity': 1, 'entry_spread': 12.3,
                    'closing_spread': 12.2, 'net_pnl': 3.2,
                    'source': 'ALGO'}])
    say(bot, '/dashboard')
    assert '(algo)' in bot.api.texts()[-1]
    press(bot, 'Close all: Oil')
    assert bot.desk.commands == []
    bot.desk.answer = {'ok': True, 'data': {'closed': 1, 'failed': []}}
    press(bot, 'Close all')
    assert bot.desk.commands == [('flatten_pair', {'pair': KEY})]


def test_kill_all_is_confirmed_first(bot):
    say(bot, '/start')
    press(bot, 'KILL ALL')
    assert bot.desk.commands == []
    press(bot, 'Confirm KILL ALL')
    assert bot.desk.commands == [('kill', {'flatten': True})]


# -- settings ---------------------------------------------------------------------


def open_settings(bot, section='Entry'):
    say(bot, '/start')
    press(bot, 'Settings')
    press(bot, 'Oil')
    press(bot, section)


def test_a_typed_setting_is_confirmed_saved_and_applied(bot):
    open_settings(bot)
    press(bot, 'Entry z: 2.0')
    say(bot, '2.5')
    assert bot.desk.saves == []
    press(bot, 'Confirm')
    typed = {'entry_z': 2.5}
    assert bot.desk.saves == [(KEY, {'algo_params': typed})]
    assert bot.desk.commands == [('set_pair', {
        'pair': KEY, 'fields': {'algo_params': typed}})]


def test_a_bad_value_changes_nothing(bot):
    open_settings(bot)
    press(bot, 'Entry z: 2.0')
    say(bot, 'abc')
    assert 'not a number' in bot.api.texts()[-1]
    assert bot.desk.saves == [] and bot.desk.commands == []


def test_an_entry_z_of_zero_is_refused_like_the_screen_refuses_it(bot):
    open_settings(bot)
    press(bot, 'Entry z: 2.0')
    say(bot, '0')
    assert 'entry z must be above 0' in bot.api.texts()[-1]
    assert bot.desk.saves == []


def test_a_choice_setting_offers_its_choices(bot):
    open_settings(bot)
    press(bot, 'Direction: Both')
    press(bot, 'H to L only')
    press(bot, 'Confirm')
    assert bot.desk.saves == [(KEY, {'algo_params': {
        'entry_z': 2.0, 'direction': 'H_TO_L'}})]


def test_every_exit_switch_is_in_the_settings_menu(bot):
    """The exits that can close a trade early are switchable from a
    phone — "Back to mean" scratched a trade at break-even once, and the
    trader has to be able to turn it off away from the desk."""
    open_settings(bot, 'Exits')
    labels = set(bot.api.buttons())
    for label in ('Back to mean: OFF', 'Z-stop: OFF',
                  'Z-stop at |z|: 4.0', 'Time stop: OFF',
                  'Time stop (candles): 20', 'Stop loss: ON'):
        assert label in labels, label


def test_back_to_mean_is_switched_and_saved(bot):
    open_settings(bot, 'Exits')
    press(bot, 'Back to mean: OFF')
    press(bot, 'ON')
    press(bot, 'Confirm')
    assert bot.desk.saves == [(KEY, {'algo_params': {
        'entry_z': 2.0, 'reversion_on': True}})]


def test_a_pair_setting_is_saved_on_the_pair(bot):
    open_settings(bot, 'Costs and session')
    press(bot, 'Commission per lot, leg A (per side): desk default')
    say(bot, '3.5')
    press(bot, 'Confirm')
    assert bot.desk.saves == [(KEY, {'commission_per_lot_a': 3.5})]


# -- what it may send --------------------------------------------------------------


def test_the_bot_can_never_place_an_order():
    """No click, no level, no quantity: it switches, saves and CLOSES."""
    assert tg.ALLOWED_COMMANDS == {'set_algo', 'set_pair', 'flatten_pair',
                                   'kill'}
    source = open(tg.__file__, encoding='utf-8').read()
    for kind in ('click', 'close_at_limit', 'cancel_order', 'cancel_where'):
        assert f"'{kind}'" not in source


def test_the_web_desk_refuses_any_other_command():
    class App:
        def test_client(self):
            return None
    desk = tg.WebDesk(App(), 'secret')
    with pytest.raises(ValueError):
        desk.command('click', {'pair': KEY, 'side': 'BUY', 'level': 1})


# -- the token stays secret ----------------------------------------------------------


def test_the_token_never_appears_in_an_error(caplog):
    token = '123456:SECRET-TOKEN'

    def opener(request, timeout):
        raise urllib.error.URLError(OSError('down'))
    api = tg.TelegramAPI(token, opener=opener)
    with pytest.raises(tg.TelegramError) as raised:
        api.call('getUpdates')
    assert token not in str(raised.value)


# -- alerts -------------------------------------------------------------------------


def watch(bot):
    return bot.alert_once()


def test_history_already_in_the_journal_is_not_replayed(bot):
    bot.desk.event_rows = [{'id': 5, 'kind': 'algo_signal', 'pair_key': KEY,
                            'detail': {'action': 'ENTER', 'side': 'SELL'}}]
    assert watch(bot) == []
    # The control: one that arrives after the first look IS told.
    bot.desk.event_rows.insert(0, {'id': 6, 'kind': 'algo_signal',
                                   'pair_key': KEY,
                                   'detail': {'action': 'ENTER',
                                              'side': 'SELL', 'z': 2.1,
                                              'spread': 12.3,
                                              'mode': 'LIVE'}})
    told = watch(bot)
    assert len(told) == 1 and 'ENTER H to L' in told[0]
    assert 'SIGNAL  ·  ENTER H to L  ·  LIVE' in told[0]


def test_an_algo_order_that_failed_says_why(bot):
    watch(bot)
    bot.desk.event_rows = [{'id': 9, 'kind': 'algo_order', 'pair_key': KEY,
                            'detail': {'action': 'ENTER', 'side': 'BUY',
                                       'ok': False,
                                       'reason': '10027 AutoTrading '
                                                 'disabled by client'}}]
    told = watch(bot)
    assert 'FAILED' in told[0] and '10027' in told[0]


def test_alerts_go_to_allowed_users_unless_they_turned_them_off(bot):
    watch(bot)
    bot.desk.snapshot['engine'] = 'stalled'
    watch(bot)
    assert any('SYSTEM ERROR' in t and 'STALLED' in t
               for t in bot.api.texts())
    # The control: with /alerts off, the same news is not sent.
    say(bot, '/alerts')
    before = len(bot.api.texts())
    bot.desk.snapshot['engine'] = 'up'
    watch(bot)
    assert len(bot.api.texts()) == before


def test_a_price_problem_is_told_once_it_has_lasted(bot):
    watch(bot)
    bot.desk.snapshot['pairs'][KEY]['market'] = {'stale_reason': 'leg A '
                                                                 'stale 30s'}
    assert watch(bot) == []                      # just started
    bot.clock_.now += tg.PROBLEM_SETTLE_SEC + 1
    told = watch(bot)
    assert 'leg A stale' in told[0]
    assert watch(bot) == []                      # once, not every look
    bot.desk.snapshot['pairs'][KEY]['market'] = {}
    assert 'back to normal' in watch(bot)[0]


def test_a_blocked_signal_is_told_and_its_repeats_are_not(bot):
    watch(bot)
    block = bot.desk.snapshot['pairs'][KEY]['algo_block']
    block['last_blocked'] = {'side': 'SELL', 'z': 2.1, 'at': 1,
                             'reason': 'past the session cutoff'}
    assert 'past the session cutoff' in watch(bot)[0]
    block['last_blocked'] = dict(block['last_blocked'], at=2)
    assert watch(bot) == []                      # the same, again
    # The control: a different reason is news.
    block['last_blocked'] = dict(block['last_blocked'], at=3,
                                 reason='edge filter: capture 0.9x')
    assert 'edge filter' in watch(bot)[0]


def test_the_daily_summary_is_sent_once_at_the_cutoff(bot):
    bot.desk.snapshot['broker_clock'] = {'broker_time': '16:50:00',
                                         'cutoff': '16:55'}
    watch(bot)
    assert watch(bot) == []
    bot.desk.snapshot['broker_clock']['broker_time'] = '16:55:03'
    told = watch(bot)
    assert 'END OF DAY' in told[0]
    bot.desk.snapshot['broker_clock']['broker_time'] = '17:30:00'
    assert watch(bot) == []


# -- through the real web app --------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    from mt5trader.webapp import create_app
    paths = {n: str(tmp_path / n) for n in ('status.json', 'commands.jsonl',
                                            'results.json', 'config.json',
                                            'db.sqlite')}
    with open(paths['config.json'], 'w') as f:
        json.dump({'accounts': {}, 'pairs': {KEY: {'name': 'Oil'}}}, f)
    app = create_app(paths['status.json'], paths['commands.jsonl'],
                     paths['results.json'], paths['config.json'],
                     paths['db.sqlite'])
    return app, paths


def test_the_bot_passes_the_screen_lock_with_its_secret(web, monkeypatch):
    from mt5trader import screenlock
    # A machine with no PIN is never locked: give this one a PIN.
    monkeypatch.setenv(screenlock.PIN_ENV_KEY,
                       screenlock.hash_pin('1234', iterations=1))
    app, paths = web
    app.config['BOT_SECRET'] = 'in-process-secret'
    browser = app.test_client()
    browser.post('/api/lock')
    # Locked: a browser cannot save.
    assert browser.post('/api/pairs/' + KEY.replace('|', '%7C'),
                        json={'tp_target_pct_of_margin': 3}).status_code == 423
    # The control: the bot's in-process request can.
    desk = tg.WebDesk(app, 'in-process-secret')
    assert desk.save_pair(KEY, {'tp_target_pct_of_margin': 3})['ok'] is True
    # And a wrong secret is just a locked browser.
    assert tg.WebDesk(app, 'guess').save_pair(
        KEY, {'tp_target_pct_of_margin': 4}).get('ok') is False


def test_the_web_desk_saves_through_the_same_checks_as_the_screen(web):
    app, paths = web
    app.config['BOT_SECRET'] = 's'
    desk = tg.WebDesk(app, 's')
    answer = desk.save_pair(KEY, {'algo_params': {'entry_z': 0}})
    assert answer['ok'] is False and 'entry z' in answer['error']
    assert desk.config_pair(KEY)['name'] == 'Oil'


def test_a_command_with_the_engine_down_is_refused_not_queued(web):
    app, paths = web
    app.config['BOT_SECRET'] = 's'
    desk = tg.WebDesk(app, 's', sleep=lambda s: None)
    answer = desk.command('set_algo', {'pair': KEY, 'algo': 'NONE'})
    assert answer['ok'] is False and 'not running' in answer['reason']


# -- the trade messages and the W3 screens -------------------------------------------


POSITION = {
    'position_id': 'P7', 'pair_key': KEY, 'side': 'SELL', 'quantity': 0.5,
    'entry_spread': 13.196, 'closing_spread': 13.365, 'net_pnl': -8.45,
    'spread_units': 50.0, 'source': 'ALGO', 'opened_at': 1_799_999_000.0,
    'entry_margin': 191.5, 'entry_slippage': 0.002, 'click_to_on_ms': 180,
    'leg_a': {'side': 'BUY', 'volume': 0.05, 'symbol': 'USOILZ6.c',
              'price': 89.08, 'contract_size': 1000},
    'leg_b': {'side': 'SELL', 'volume': 0.05, 'symbol': 'UKOILZ26.p',
              'price': 102.276, 'contract_size': 1000}}
LEVELS = {'position_id': 'P7', 'tp': 13.119, 'sl': 13.388,
          'break_even': 13.196, 'entry_z': 1.49}


def holding(bot):
    pair = bot.desk.snapshot['pairs'][KEY]
    pair['positions'] = [dict(POSITION)]
    pair['algo_block']['positions'] = [dict(LEVELS)]
    pair['market'] = {'leg_a_bid': 89.171, 'leg_a_ask': 89.223,
                      'leg_b_bid': 102.503, 'leg_b_ask': 102.536}


def test_a_position_that_opens_is_a_trade_entry(bot):
    watch(bot)                                   # bearings: flat
    holding(bot)
    [told] = watch(bot)
    assert 'TRADE ENTRY  ·  H to L  ·  Oil' in told
    assert '<b>Entry Spread</b>  <code>13.1960  (Z: +1.49)</code>' in told
    assert 'Take Profit' in told and '+$3.85' in told    # 0.077 x 50
    assert 'Margin Req' in told and '(' in told          # leverage shown


def test_a_position_that_closes_is_a_trade_exit_with_its_analysis(bot):
    holding(bot)
    watch(bot)                                   # bearings: it is open
    closed = dict(POSITION, closed_at=POSITION['opened_at'] + 1359,
                  exit_spread=13.119, realized_pnl=3.85,
                  close_reason='Algo: profit target (after costs)',
                  peak_pnl=4.10, peak_min=20.0, trough_pnl=-9.0,
                  trough_min=6.0, exit_slippage=0.001)
    bot.desk.records['P7'] = {'ok': True, 'position': closed,
                              'exit_prices': {'leg_a': 89.2, 'leg_b': 102.3},
                              'entry_z': 1.49, 'exit_z': -0.4}
    bot.desk.snapshot['pairs'][KEY]['positions'] = []
    [told] = watch(bot)
    assert 'TRADE EXIT  ·  H to L  ·  Oil  ·  PROFIT' in told
    for part in ('Duration</b>  <code>22m 39s', 'Leg A Exit',
                 'Spread Chg</b>  <code>+0.0770  (with)',
                 'ANALYSIS', 'TARGET HIT', 'Peak/Trough',
                 'Capture</b>  <code>+$3.85 of +$4.10 best (+94%)',
                 'Z path</b>  <code>+1.49 -&gt; -0.40'):
        assert part in told, part


def test_the_CONTROL_positions_open_at_start_are_not_news(bot):
    holding(bot)
    assert watch(bot) == []


def test_open_positions_shows_the_whole_position_now(bot):
    holding(bot)
    say(bot, '/positions')
    text = bot.api.texts()[-1]
    for part in ('OPEN POSITIONS', 'Leg A Now', '(+0.10%)',
                 'Δ Spread</b>  <code>+0.1690  (against)',
                 'BE 13.196 · TP 13.119 · SL 13.388',
                 'Target/Stop</b>  <code>+$3.85  /  -$9.60'):
        assert part in text, part
    assert 'Close all: Oil' in bot.api.buttons()


def test_the_settings_list_carries_every_key_set_takes(bot):
    say(bot, '/settings')
    listing = bot.api.texts()[-2]
    for key in ('mode', 'entry_z', 'reversion_on', 'stop_loss_pct',
                'commission_per_lot_a'):
        assert f'  {key}' in listing, key
    assert '/set &lt;key&gt; &lt;value&gt;' in bot.api.texts()[-1]


def test_set_is_confirmed_before_it_saves(bot):
    say(bot, '/set entry_z 2.5')
    assert bot.desk.saves == []
    press(bot, 'Confirm')
    assert bot.desk.saves == [(KEY, {'algo_params': {'entry_z': 2.5}})]


def test_set_a_switch_and_a_choice_by_their_words(bot):
    say(bot, '/set reversion_on off')
    press(bot, 'Confirm')
    say(bot, '/set direction h_to_l')
    press(bot, 'Confirm')
    assert [s[1]['algo_params'] for s in bot.desk.saves] == [
        {'entry_z': 2.0, 'reversion_on': False},
        {'entry_z': 2.0, 'direction': 'H_TO_L'}]


def test_set_mode_live_still_asks_twice(bot):
    say(bot, '/set mode live')
    assert bot.desk.commands == [] and 'Continue' in bot.api.buttons()
    press(bot, 'Continue')
    assert bot.desk.commands == []
    press(bot, 'Confirm LIVE')
    assert bot.desk.commands[-1][1]['mode'] == 'LIVE'


def test_set_with_a_bad_key_or_value_changes_nothing(bot):
    say(bot, '/set no_such_key 1')
    assert 'No setting' in bot.api.texts()[-1]
    say(bot, '/set entry_z 0')
    assert 'entry z must be above 0' in bot.api.texts()[-1]
    assert bot.desk.saves == [] and bot.desk.commands == []


def test_balance_and_ping(bot):
    bot.desk.snapshot['accounts'] = {'AC-10006': {
        'balance': 16000.0, 'equity': 16031.2, 'margin': 45.0,
        'margin_free': 15986.2, 'margin_level': 35625, 'profit': 31.2,
        'currency': 'USD'}}
    say(bot, '/balance')
    text = bot.api.texts()[-1]
    assert 'ACCOUNT BALANCE' in text and '16,031.20 USD' in text
    assert 'Margin used</b>  <code>45.00 USD' in text
    say(bot, '/ping')
    assert bot.api.texts()[-1].startswith('pong  ·  ')


TRIP = {'position_id': 'P1', 'pair_key': KEY, 'side': 'SELL',
        'quantity': 0.5, 'source': 'ALGO', 'closed_at': 1_800_000_000.0,
        'held_sec': 1359, 'entry_spread': 13.196, 'exit_spread': 13.119,
        'entry_z': 1.49, 'exit_z': -0.4,
        'exit_reason': 'Algo: profit target (after costs)', 'pnl': 3.85,
        'cum_pnl': 3.85,
        'leg_a': {'side': 'BUY', 'volume': 0.05, 'symbol': 'USOILZ6.c',
                  'entry': 89.08},
        'leg_b': {'side': 'SELL', 'volume': 0.05, 'symbol': 'UKOILZ26.p',
                  'entry': 102.276}}


def test_the_command_menu_is_registered_with_telegram(bot):
    bot.register_commands()
    [(method, params)] = [c for c in bot.api.calls if c[0] == 'setMyCommands']
    listed = {c['command']: c['description'] for c in params['commands']}
    assert listed['positions'] == 'Open positions: live P&L + exit levels'
    assert {'status', 'trades', 'pnl', 'balance', 'settings', 'set',
            'alerts', 'ping', 'start'} <= set(listed)
    # Telegram takes the names without the slash.
    assert not any(name.startswith('/') for name in listed)


def test_trades_shows_the_recent_closed_trades(bot):
    bot.desk.report['journal'] = [dict(TRIP)]
    say(bot, '/trades')
    text = bot.api.texts()[-1]
    for part in ('RECENT TRADES', 'H to L  Oil  PROFIT',
                 'Entry Spread</b>  <code>13.1960  (Z: +1.49)',
                 'Spread Chg</b>  <code>+0.0770',
                 'Net P&amp;L</b>  <code>+$3.85  PROFIT'):
        assert part in text, part


def test_the_CONTROL_no_trades_says_so(bot):
    say(bot, '/trades')
    assert 'No closed trades yet.' in bot.api.texts()[-1]


def test_pnl_sums_the_record(bot):
    loss = dict(TRIP, position_id='P2', pnl=-1.85, cum_pnl=2.0)
    bot.desk.report['journal'] = [loss, dict(TRIP)]
    bot.desk.snapshot['pnl_check'] = {'ours': -8.45}
    say(bot, '/pnl')
    text = bot.api.texts()[-1]
    for part in ('P&amp;L SUMMARY', 'Closed Trades</b>  <code>2',
                 'Win Rate</b>  <code>50.0%  (1W / 1L)',
                 'All-time Net</b>  <code>+$2.00',
                 'Unrealized</b>  <code>-$8.45'):
        assert part in text, part

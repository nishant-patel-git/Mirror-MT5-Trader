"""Telegram: watch the desk and drive the Algo from a phone.

A bot that lives in the WEB process and talks to the desk through the
web app itself, the same requests the browser makes. So every refusal,
every check and every "LIVE needs confirming" is the one the screen
already gets; there is no second way in with rules of its own.

What it may do, and how:

- **Who.** Only the Telegram user ids listed in `TELEGRAM_ALLOWED_USERS`
  get an answer. Anyone else is ignored — not told "no", ignored — and
  their id is logged once, so adding a colleague is a matter of reading
  the log. The bot token lives in `.env` and nowhere else, and never in
  a log line: Telegram puts it in the URL, so no URL is ever logged.
- **What.** Monitor (status, positions, the Algo panel), switch a
  ladder's Algo Off / Dry run / LIVE, change its Algo and exit
  settings, CLOSE ALL on a ladder, KILL ALL. Every one of those asks
  Confirm / Cancel first, and LIVE asks twice. It can never place a NEW
  order — there is no click, no level, no quantity in it — and a test
  holds it to the commands it is allowed to send.
- **Buttons expire.** A button is a short token, good for ten minutes.
  A confirm pressed on yesterday's menu does nothing but say so.
- **Alerts** are pushed to every allowed user: the Algo's entries and
  exits, problems (the engine stalling, an account dropping out, a
  stale or jumping price, a day's limit hit, a refusal), blocked
  signals (throttled), and a summary once a day at the session cutoff.
  `/alerts` turns them off and on per chat.

The desk's screen lock does not apply here: the lock guards the desk
PC, and the bot has its own guard — the allow-list. It passes the web
app's lock with a secret that is made fresh when the process starts and
never leaves it.
"""

import html
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = 'https://api.telegram.org'

#: The header that carries the in-process secret past the screen lock.
BOT_HEADER = 'X-MT5Trader-Bot'

#: How long a button stays good.
TOKEN_TTL_SEC = 600.0

#: How long a "send me the new value" prompt waits for the reply.
INPUT_TTL_SEC = 300.0

#: The commands the bot may send to the engine, and nothing else. No
#: `click`, no `close_at_limit`: it never opens a position.
ALLOWED_COMMANDS = frozenset({'set_algo', 'set_pair', 'flatten_pair', 'kill'})

#: The same blocked signal again within this long is not news.
BLOCKED_REPEAT_SEC = 900.0

#: A price problem has to last this long before it is worth a message:
#: a feed that is stale for one poll and back the next is not one.
PROBLEM_SETTLE_SEC = 20.0

DIRECTIONS = [('BOTH', 'Both'), ('H_TO_L', 'H to L only'),
              ('L_TO_H', 'L to H only')]
OVERNIGHT = [('ALLOW', 'Hold overnight'),
             ('EXIT_IF_PROFIT', 'Exit if in profit'),
             ('EXIT_ALWAYS', 'Exit anyway')]

#: What can be changed from the phone: the name, what the trader calls
#: it, where it is kept (the Algo's own numbers, or the pair's), and how
#: it is typed.
FIELDS = [
    ('entry_z', 'Entry z', 'algo', 'number'),
    ('direction', 'Direction', 'algo', DIRECTIONS),
    ('algo_qty', 'Algo qty (spreads)', 'algo', 'number'),
    ('warmup_min', 'Warm-up (min)', 'algo', 'number'),
    ('cutoff_buffer_min', 'Cutoff (min)', 'algo', 'number'),
    ('cooldown_min', 'Cooldown (min)', 'algo', 'number'),
    ('max_entry_z', 'Max entry z', 'algo', 'number'),
    ('stop_loss_on', 'Stop loss', 'algo', 'bool'),
    ('stop_loss_pct', 'SL % of margin', 'algo', 'number'),
    ('edge_on', 'Edge filter', 'algo', 'bool'),
    ('edge_multiple', 'Edge required (x)', 'algo', 'number'),
    ('regime_on', 'Regime filter', 'algo', 'bool'),
    ('max_trades_day', 'Max trades a day', 'algo', 'number'),
    ('max_losses_row', 'Max losses in a row', 'algo', 'number'),
    ('daily_loss_limit', 'Daily loss limit', 'algo', 'number'),
    ('tp_target_pct_of_margin', 'TP % of margin', 'pair', 'number'),
    ('commission_per_lot_a', 'Comm/lot A (per side)', 'pair', 'number'),
    ('commission_per_lot_b', 'Comm/lot B (per side)', 'pair', 'number'),
    ('overnight', 'Overnight', 'pair', OVERNIGHT),
]
FIELD = {f[0]: f for f in FIELDS}


def esc(value):
    return html.escape('' if value is None else str(value), quote=False)


def num(value, digits=2, signed=False):
    """A number for a message; unmeasured is a dash, never 0."""
    if value is None:
        return '—'
    try:
        value = float(value)
    except (TypeError, ValueError):
        return esc(value)
    return f'{value:+.{digits}f}' if signed else f'{value:.{digits}f}'


def money(value):
    if value is None:
        return '—'
    return f'{float(value):+,.2f}'


def mode_word(row):
    if not row.get('algo_on'):
        return 'OFF'
    return 'LIVE' if row.get('algo_mode') == 'LIVE' else 'DRY'


def effective_params(row):
    """The Algo settings IN FORCE, defaults included — what the desk's
    settings pane shows, not only what someone typed."""
    from . import algo as algo_module
    return algo_module.clean_params(row.get('algo_params'))


def side_words(side):
    return {'SELL': 'H to L', 'BUY': 'L to H'}.get(side, side or '?')


class TelegramError(Exception):
    pass


class TelegramAPI:
    """The Bot API over HTTPS, with the standard library only.

    The token is in every URL Telegram takes, so no URL — and no
    exception that might carry one — is ever logged or raised from
    here: only Telegram's own description of what went wrong.
    """

    def __init__(self, token, opener=None, timeout=40.0):
        self._token = token
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout

    def call(self, method, **params):
        url = f'{API_ROOT}/bot{self._token}/{method}'
        data = json.dumps({k: v for k, v in params.items()
                           if v is not None}).encode('utf-8')
        request = urllib.request.Request(
            url, data=data, headers={'Content-Type': 'application/json'})
        try:
            with self._open(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode('utf-8'))
            except Exception:
                body = {}
            raise TelegramError(f'{method}: HTTP {e.code} '
                                f'{body.get("description") or ""}'.strip())
        except urllib.error.URLError as e:
            raise TelegramError(f'{method}: cannot reach Telegram '
                                f'({type(e.reason).__name__})')
        except (OSError, ValueError) as e:
            raise TelegramError(f'{method}: {type(e).__name__}')
        if not body.get('ok'):
            raise TelegramError(f'{method}: {body.get("description")}')
        return body.get('result')


class WebDesk:
    """The desk, through the web app's own routes.

    In process, with Flask's test client: nothing goes over the network,
    and every request meets the checks a browser's would.
    """

    def __init__(self, app, secret, sleep=time.sleep, clock=time.time):
        self.client = app.test_client()
        self.headers = {BOT_HEADER: secret}
        self.sleep = sleep
        self.clock = clock

    def _get(self, path):
        response = self.client.get(path, headers=self.headers)
        return response.get_json(silent=True) or {}

    def _post(self, path, body):
        response = self.client.post(path, json=body, headers=self.headers)
        answer = response.get_json(silent=True) or {}
        if response.status_code >= 400 and 'ok' not in answer:
            answer['ok'] = False
        return answer

    def status(self):
        return self._get('/api/status')

    def config_pair(self, key):
        return ((self._get('/api/config').get('pairs') or {}).get(key)
                or {})

    def events(self, limit=50):
        return self._get(f'/api/events?limit={int(limit)}').get('events') \
            or []

    def save_pair(self, key, payload):
        return self._post('/api/pairs/' + urllib.parse.quote(key, safe=''),
                          payload)

    def command(self, kind, payload, wait=10.0):
        """Send one engine command and wait for its answer.

        `{'ok': True/False/None, 'reason': ..., 'data': ...}` — None
        when it was queued and the engine has not answered yet.
        """
        if kind not in ALLOWED_COMMANDS:
            raise ValueError(f'the Telegram bot does not send {kind!r}')
        sent = self._post('/api/command', {'kind': kind, 'payload': payload})
        if not sent.get('ok'):
            return {'ok': False, 'reason': sent.get('error') or 'refused'}
        deadline = self.clock() + wait
        while self.clock() < deadline:
            result = self._get(f'/api/result/{sent["id"]}')
            if not result.get('pending'):
                data = result.get('data')
                detail = data if isinstance(data, dict) else {}
                # The runner's ok is "the command ran"; the engine's own
                # answer inside it can still be a refusal.
                ok = bool(result.get('ok')) and detail.get('ok') is not False
                return {'ok': ok, 'data': data,
                        'reason': result.get('error') or detail.get('reason')}
            self.sleep(0.25)
        return {'ok': None, 'reason': 'sent — the engine has not answered '
                                      'yet; check the status in a moment'}


def allowed_users(raw):
    """`TELEGRAM_ALLOWED_USERS`: numeric ids, comma or space separated."""
    ids = set()
    for part in (raw or '').replace(',', ' ').split():
        try:
            ids.add(int(part))
        except ValueError:
            logging.warning('[telegram] %r in TELEGRAM_ALLOWED_USERS is not '
                            'a numeric user id — ignored', part)
    return ids


class Bot:
    """The menu, the confirms and the alerts."""

    def __init__(self, api, desk, allowed, clock=time.time):
        self.api = api
        self.desk = desk
        self.allowed = set(allowed)
        self.clock = clock
        self.offset = None
        self._tokens = {}
        self._waiting = {}          # chat -> (expires, pair key, field)
        self._ignored = set()
        self.alerts_off = set()     # chats that turned alerts off
        self.watch = AlertWatch(clock)
        self.lock = threading.Lock()

    # -- the transport --------------------------------------------------------

    def poll_once(self, timeout=25):
        updates = self.api.call('getUpdates', offset=self.offset,
                                timeout=timeout,
                                allowed_updates=['message',
                                                 'callback_query']) or []
        for update in updates:
            self.offset = update['update_id'] + 1
            try:
                with self.lock:
                    self.handle(update)
            except Exception as e:          # one bad update never stops it
                logging.exception('[telegram] update failed: %s', e)
        return len(updates)

    def send(self, chat, text, buttons=None):
        return self.api.call('sendMessage', chat_id=chat, text=text,
                             parse_mode='HTML',
                             disable_web_page_preview=True,
                             reply_markup=self._markup(buttons))

    def show(self, chat, message_id, text, buttons=None):
        """Replace a menu in place, rather than stacking up messages."""
        if message_id is None:
            return self.send(chat, text, buttons)
        try:
            return self.api.call('editMessageText', chat_id=chat,
                                 message_id=message_id, text=text,
                                 parse_mode='HTML',
                                 disable_web_page_preview=True,
                                 reply_markup=self._markup(buttons))
        except TelegramError as e:
            if 'not modified' in str(e):
                return None
            return self.send(chat, text, buttons)

    def _markup(self, buttons):
        if not buttons:
            return None
        return {'inline_keyboard': [
            [{'text': label, 'callback_data': self._token(action, args)}
             for label, action, args in row] for row in buttons]}

    def _token(self, action, args):
        now = self.clock()
        for token in [t for t, v in self._tokens.items() if v[0] < now]:
            del self._tokens[token]
        token = secrets.token_urlsafe(9)
        self._tokens[token] = (now + TOKEN_TTL_SEC, action, args)
        return token

    # -- one update -------------------------------------------------------------

    def handle(self, update):
        if 'callback_query' in update:
            query = update['callback_query']
            user = (query.get('from') or {}).get('id')
            if not self._may(user):
                return
            message = query.get('message') or {}
            chat = (message.get('chat') or {}).get('id', user)
            try:
                self.api.call('answerCallbackQuery',
                              callback_query_id=query.get('id'))
            except TelegramError:
                pass
            entry = self._tokens.pop(query.get('data'), None)
            if entry is None or entry[0] < self.clock():
                self.show(chat, message.get('message_id'),
                          'That button has expired. Here is the menu again.',
                          self._main_buttons(chat))
                return
            _, action, args = entry
            getattr(self, 'on_' + action)(chat, message.get('message_id'),
                                          *args)
            return
        message = update.get('message') or {}
        user = (message.get('from') or {}).get('id')
        if not self._may(user):
            return
        chat = (message.get('chat') or {}).get('id', user)
        text = (message.get('text') or '').strip()
        waiting = self._waiting.pop(chat, None)
        if waiting and waiting[0] >= self.clock() \
                and not text.startswith('/'):
            return self._typed_value(chat, waiting[1], waiting[2], text)
        command = text.split()[0].split('@')[0].lower() if text else ''
        if command == '/alerts':
            if chat in self.alerts_off:
                self.alerts_off.discard(chat)
                return self.send(chat, 'Alerts are <b>ON</b> for this chat.')
            self.alerts_off.add(chat)
            return self.send(chat, 'Alerts are <b>OFF</b> for this chat. '
                                   'Send /alerts to turn them back on.')
        if command == '/status':
            return self.on_status(chat, None)
        if command == '/positions':
            return self.on_positions(chat, None)
        if command == '/algo':
            return self.on_algo_list(chat, None)
        return self.on_main(chat, None)

    def _may(self, user):
        if user in self.allowed:
            return True
        if user not in self._ignored:
            self._ignored.add(user)
            logging.warning('[telegram] ignored a message from user id %s — '
                            'add it to TELEGRAM_ALLOWED_USERS in .env to '
                            'allow it', user)
        return False

    # -- reading the desk ------------------------------------------------------

    def _snapshot(self):
        snapshot = self.desk.status() or {}
        return snapshot, snapshot.get('pairs') or {}

    def _engine_line(self, snapshot):
        state = snapshot.get('engine') or 'down'
        if state == 'up':
            return '🟢 Engine running'
        note = snapshot.get('engine_note')
        return f'🔴 Engine {esc(state)}' + (f' — {esc(note)}' if note else '')

    def _pair_name(self, pairs, key):
        return esc((pairs.get(key) or {}).get('name') or key)

    def _main_buttons(self, chat):
        alerts = 'OFF' if chat in self.alerts_off else 'ON'
        return [[('📊 Status', 'status', ()), ('📈 Positions', 'positions', ())],
                [('🤖 Algo', 'algo_list', ()),
                 ('⚙️ Settings', 'settings_list', ())],
                [(f'🔔 Alerts: {alerts}', 'toggle_alerts', ()),
                 ('🔄 Refresh', 'main', ())],
                [('🛑 KILL ALL', 'kill_ask', ())]]

    # -- screens -----------------------------------------------------------------

    def on_main(self, chat, message_id):
        snapshot, pairs = self._snapshot()
        lines = ['<b>MT5-Trader</b>', self._engine_line(snapshot), '']
        for key, row in sorted(pairs.items()):
            pnl = row.get('open_pnl')
            lines.append(f'<b>{self._pair_name(pairs, key)}</b> · Algo '
                         f'{mode_word(row)} · net '
                         f'{num(row.get("net_position"), 2, True)}'
                         + (f' · P&amp;L {money(pnl)}' if pnl is not None
                            else ''))
        if not pairs:
            lines.append('No pairs are configured.')
        self.show(chat, message_id, '\n'.join(lines),
                  self._main_buttons(chat))

    def on_toggle_alerts(self, chat, message_id):
        if chat in self.alerts_off:
            self.alerts_off.discard(chat)
        else:
            self.alerts_off.add(chat)
        self.on_main(chat, message_id)

    def on_status(self, chat, message_id):
        snapshot, pairs = self._snapshot()
        lines = ['<b>Status</b>', self._engine_line(snapshot)]
        clock = snapshot.get('broker_clock') or {}
        if clock.get('broker_time'):
            lines.append(f'Broker time {esc(clock["broker_time"])} · '
                         f'cutoff {esc(clock.get("cutoff"))}')
        lines.append('')
        for name, info in sorted((snapshot.get('accounts') or {}).items()):
            if not info:
                lines.append(f'🔴 {esc(name)}: not answering')
                continue
            level = info.get('margin_level')
            lines.append(f'🟢 {esc(name)}: equity {num(info.get("equity"))}'
                         f' {esc(info.get("currency") or "")} · margin level '
                         + (f'{float(level):,.0f}%' if level else '—'))
        for name in snapshot.get('dark_accounts') or []:
            lines.append(f'🔴 {esc(name)}: its leg runner is not answering')
        for key, row in sorted(pairs.items()):
            lines.append('')
            lines.append(f'<b>{self._pair_name(pairs, key)}</b>')
            lines.append(f'H to L {num(row.get("short_spread"), 3)} · '
                         f'L to H {num(row.get("long_spread"), 3)}')
            problem = self._price_problem(row)
            if problem:
                lines.append(f'⚠️ {esc(problem)}')
            lines.append(f'Algo {mode_word(row)}')
        self.show(chat, message_id, '\n'.join(lines),
                  [[('🔄 Refresh', 'status', ()), ('⬅ Menu', 'main', ())]])

    def on_positions(self, chat, message_id):
        snapshot, pairs = self._snapshot()
        lines = ['<b>Positions</b>']
        buttons = []
        check = snapshot.get('pnl_check') or {}
        any_open = False
        for key, row in sorted(pairs.items()):
            positions = row.get('positions') or []
            if not positions:
                continue
            any_open = True
            lines.append('')
            lines.append(f'<b>{self._pair_name(pairs, key)}</b> · net '
                         f'{num(row.get("net_position"), 2, True)} · '
                         f'P&amp;L {money(row.get("open_pnl"))}')
            for position in positions:
                lines.append(
                    f'• {side_words(position.get("side"))} '
                    f'{num(position.get("quantity"), 2)} @ '
                    f'{num(position.get("entry_spread"), 3)} · now '
                    f'{num(position.get("closing_spread"), 3)} · '
                    f'{money(position.get("net_pnl"))} · '
                    f'{esc(position.get("source") or "MANUAL")}')
            buttons.append([(f'Close all: {row.get("name") or key}',
                             'flatten_ask', (key,))])
        if not any_open:
            lines.append('Nothing open.')
        if check.get('ours') is not None:
            lines.append('')
            lines.append(f'Total (gross) {money(check.get("ours"))} · '
                         f'MT5 says {money(check.get("theirs"))}')
        buttons.append([('🔄 Refresh', 'positions', ()),
                        ('⬅ Menu', 'main', ())])
        self.show(chat, message_id, '\n'.join(lines), buttons)

    def on_algo_list(self, chat, message_id):
        _, pairs = self._snapshot()
        buttons = [[(f'{row.get("name") or key} · {mode_word(row)}',
                     'algo_pair', (key,))]
                   for key, row in sorted(pairs.items())]
        buttons.append([('⬅ Menu', 'main', ())])
        self.show(chat, message_id, '<b>Algo</b> — pick a ladder', buttons)

    def on_algo_pair(self, chat, message_id, key):
        _, pairs = self._snapshot()
        row = pairs.get(key)
        if row is None:
            return self.show(chat, message_id, f'No pair {esc(key)}.',
                             [[('⬅ Menu', 'main', ())]])
        block = row.get('algo_block') or {}
        params = effective_params(row)
        entry = params.get('entry_z')
        lines = [f'<b>{self._pair_name(pairs, key)}</b> · Algo '
                 f'<b>{mode_word(row)}</b>']
        if row.get('algo_on'):
            lines.append(f'H to L {num(row.get("short_spread"), 3)} '
                         f'(<b>{num(block.get("z_sell"), 2, True)}</b>) · '
                         f'short at ≥ +{num(entry, 2)} '
                         f'({num(block.get("upper"), 3)})')
            lines.append(f'L to H {num(row.get("long_spread"), 3)} '
                         f'(<b>{num(block.get("z_buy"), 2, True)}</b>) · '
                         f'long at ≤ −{num(entry, 2)} '
                         f'({num(block.get("lower"), 3)})')
            state = block.get('state') or 'WATCHING'
            lines.append(f'State: {esc(state.lower().replace("_", " "))}'
                         + (f' — {esc(block.get("blocked"))}'
                            if block.get('blocked') else ''))
            warmup = block.get('warmup') or {}
            if warmup.get('need_sec'):
                lines.append('Warm-up: done' if warmup.get('done') else
                             f'Warm-up: {int((warmup.get("sec") or 0) // 60)}'
                             f'/{round(warmup["need_sec"] / 60)} min')
            filters = block.get('filters') or {}
            edge = filters.get('edge') or {}
            regime = filters.get('regime') or {}
            lines.append(
                'Edge ' + ('off' if not edge.get('on') else
                           ('✓' if edge.get('ok') else '✗')
                           + f' {num(edge.get("ratio"))}x/'
                             f'{num(edge.get("required"), 1)}x')
                + ' · Regime ' + ('off' if not regime.get('on') else
                                  esc(regime.get('state')))
                + ' · Ready ' + ('yes' if filters.get('ready') else 'no'))
            day = block.get('day') or {}
            lines.append(f'Today {day.get("trades") or 0}/'
                         f'{params.get("max_trades_day") or "∞"} trades · '
                         f'{day.get("losses_row") or 0} losses in a row · '
                         f'{money(day.get("pnl"))}')
            last = block.get('last_blocked')
            if last:
                lines.append(f'Last blocked: {side_words(last.get("side"))} '
                             f'{num(last.get("z"), 2, True)} — '
                             f'{esc(last.get("reason"))}')
        else:
            lines.append('The Algo is off on this ladder.')
        live = mode_word(row)
        mark = {m: ('✓ ' if live == m else '') for m in ('OFF', 'DRY', 'LIVE')}
        buttons = [[(mark['OFF'] + 'Off', 'algo_ask', (key, 'NONE')),
                    (mark['DRY'] + 'Dry run', 'algo_ask', (key, 'ALGO')),
                    (mark['LIVE'] + 'LIVE', 'algo_ask', (key, 'ALGO_LIVE'))],
                   [('⚙️ Settings', 'settings_pair', (key,)),
                    ('🔄 Refresh', 'algo_pair', (key,))],
                   [('⬅ Algo', 'algo_list', ()), ('⬅ Menu', 'main', ())]]
        self.show(chat, message_id, '\n'.join(lines), buttons)

    # -- the Algo switch ----------------------------------------------------------

    def on_algo_ask(self, chat, message_id, key, choice):
        _, pairs = self._snapshot()
        name = self._pair_name(pairs, key)
        words = {'NONE': 'OFF', 'ALGO': 'DRY RUN (signals only, sends '
                 'nothing)', 'ALGO_LIVE': 'LIVE'}[choice]
        if choice == 'ALGO_LIVE':
            text = (f'⚠️ <b>Algo LIVE on {name}?</b>\n\nIt will send REAL '
                    f'orders to both accounts on this ladder: MARKET in when '
                    f'the spread stretches past its band, out at the '
                    f'take-profit or the stop loss. Manual orders on this '
                    f'ladder are off while it runs.')
            return self.show(chat, message_id, text, [
                [('Yes, continue', 'algo_live_again', (key,)),
                 ('✖ Cancel', 'algo_pair', (key,))]])
        self.show(chat, message_id, f'Algo on <b>{name}</b> → <b>{words}</b>?',
                  [[('✅ Confirm', 'algo_do', (key, choice, None)),
                    ('✖ Cancel', 'algo_pair', (key,))]])

    def on_algo_live_again(self, chat, message_id, key):
        _, pairs = self._snapshot()
        self.show(chat, message_id,
                  f'🔴 <b>Confirm again: LIVE on '
                  f'{self._pair_name(pairs, key)}.</b>\nReal orders, '
                  f'real money.',
                  [[('🔴 GO LIVE', 'algo_do', (key, 'ALGO_LIVE', None)),
                    ('✖ Cancel', 'algo_pair', (key,))]])

    def on_algo_do(self, chat, message_id, key, choice, off_action):
        live = choice == 'ALGO_LIVE'
        payload = {'pair': key, 'algo': 'ALGO' if live else choice,
                   'mode': 'LIVE' if live else 'DRY_RUN'}
        if live:
            payload['confirmed'] = True
        if off_action:
            payload['off_action'] = off_action
        answer = self.desk.command('set_algo', payload)
        data = answer.get('data') if isinstance(answer.get('data'),
                                                dict) else {}
        if data.get('choose'):
            count = len(data.get('positions') or []) or 1
            return self.show(
                chat, message_id,
                f'The Algo holds {count} position(s) on this ladder. Close '
                f'now at market, by ticket — or hand to manual?',
                [[('Close it now', 'algo_do', (key, choice, 'close')),
                  ('Hand to manual', 'algo_do', (key, choice, 'manual'))],
                 [('✖ Cancel', 'algo_pair', (key,))]])
        self._report(chat, answer, 'Done.')
        self.on_algo_pair(chat, None, key)

    def _report(self, chat, answer, done):
        if answer.get('ok'):
            self.send(chat, f'✅ {done}')
        elif answer.get('ok') is None:
            self.send(chat, f'⏳ {esc(answer.get("reason"))}')
        else:
            self.send(chat, f'❌ Refused: {esc(answer.get("reason"))}')

    # -- closing --------------------------------------------------------------------

    def on_flatten_ask(self, chat, message_id, key):
        _, pairs = self._snapshot()
        row = pairs.get(key) or {}
        self.show(chat, message_id,
                  f'Close <b>every</b> position on '
                  f'<b>{self._pair_name(pairs, key)}</b> '
                  f'({num(row.get("net_position"), 2, True)} spreads) at '
                  f'market, by ticket? This cannot be undone.',
                  [[('✅ Close all', 'flatten_do', (key,)),
                    ('✖ Cancel', 'positions', ())]])

    def on_flatten_do(self, chat, message_id, key):
        answer = self.desk.command('flatten_pair', {'pair': key})
        data = answer.get('data') or {}
        failed = data.get('failed') if isinstance(data, dict) else None
        if answer.get('ok') and failed:
            answer = {'ok': False, 'reason': '; '.join(
                str(f.get('reason')) for f in failed)}
        self._report(chat, answer, f'Closed {data.get("closed", 0)} '
                                   f'position(s).' if isinstance(data, dict)
                     else 'Closed.')
        self.on_positions(chat, None)

    def on_kill_ask(self, chat, message_id):
        self.show(chat, message_id,
                  '🛑 <b>KILL ALL?</b>\nCancels every working order on every '
                  'ladder AND closes every open position at market, by '
                  'ticket. This cannot be undone.',
                  [[('🛑 KILL ALL', 'kill_do', ()),
                    ('✖ Cancel', 'main', ())]])

    def on_kill_do(self, chat, message_id):
        answer = self.desk.command('kill', {'flatten': True})
        self._report(chat, answer, 'Everything cancelled and flattened.')
        self.on_main(chat, None)

    # -- settings --------------------------------------------------------------------

    def on_settings_list(self, chat, message_id):
        _, pairs = self._snapshot()
        buttons = [[(row.get('name') or key, 'settings_pair', (key,))]
                   for key, row in sorted(pairs.items())]
        buttons.append([('⬅ Menu', 'main', ())])
        self.show(chat, message_id, '<b>Settings</b> — pick a ladder',
                  buttons)

    def _current(self, row, saved, field):
        name, _, where, _ = FIELD[field]
        if where == 'algo':
            return effective_params(row).get(name)
        if name == 'overnight':
            return row.get('overnight') or saved.get(name)
        value = saved.get(name)
        return 'default' if value is None else value

    @staticmethod
    def _shown(field, value):
        kind = FIELD[field][3]
        if kind == 'bool':
            return 'ON' if value else 'OFF'
        if isinstance(kind, list):
            return dict(kind).get(value, value)
        return value

    def on_settings_pair(self, chat, message_id, key):
        _, pairs = self._snapshot()
        row = pairs.get(key) or {}
        saved = self.desk.config_pair(key)
        buttons = []
        for field, label, _, _ in FIELDS:
            value = self._shown(field, self._current(row, saved, field))
            buttons.append([(f'{label}: {value}', 'edit', (key, field))])
        buttons.append([('⬅ Algo', 'algo_pair', (key,)),
                        ('⬅ Menu', 'main', ())])
        self.show(chat, message_id,
                  f'<b>Settings — {self._pair_name(pairs, key)}</b>\n'
                  f'Tap one to change it.', buttons)

    def on_edit(self, chat, message_id, key, field):
        _, label, _, kind = FIELD[field]
        _, pairs = self._snapshot()
        row = pairs.get(key) or {}
        now = self._shown(field, self._current(row, self.desk.config_pair(
            key), field))
        if kind == 'bool':
            options = [(True, 'ON'), (False, 'OFF')]
        elif isinstance(kind, list):
            options = kind
        else:
            self._waiting[chat] = (self.clock() + INPUT_TTL_SEC, key, field)
            return self.show(chat, message_id,
                             f'<b>{esc(label)}</b> is <b>{esc(now)}</b>.\n'
                             f'Send the new value as a message.',
                             [[('✖ Cancel', 'settings_pair', (key,))]])
        self.show(chat, message_id,
                  f'<b>{esc(label)}</b> is <b>{esc(now)}</b>. Change to:',
                  [[(word, 'set_ask', (key, field, value))
                    for value, word in options],
                   [('✖ Cancel', 'settings_pair', (key,))]])

    def _typed_value(self, chat, key, field, text):
        from . import algo as algo_module
        _, label, where, _ = FIELD[field]
        try:
            value = float(text.replace(',', '.'))
        except ValueError:
            return self.send(chat, f'❌ {esc(text)!s} is not a number — '
                                   f'nothing changed.')
        if field in algo_module._INTS:
            value = int(value)
        problems = (algo_module.check_params({field: value})
                    if where == 'algo'
                    else ([] if value >= 0 else
                          [f'{label} cannot be negative']))
        if problems:
            return self.send(chat, '❌ ' + esc('; '.join(problems))
                             + ' — nothing changed.')
        self.on_set_ask(chat, None, key, field, value)

    def on_set_ask(self, chat, message_id, key, field, value):
        _, pairs = self._snapshot()
        label = FIELD[field][1]
        self.show(chat, message_id,
                  f'<b>{self._pair_name(pairs, key)}</b>: '
                  f'{esc(label)} → <b>{esc(self._shown(field, value))}</b>?',
                  [[('✅ Confirm', 'set_do', (key, field, value)),
                    ('✖ Cancel', 'settings_pair', (key,))]])

    def on_set_do(self, chat, message_id, key, field, value):
        """Save it the way the settings pane does: written to the pair,
        then sent to the running engine so it applies now."""
        where = FIELD[field][2]
        if where == 'algo':
            typed = dict(self.desk.config_pair(key).get('algo_params') or {})
            typed[field] = value
            payload = {'algo_params': typed}
        else:
            payload = {field: value}
        saved = self.desk.save_pair(key, payload)
        if not saved.get('ok'):
            self.send(chat, f'❌ Not saved: {esc(saved.get("error"))}')
            return self.on_settings_pair(chat, None, key)
        answer = self.desk.command('set_pair', {'pair': key,
                                                'fields': payload})
        for note in saved.get('notes') or []:
            self.send(chat, f'ℹ️ {esc(note)}')
        self._report(chat, answer, f'{FIELD[field][1]} is now '
                                   f'{self._shown(field, value)}.')
        self.on_settings_pair(chat, None, key)

    # -- alerts -----------------------------------------------------------------------

    def _price_problem(self, row):
        market = row.get('market') or {}
        return (row.get('dark_reason') or market.get('stale_reason')
                or market.get('jump_reason') or market.get('guard_reason'))

    def alert_once(self):
        """Look once, and push what is new to every allowed user."""
        snapshot = self.desk.status() or {}
        events = self.desk.events(limit=50)
        messages = self.watch.check(snapshot, events, self._price_problem)
        for text in messages:
            for user in sorted(self.allowed):
                if user in self.alerts_off:
                    continue
                try:
                    self.send(user, text)
                except TelegramError as e:
                    logging.warning('[telegram] alert to %s not sent: %s',
                                    user, e)
        return messages


class AlertWatch:
    """What has changed since the last look, as messages.

    The first look only takes its bearings: history already in the
    journal and problems already on screen when the bot starts are not
    news.
    """

    def __init__(self, clock=time.time):
        self.clock = clock
        self.started = False
        self.last_event = 0
        self.engine = None
        self.dark = set()
        self.problems = {}          # pair -> (problem, since, told)
        self.errors = {}
        self.halts = {}
        self.blocked = {}           # pair -> (at, (side, reason), told_at)
        self.summary_day = None

    def check(self, snapshot, events, price_problem):
        out = []
        now = self.clock()
        pairs = snapshot.get('pairs') or {}
        new_events = sorted((e for e in events or ()
                             if (e.get('id') or 0) > self.last_event),
                            key=lambda e: e.get('id') or 0)
        if new_events:
            self.last_event = new_events[-1].get('id') or self.last_event
        first = not self.started
        self.started = True

        # The engine itself.
        engine = snapshot.get('engine') or 'down'
        if not first and engine != self.engine:
            out.append('✅ Engine running again.' if engine == 'up' else
                       f'🚨 Engine {esc(engine)}: '
                       f'{esc(snapshot.get("engine_note"))}')
        self.engine = engine
        dark = set(snapshot.get('dark_accounts') or ())
        if not first:
            for name in sorted(dark - self.dark):
                out.append(f'🚨 Account {esc(name)} is not answering.')
            for name in sorted(self.dark - dark):
                out.append(f'✅ Account {esc(name)} is back.')
        self.dark = dark

        for key, row in pairs.items():
            name = esc(row.get('name') or key)
            # A price problem, once it has lasted.
            problem = price_problem(row)
            was = self.problems.get(key)
            if problem:
                if was is None or was[0] != problem:
                    self.problems[key] = (problem, now, first)
                elif not was[2] and now - was[1] >= PROBLEM_SETTLE_SEC:
                    out.append(f'⚠️ {name}: {esc(problem)}')
                    self.problems[key] = (problem, was[1], True)
            elif was is not None:
                if was[2] and not first:
                    out.append(f'✅ {name}: prices are back to normal.')
                del self.problems[key]
            # Engine errors on the pair.
            errors = set(row.get('errors') or ())
            if not first:
                for error in sorted(errors - self.errors.get(key, set())):
                    out.append(f'🚨 {name}: {esc(error)}')
            self.errors[key] = errors
            block = row.get('algo_block') or {}
            # A day's limit reached.
            halt = block.get('halt')
            if halt and halt != self.halts.get(key) and not first:
                out.append(f'⛔ {name}: no more Algo entries today — '
                           f'{esc(halt)}')
            self.halts[key] = halt
            # A signal held back.
            last = block.get('last_blocked')
            if last and last.get('at'):
                seen = self.blocked.get(key)
                what = (last.get('side'), last.get('reason'))
                if seen is None or seen[0] != last['at']:
                    told = seen[2] if seen else None
                    repeat = (seen is not None and seen[1] == what
                              and told is not None
                              and now - told < BLOCKED_REPEAT_SEC)
                    if not first and not repeat:
                        out.append(
                            f'⏸ {name}: {side_words(last.get("side"))} '
                            f'signal at z {num(last.get("z"), 2, True)} held '
                            f'back — {esc(last.get("reason"))}')
                        told = now
                    self.blocked[key] = (last['at'], what, told)

        for event in new_events if not first else ():
            text = self._event(event, pairs)
            if text:
                out.append(text)

        # Once a day, at the session cutoff on the broker's clock.
        clock = snapshot.get('broker_clock') or {}
        broker_time, cutoff = clock.get('broker_time'), clock.get('cutoff')
        today = time.strftime('%Y-%m-%d', time.localtime(now))
        if broker_time and cutoff and broker_time[:5] >= cutoff \
                and self.summary_day != today:
            if not first:
                out.append(self._summary(snapshot))
            self.summary_day = today
        return out

    @staticmethod
    def _event(event, pairs):
        kind = event.get('kind')
        detail = event.get('detail') or {}
        key = event.get('pair_key')
        name = esc((pairs.get(key) or {}).get('name') or key or '')
        if kind == 'algo_signal':
            mode = 'LIVE' if detail.get('mode') == 'LIVE' else 'DRY'
            action = detail.get('action')
            if action == 'ENTER':
                return (f'🤖 [{mode}] {name}: ENTER '
                        f'{side_words(detail.get("side"))} at '
                        f'{num(detail.get("spread"), 3)} (z '
                        f'{num(detail.get("z"), 2, True)})')
            return (f'🤖 [{mode}] {name}: EXIT '
                    f'{side_words(detail.get("side"))} — '
                    f'{esc(detail.get("reason") or "")}'
                    + (f' · P&amp;L {money(detail.get("net_pnl"))}'
                       if detail.get('net_pnl') is not None else ''))
        if kind == 'algo_order':
            if detail.get('ok'):
                return (f'✅ LIVE {name}: {esc(detail.get("action"))} '
                        f'{side_words(detail.get("side"))} done')
            return (f'❌ LIVE {name}: {esc(detail.get("action"))} '
                    f'{side_words(detail.get("side"))} FAILED — '
                    f'{esc(detail.get("reason"))}')
        if kind == 'algo_switch':
            return (f'🔀 {name}: Algo → {esc(detail.get("algo"))} '
                    f'{esc(detail.get("mode") or "")}').strip()
        if kind == 'refused':
            return f'⚠️ {name}: refused — {esc(detail.get("reason"))}'
        if kind in ('reconcile', 'unclaimed_closed', 'recovery', 'adopted'):
            summary = ', '.join(f'{k}={v}' for k, v in list(detail.items())[:4])
            return f'ℹ️ {esc(kind)} {name}: {esc(summary)}'
        return None

    @staticmethod
    def _summary(snapshot):
        lines = ['📋 <b>Daily summary</b> (session cutoff)']
        for key, row in sorted((snapshot.get('pairs') or {}).items()):
            block = row.get('algo_block') or {}
            day = block.get('day') or {}
            lines.append(f'<b>{esc(row.get("name") or key)}</b> · Algo '
                         f'{mode_word(row)} · {day.get("trades") or 0} '
                         f'trade(s) · {money(day.get("pnl"))} · net '
                         f'{num(row.get("net_position"), 2, True)} · open '
                         f'P&amp;L {money(row.get("open_pnl"))}')
        return '\n'.join(lines)


def start(app, env=None, api=None):
    """Start the bot beside the web app, if `.env` asks for one.

    Returns the Bot, or None when there is no token. Two daemon threads:
    one long-polls Telegram for button presses and messages, one looks
    for alerts every few seconds. Neither can stop the web app.
    """
    env = os.environ if env is None else env
    token = (env.get('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        return None
    users = allowed_users(env.get('TELEGRAM_ALLOWED_USERS'))
    if not users:
        logging.warning('[telegram] TELEGRAM_BOT_TOKEN is set but '
                        'TELEGRAM_ALLOWED_USERS is empty — the bot will '
                        'answer nobody')
    secret = secrets.token_urlsafe(24)
    app.config['BOT_SECRET'] = secret
    bot = Bot(api or TelegramAPI(token), WebDesk(app, secret), users)

    def loop(work, pause, name):
        backoff = 5.0
        while True:
            try:
                work()
                backoff = 5.0
            except Exception as e:
                # Telegram down, the network out: wait longer each time,
                # up to a minute, and say so once per failure.
                logging.warning('[telegram] %s: %s — retrying in %.0fs',
                                name, e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            if pause:
                time.sleep(pause)

    threading.Thread(target=loop, args=(bot.poll_once, 0, 'updates'),
                     name='telegram-updates', daemon=True).start()

    def alerts():
        with bot.lock:
            bot.alert_once()

    threading.Thread(target=loop, args=(alerts, 5.0, 'alerts'),
                     name='telegram-alerts', daemon=True).start()
    logging.info('[telegram] bot started for %d allowed user(s)', len(users))
    return bot

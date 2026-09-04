"""The setup wizard: six questions, then `config.json` and `.env`.

This replaces the Exchanges page for a first install. A trader who has
never seen the app answers two logins, two passwords, two servers and
one instrument pair, and the machine is configured.

Three rules shape every line below.

**It never invents a format.** The `.env` key, the free port, and the
three clash refusals all come from `mt5trader.config` - the same
functions the Exchanges page calls. A wizard that hand-rolls
`MT5_PASSWORD_` + name is a wizard that writes a key the loader does
not read, and the symptom is a password that is silently never there.

**It never overwrites a working config.** A machine that is already
trading gets a refusal, not a fresh file. `save_raw` keeps a `.bak`
either way, but the refusal is what stops the mistake.

**Passwords go to `.env` and nowhere else.** Not into `config.json`,
not into a print, not into the exception text. `write_env_value` quotes
them so a password with a space or a `#` survives.

Run it from the repo root:

    py -3.11 deploy\\configure.py

or, for an unattended install, with the answers in a file:

    py -3.11 deploy\\configure.py --answers answers.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5trader import config as cfg                             # noqa: E402
from mt5trader.hedgeratio import pair_signature                 # noqa: E402


class SetupError(Exception):
    """A refusal the operator can act on. The message is shown as-is, so
    it says what is wrong and what to type instead - never 'check the
    log', which on a trader's PC means nothing."""


#: The instruments the office trades, so the common case is a choice
#: and not two symbol names typed from memory. `pair_type` matters:
#: SPOT_FUTURE and FUTURE_FUTURE are the SAME underlying, so the spread
#: is a basis with a fair value; RELATED has none.
PRESETS = {
    'Gold basis (XAUUSD.f vs GCZ6)': {
        'symbol_a': 'XAUUSD.f', 'symbol_b': 'GCZ6',
        'pair_type': 'SPOT_FUTURE',
        'name': 'Gold spot vs future',
    },
    'Gold basis (XAUUSD_ vs GC1226)': {
        'symbol_a': 'XAUUSD_', 'symbol_b': 'GC1226',
        'pair_type': 'SPOT_FUTURE',
        'name': 'Gold spot vs Dec future',
    },
    'Silver basis (XAGUSD.f vs SIZ6)': {
        'symbol_a': 'XAGUSD.f', 'symbol_b': 'SIZ6',
        'pair_type': 'SPOT_FUTURE',
        'name': 'Silver spot vs future',
    },
}

#: What the wizard calls the two accounts. The name is what
#: `env_key_for` turns into the `.env` key, so changing it here changes
#: the key - which is exactly why nothing else may guess the key.
ACCOUNT_A = 'Account A'
ACCOUNT_B = 'Account B'

#: Where SETUP.bat unzips the two terminals. Two FOLDERS, never one
#: folder and a shortcut: a terminal holds one login, so two runners on
#: one installation are one account trading against itself.
DEFAULT_TERMINAL_A = r'C:\MT5-A\terminal64.exe'
DEFAULT_TERMINAL_B = r'C:\MT5-B\terminal64.exe'


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _example_raw(root):
    try:
        with open(os.path.join(root, 'config.example.json'),
                  'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_pristine(raw, example):
    """Is this config still the shipped example, or somebody's work?

    `start.py` copies `config.example.json` to `config.json` on a first
    run, so "the file exists" is NOT evidence that anyone configured
    anything. Replacing the example is right; replacing a desk's real
    accounts is the mistake this exists to prevent.
    """
    if not raw:
        return True
    if not (raw.get('accounts') or raw.get('pairs')):
        return True
    return (raw.get('accounts') == (example.get('accounts') or {})
            and raw.get('pairs') == (example.get('pairs') or {}))


def _need(value, what):
    text = str(value or '').strip()
    if not text:
        raise SetupError(f'{what} is blank. Every field on this page is '
                         f'needed before the engine can connect.')
    return text


def _login(value, which):
    text = _need(value, f'{which} login')
    try:
        return int(text)
    except ValueError:
        raise SetupError(
            f"{which} login {text!r} is not a number. An MT5 login is the "
            f"digits the broker gave you (for example 10006), not the "
            f"e-mail address you sign in to their website with.") from None


def build_config(answers, raw, example=None):
    """The new config as a dict, or a `SetupError` saying why not.

    Pure: it reads nothing and writes nothing. That is what lets the
    guards below be tested - each one with a control that turns it off
    and asserts the save goes through.
    """
    example = example or {}
    raw = dict(raw or {})

    login_a = _login(answers.get('login_a'), 'Account A')
    login_b = _login(answers.get('login_b'), 'Account B')
    server_a = _need(answers.get('server_a'), 'Account A server')
    server_b = _need(answers.get('server_b'), 'Account B server')
    _need(answers.get('password_a'), 'Account A password')
    _need(answers.get('password_b'), 'Account B password')
    symbol_a = _need(answers.get('symbol_a'), 'Leg A symbol')
    symbol_b = _need(answers.get('symbol_b'), 'Leg B symbol')
    terminal_a = _need(answers.get('terminal_a') or DEFAULT_TERMINAL_A,
                       'Account A terminal')
    terminal_b = _need(answers.get('terminal_b') or DEFAULT_TERMINAL_B,
                       'Account B terminal')

    if login_a == login_b:
        raise SetupError(
            f'Both accounts have login {login_a}. That is one MT5 account '
            f'entered twice - both legs would trade it and the pair would '
            f'hedge against itself. Account B needs the SECOND account the '
            f'broker gave you.')
    if terminal_a.strip().lower() == terminal_b.strip().lower():
        raise SetupError(
            f'Both accounts point at {terminal_a}. One MetaTrader 5 '
            f'installation holds ONE login, so both legs would end up on '
            f'the same account. Give Account B its own folder - SETUP '
            f'makes {DEFAULT_TERMINAL_B} for exactly this.')
    if symbol_a.strip().lower() == symbol_b.strip().lower():
        raise SetupError(
            f'Both legs are {symbol_a}. A spread of an instrument against '
            f'itself is always zero - Leg B must be the other contract.')

    # Settings only. The example's ACCOUNTS and PAIRS are placeholders
    # with a fictional broker in them; carrying those across would leave
    # a row on the screen that can never connect.
    settings = raw.get('settings') or example.get('settings') or {}

    accounts = dict(raw.get('accounts') or {})
    pairs = dict(raw.get('pairs') or {})
    if is_pristine(raw, example):
        accounts, pairs = {}, {}
    # Drop the two rows we are about to write BEFORE asking for a free
    # port, or a re-run reads this machine's own endpoints as taken and
    # walks the pair up to 9103, 9105, 9107 a run at a time.
    accounts.pop(ACCOUNT_A, None)
    accounts.pop(ACCOUNT_B, None)

    endpoint_a = (answers.get('endpoint_a') or '').strip()
    endpoint_b = (answers.get('endpoint_b') or '').strip()
    accounts[ACCOUNT_A] = {
        'terminal_path': terminal_a,
        'login': login_a,
        'password_env': cfg.env_key_for(ACCOUNT_A),
        'server': server_a,
        # One port serves ONE leg runner. Asking the config for a free
        # one keeps this right even on a box that already had accounts.
        'endpoint': endpoint_a or cfg.next_free_port({'accounts': accounts}),
    }
    accounts[ACCOUNT_B] = {
        'terminal_path': terminal_b,
        'login': login_b,
        'password_env': cfg.env_key_for(ACCOUNT_B),
        'server': server_b,
        'endpoint': endpoint_b or cfg.next_free_port({'accounts': accounts}),
    }

    staged = {'accounts': accounts}
    for name in (ACCOUNT_A, ACCOUNT_B):
        acct = accounts[name]
        for clash in (cfg.endpoint_clash(staged, name, acct['endpoint']),
                      cfg.login_clash(staged, name, acct['login']),
                      cfg.terminal_clash(staged, name, acct['terminal_path'])):
            if clash:
                raise SetupError(clash)

    key = f'{symbol_a}|{symbol_b}'
    pairs[key] = {
        'name': answers.get('name') or key,
        'leg_a': {'account': ACCOUNT_A, 'symbol': symbol_a},
        'leg_b': {'account': ACCOUNT_B, 'symbol': symbol_b},
        'pair_type': cfg.pair_type_name(answers.get('pair_type')),
        # Beta is stamped with what it was computed FOR, so a stale one
        # from another instrument cannot silently define the spread.
        'hedge_ratio': 1.0,
        'hedge_ratio_for': pair_signature(symbol_a, symbol_b),
        # Deliberately unset: the app reads tick size, contract size and
        # lot steps from MT5 on the first connect. A number typed here
        # is a number that can be wrong, and every money figure on the
        # screen runs through it.
        'increment': None,
        'clip_lots_a': None,
        'clip_lots_b': None,
        'default_quantity': 1.0,
        'order_type': 'LIMIT',
        'time_in_force': 'DAY',
        'overnight': 'ALLOW',
        'quoting_leg': None,
        'enabled': True,
        'rows': 30,
    }

    out = dict(raw)
    out['accounts'] = accounts
    out['pairs'] = pairs
    out['settings'] = settings
    return out


def apply_config(answers, root=None, force=False):
    """Write `config.json` and `.env`. Returns the paths written.

    Refuses a machine that is already configured unless `force` - and
    `save_raw` keeps a `.bak` beside the file even then.
    """
    root = root or repo_root()
    config_path = os.path.join(root, 'config.json')
    env_path = os.path.join(root, '.env')
    example = _example_raw(root)
    raw = cfg.load_raw(config_path)

    if not force and not is_pristine(raw, example):
        names = ', '.join(sorted((raw.get('accounts') or {}))) or 'none'
        raise SetupError(
            f'{config_path} already has accounts on it ({names}). This '
            f'machine is set up. Re-run with --force to replace it - the '
            f'old file is kept as config.json.bak.')

    out = build_config(answers, raw, example)
    cfg.save_raw(config_path, out)

    # LAST, and only here. A password must never reach config.json, a
    # print, or an exception message.
    for name, field in ((ACCOUNT_A, 'password_a'), (ACCOUNT_B, 'password_b')):
        cfg.write_env_value(env_path, cfg.env_key_for(name),
                            answers.get(field))
    return config_path, env_path


# --- the window ---------------------------------------------------------

def _run_gui(root, terminal_a, terminal_b):          # pragma: no cover
    """Six questions in one window. No menus, no tabs, no scrolling.

    The two terminal folders are NOT among the six: SETUP made them and
    passes them in. A trader asked to type a path is a trader who can
    type the same path twice, and two accounts on one installation is
    the failure this whole setup is arranged to prevent.
    """
    import tkinter as tk
    from tkinter import messagebox, ttk

    win = tk.Tk()
    win.title('MT5-Trader setup')
    win.resizable(False, False)
    pad = {'padx': 8, 'pady': 4}
    fields = {}

    def account_frame(text, grid_row, keys):
        """One account: login, password, server. The grid row is the
        position WITHIN this frame, so the two frames cannot drift."""
        frame = ttk.LabelFrame(win, text=text)
        frame.grid(row=grid_row, column=0, sticky='ew', padx=10, pady=6)
        for line, (label, key, show) in enumerate(keys):
            ttk.Label(frame, text=label).grid(row=line, column=0,
                                              sticky='e', **pad)
            entry = ttk.Entry(frame, width=32, show=show)
            entry.grid(row=line, column=1, sticky='w', **pad)
            fields[key] = entry

    account_frame('Account A  (Leg A)', 0,
                  [('Login', 'login_a', None),
                   ('Password', 'password_a', '*'),
                   ('Server', 'server_a', None)])
    account_frame('Account B  (Leg B)', 1,
                  [('Login', 'login_b', None),
                   ('Password', 'password_b', '*'),
                   ('Server', 'server_b', None)])

    frame_p = ttk.LabelFrame(win, text='What this desk trades')
    frame_p.grid(row=2, column=0, sticky='ew', padx=10, pady=6)
    ttk.Label(frame_p, text='Pair').grid(row=0, column=0, sticky='e', **pad)
    choice = ttk.Combobox(frame_p, width=30, state='readonly',
                          values=list(PRESETS) + ['Type my own...'])
    choice.current(0)
    choice.grid(row=0, column=1, sticky='w', **pad)
    ttk.Label(frame_p, text='Leg A symbol').grid(row=1, column=0, sticky='e',
                                                 **pad)
    sym_a = ttk.Entry(frame_p, width=30)
    sym_a.grid(row=1, column=1, sticky='w', **pad)
    ttk.Label(frame_p, text='Leg B symbol').grid(row=2, column=0, sticky='e',
                                                 **pad)
    sym_b = ttk.Entry(frame_p, width=30)
    sym_b.grid(row=2, column=1, sticky='w', **pad)

    def fill(*_):
        preset = PRESETS.get(choice.get())
        sym_a.delete(0, 'end')
        sym_b.delete(0, 'end')
        if preset:
            sym_a.insert(0, preset['symbol_a'])
            sym_b.insert(0, preset['symbol_b'])
    choice.bind('<<ComboboxSelected>>', fill)
    fill()

    def save():
        preset = PRESETS.get(choice.get()) or {}
        answers = {key: entry.get() for key, entry in fields.items()}
        answers.update({'symbol_a': sym_a.get(), 'symbol_b': sym_b.get(),
                        'pair_type': preset.get('pair_type', 'SPOT_FUTURE'),
                        'name': preset.get('name'),
                        'terminal_a': terminal_a, 'terminal_b': terminal_b})
        try:
            config_path, _ = apply_config(answers, root)
        except SetupError as e:
            messagebox.showerror('Not saved', str(e))
            return
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror('Not saved', f'{type(e).__name__}: {e}')
            return
        messagebox.showinfo(
            'Saved',
            f'This machine is configured.\n\n{config_path}\n\n'
            f'Double-click START TRADING on the Desktop to begin.')
        win.destroy()

    ttk.Button(win, text='Save and finish', command=save).grid(
        row=3, column=0, pady=10)
    win.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answers',
                        help='JSON file of answers, for an unattended '
                             'install. Without it a window is shown.')
    parser.add_argument('--root', default=repo_root(),
                        help='The MT5-Trader folder to configure.')
    parser.add_argument('--force', action='store_true',
                        help='Replace a config that already has accounts. '
                             'The old one is kept as config.json.bak.')
    parser.add_argument('--terminal-a', default=DEFAULT_TERMINAL_A,
                        help='Account A terminal64.exe, as SETUP unpacked '
                             'it.')
    parser.add_argument('--terminal-b', default=DEFAULT_TERMINAL_B,
                        help='Account B terminal64.exe. Must be a DIFFERENT '
                             'folder from Account A.')
    args = parser.parse_args(argv)

    if not args.answers:
        try:
            return _run_gui(args.root, args.terminal_a, args.terminal_b)
        except ImportError:
            print('This Python has no tkinter, so the window cannot open.\n'
                  'Write the answers to a file and pass --answers instead.',
                  file=sys.stderr)
            return 2

    with open(args.answers, 'r', encoding='utf-8') as f:
        answers = json.load(f)
    # The answers file wins; the flags are what SETUP knows about this
    # machine, which is better than the built-in default.
    answers.setdefault('terminal_a', args.terminal_a)
    answers.setdefault('terminal_b', args.terminal_b)
    try:
        config_path, env_path = apply_config(answers, args.root, args.force)
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 1
    # The paths, never the values: one of those files is passwords.
    print(f'Wrote {config_path}')
    print(f'Wrote {env_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

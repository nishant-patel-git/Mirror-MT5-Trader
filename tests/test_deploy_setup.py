"""The setup wizard and the start-up preflight.

These two run on a machine nobody is watching, in front of somebody who
cannot read a traceback, and what they write is the file the engine
trades from. So every refusal below is paired with a CONTROL that turns
the same guard off and asserts the opposite — a test that only ever
sees a refusal cannot tell a working guard from a function that refuses
everything.
"""

import importlib.util
import json
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / 'deploy'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = _load('configure')
preflight = _load('preflight')

PASSWORD_A = 'a-secret-with a space and #hash'
PASSWORD_B = 'b-secret'


def answers(**over):
    base = {
        'login_a': '10006', 'password_a': PASSWORD_A,
        'server_a': 'MentoMarkets-Server',
        'login_b': '10007', 'password_b': PASSWORD_B,
        'server_b': 'MentoMarkets-Server',
        'symbol_a': 'XAUUSD.f', 'symbol_b': 'GCZ6',
        'pair_type': 'SPOT_FUTURE',
        'terminal_a': r'C:\MT5-A\terminal64.exe',
        'terminal_b': r'C:\MT5-B\terminal64.exe',
    }
    base.update(over)
    return base


# --- what a good run writes ---------------------------------------------

def test_the_wizard_writes_a_config_the_app_can_read():
    out = configure.build_config(answers(), {})
    account_a = out['accounts']['Account A']
    account_b = out['accounts']['Account B']

    assert account_a['login'] == 10006
    assert account_b['login'] == 10007
    # One port each. Two accounts on one port is one leg runner serving
    # both legs, which is the same terminal twice.
    assert account_a['endpoint'] != account_b['endpoint']
    assert account_a['terminal_path'] != account_b['terminal_path']

    pair = out['pairs']['XAUUSD.f|GCZ6']
    assert pair['leg_a'] == {'account': 'Account A', 'symbol': 'XAUUSD.f'}
    assert pair['leg_b'] == {'account': 'Account B', 'symbol': 'GCZ6'}
    assert pair['enabled'] is True
    # Beta stamped with what it was computed FOR, so a stale one from
    # another instrument cannot silently define the spread.
    assert pair['hedge_ratio_for'] == 'XAUUSD.f|GCZ6'


def test_the_env_key_comes_from_the_app_not_from_the_wizard():
    """A wizard that spells the key itself writes one the loader does
    not read, and the symptom is a password that is silently never
    there."""
    from mt5trader.config import env_key_for
    out = configure.build_config(answers(), {})
    assert out['accounts']['Account A']['password_env'] == \
        env_key_for('Account A')
    assert out['accounts']['Account B']['password_env'] == \
        env_key_for('Account B')


def test_the_instrument_details_are_left_for_mt5_to_answer():
    """Tick size, contract size and lot steps are READ from the broker.
    A number typed at setup is a number that can be wrong, and every
    money figure on the ladder runs through it."""
    pair = configure.build_config(answers(), {})['pairs']['XAUUSD.f|GCZ6']
    assert pair['increment'] is None
    assert pair['clip_lots_a'] is None
    assert pair['clip_lots_b'] is None


# --- the refusals, each with its control --------------------------------

def test_one_login_on_both_accounts_is_refused():
    with pytest.raises(configure.SetupError, match='hedge against itself'):
        configure.build_config(answers(login_b='10006'), {})


def test_control_two_logins_are_accepted():
    out = configure.build_config(answers(login_b='10007'), {})
    assert out['accounts']['Account B']['login'] == 10007


def test_one_terminal_folder_for_both_accounts_is_refused():
    with pytest.raises(configure.SetupError, match='ONE login'):
        configure.build_config(
            answers(terminal_b=r'C:\MT5-A\terminal64.exe'), {})


def test_control_two_terminal_folders_are_accepted():
    out = configure.build_config(
        answers(terminal_b=r'C:\MT5-B\terminal64.exe'), {})
    assert out['accounts']['Account B']['terminal_path'] == \
        r'C:\MT5-B\terminal64.exe'


def test_the_same_symbol_on_both_legs_is_refused():
    """A spread of an instrument against itself is always zero."""
    with pytest.raises(configure.SetupError, match='always zero'):
        configure.build_config(answers(symbol_b='XAUUSD.f'), {})


def test_control_two_symbols_are_accepted():
    out = configure.build_config(answers(symbol_b='GCZ6'), {})
    assert 'XAUUSD.f|GCZ6' in out['pairs']


@pytest.mark.parametrize('field', ['login_a', 'password_a', 'server_a',
                                   'login_b', 'password_b', 'server_b',
                                   'symbol_a', 'symbol_b'])
def test_a_blank_answer_is_refused(field):
    with pytest.raises(configure.SetupError):
        configure.build_config(answers(**{field: ''}), {})


def test_control_every_answer_filled_in_is_accepted():
    assert configure.build_config(answers(), {})['accounts']


def test_an_email_address_where_the_login_goes_is_refused():
    """The commonest wrong answer: brokers e-mail the account details,
    so the address is what is on the screen when the trader types."""
    with pytest.raises(configure.SetupError, match='digits the broker'):
        configure.build_config(answers(login_a='trader@example.com'), {})


def test_control_the_digits_are_accepted():
    out = configure.build_config(answers(login_a='10006'), {})
    assert out['accounts']['Account A']['login'] == 10006


# --- not overwriting a desk that is already trading ----------------------

def test_a_configured_machine_is_not_overwritten(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({
        'accounts': {'Real Desk': {'login': 55555, 'endpoint': '127.0.0.1:9101'}},
        'pairs': {}}), encoding='utf-8')
    with pytest.raises(configure.SetupError, match='already has accounts'):
        configure.apply_config(answers(), str(tmp_path))
    # And it is still there, untouched.
    still = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'Real Desk' in still['accounts']


def test_control_force_replaces_it_and_keeps_a_backup(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({
        'accounts': {'Real Desk': {'login': 55555, 'endpoint': '127.0.0.1:9101'}},
        'pairs': {}}), encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path), force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'Account A' in now['accounts']
    backup = json.loads((tmp_path / 'config.json.bak').read_text(
        encoding='utf-8'))
    assert 'Real Desk' in backup['accounts']


def test_the_shipped_example_is_replaced_without_being_asked(tmp_path):
    """`start.py` copies config.example.json on a first run, so the file
    EXISTING is not evidence that anyone configured anything."""
    example = json.loads(
        (Path(__file__).resolve().parent.parent
         / 'config.example.json').read_text(encoding='utf-8'))
    (tmp_path / 'config.example.json').write_text(json.dumps(example),
                                                  encoding='utf-8')
    (tmp_path / 'config.json').write_text(json.dumps(example),
                                          encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert set(now['accounts']) == {'Account A', 'Account B'}
    # The example's fictional broker rows are gone, not sitting on the
    # screen as two accounts that can never connect.
    assert 'leg_a' not in now['accounts']


def test_the_settings_block_survives(tmp_path):
    example = json.loads(
        (Path(__file__).resolve().parent.parent
         / 'config.example.json').read_text(encoding='utf-8'))
    (tmp_path / 'config.example.json').write_text(json.dumps(example),
                                                  encoding='utf-8')
    configure.apply_config(answers(), str(tmp_path))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert now['settings']['POLL_INTERVAL_SEC'] == \
        example['settings']['POLL_INTERVAL_SEC']


# --- the passwords -------------------------------------------------------

def test_passwords_reach_env_and_nothing_else(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    written = (tmp_path / 'config.json').read_text(encoding='utf-8')
    assert PASSWORD_A not in written
    assert PASSWORD_B not in written

    env = (tmp_path / '.env').read_text(encoding='utf-8')
    # Quoted, so a password with a space or a # survives the read back.
    assert f'MT5_PASSWORD_ACCOUNT_A="{PASSWORD_A}"' in env
    assert f'MT5_PASSWORD_ACCOUNT_B="{PASSWORD_B}"' in env


def test_a_password_is_not_in_the_refusal_text():
    """A refusal is shown on screen and pasted into chat when it is
    reported. It must not carry the password that caused it."""
    with pytest.raises(configure.SetupError) as caught:
        configure.build_config(answers(login_b='10006'), {})
    assert PASSWORD_A not in str(caught.value)
    assert PASSWORD_B not in str(caught.value)


def test_rerunning_does_not_walk_the_ports_upward(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    first = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    configure.apply_config(answers(), str(tmp_path), force=True)
    again = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert first['accounts']['Account A']['endpoint'] == \
        again['accounts']['Account A']['endpoint']
    assert first['accounts']['Account B']['endpoint'] == \
        again['accounts']['Account B']['endpoint']


# --- the preflight -------------------------------------------------------

def _configured(tmp_path, **over):
    """A config as the wizard writes it, with the two terminals actually
    on disk so the existence check has something to find."""
    terminals = {}
    for leg in ('A', 'B'):
        folder = tmp_path / f'MT5-{leg}'
        folder.mkdir(exist_ok=True)
        exe = folder / 'terminal64.exe'
        exe.write_text('', encoding='utf-8')
        terminals[leg] = str(exe)
    raw = configure.build_config(
        answers(terminal_a=terminals['A'], terminal_b=terminals['B']), {})
    for name, changes in over.items():
        raw['accounts'][name].update(changes)
    return raw


def test_a_configured_machine_starts_with_no_terminal_open(tmp_path):
    """The whole point. An account that names its own folder is OPENED
    and signed in by the engine, so nothing needs to be running — and
    the old check refused exactly this."""
    ok, lines = preflight.check(_configured(tmp_path), terminals_running=0)
    assert ok, lines


def test_control_an_attaching_account_still_needs_a_terminal(tmp_path):
    """Blank terminal_path means attach to whatever is open. Then
    something must BE open, and refusing is right."""
    raw = _configured(tmp_path, **{'Account A': {'terminal_path': ''}})
    ok, lines = preflight.check(raw, terminals_running=0)
    assert not ok
    assert 'no MetaTrader 5 folder set' in lines[0]

    ok_now, _ = preflight.check(raw, terminals_running=1)
    assert ok_now


def test_a_terminal_folder_that_is_not_there_is_refused(tmp_path):
    raw = _configured(tmp_path, **{
        'Account B': {'terminal_path': str(tmp_path / 'gone' / 'terminal64.exe')}})
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'no file there' in lines[0]


@pytest.mark.parametrize('field,value', [
    ('login', 10006),
    ('endpoint', '127.0.0.1:9101'),
])
def test_one_account_entered_twice_is_refused(tmp_path, field, value):
    """A hand-edit that puts one login, port or folder on both rows is a
    pair that hedges against itself. Caught before the engine starts."""
    raw = _configured(tmp_path, **{'Account B': {field: value}})
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'hedge against itself' in lines[0]


def test_one_terminal_folder_on_both_rows_is_refused(tmp_path):
    raw = _configured(tmp_path)
    raw['accounts']['Account B']['terminal_path'] = \
        raw['accounts']['Account A']['terminal_path']
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'hedge against itself' in lines[0]


def test_control_two_distinct_accounts_are_allowed_through(tmp_path):
    ok, _ = preflight.check(_configured(tmp_path), terminals_running=2)
    assert ok


def test_an_unconfigured_machine_is_allowed_to_start(tmp_path):
    """A fresh install has no accounts, and they are entered ON THE
    SCREEN. Refusing here would leave the trader nowhere to enter them."""
    ok, lines = preflight.check({}, terminals_running=0)
    assert ok
    assert 'start' in lines[0]


def test_no_enabled_pair_is_said_but_not_refused(tmp_path):
    raw = _configured(tmp_path)
    for pair in raw['pairs'].values():
        pair['enabled'] = False
    ok, lines = preflight.check(raw, terminals_running=2)
    assert ok
    assert any('No pair is enabled' in line for line in lines)

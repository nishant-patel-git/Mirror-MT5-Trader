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
add_pairs = _load('add_pairs')

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
    account_a = out['accounts'][NAME_A]
    account_b = out['accounts'][NAME_B]

    assert account_a['login'] == 10006
    assert account_b['login'] == 10007
    # One port each. Two accounts on one port is one leg runner serving
    # both legs, which is the same terminal twice.
    assert account_a['endpoint'] != account_b['endpoint']
    assert account_a['terminal_path'] != account_b['terminal_path']

    pair = out['pairs']['XAUUSD.f|GCZ6']
    assert pair['leg_a'] == {'account': NAME_A, 'symbol': 'XAUUSD.f'}
    assert pair['leg_b'] == {'account': NAME_B, 'symbol': 'GCZ6'}
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
    assert out['accounts'][NAME_A]['password_env'] == \
        env_key_for(NAME_A)
    assert out['accounts'][NAME_B]['password_env'] == \
        env_key_for(NAME_B)


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
    assert out['accounts'][NAME_B]['login'] == 10007


def test_one_terminal_folder_for_both_accounts_is_refused():
    with pytest.raises(configure.SetupError, match='ONE login'):
        configure.build_config(
            answers(terminal_b=r'C:\MT5-A\terminal64.exe'), {})


def test_control_two_terminal_folders_are_accepted():
    out = configure.build_config(
        answers(terminal_b=r'C:\MT5-B\terminal64.exe'), {})
    assert out['accounts'][NAME_B]['terminal_path'] == \
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
    assert out['accounts'][NAME_A]['login'] == 10006


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
    assert NAME_A in now['accounts']
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
    assert set(now['accounts']) == {NAME_A, NAME_B}
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
    # The key is the app's own, off the account name: AC-10006 becomes
    # MT5_PASSWORD_AC_10006.
    from mt5trader.config import env_key_for
    assert f'{env_key_for(NAME_A)}="{PASSWORD_A}"' in env
    assert f'{env_key_for(NAME_B)}="{PASSWORD_B}"' in env
    assert 'MT5_PASSWORD_AC_10006' in env


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
    assert first['accounts'][NAME_A]['endpoint'] == \
        again['accounts'][NAME_A]['endpoint']
    assert first['accounts'][NAME_B]['endpoint'] == \
        again['accounts'][NAME_B]['endpoint']


# --- the presets ---------------------------------------------------------

def test_the_shipped_presets_file_parses_and_is_usable():
    """It is DATA the office edits, so a stray comma is a real risk -
    and a broken file must be caught here, not on a trader's PC."""
    presets = configure.load_presets()
    assert presets, 'deploy/presets.json produced no pairs'
    for preset in presets:
        assert preset['label']
        assert preset['leg_a'] and preset['leg_b']
        # Anything unrecognised reads as RELATED, which silently drops
        # the fair value off a basis. Caught here instead.
        assert preset['pair_type'] in ('SPOT_FUTURE', 'FUTURE_FUTURE',
                                       'RELATED')


def test_a_broken_presets_file_leaves_the_wizard_usable(tmp_path):
    """No menu is survivable - the trader types both symbols. A setup
    that refuses to open over a stray comma is not."""
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy' / 'presets.json').write_text('{ not json',
                                                      encoding='utf-8')
    assert configure.load_presets(str(tmp_path)) == []


def test_a_dated_leg_is_filled_in_from_the_contract_box():
    preset = {'label': 'Gold', 'leg_a': 'XAUUSD', 'leg_b': 'GC{contract}',
              'pair_type': 'SPOT_FUTURE'}
    assert configure.preset_needs(preset) == (False, True)
    assert configure.expand_preset(preset, '', 'Z6') == ('XAUUSD', 'GCZ6')


def test_a_calendar_spread_dates_both_legs_separately():
    """Two months of one instrument. One contract box would make the
    two legs the same symbol, and that spread is always zero."""
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    assert configure.preset_needs(preset) == (True, True)
    assert configure.expand_preset(preset, 'X6', 'Z6') == ('USOILX6',
                                                           'USOILZ6')


def test_a_missing_contract_month_is_refused():
    preset = {'label': 'Gold', 'leg_a': 'XAUUSD', 'leg_b': 'GC{contract}',
              'pair_type': 'SPOT_FUTURE'}
    with pytest.raises(configure.SetupError, match='contract month'):
        configure.expand_preset(preset, '', '')


def test_control_a_pair_that_needs_no_contract_asks_for_none():
    preset = {'label': 'Spot pair', 'leg_a': 'XAUUSD', 'leg_b': 'XAGUSD',
              'pair_type': 'RELATED'}
    assert configure.preset_needs(preset) == (False, False)
    assert configure.expand_preset(preset) == ('XAUUSD', 'XAGUSD')


def test_a_calendar_spread_with_one_month_twice_is_refused():
    """Straight through expand_preset into build_config: both legs come
    out as USOILZ6, and a spread of an instrument against itself is
    always zero."""
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    symbol_a, symbol_b = configure.expand_preset(preset, 'Z6', 'Z6')
    with pytest.raises(configure.SetupError, match='always zero'):
        configure.build_config(
            answers(symbol_a=symbol_a, symbol_b=symbol_b), {})


def test_control_two_different_months_go_through():
    preset = {'label': 'WTI', 'leg_a': 'USOIL{contract}',
              'leg_b': 'USOIL{contract}', 'pair_type': 'FUTURE_FUTURE'}
    symbol_a, symbol_b = configure.expand_preset(preset, 'X6', 'Z6')
    out = configure.build_config(
        answers(symbol_a=symbol_a, symbol_b=symbol_b,
                pair_type=preset['pair_type']), {})
    assert out['pairs']['USOILX6|USOILZ6']['pair_type'] == 'FUTURE_FUTURE'


# --- the monthly roll ----------------------------------------------------

#: The two accounts the wizard writes for the logins in `answers()`.
NAME_A = configure.account_name(10006)
NAME_B = configure.account_name(10007)


def _roll(**over):
    spec = {'pairs': [{'name': 'Gold basis', 'leg_a': 'XAUUSD.f',
                       'leg_b': 'GCZ6', 'pair_type': 'SPOT_FUTURE'}]}
    spec.update(over)
    return spec


def _configured_raw():
    return configure.build_config(answers(), {})


def test_the_shipped_roll_list_parses_and_matches_the_wizard():
    """pairs.json names accounts by NAME, and the wizard is what creates
    them. If those two ever disagree the trader gets a refusal on a
    machine that is perfectly well set up."""
    spec = add_pairs.load_list(str(DEPLOY / 'pairs.json'))
    # It must NOT name the accounts: names carry the login, so they
    # differ on every desk and one file has to serve them all.
    assert 'leg_a_account' not in spec
    assert 'leg_b_account' not in spec
    for entry in spec['pairs']:
        assert entry['leg_a'] and entry['leg_b']
        assert entry['pair_type'] in ('SPOT_FUTURE', 'FUTURE_FUTURE',
                                      'RELATED')


def test_a_new_contract_month_is_added():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'name': 'Gold basis', 'leg_a': 'XAUUSD.f',
                      'leg_b': 'GCH7', 'pair_type': 'SPOT_FUTURE'}]), raw)
    assert 'XAUUSD.f|GCH7' in to_add
    added = to_add['XAUUSD.f|GCH7']
    assert added['leg_a'] == {'account': NAME_A, 'symbol': 'XAUUSD.f'}
    assert added['enabled'] is True
    assert added['hedge_ratio_for'] == 'XAUUSD.f|GCH7'


def test_a_pair_already_there_is_left_exactly_as_it_is():
    """The rule the whole script is built around. A trader may be
    holding a position on it, and re-writing the pair moves the ladder
    out from under the money."""
    raw = _configured_raw()
    raw['pairs']['XAUUSD.f|GCZ6']['enabled'] = False
    raw['pairs']['XAUUSD.f|GCZ6']['increment'] = 0.05
    before = dict(raw['pairs']['XAUUSD.f|GCZ6'])

    to_add, notes = add_pairs.plan(_roll(), raw)
    assert to_add == {}
    assert any('already here' in note for note in notes)
    # Not re-enabled, not re-stamped, not touched.
    assert raw['pairs']['XAUUSD.f|GCZ6'] == before


def test_control_a_pair_not_there_yet_is_added(tmp_path):
    raw = _configured_raw()
    assert 'XAUUSD.f|GCZ6' in raw['pairs']
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOT_FUTURE'}]), raw)
    assert list(to_add) == ['XAGUSD.f|SIU6']


def test_nothing_is_ever_removed(tmp_path):
    """The roll list is short and the config is not. Adding this month
    must not take away last month."""
    configure.apply_config(answers(), str(tmp_path))
    spec = _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                         'pair_type': 'SPOT_FUTURE'}])
    add_pairs.apply(spec, str(tmp_path / 'config.json'))
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'XAUUSD.f|GCZ6' in now['pairs']       # what the wizard wrote
    assert 'XAGUSD.f|SIU6' in now['pairs']       # what the roll added
    # And the accounts are untouched.
    assert set(now['accounts']) == {NAME_A, NAME_B}


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    config_path = str(tmp_path / 'config.json')
    spec = _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                         'pair_type': 'SPOT_FUTURE'}])
    add_pairs.apply(spec, config_path)
    first = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    added, _ = add_pairs.apply(spec, config_path)
    second = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert added == []
    assert first == second


def test_a_dry_run_writes_nothing(tmp_path):
    configure.apply_config(answers(), str(tmp_path))
    config_path = str(tmp_path / 'config.json')
    before = (tmp_path / 'config.json').read_text(encoding='utf-8')
    added, _ = add_pairs.apply(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6'}]),
        config_path, dry_run=True)
    assert added == ['XAGUSD.f|SIU6']
    assert (tmp_path / 'config.json').read_text(encoding='utf-8') == before


def test_a_roll_list_for_another_setup_is_refused():
    """Account names the machine does not have. Guessing which local
    account was meant is how a leg ends up on the wrong terminal."""
    raw = _configured_raw()
    with pytest.raises(add_pairs.PairsError, match='differently-named'):
        add_pairs.plan(_roll(leg_a_account='Leg A', leg_b_account=NAME_B),
                       raw)


def test_control_the_matching_account_names_go_through():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6'}]), raw)
    assert to_add


def test_both_legs_on_one_account_is_refused():
    raw = _configured_raw()
    with pytest.raises(add_pairs.PairsError, match='against itself'):
        add_pairs.plan(_roll(leg_a_account=NAME_A, leg_b_account=NAME_A), raw)


def test_a_machine_with_no_accounts_is_refused():
    with pytest.raises(add_pairs.PairsError, match='no accounts'):
        add_pairs.plan(_roll(), {})


def test_the_same_symbol_on_both_legs_is_skipped_not_added():
    raw = _configured_raw()
    to_add, notes = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'USOILV6', 'leg_b': 'USOILV6'}]), raw)
    assert to_add == {}
    assert any('always zero' in note for note in notes)


def test_an_unparseable_roll_list_changes_nothing(tmp_path):
    bad = tmp_path / 'pairs.json'
    bad.write_text('{ "pairs": [ ,, ] }', encoding='utf-8')
    with pytest.raises(add_pairs.PairsError, match='not valid JSON'):
        add_pairs.load_list(str(bad))


def test_an_unknown_pair_type_reads_as_related():
    """RELATED is the reading with NO fair value - the safe way to be
    wrong about a typo in a hand-edited list."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOTFUTURE'}]), raw)
    assert to_add['XAGUSD.f|SIU6']['pair_type'] == 'RELATED'


def test_control_a_spelled_pair_type_is_kept():
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIU6',
                      'pair_type': 'SPOT_FUTURE'}]), raw)
    assert to_add['XAGUSD.f|SIU6']['pair_type'] == 'SPOT_FUTURE'


# --- one roll list for every desk ---------------------------------------

def test_the_accounts_are_read_off_the_machine_when_the_list_omits_them():
    """The whole point of not naming them: names carry the login, so
    they differ on every desk, and one file has to serve them all."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        {'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]}, raw)
    added = to_add['XAGUSD.f|SIZ6']
    assert added['leg_a']['account'] == NAME_A
    assert added['leg_b']['account'] == NAME_B


def test_the_same_list_works_on_a_differently_named_machine():
    """A machine set up by hand, whose accounts are called something
    else entirely. The list does not change; the reading does."""
    raw = _configured_raw()
    raw['accounts'] = {'Account : 10006': raw['accounts'][NAME_A],
                       'Account : 10007': raw['accounts'][NAME_B]}
    pair = raw['pairs']['XAUUSD.f|GCZ6']
    pair['leg_a']['account'] = 'Account : 10006'
    pair['leg_b']['account'] = 'Account : 10007'

    to_add, _ = add_pairs.plan(
        {'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]}, raw)
    added = to_add['XAGUSD.f|SIZ6']
    assert added['leg_a']['account'] == 'Account : 10006'
    assert added['leg_b']['account'] == 'Account : 10007'


def test_control_a_named_list_still_wins():
    """Naming them is still allowed, and still takes precedence."""
    raw = _configured_raw()
    to_add, _ = add_pairs.plan(
        _roll(leg_a_account=NAME_A, leg_b_account=NAME_B,
              pairs=[{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]), raw)
    assert to_add['XAGUSD.f|SIZ6']['leg_a']['account'] == NAME_A


def test_a_machine_with_no_pairs_yet_is_refused_not_guessed():
    raw = _configured_raw()
    raw['pairs'] = {}
    with pytest.raises(add_pairs.PairsError, match='no pair to read them'):
        add_pairs.plan({'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]},
                       raw)


def test_pairs_that_disagree_about_the_legs_are_refused_not_guessed():
    """Two pairs routed opposite ways have no single answer, and
    picking one is how a leg lands on the wrong terminal."""
    raw = _configured_raw()
    raw['pairs']['B|A'] = {
        'leg_a': {'account': NAME_B, 'symbol': 'B'},
        'leg_b': {'account': NAME_A, 'symbol': 'A'}}
    with pytest.raises(add_pairs.PairsError, match='disagree'):
        add_pairs.plan({'pairs': [{'leg_a': 'XAGUSD.f', 'leg_b': 'SIZ6'}]},
                       raw)


# --- the account names --------------------------------------------------

def test_the_account_is_named_for_its_login():
    """`AC-100015 -> AC-100016` in the ladder header. 'Account A' told a
    trader looking at two ladders nothing at all."""
    assert configure.account_name(100015) == 'AC-100015'
    out = configure.build_config(answers(), {})
    assert set(out['accounts']) == {'AC-10006', 'AC-10007'}


def test_a_re_run_with_a_corrected_login_leaves_no_stale_row(tmp_path):
    """Names carry the login, so a corrected one writes a NEW name. The
    row it replaces sits on the same MT5 folder, and one installation
    holds one login - left behind, it is a terminal clash the trader
    never caused."""
    configure.apply_config(answers(), str(tmp_path))
    configure.apply_config(answers(login_b='10099'), str(tmp_path),
                           force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert set(now['accounts']) == {'AC-10006', 'AC-10099'}
    # And the pair that named the row now gone went with it, rather than
    # pointing at an account that cannot resolve.
    for pair in now['pairs'].values():
        assert pair['leg_b']['account'] in now['accounts']


def test_control_an_unrelated_account_is_left_alone(tmp_path):
    """Only rows on the two terminals being written are cleared. A third
    account on its own installation is somebody else's and stays."""
    configure.apply_config(answers(), str(tmp_path))
    raw = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    raw['accounts']['AC-77777'] = {
        'terminal_path': r'C:\MT5-C\terminal64.exe', 'login': 77777,
        'endpoint': '127.0.0.1:9109'}
    (tmp_path / 'config.json').write_text(json.dumps(raw), encoding='utf-8')

    configure.apply_config(answers(login_b='10099'), str(tmp_path),
                           force=True)
    now = json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))
    assert 'AC-77777' in now['accounts']


# --- the default server --------------------------------------------------

def test_the_default_server_is_filled_in_from_presets():
    assert configure.default_server() == 'MentoMarkets-Server'


def test_two_legs_can_sit_at_different_brokers():
    """Filled in, not fixed. The two servers are separate answers, so a
    desk running one leg elsewhere types over the second box."""
    out = configure.build_config(
        answers(server_a='MentoMarkets-Server',
                server_b='OtherBroker-Live'), {})
    assert out['accounts'][NAME_A]['server'] == 'MentoMarkets-Server'
    assert out['accounts'][NAME_B]['server'] == 'OtherBroker-Live'


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
    raw = _configured(tmp_path, **{NAME_A: {'terminal_path': ''}})
    ok, lines = preflight.check(raw, terminals_running=0)
    assert not ok
    assert 'no MetaTrader 5 folder set' in lines[0]

    ok_now, _ = preflight.check(raw, terminals_running=1)
    assert ok_now


def test_a_terminal_folder_that_is_not_there_is_refused(tmp_path):
    raw = _configured(tmp_path, **{
        NAME_B: {'terminal_path': str(tmp_path / 'gone' / 'terminal64.exe')}})
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
    raw = _configured(tmp_path, **{NAME_B: {field: value}})
    ok, lines = preflight.check(raw, terminals_running=2)
    assert not ok
    assert 'hedge against itself' in lines[0]


def test_one_terminal_folder_on_both_rows_is_refused(tmp_path):
    raw = _configured(tmp_path)
    raw['accounts'][NAME_B]['terminal_path'] = \
        raw['accounts'][NAME_A]['terminal_path']
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

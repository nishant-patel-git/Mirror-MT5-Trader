"""Which COLUMN sends which side, and nothing else.

The desk arrives from TT, where clicking the BIDS column joins the bid
and is a resting BUY. This app read the other way — asks buy — so every
click a trader made was the opposite of the one they intended.
"""

from mt5trader.commands import CommandRunner
from mt5trader.config import DEFAULT_SETTINGS


def coerce(value):
    return CommandRunner.HOT_SETTINGS['CLICK_CONVENTION'](value)


def test_a_fresh_machine_trades_the_way_its_screen_says_it_does():
    """The default is TT, and that decision was taken on its own,
    after the switch had been in use for a while.

    It also settles a disagreement between three files. The Settings
    pane has always drawn an unset value as TT, and the snapshot has
    always defaulted to TT, while DEFAULT_SETTINGS said TOUCH. A fresh
    machine therefore SHOWED 'Bids buy - TT price ladder' and CROSSED
    the other way: the one shape of bug that costs money without
    looking wrong on the screen."""
    assert DEFAULT_SETTINGS['CLICK_CONVENTION'] == 'TT'


def test_control_the_three_places_that_default_it_now_agree():
    """The control, and the part worth keeping. Any one of them
    drifting reintroduces a ladder that trades against its own
    label."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    settings_js = (root / 'mt5trader' / 'static' /
                   'settings.js').read_text(encoding='utf-8')
    coordinator = (root / 'mt5trader' /
                   'coordinator.py').read_text(encoding='utf-8')
    assert "settings.CLICK_CONVENTION || 'TT'" in settings_js
    assert "self.config.get('CLICK_CONVENTION', 'TT')" in coordinator
    assert DEFAULT_SETTINGS['CLICK_CONVENTION'] == 'TT'


def test_a_desk_that_chose_touch_keeps_it():
    """Nobody who set it deliberately is moved by the default, and a
    machine that has ever pressed Apply on the Trading pane carries its
    own value."""
    from mt5trader.config import TraderConfig
    config = TraderConfig.from_raw(
        {'accounts': {}, 'pairs': {},
         'settings': {'CLICK_CONVENTION': 'TOUCH'}}, 'x.json')
    assert config.get('CLICK_CONVENTION') == 'TOUCH'


def test_tt_is_reachable_because_that_is_the_point():
    assert coerce('TT') == 'TT'
    assert coerce('tt') == 'TT'


def test_it_is_hot_so_the_desk_can_be_shown_both():
    """A restart to settle an argument about a click is a restart
    nobody makes; it has to change under them."""
    assert 'CLICK_CONVENTION' in CommandRunner.HOT_SETTINGS


def test_only_touch_turns_it_off():
    assert coerce('TOUCH') == 'TOUCH'
    assert coerce('touch') == 'TOUCH'
    assert coerce('  Touch  ') == 'TOUCH'


def test_anything_unrecognised_falls_back_to_tt():
    """A ladder with no convention at all is a ladder whose clicks mean
    nothing. Garbage picks the safe reading rather than breaking."""
    for junk in ('', None, 'nonsense', 0, 'BUY'):
        assert coerce(junk) == 'TT', junk


def test_the_engine_never_sees_a_column():
    """The guarantee the whole change rests on: the side reaches the
    coordinator already decided, so sizing, hedging and execution are
    identical under either convention."""
    import inspect
    from mt5trader.coordinator import Coordinator
    source = inspect.getsource(Coordinator._click)
    assert "'ask'" not in source and "'bid'" not in source


def test_a_fresh_install_carries_it_explicitly():
    """In config.example.json as well as in the defaults, so the file a
    trader can open says which way their clicks go rather than leaving
    it to be inferred from code they cannot read."""
    import json
    from pathlib import Path
    example = json.loads(
        (Path(__file__).resolve().parent.parent /
         'config.example.json').read_text(encoding='utf-8'))
    assert example['settings']['CLICK_CONVENTION'] == 'TT'

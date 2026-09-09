"""Logs on disk, for the morning after.

A trader reported that two legs of a live spread had been closed by
this system sixty seconds after they put them on. The reconciler said
so at CRITICAL, with the account, the ticket and the reason - into a
console window that had been closed. There was nothing on disk, so the
incident could not be investigated, only reasoned about.

Reasoning about money is not good enough. Every process writes to
logs/ now.
"""

import logging
import re
from pathlib import Path

from mt5trader import logsetup

ROOT = Path(__file__).resolve().parent.parent


def _clean():
    logger = logging.getLogger()
    for handler in list(logger.handlers):
        if getattr(handler, '_mt5trader_log', None):
            logger.removeHandler(handler)
            handler.close()


def test_a_critical_line_reaches_a_file(tmp_path):
    _clean()
    try:
        path = logsetup.setup('coordinator', root=str(tmp_path))
        logging.critical('closed ORPHAN XAUUSD_ ticket 1001')
        assert path and Path(path).exists()
        assert 'ticket 1001' in Path(path).read_text(encoding='utf-8')
    finally:
        _clean()


def test_the_line_carries_the_time_to_the_millisecond(tmp_path):
    """Two legs filling in the same second is the ordinary case, and
    the ORDER of them is the whole question afterwards."""
    _clean()
    try:
        path = logsetup.setup('coordinator', root=str(tmp_path))
        logging.critical('a decision')
        text = Path(path).read_text(encoding='utf-8')
        assert re.search(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}', text)
        assert 'CRITICAL' in text
    finally:
        _clean()


def test_it_never_stops_a_process_from_starting():
    """The control that matters most. A read-only folder or a full disk
    means we lose the logs - stopping a trading process over it would
    be worse than the fault being logged."""
    _clean()
    assert logsetup.setup('x', root='/proc/definitely/not/writable') is None


def test_control_calling_it_twice_does_not_double_every_line(tmp_path):
    _clean()
    try:
        first = logsetup.setup('coordinator', root=str(tmp_path))
        second = logsetup.setup('coordinator', root=str(tmp_path))
        assert first == second
        logging.critical('once')
        text = Path(first).read_text(encoding='utf-8')
        assert text.count('once') == 1
    finally:
        _clean()


def test_the_files_are_rotated_rather_than_growing_for_ever(tmp_path):
    """A trading box that stops because a log filled the volume is a
    worse fault than the one being logged."""
    _clean()
    try:
        path = logsetup.setup('coordinator', root=str(tmp_path),
                              max_bytes=2000, backups=2)
        for i in range(400):
            logging.critical('a reconciler decision number %d', i)
        files = sorted(p.name for p in Path(path).parent.iterdir())
        assert len(files) <= 3, files          # the file plus its backups
        assert 'coordinator.log' in files
    finally:
        _clean()


def test_every_process_writes_one():
    """The launcher, the web server, the coordinator and each leg. A
    leg's chatter must never bury a reconciler decision, so they are
    separate files."""
    sources = {
        'start.py': "logsetup.setup('launcher')",
        'run_coordinator.py': "logsetup.setup('coordinator')",
        'mt5trader/leg_runner.py': "logsetup.setup(f'leg-{safe}')",
        'mt5trader/webapp.py': "logsetup.setup('web')",
    }
    for name, expected in sources.items():
        text = (ROOT / name).read_text(encoding='utf-8')
        assert expected in text, name


def test_control_the_console_keeps_everything_it_had():
    """This ADDS a destination. A desk watching the black window must
    see exactly what it saw before."""
    for name in ('run_coordinator.py', 'mt5trader/leg_runner.py'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert 'logging.StreamHandler()' in text, name
    # And the unbounded files that used to sit loose in the repo root
    # are gone - they were both a disk risk and hard to find.
    for name in ('run_coordinator.py', 'mt5trader/leg_runner.py'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert "FileHandler('coordinator.log'" not in text, name
        assert "FileHandler(f'leg_" not in text, name

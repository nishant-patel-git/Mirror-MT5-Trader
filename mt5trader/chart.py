"""The pair chart's series: each ladder's spread and the Algo's z, over
time.

Observes only. Fed from the poll the signal journal already runs - the
Algo's OWN z, read off the same candles it trades on - so the line on
the chart is the number the Algo decides on, not a second reckoning of
it. Arrow-statarb computed its chart z against the window's own mean;
here that would draw a z the Algo never saw.

Written to a file of its own beside the status file, every couple of
seconds: the status file is read several times a second and carrying
two hours of points in it would make every one of those reads heavy.
"""

import collections
import json
import os

from . import atomicfile

#: One point a second at most: the poll is faster than that, and a
#: chart of two hours does not need more.
SAMPLE_EVERY_SEC = 1.0
#: How much is kept, and how much the screen can ask for.
KEEP_SEC = 2 * 60 * 60
#: The most points one pair writes. Two hours at one a second is 7,200;
#: a chart 600 pixels wide cannot show more than this anyway.
MAX_POINTS = 720
#: How often the file is rewritten.
WRITE_EVERY_SEC = 2.0


def chart_path(status_path):
    """Beside the status file, so the web process finds it the same
    way."""
    return os.path.join(os.path.dirname(os.path.abspath(status_path)),
                        'chart.json')


def _round(value, places=6):
    return None if value is None else round(float(value), places)


class SpreadTape:
    def __init__(self, keep_sec=KEEP_SEC, every_sec=SAMPLE_EVERY_SEC):
        self.keep_sec = keep_sec
        self.every_sec = every_sec
        self._points = {}            # key -> deque of (t, spread, z)
        self._lines = {}             # key -> the Algo's levels now
        self._written = None

    def observe(self, key, now, md, body, params):
        """One poll's reading. `body` is what the Algo's own code made
        of it (`shadow.ShadowAlgo.step`); `params` its settings.

        A price the jump guard is holding back is not drawn: the candles
        do not take it either, and a spike that was never a price is
        what makes a chart lie."""
        if not md or md.get('mid_spread') is None or md.get('jump_reason'):
            return
        body = body or {}
        params = params or {}
        self._lines[key] = {
            'entry_z': params.get('entry_z'),
            'stop_z': params.get('stop_z') if params.get('stop_z_on')
            else None,
            'mean': _round(body.get('mean')),
            'sigma': _round(body.get('sigma')),
            'ready': bool(body.get('ready')),
            'count': body.get('count'), 'needed': body.get('needed'),
        }
        points = self._points.setdefault(key, collections.deque())
        if points and now - points[-1][0] < self.every_sec:
            return
        points.append((float(now), float(md['mid_spread']),
                       _round(body.get('z_mid'), 4)))
        while points and now - points[0][0] > self.keep_sec:
            points.popleft()

    def forget(self, key):
        self._points.pop(key, None)
        self._lines.pop(key, None)

    def series(self, key, max_points=MAX_POINTS):
        """The pair's points, thinned evenly to `max_points` - the last
        one always kept, so the right-hand end is now."""
        points = list(self._points.get(key) or ())
        step = max(1, -(-len(points) // max_points))
        picked = points[::step]
        if points and picked[-1] is not points[-1]:
            picked.append(points[-1])
        spreads = [p[1] for p in points]
        out = dict(self._lines.get(key) or {})
        out.update({
            'points': [{'t': round(t, 1), 's': round(s, 6), 'z': z}
                       for t, s, z in picked],
            'spread_min': min(spreads) if spreads else None,
            'spread_max': max(spreads) if spreads else None,
        })
        return out

    def snapshot(self, now, keys=None):
        keys = self._points.keys() if keys is None else keys
        return {'at': now, 'keep_sec': self.keep_sec,
                'pairs': {key: self.series(key) for key in keys}}

    def write(self, path, now, keys=None, force=False):
        """Rewrite the file if it is due. A failure is the previous
        file left standing - the chart a couple of seconds old - never a
        stopped poll."""
        if not force and self._written is not None and \
                now - self._written < WRITE_EVERY_SEC:
            return False
        self._written = now
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.snapshot(now, keys), f)
        return atomicfile.replace(tmp, path)

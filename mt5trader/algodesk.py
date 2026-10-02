"""Where the Algo runs: one switch per ladder, its candles, its sink.

`algo.AlgoSignal` decides; `bands.SpreadCandles` measures; this module
feeds them on every poll and hands what they decide to a SINK.

**The sink is the seam execution will use.** Today there is one,
`RecordingSink`: an intent — "enter, selling the spread", "exit
POS-12, profit target" — is written to the audit trail, kept for the
screen, and logged. It sends nothing. When execution is asked for, an
execution sink takes the same intents to the executor (MARKET both
legs, closes by TICKET) and nothing upstream of it changes. Until then
`MODES` holds DRY_RUN alone and asking for anything else is refused.

Rules this module keeps:

- **The switch is per ladder, in memory, and OFF at every start.** It
  is never written to the config: an algo nobody turned on today is an
  algo nobody is watching.
- **Manual trading is untouched.** `manual_order_refusal` is the one
  place the click path will consult when execution exists ("the Algo
  is trading this ladder — manual orders are off"). Today it returns
  None for every ladder, always: the trader asked for the signal
  beside the ladder, with their own clicks unaffected.
- **History first, then the live tape.** Turning a ladder on loads its
  saved candles and asks MT5 for its bars, so the band is there at
  once. If the terminals will not answer — or a broker's clock is not
  measured, so a bar cannot be placed in time — the candles are built
  from the live mid instead and the screen says "collecting 7/20".
  Every candle that closes is saved, so a restart picks up where it
  left off.
"""

import logging
from collections import deque

from . import algo as algo_module
from . import bands

#: How often a ladder that is still short of candles asks MT5 again.
BACKFILL_RETRY_SEC = 60.0


class RecordingSink:
    """Today's only sink: record the intent, show it, send nothing."""

    mode = algo_module.DRY_RUN

    def __init__(self, store=None):
        self.store = store

    def handle(self, pair_key, intent):
        detail = dict(intent, mode=self.mode)
        z = intent.get('z')
        logging.info('[ALGO %s] %s %s %s z=%s spread=%s (%s, nothing sent)',
                     pair_key, intent.get('action'), intent.get('side'),
                     intent.get('reason') or '',
                     '—' if z is None else f'{z:+.2f}',
                     intent.get('spread'), self.mode)
        if self.store is not None:
            try:
                self.store.event('algo_signal', pair_key, **detail)
            except Exception as e:           # never let a record stop a poll
                logging.error('could not record an Algo signal: %s', e)
        return detail


class _Run:
    """One ladder's Algo while it is on."""

    def __init__(self, signature, params, anchor, started_at):
        self.signature = signature
        self.params = params
        self.candles = bands.SpreadCandles(params['timeframe_min'] * 60.0,
                                           params['length'], anchor)
        self.signal = algo_module.AlgoSignal(params)
        self.started_at = started_at
        self.history = {'state': 'pending', 'note': None, 'at': None,
                        'candles': 0}
        self.recent = deque(maxlen=20)
        self.body = None


class AlgoDesk:
    """Every ladder's Algo switch, and the Algo on the ones that are on."""

    def __init__(self, legs, store=None, clock=None, offset_for=None,
                 sink=None):
        self.legs = legs
        self.store = store
        self.clock = clock
        #: account name -> the broker's measured offset from UTC, or None.
        self.offset_for = offset_for or (lambda name: None)
        self.sink = sink or RecordingSink(store)
        self._runs = {}

    # -- the switch ---------------------------------------------------------

    def is_on(self, key):
        return key in self._runs

    def turn_on(self, pair, mode=algo_module.DRY_RUN):
        """Switch one ladder's Algo on. Refused, in words, if it cannot be."""
        if mode not in algo_module.MODES:
            return {'ok': False, 'pair': pair.key,
                    'reason': f'{mode} is not available — the Algo signals '
                              f'only; it does not trade yet'}
        if not self.is_on(pair.key):
            self._runs[pair.key] = self._new_run(pair)
            logging.info('[ALGO %s] ON (%s)', pair.key, mode)
        return {'ok': True, 'pair': pair.key, 'on': True, 'mode': mode}

    def turn_off(self, key):
        was = self._runs.pop(key, None)
        if was is not None:
            logging.info('[ALGO %s] OFF', key)
        return {'ok': True, 'pair': key, 'on': False}

    def manual_order_refusal(self, key):
        """Why a MANUAL order on this ladder is refused, or None.

        Always None today: the Algo only signals, and the trader keeps
        their clicks. When execution arrives, this is where "Algo or
        Manual, never both" is enforced — on the ORDER PATH, never only
        on the screen — and a close is still never refused.
        """
        return None

    # -- every poll ---------------------------------------------------------

    def observe(self, pair, md, positions=(), gates=None):
        """Feed one ladder's Algo this poll's market. Returns its body,
        or None for a ladder whose Algo is off — which computes nothing."""
        run = self._runs.get(pair.key)
        if run is None:
            return None
        if run.signature != self._signature(pair):
            # A new timeframe, length or beta is a different series:
            # start it again from what is saved and what MT5 has.
            run = self._runs[pair.key] = self._new_run(pair)
        params = algo_module.clean_params(getattr(pair, 'algo_params', None))
        if params != run.params:
            # A new threshold is the same series read differently: the
            # band stays, the rules change on this pass.
            run.params = params
            run.signal.params = params
        now = self.clock()
        if md and md.get('mid_spread') is not None \
                and not md.get('jump_reason'):
            # A price the jump guard is holding back is not fed to the
            # band either: one bad print in a close moves sigma for N
            # candles.
            closed = run.candles.observe(now, md['mid_spread'])
            if closed is not None and self.store is not None:
                self._save(pair, run, [closed], 'live')
        self._backfill_if_due(pair, run, now)
        body = run.signal.evaluate(now, md, run.candles.stats(), positions,
                                   gates)
        for intent in body['intents']:
            recorded = self.sink.handle(pair.key, intent)
            run.recent.appendleft(dict(recorded, at=now))
        run.body = body
        return body

    def block(self, key):
        """What the screen shows for one ladder's Algo, or None if off."""
        run = self._runs.get(key)
        if run is None:
            return None
        body = dict(run.body or {'algo': algo_module.ALGO, 'state': 'STARTING',
                                 'params': dict(run.params)})
        body.pop('intents', None)
        body.update(on=True, mode=self.sink.mode, history=dict(run.history),
                    recent=list(run.recent), started_at=run.started_at,
                    timeframe_min=run.params['timeframe_min'],
                    length=run.params['length'])
        return body

    # -- candles ------------------------------------------------------------

    @staticmethod
    def _signature(pair):
        """What makes a candle series: its size, its length, its beta."""
        params = algo_module.clean_params(getattr(pair, 'algo_params', None))
        return (params['timeframe_min'], params['length'],
                float(pair.hedge_ratio or 1.0))

    def _new_run(self, pair):
        params = algo_module.clean_params(getattr(pair, 'algo_params', None))
        anchor = bands.round_offset(self.offset_for(pair.account_a)) or 0.0
        run = _Run(self._signature(pair), params, anchor, self.clock())
        if self.store is not None:
            try:
                saved = self.store.candles(
                    pair.key, params['timeframe_min'] * 60.0,
                    pair.hedge_ratio or 1.0,
                    limit=params['length'] * bands.KEEP_MULTIPLE)
                run.candles.seed(saved)
            except Exception as e:
                logging.error('[ALGO %s] saved candles unreadable: %s',
                              pair.key, e)
        return run

    def _save(self, pair, run, rows, source):
        try:
            self.store.save_candles(pair.key, run.candles.timeframe_sec,
                                    pair.hedge_ratio or 1.0, rows, source)
        except Exception as e:
            logging.error('[ALGO %s] could not save candles: %s', pair.key, e)

    def _backfill_if_due(self, pair, run, now):
        """Ask MT5 for the bars once, and again a minute later if short."""
        history = run.history
        if history['state'] == 'done':
            return
        if history['at'] is not None and \
                now - history['at'] < BACKFILL_RETRY_SEC:
            return
        history['at'] = now
        rows, note = self._history(pair, run, now)
        if rows:
            added = run.candles.seed(rows)
            history['candles'] = len(rows)
            if self.store is not None:
                self._save(pair, run, rows, 'mt5')
            logging.info('[ALGO %s] %d candle(s) from MT5, %d new',
                         pair.key, len(rows), added)
        if run.candles.stats()['ready']:
            history['state'] = 'done'
            history['note'] = note or (f'{len(rows)} candles from MT5'
                                       if rows else 'saved candles')
        else:
            history['state'] = 'collecting'
            history['note'] = note or 'collecting candles from the live price'

    def _history(self, pair, run, now):
        """Closed spread candles from both legs' MT5 bars, and why not."""
        params = run.params
        count = params['length'] * bands.KEEP_MULTIPLE + 2
        leg_a = self.legs.get(pair.account_a)
        leg_b = self.legs.get(pair.account_b)
        if leg_a is None or leg_b is None:
            return [], 'a leg is not connected — collecting from the live price'
        try:
            bars_a = leg_a.rates(pair.symbol_a, params['timeframe_min'], count)
            bars_b = leg_b.rates(pair.symbol_b, params['timeframe_min'], count)
        except Exception as e:
            return [], f'MT5 history unavailable ({e}) — collecting live'
        if not bars_a or not bars_b:
            which = 'leg A' if not bars_a else 'leg B'
            return [], (f'MT5 returned no history for {which} — '
                        f'collecting from the live price')
        offset_a = self.offset_for(pair.account_a)
        offset_b = self.offset_for(pair.account_b)
        if offset_a is None or offset_b is None:
            return [], ("a broker's clock is not measured, so its bars "
                        "cannot be placed in time — collecting live")
        rows = bands.spread_bars(
            bars_a, bars_b, pair.hedge_ratio, run.candles.timeframe_sec,
            bands.round_offset(offset_a), bands.round_offset(offset_b),
            anchor=run.candles.anchor)
        current = bands.bucket_of(now, run.candles.timeframe_sec,
                                  run.candles.anchor)
        rows = [(b, c) for b, c in rows if b < current]
        if not rows:
            return [], ('the two legs share no closed bars — collecting '
                        'from the live price')
        return rows, None

"""Content-free request timings. Never records SQL, URLs, tokens or payloads."""
from contextvars import ContextVar
from contextlib import contextmanager
from threading import Lock
from time import perf_counter

from sqlalchemy import event

_current = ContextVar('bonwise_request_timings', default=None)
_ALLOWED = {'auth', 'db_connect', 'db_query', 'ai_stage', 'retrieval'}


class Timings:
    def __init__(self):
        self.values = {}
        self.lock = Lock()

    def add(self, name, ms):
        if name not in _ALLOWED:
            raise ValueError('Unknown timing category')
        with self.lock:
            total, count = self.values.get(name, (0, 0))
            self.values[name] = (total + ms, count + 1)

    def header(self, elapsed):
        with self.lock:
            values = dict(self.values)
        parts = [f'app;dur={elapsed:.2f}']
        for name, (duration, count) in sorted(values.items()):
            parts += [f'{name};dur={duration:.2f}', f'{name}_count;desc="{count}"']
        return ', '.join(parts)


def record(name, milliseconds):
    stats = _current.get()
    if stats is not None:
        stats.add(name, milliseconds)


@contextmanager
def measure(name):
    start = perf_counter()
    try:
        yield
    finally:
        record(name, (perf_counter() - start) * 1000)


class PerformanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        stats, start = Timings(), perf_counter()
        token = _current.set(stats)
        async def timed_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers.append((b'server-timing', stats.header((perf_counter()-start)*1000).encode('ascii')))
                message = {**message, 'headers': headers}
            await send(message)
        try:
            await self.app(scope, receive, timed_send)
        finally:
            _current.reset(token)


def instrument_engine(engine):
    # DB connection time excludes pool waiting; query durations exclude row fetching.
    if getattr(engine, '_bonwise_timed', False):
        return engine
    engine._bonwise_timed = True
    @event.listens_for(engine, 'do_connect')
    def connect(dialect, connection_record, args, kwargs):
        with measure('db_connect'):
            return dialect.connect(*args, **kwargs)
    @event.listens_for(engine, 'before_cursor_execute')
    def before(conn, cursor, statement, parameters, context, executemany):
        context._bonwise_started = perf_counter()
    def finish(context):
        start = getattr(context, '_bonwise_started', None)
        if start is not None:
            record('db_query', (perf_counter()-start)*1000)
            context._bonwise_started = None
    @event.listens_for(engine, 'after_cursor_execute')
    def after(conn, cursor, statement, parameters, context, executemany):
        finish(context)
    @event.listens_for(engine, 'handle_error')
    def failed(context):
        if context.execution_context is not None:
            finish(context.execution_context)
    return engine


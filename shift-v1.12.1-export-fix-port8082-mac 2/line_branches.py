"""Isolated LINE state for each store, sharing the existing staff/shift database.

Tsukiji keeps its original public tables. Other stores get empty LINE tables in
their own PostgreSQL schema: credentials, recipients, snapshots and retry keys
are never copied. Shared staff queries must still filter by branch_id.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import g, has_request_context

_worker_branch = ContextVar('line_worker_branch', default=None)
BOT_IDS = {1: '@323txucc', 2: '@829gakri'}
TABLES = (
    'line_schedule_config', 'line_schedule_deliveries',
    'staff_schedule_publications', 'line_identity_config', 'line_staff_links',
    'line_manager_menu_links', 'line_private_config', 'line_private_follows',
    'line_private_deliveries', 'line_private_publications',
)


def current_branch():
    worker = _worker_branch.get()
    if worker:
        return worker
    return getattr(g, 'branch', {'id': 1, 'name': 'ベト屋築地店'}) if has_request_context() else {'id': 1, 'name': 'ベト屋築地店'}


def branch_number():
    return int(current_branch()['id'])


def branch_name():
    return current_branch()['name']


@contextmanager
def branch_scope(branch):
    token = _worker_branch.set(dict(branch))
    try:
        yield
    finally:
        _worker_branch.reset(token)


def branch_url(value):
    """Keep externally fetched images/webhooks and redirects in their store."""
    if branch_number() == 1:
        return value
    parts = urlsplit(value)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['branch'] = str(branch_number())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class LineDatabase:
    def __init__(self, connect):
        self.base_connect = connect
        self.ready = set()
        self.lock = Lock()

    def branches(self):
        with self.base_connect() as c:
            return c.execute('SELECT id,name FROM branches WHERE id IN (1,2) ORDER BY id').fetchall()

    def prepare(self, number):
        # Only validated integer branch IDs enter identifiers. No request text or
        # credentials are ever interpolated into SQL.
        number = int(number)
        if number == 1 or number in self.ready:
            return
        with self.lock:
            if number in self.ready:
                return
            schema = f'line_branch_{number}'
            with self.base_connect() as c:
                c.execute('SELECT pg_advisory_xact_lock(%s)', (72906000 + number,))
                c.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
                for table in TABLES:
                    c.execute(f'CREATE TABLE IF NOT EXISTS {schema}.{table} (LIKE public.{table} INCLUDING ALL)')
                for table in ('line_schedule_config', 'line_identity_config', 'line_private_config'):
                    c.execute(f'INSERT INTO {schema}.{table}(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            # The transaction must commit before another connection can use it.
            self.ready.add(number)

    def __call__(self):
        number = branch_number()
        self.prepare(number)
        c = self.base_connect()
        try:
            if number != 1:
                # All LINE tables exist before public enters the search path.
                # Session setting survives explicit commits during HTTP retries;
                # closing this dedicated connection discards it.
                c.execute("SELECT set_config('search_path', %s, false)", (f'line_branch_{number},public',))
            return c
        except Exception:
            c.close()
            raise

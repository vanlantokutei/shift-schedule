"""Exercise Kibo reopening, expiry, weekly isolation, and the manager boundary."""
import sqlite3
import types
from datetime import date, datetime
from pathlib import Path

import pytest
import kibo_deadlines
from kibo_deadlines import JST
from manager_auth import install_manager_auth
from test_manager_auth import LocalConnection


class Clock(datetime):
    instant = datetime(2026, 11, 6, 23, tzinfo=JST)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


class Result:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None
    def fetchall(self):
        rows = [dict(row) for row in self.cursor.fetchall()]
        for row in rows:
            for key in ('submitted_at', 'submitted_at_jst'):
                if row.get(key): row[key] = datetime.fromisoformat(row[key])
        return rows


class Connection(LocalConnection):
    def close(self): pass
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def execute(self, sql, args=()):
        sql = sql.replace("(submitted_at + interval '9 hours')", "datetime(submitted_at, '+9 hours')")
        return Result(super().execute(sql, tuple(value.isoformat() if isinstance(value, datetime) else value for value in args)))


@pytest.fixture
def env(monkeypatch):
    # Load the actual app routes without connecting to a production database at import.
    path = Path(__file__).with_name('app.py')
    module = types.ModuleType('deadline_test_app')
    module.__file__ = str(path)
    exec(compile(path.read_text().split('\ninstall_deadline_route(app, db)')[0], str(path), 'exec'), module.__dict__)
    raw = sqlite3.connect(':memory:')
    raw.row_factory = sqlite3.Row
    raw.executescript('''
        CREATE TABLE kibo_deadlines(week_start TEXT PRIMARY KEY,deadline TEXT NOT NULL);
        CREATE TABLE staff(id INTEGER PRIMARY KEY,name TEXT,active INTEGER,sort_order INTEGER,staff_type TEXT,pay_type TEXT,hourly_rate INTEGER,monthly_salary INTEGER);
        INSERT INTO staff VALUES(1,'Lân',1,0,'fulltime','hourly',1280,0);
        CREATE TABLE shifts(staff_id INTEGER,work_date TEXT,start TEXT,"end" TEXT,break_min INTEGER,break_start TEXT,break_end TEXT,break2_start TEXT,break2_end TEXT,is_kibo INTEGER,UNIQUE(staff_id,work_date));
        CREATE TABLE fixed_kibo(staff_id INTEGER,weekday INTEGER,start TEXT,"end" TEXT);
        CREATE TABLE kibo_submissions(staff_id INTEGER,week_start TEXT,submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(staff_id,week_start));
    ''')
    connect = lambda: Connection(raw)
    module.db = connect
    module.datetime = Clock
    monkeypatch.setattr(kibo_deadlines, 'datetime', Clock)
    monkeypatch.setattr(Clock, 'instant', datetime(2026, 11, 6, 23, tzinfo=JST))
    app = module.app
    app.template_folder = str(path.parent / 'templates')
    app.testing = True
    kibo_deadlines.install_deadline_route(app, connect)
    install_manager_auth(app, connect)
    raw.execute("INSERT INTO manager_accounts(id,username,display_name,role,active,version) VALUES(1,'owner','Lân','owner',1,1)")
    raw.commit()
    client = app.test_client()
    with client.session_transaction(base_url='https://localhost') as session:
        session.update(manager_id=1,manager_version=1,csrf='test-csrf')
    yield app, client, raw, connect
    raw.close()


def extend(client, deadline='2026-11-07T22:00', week='2026-11-09', headers=True):
    return client.post('/kibo/deadline', json=dict(week=week,deadline=deadline),
                       headers={'X-CSRF-Token':'test-csrf'} if headers else {}, base_url='https://localhost')


def test_reopen_submit_and_close_at_exact_japan_time(env, monkeypatch):
    _, client, raw, _ = env
    form = dict(staff_id='1',week='2026-11-09',start_0='09:00',end_0='15:00')
    assert 'Đã hết hạn' in client.post('/kibo',data=form).text
    assert raw.execute('SELECT count(*) FROM shifts').fetchone()[0] == 0
    response = extend(client)
    assert response.status_code == 200
    assert response.json['deadline'] == '2026-11-07T22:00:00+09:00'
    page = client.get('/kibo?staff_id=1&week=2026-11-09').text
    assert '07/11/2026 lúc 22:00' in page
    assert '<fieldset disabled' not in page
    assert 'Đăng ký thành công!' in client.post('/kibo',data=form).text
    monkeypatch.setattr(Clock,'instant',datetime(2026,11,7,22,tzinfo=JST))
    form['start_0']='10:00'
    assert 'Đã hết hạn' in client.post('/kibo',data=form).text
    assert raw.execute('SELECT start FROM shifts').fetchone()[0] == '09:00'


def test_week_isolation_and_persistent_read(env):
    _, client, raw, connect = env
    assert extend(client).status_code == 200
    assert kibo_deadlines.window(connect(),date(2026,11,9))[1].hour == 22
    assert kibo_deadlines.window(connect(),date(2026,11,16))[1] == datetime(2026,11,12,22,tzinfo=JST)
    assert raw.execute('SELECT count(*) FROM kibo_deadlines').fetchone()[0] == 1
    assert extend(client,'2026-11-07T21:00').status_code == 400
    assert extend(client,'2026-11-08T12:15').status_code == 200


@pytest.mark.parametrize('deadline,week',[
    ('2026-11-06T22:59','2026-11-09'),
    ('2026-11-05T22:00','2026-11-09'),
    ('bad','2026-11-09'),
    ('2026-11-07T22:00','2026-11-10'),
    ('2026-11-20T22:00','2026-11-16'),
])
def test_invalid_deadlines_do_not_write(env,deadline,week):
    _,client,raw,_=env
    assert extend(client,deadline,week).status_code == 400
    assert raw.execute('SELECT count(*) FROM kibo_deadlines').fetchone()[0] == 0


def test_manager_auth_and_csrf_required(env):
    app,client,raw,_=env
    assert extend(app.test_client()).status_code == 401
    assert extend(client,headers=False).status_code == 403
    assert raw.execute('SELECT count(*) FROM kibo_deadlines').fetchone()[0] == 0


def test_confirmed_shift_preserved_and_manager_template(env):
    _,client,raw,_=env
    raw.execute("INSERT INTO shifts(staff_id,work_date,start,\"end\",is_kibo) VALUES(1,'2026-11-09','08:00','16:00',0)")
    raw.commit()
    assert extend(client).status_code == 200
    response=client.post('/kibo',data=dict(staff_id='1',week='2026-11-09',start_0='10:00',end_0='15:00'))
    assert 'Đăng ký thành công!' in response.text
    assert raw.execute('SELECT start FROM shifts').fetchone()[0] == '08:00'
    page=client.get('/',base_url='https://localhost')
    assert page.status_code == 200
    assert 'Gia hạn Kibo' in page.text
    assert '07/11/2026 22:00' in page.text
    assert 'type="datetime-local"' in page.text

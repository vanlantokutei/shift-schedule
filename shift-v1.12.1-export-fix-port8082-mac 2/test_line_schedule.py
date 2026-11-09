"""LINE tests use fake HTTP and local SQL; no live messages or production data."""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
import line_schedule as line
from test_manager_auth import env, setup, csrf, LocalConnection


class Result:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None: return None
        data = dict(row)
        for key in ('pair_expires','accepted_at','next_attempt','expires_at'):
            if data.get(key): data[key] = datetime.fromisoformat(data[key])
        return data
    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class Connection(LocalConnection):
    def execute(self, sql, args=()):
        if 'pg_try_advisory_lock' in sql:
            return Result(self.raw.execute('SELECT 1 AS locked'))
        sql = sql.replace(' FOR UPDATE', '')
        args = tuple(x.isoformat() if isinstance(x, datetime) else x for x in args)
        return Result(super().execute(sql,args))
    def commit(self): self.raw.commit()


@pytest.fixture
def state(env, monkeypatch):
    app, raw, _ = env
    connect = lambda: Connection(raw)
    service = line.install_line_schedule(app, connect, start_worker=False)
    raw.executescript('''CREATE TABLE kibo_deadlines(week_start TEXT PRIMARY KEY,deadline TEXT);
        CREATE TABLE staff(id INTEGER PRIMARY KEY,name TEXT,active INTEGER,sort_order INTEGER,hourly_rate INTEGER);
        INSERT INTO staff VALUES(1,'Lân',1,0,99999);
        INSERT INTO staff VALUES(2,'Sương',1,1,99999);
        CREATE TABLE shifts(staff_id INTEGER,work_date TEXT,start TEXT,"end" TEXT,break_start TEXT,
        break_end TEXT,break2_start TEXT,break2_end TEXT,is_kibo INTEGER);
        INSERT INTO shifts VALUES(1,'2026-11-16','07:30','19:30','12:00','12:45','16:00','16:15',0);
        INSERT INTO shifts VALUES(2,'2026-11-16','17:00','22:00',NULL,NULL,NULL,NULL,1);''')
    encrypted = service.cipher.encrypt(json.dumps({'secret':'s'*32,'token':'t'*50}).encode()).decode()
    raw.execute('''UPDATE line_schedule_config SET credentials=?,group_id='Cstaff',group_name='Nhân viên',
        public_origin='https://shift-schedule-1fk2.onrender.com',enabled=1 WHERE id=1''',(encrypted,))
    raw.commit()
    calls = []
    def fake(token,path,payload=None,retry_key=None):
        calls.append((token,path,payload,retry_key))
        return {'basicId':'@323txucc','groupName':'Nhân viên Tsukiji'}
    monkeypatch.setattr(line, 'line_request', fake)
    return app, raw, service, calls, env


@pytest.mark.parametrize('utc,expected',[
    ((2026,11,16,0,59),None), ((2026,11,16,1,0),date(2026,11,16)),
    ((2026,11,16,14,59),date(2026,11,16)), ((2026,11,16,15,0),None),
    ((2026,11,15,1,0),None)])
def test_monday_10am_japan(utc,expected):
    assert line.scheduled_week(datetime(*utc,tzinfo=timezone.utc)) == expected


def test_worker_once_after_restart_sends_registration_link_not_image(state):
    app, raw, service, calls, _ = state
    now = datetime(2026,11,16,10,tzinfo=line.JST)
    service.tick(now)
    line.LineSchedule(app,service.connect).tick(now + timedelta(minutes=2))
    assert len(calls)==1
    payload = calls[0][2]
    assert payload['to']=='Cstaff' and len(payload['messages'])==1
    message=payload['messages'][0]
    assert message['type']=='text'
    assert '23/11–29/11/2026' in message['text']
    assert '19/11/2026 lúc 22:00' in message['text']
    assert '/kibo?week=2026-11-23' in message['text']
    assert 'Lân' not in message['text'] and '99999' not in message['text']
    assert raw.execute('SELECT accepted_at FROM line_schedule_deliveries').fetchone()['accepted_at']
    assert raw.execute('SELECT week_start FROM line_schedule_deliveries').fetchone()[0]=='reminder:2026-11-16'


def test_reminder_uses_extended_deadline_and_skips_closed_windows(state):
    _,raw,service,calls,_=state
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-23',datetime(2026,11,20,23,30,tzinfo=line.JST).isoformat()));raw.commit()
    now=datetime(2026,11,16,10,tzinfo=line.JST)
    service.tick(now)
    assert '20/11/2026 lúc 23:30' in calls[0][2]['messages'][0]['text']
    assert service.deliver(date(2026,11,16),datetime(2026,11,21,10,tzinfo=line.JST),'reminder-test:closed',test=True)=='closed'
    assert len(calls)==1


def test_retry_preserves_snapshot_and_key(state,monkeypatch):
    _,raw,service,calls,_ = state
    attempts=[]
    def fail(token,path,payload,retry_key):
        attempts.append((payload,retry_key))
        raise RuntimeError('offline')
    monkeypatch.setattr(line,'line_request',fail)
    now=datetime(2026,11,16,10,tzinfo=line.JST)
    service.tick(now)
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-23',datetime(2026,11,20,23,30,tzinfo=line.JST).isoformat()));raw.commit()
    service.tick(now+timedelta(minutes=1))
    assert len(attempts)==1
    service.tick(now+timedelta(minutes=5))
    assert len(attempts)==2 and attempts[0]==attempts[1]
    raw.execute("UPDATE line_schedule_config SET group_id='Cother'");raw.commit()
    service.tick(now+timedelta(minutes=10))
    assert len(attempts)==2


def test_manual_tuesday_delivery_does_not_consume_weekly_job(state):
    _,raw,service,calls,_=state
    now=datetime(2026,11,17,10,tzinfo=line.JST)
    week=date(2026,11,16)
    assert service.deliver(week,now,'reminder-test:one',test=True)=='sent'
    assert service.deliver(week,now,'reminder-test:one',test=True)=='sent'
    assert len(calls)==1 and calls[0][2]['messages'][0]['text'].startswith('GỬI THỬ')
    assert raw.execute('SELECT enabled FROM line_schedule_config').fetchone()[0]==1
    assert raw.execute('SELECT week_start FROM line_schedule_deliveries').fetchone()[0]=='reminder-test:one'
    service.tick(datetime(2026,11,16,10,tzinfo=line.JST))
    assert len(calls)==2
    assert not calls[1][2]['messages'][0]['text'].startswith('GỬI THỬ')


def test_manual_button_requires_owner_csrf_and_nonce(state,monkeypatch):
    app,raw,service,calls,env=state
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls(2026,11,17,10,tzinfo=line.JST).astimezone(tz or line.JST)
    monkeypatch.setattr(line,'datetime',Clock)
    owner=setup(env)
    token=csrf(owner,'/admin/line')
    with owner.session_transaction() as data:
        nonce=data['line_test_key']
    data={'action':'test','csrf_token':token,'test_key':nonce}
    assert owner.post('/admin/line',data={**data,'test_key':'wrong'},base_url='https://localhost').status_code==403
    result=owner.post('/admin/line',data=data,base_url='https://localhost')
    assert result.status_code==200 and 'LINE đã nhận tin nhắc đăng ký'.encode() in result.data
    assert owner.post('/admin/line',data=data,base_url='https://localhost').status_code==200
    assert len(calls)==1
    assert raw.execute('SELECT enabled FROM line_schedule_config').fetchone()[0]==1


def test_disabled_stops_reminder_but_empty_schedule_still_gets_reminder(state):
    _,raw,service,calls,_=state
    now=datetime(2026,11,16,10,tzinfo=line.JST)
    raw.execute('UPDATE line_schedule_config SET enabled=0');raw.commit()
    service.tick(now);assert not calls
    raw.execute('UPDATE line_schedule_config SET enabled=1')
    raw.execute('DELETE FROM shifts');raw.commit()
    service.tick(now);assert len(calls)==1
    assert raw.execute('SELECT count(*) FROM line_schedule_deliveries').fetchone()[0]==1


def webhook(client,events,secret='s'*32):
    body=json.dumps({'events':events}).encode()
    sign=base64.b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).decode()
    return client.post('/line/webhook',data=body,headers={'X-Line-Signature':sign},base_url='https://localhost')


def test_webhook_auth_pair_expiry_and_leave(state):
    app,raw,_,calls,_=state;c=app.test_client()
    event={'type':'message','source':{'type':'group','groupId':'Cnew'},'message':{'type':'text','text':'/lich ABCD1234'}}
    raw.execute('UPDATE line_schedule_config SET pair_hash=?,pair_expires=?',
        (hashlib.sha256(b'ABCD1234').hexdigest(),(datetime.now(line.JST)+timedelta(minutes=30)).isoformat()));raw.commit()
    assert webhook(c,[event],secret='wrong').status_code==403
    assert raw.execute('SELECT group_id FROM line_schedule_config').fetchone()[0]=='Cstaff'
    assert webhook(c,[{**event,'source':{'type':'user','userId':'U1'}}]).status_code==200
    assert raw.execute('SELECT group_id FROM line_schedule_config').fetchone()[0]=='Cstaff'
    assert webhook(c,[event]).status_code==200
    config=raw.execute('SELECT * FROM line_schedule_config').fetchone()
    assert config['group_id']=='Cnew' and not config['enabled'] and config['pair_hash'] is None
    assert webhook(c,[{**event,'source':{'type':'group','groupId':'Cother'}}]).status_code==200
    assert raw.execute('SELECT group_id FROM line_schedule_config').fetchone()[0]=='Cnew'
    assert webhook(c,[{'type':'leave','source':{'type':'group','groupId':'Cnew'}}]).status_code==200
    assert raw.execute('SELECT group_id FROM line_schedule_config').fetchone()[0] is None
    raw.execute('UPDATE line_schedule_config SET pair_hash=?,pair_expires=?',
        (hashlib.sha256(b'ABCD1234').hexdigest(),(datetime.now(line.JST)-timedelta(minutes=1)).isoformat()));raw.commit()
    webhook(c,[event])
    assert raw.execute('SELECT group_id FROM line_schedule_config').fetchone()[0] is None


def test_manager_auth_csrf_encrypted_secrets_and_image_expiry(state):
    app,raw,service,calls,env=state
    client=app.test_client()
    assert client.get('/admin/line',base_url='https://localhost').status_code==302
    assert client.get('/admin/line/preview',base_url='https://localhost').status_code==302
    assert client.post('/admin/line',data={'action':'enable'},base_url='https://localhost').status_code==401
    owner=setup(env)
    assert owner.post('/admin/line',data={'action':'disable'},base_url='https://localhost').status_code==403
    token=csrf(owner,'/admin/line')
    response=owner.post('/admin/line',data={'action':'credentials','secret':'k'*32,'token':'z'*50,'csrf_token':token},base_url='https://localhost')
    assert response.status_code==200
    assert b'z'*50 not in response.data and b'k'*32 not in response.data
    config=raw.execute('SELECT * FROM line_schedule_config').fetchone()
    assert 'z'*50 not in config['credentials'] and not config['enabled'] and config['group_id'] is None
    assert service.credentials(config)['token']=='z'*50
    raw.execute("UPDATE line_schedule_config SET enabled=1,group_id='Cstaff'");raw.commit()
    service.tick(datetime.now(line.JST).replace(year=2026,month=11,day=16,hour=10))
    raw.execute('INSERT INTO line_schedule_deliveries VALUES(?,?,?,?,?,?,?,?)',('legacy-image','x'*43,b'legacy-png','{}','legacy-key',None,datetime.now(line.JST).isoformat(),(datetime.now(line.JST)+timedelta(days=1)).isoformat()));raw.commit()
    path='/line/schedule/'+'x'*43+'.png'
    assert client.get(path,base_url='https://localhost').status_code==200
    raw.execute('UPDATE line_schedule_deliveries SET expires_at=?',((datetime.now(line.JST)-timedelta(days=1)).isoformat(),));raw.commit()
    assert client.get(path,base_url='https://localhost').status_code==404

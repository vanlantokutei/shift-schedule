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
        if sql.startswith('LOCK TABLE'):
            return Result(self.raw.execute('SELECT 1'))
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
    assert message['type']=='flex'
    assert '23/11–29/11/2026' in message['altText']
    assert '19/11/2026 lúc 22:00' in message['altText']
    assert '/kibo?week=2026-11-23' in message['altText']
    assert 'Lân' not in message['altText'] and '99999' not in message['altText']
    assert raw.execute('SELECT accepted_at FROM line_schedule_deliveries').fetchone()['accepted_at']
    assert raw.execute('SELECT week_start FROM line_schedule_deliveries').fetchone()[0]=='reminder:2026-11-16'


def test_reminder_uses_extended_deadline_and_skips_closed_windows(state):
    _,raw,service,calls,_=state
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-23',datetime(2026,11,20,23,30,tzinfo=line.JST).isoformat()));raw.commit()
    now=datetime(2026,11,16,10,tzinfo=line.JST)
    service.tick(now)
    assert '20/11/2026 lúc 23:30' in calls[0][2]['messages'][0]['altText']
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
    assert len(calls)==1 and calls[0][2]['messages'][0]['altText'].startswith('GỬI THỬ')
    assert raw.execute('SELECT enabled FROM line_schedule_config').fetchone()[0]==1
    assert raw.execute('SELECT week_start FROM line_schedule_deliveries').fetchone()[0]=='reminder-test:one'
    service.tick(datetime(2026,11,16,10,tzinfo=line.JST))
    assert len(calls)==2
    assert not calls[1][2]['messages'][0]['altText'].startswith('GỬI THỬ')


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


def publication_preview(owner, week='2026-11-16'):
    return owner.get('/line/publish-preview?week='+week,base_url='https://localhost').get_json()


def publication_post(owner, preview, csrf_token):
    return owner.post('/line/publish',json={'week':preview['week'],'version':preview['version']},
                      headers={'X-CSRF-Token':csrf_token},base_url='https://localhost')


def test_publish_viewed_week_approves_wishes_once_and_preserves_reminders(state,monkeypatch):
    app,raw,service,calls,env=state
    raw.execute('UPDATE line_schedule_config SET enabled=0')
    raw.execute("INSERT INTO shifts VALUES(2,'2026-11-23','08:00','12:00',NULL,NULL,NULL,NULL,1)");raw.commit()
    owner=setup(env);token=csrf(owner,'/admin/line')
    preview=publication_preview(owner)
    assert preview['ok'] and preview['shifts']==2 and preview['wishes']==1
    assert not preview['already_sent']
    image=owner.get('/line/publish-preview.png?week=2026-11-16&version='+preview['version'],base_url='https://localhost')
    assert image.status_code==200 and image.data.startswith(b'\x89PNG')
    capture=[]
    original=line.schedule_image
    def render(week,people,rows):
        capture.append((week,people,rows))
        return original(week,people,rows)
    monkeypatch.setattr(line,'schedule_image',render)
    result=publication_post(owner,preview,token)
    assert result.status_code==200 and result.get_json()['ok']
    assert capture[0][0]==date(2026,11,16) and len(capture[0][2])==2
    assert all('hourly_rate' not in p for p in capture[0][1])
    assert len(calls)==1
    assert calls[0][2]['to']=='Cstaff'
    messages=calls[0][2]['messages']
    assert len(messages)==2 and messages[1]['type']=='image'
    assert '16/11–22/11/2026' in messages[0]['text'] and 'Lịch đã chốt' in messages[0]['text']
    assert raw.execute("SELECT sum(is_kibo) FROM shifts WHERE work_date='2026-11-16'").fetchone()[0]==0
    assert raw.execute("SELECT is_kibo FROM shifts WHERE work_date='2026-11-23'").fetchone()[0]==1
    assert raw.execute('SELECT enabled FROM line_schedule_config').fetchone()[0]==0
    duplicate=publication_post(owner,preview,token).get_json()
    assert duplicate['duplicate'] and len(calls)==1
    assert publication_preview(owner)['already_sent']


def test_publish_rejects_stale_schedule_and_group(state):
    _,raw,_,calls,env=state
    owner=setup(env);token=csrf(owner,'/admin/line')
    preview=publication_preview(owner)
    raw.execute("UPDATE shifts SET start='08:00' WHERE staff_id=1");raw.commit()
    assert publication_post(owner,preview,token).status_code==409
    assert not calls
    preview=publication_preview(owner)
    raw.execute("UPDATE line_schedule_config SET group_id='Cchanged'");raw.commit()
    assert publication_post(owner,preview,token).status_code==409
    assert not calls
    assert raw.execute('SELECT sum(is_kibo) FROM shifts').fetchone()[0]==1


def test_publish_updates_and_failed_retry_keep_exact_snapshot(state,monkeypatch):
    _,raw,_,calls,env=state
    owner=setup(env);token=csrf(owner,'/admin/line')
    assert publication_post(owner,publication_preview(owner),token).status_code==200
    raw.execute("UPDATE shifts SET start='08:30' WHERE staff_id=1");raw.commit()
    preview=publication_preview(owner)
    assert preview['has_previous'] and not preview['already_sent']
    attempts=[]
    def fail(token,path,payload,retry_key):
        attempts.append((payload,retry_key))
        raise RuntimeError('timeout')
    monkeypatch.setattr(line,'line_request',fail)
    response=publication_post(owner,preview,token)
    assert response.status_code==502 and response.get_json()['confirmed']
    assert 'Lịch cập nhật' in attempts[0][0]['messages'][0]['text']
    def succeed(token,path,payload,retry_key):
        attempts.append((payload,retry_key));return {}
    monkeypatch.setattr(line,'line_request',succeed)
    assert publication_post(owner,preview,token).status_code==200
    assert attempts[0]==attempts[1]
    assert raw.execute("SELECT count(*) FROM line_schedule_deliveries WHERE accepted_at IS NOT NULL").fetchone()[0]==2
    assert raw.execute('SELECT enabled FROM line_schedule_config').fetchone()[0]==1


def test_publish_auth_csrf_invalid_week_and_empty_schedule(state):
    app,raw,_,calls,env=state
    anonymous=app.test_client()
    assert anonymous.get('/line/publish-preview?week=2026-11-16',base_url='https://localhost').status_code==302
    assert anonymous.get('/line/publish-preview.png?week=2026-11-16',base_url='https://localhost').status_code==302
    assert anonymous.post('/line/publish',json={},base_url='https://localhost').status_code==401
    owner=setup(env);token=csrf(owner,'/admin/line')
    assert owner.post('/line/publish',json={},base_url='https://localhost').status_code==403
    assert owner.get('/line/publish-preview?week=2026-11-17',base_url='https://localhost').status_code==400
    preview=publication_preview(owner)
    assert publication_post(owner,{**preview,'version':'á'},token).status_code==409
    empty=publication_preview(owner,'2026-11-23')
    assert empty['shifts']==0 and publication_post(owner,empty,token).status_code==400
    raw.execute('UPDATE line_schedule_config SET group_id=NULL');raw.commit()
    assert owner.get('/line/publish-preview?week=2026-11-16',base_url='https://localhost').status_code==409
    assert not calls


def test_rich_menu_only_returns_published_schedule_to_active_linked_staff(state):
    app,raw,_,calls,env=state
    owner=setup(env);token=csrf(owner,'/admin/line')
    assert publication_post(owner,publication_preview(owner),token).status_code==200
    expected=calls[-1][2]['messages']
    uid='U'+'a'*32
    event={'type':'message','replyToken':'reply','source':{'type':'user','userId':uid},
           'message':{'type':'text','text':'Xem lịch'}}
    client=app.test_client()
    # Unlinked outsiders never see a schedule, even with a valid signed event.
    assert webhook(client,[event]).status_code==200
    assert calls[-1][2]['messages'][0]['type']=='text'
    assert 'liên kết tên' in calls[-1][2]['messages'][0]['text']
    raw.execute('INSERT INTO line_staff_links VALUES(?,?,?)',(uid,1,datetime.now(line.JST).isoformat()));raw.commit()
    assert webhook(client,[event]).status_code==200
    assert calls[-1][1]=='/message/reply' and calls[-1][2]['messages']==expected
    raw.execute('UPDATE staff SET active=0 WHERE id=1');raw.commit()
    webhook(client,[event])
    assert calls[-1][2]['messages'][0]['type']=='text'
    # Unrelated groups never trigger the private menu response.
    before=len(calls)
    webhook(client,[{**event,'source':{'type':'group','groupId':'Cother','userId':uid}}])
    assert len(calls)==before


def test_flex_card_has_live_date_deadline_and_registration_action(state):
    _,raw,service,calls,_=state
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-23',datetime(2026,11,20,23,30,tzinfo=line.JST).isoformat()));raw.commit()
    service.tick(datetime(2026,11,16,10,tzinfo=line.JST))
    card=calls[-1][2]['messages'][0]
    contents=json.dumps(card['contents'],ensure_ascii=False)
    assert '23/11 – 29/11' in contents and 'Thứ Sáu · 23:30' in contents
    assert card['contents']['hero']['url'].endswith('/static/line/pho-betoya.jpg')
    assert card['contents']['footer']['contents'][0]['action']['uri'].endswith('/kibo?week=2026-11-23')
    assert '99999' not in contents

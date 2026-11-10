"""Week off is an explicit submission; never erases confirmed/fixed/history data."""
from datetime import datetime
from flask import template_rendered
from test_kibo_deadlines import env, Clock, JST


def ready(monkeypatch,raw):
    monkeypatch.setattr(Clock,'instant',datetime(2026,11,5,18,tzinfo=JST))
    raw.execute("INSERT INTO fixed_kibo VALUES(1,0,'09:00','17:00')")
    raw.execute('''INSERT INTO shifts(staff_id,work_date,start,"end",is_kibo)
        VALUES(1,'2026-11-09','09:00','17:00',1)''')
    raw.execute('''INSERT INTO shifts(staff_id,work_date,start,"end",is_kibo)
        VALUES(1,'2026-11-02','09:00','17:00',0)''');raw.commit()


def context(app,client,path):
    captured=[]
    def capture(sender,template,context,**kw):captured.append(context)
    with template_rendered.connected_to(capture,app):
        response=client.get(path,base_url='https://localhost')
    assert response.status_code==200
    return response,captured[-1]


def test_week_off_records_response_and_does_not_resurrect_fixed_prefill(env,monkeypatch):
    app,client,raw,_=env;ready(monkeypatch,raw)
    result=client.post('/kibo',data={'staff_id':1,'week':'2026-11-09','week_off':'1','start_0':'bad-time','end_0':'17:00'},base_url='https://localhost')
    assert result.status_code==200 and 'Đã ghi nhận Lân nghỉ cả tuần'.encode() in result.data
    assert raw.execute("SELECT count(*) FROM shifts WHERE work_date='2026-11-09'").fetchone()[0]==0
    assert raw.execute("SELECT count(*) FROM shifts WHERE work_date='2026-11-02'").fetchone()[0]==1
    assert raw.execute('SELECT count(*) FROM fixed_kibo').fetchone()[0]==1
    assert raw.execute("SELECT count(*) FROM kibo_submissions WHERE week_start='2026-11-09'").fetchone()[0]==1
    response,c=context(app,client,'/kibo?staff_id=1&week=2026-11-09')
    assert c['week_off_registered'] and not c['existing']
    assert 'Đã báo nghỉ cả tuần'.encode() in response.data
    assert client.get('/kibo/status',base_url='https://localhost').json['count']==1
    _,board=context(app,client,'/')
    assert 1 in board['week_off_ids']


def test_confirmed_week_rejects_off_without_erasing_other_wishes(env,monkeypatch):
    _,client,raw,_=env;ready(monkeypatch,raw)
    raw.execute('''INSERT INTO shifts(staff_id,work_date,start,"end",is_kibo)
        VALUES(1,'2026-11-10','17:00','22:00',0)''');raw.commit()
    before=[tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]
    result=client.post('/kibo',data={'staff_id':1,'week':'2026-11-09','week_off':'1'},base_url='https://localhost')
    assert 'đã có ca quản lý chốt'.encode() in result.data
    assert [tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]==before
    assert raw.execute('SELECT count(*) FROM kibo_submissions').fetchone()[0]==0


def test_week_off_cannot_bypass_deadline(env,monkeypatch):
    _,client,raw,_=env;ready(monkeypatch,raw)
    monkeypatch.setattr(Clock,'instant',datetime(2026,11,5,22,tzinfo=JST))
    result=client.post('/kibo',data={'staff_id':1,'week':'2026-11-09','week_off':'1'},base_url='https://localhost')
    assert 'Đã hết hạn đăng ký'.encode() in result.data
    assert raw.execute("SELECT count(*) FROM shifts WHERE work_date='2026-11-09'").fetchone()[0]==1
    assert raw.execute('SELECT count(*) FROM kibo_submissions').fetchone()[0]==0


def test_can_change_off_week_to_work_and_fixed_prefills_next_week(env,monkeypatch):
    app,client,raw,_=env;ready(monkeypatch,raw)
    client.post('/kibo',data={'staff_id':1,'week':'2026-11-09','week_off':'1'},base_url='https://localhost')
    client.post('/kibo',data={'staff_id':1,'week':'2026-11-09','start_1':'17:00','end_1':'22:00'},base_url='https://localhost')
    _,c=context(app,client,'/kibo?staff_id=1&week=2026-11-09')
    assert not c['week_off_registered'] and set(c['existing'])=={'2026-11-10'}
    _,next_week=context(app,client,'/kibo?staff_id=1&week=2026-11-16')
    assert next_week['existing']['2026-11-16']['prefill_fixed']
    assert raw.execute('SELECT count(*) FROM kibo_submissions').fetchone()[0]==1

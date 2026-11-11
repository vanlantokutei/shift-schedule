from datetime import datetime
import re
import pytest
from kibo_deadlines import JST
from test_branches import tenants, BASE, HEADERS
from test_kibo_deadlines import env,Clock

@pytest.fixture
def full_day(tenants,monkeypatch):
    app,client,raw=tenants
    raw.execute('INSERT INTO branch_kibo_settings(branch_id,always_open,full_day_hours) VALUES(2,0,1)');raw.commit()
    monkeypatch.setattr(Clock,'instant',datetime(2026,11,10,12,tzinfo=JST))
    return app,client,raw


def test_all_48_half_hour_options_only_for_24h_store(full_day):
    _,client,_=full_day
    for branch,sid,expected in [(2,2,48),(1,1,32)]:
        page=client.get(f'/kibo?branch={branch}&staff_id={sid}&week=2026-11-16').text
        options=re.search(r'<select name="start_0">(.*?)</select>',page).group(1)
        times=re.findall(r'<option value="(\d{2}:\d{2})"',options)
        assert len(times)==expected
        assert times[0]==('00:00' if branch==2 else '07:00')
        assert times[-1]==('23:30' if branch==2 else '22:30')
    assert '22:00–06:00' in client.get('/kibo?branch=2&staff_id=2&week=2026-11-16').text


def test_submit_fixed_prefill_and_repeat_overnight(full_day):
    _,client,raw=full_day
    form={'staff_id':2,'week':'2026-11-16','start_0':'22:00','end_0':'06:00'}
    assert 'Đăng ký thành công!' in client.post('/kibo?branch=2',data=form).text
    assert raw.execute("SELECT start,\"end\" FROM shifts WHERE staff_id=2 AND work_date='2026-11-16'").fetchone()[:]==('22:00','06:00')
    response=client.post('/kibo/fixed?branch=2',json={'staff_id':2,'entries':[{'weekday':0,'start':'22:00','end':'06:00'}]})
    assert response.json['saved']==1
    prefill=client.get('/kibo/prefill?branch=2&staff_id=2&week=2026-11-23').json
    assert prefill['entries']['0']=={'start':'22:00','end':'06:00'}
    form.update(start_0='00:00',end_0='08:00')
    assert 'Đăng ký thành công!' in client.post('/kibo?branch=2',data=form).text


def test_manager_accepts_overnight_breaks_and_counts_net_hours(full_day):
    app,client,raw=full_day
    payload={'staff_id':2,'date':'2026-11-16','start':'22:00','end':'06:00','break_start':'23:30','break_end':'00:30','break2_start':'03:00','break2_end':'04:00'}
    response=client.post('/shift?branch=2',json=payload,headers=HEADERS,base_url=BASE)
    assert response.status_code==200
    row=dict(raw.execute("SELECT * FROM shifts WHERE staff_id=2 AND work_date='2026-11-16'").fetchone())
    assert app.view_functions['shift'].__globals__['hours'](row)==6
    page=client.get('/?branch=2&week=2026-11-16',base_url=BASE)
    assert page.status_code==200 and '(+1 ngày)' in page.text
    assert '6.0h' in page.text
    assert '(+1 ngày)' in client.get('/month?branch=2&year=2026&month=11',base_url=BASE).text
    payload['break2_start']='00:00';payload['break2_end']='01:00'
    assert client.post('/shift?branch=2',json=payload,headers=HEADERS,base_url=BASE).status_code==400
    payload['break2_start']='20:00';payload['break2_end']='21:00'
    assert client.post('/shift?branch=2',json=payload,headers=HEADERS,base_url=BASE).status_code==400


def test_overnight_not_accepted_for_tsukiji_and_equal_times_rejected(full_day):
    _,client,_=full_day
    payload={'staff_id':1,'date':'2026-11-16','start':'22:00','end':'06:00'}
    assert client.post('/shift?branch=1',json=payload,headers=HEADERS,base_url=BASE).status_code==400
    form={'staff_id':1,'week':'2026-11-16','start_0':'22:00','end_0':'06:00'}
    assert 'Đăng ký thành công!' not in client.post('/kibo?branch=1',data=form).text
    form.update(staff_id=2,start_0='06:00',end_0='06:00')
    assert 'Đăng ký thành công!' not in client.post('/kibo?branch=2',data=form).text


def test_normal_deadline_is_restored_for_24h_store(full_day,monkeypatch):
    _,client,raw=full_day
    monkeypatch.setattr(Clock,'instant',datetime(2026,11,13,12,tzinfo=JST))
    form={'staff_id':2,'week':'2026-11-16','start_0':'22:00','end_0':'06:00'}
    response=client.post('/kibo?branch=2',data=form)
    assert 'Đã hết hạn' in response.text
    assert 'Kibo mở 24/24' not in response.text
    assert raw.execute("SELECT count(*) FROM shifts WHERE staff_id=2 AND work_date='2026-11-16'").fetchone()[0]==0

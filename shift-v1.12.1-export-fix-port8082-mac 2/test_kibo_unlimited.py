"""24/24 intake only for the configured store, without changing approved shifts."""
from datetime import datetime
import pytest
from kibo_deadlines import JST
from test_branches import tenants, BASE, HEADERS
from test_kibo_deadlines import env, Clock

@pytest.fixture
def unlimited(tenants):
    app,client,raw=tenants
    raw.execute('INSERT INTO branch_kibo_settings(branch_id,always_open) VALUES(2,1)');raw.commit()
    return app,client,raw


@pytest.mark.parametrize('instant',[
    datetime(2026,11,1,12,tzinfo=JST),
    datetime(2026,11,12,22,tzinfo=JST),
    datetime(2026,11,20,23,tzinfo=JST),
])
def test_store_accepts_kibo_before_and_after_normal_window(unlimited,monkeypatch,instant):
    _,client,raw=unlimited
    monkeypatch.setattr(Clock,'instant',instant)
    form={'staff_id':2,'week':'2026-11-16','start_0':'09:00','end_0':'15:00'}
    page=client.get('/kibo?branch=2&staff_id=2&week=2026-11-16')
    assert 'Kibo mở 24/24' in page.text
    assert 'id="kibo-timer"' not in page.text
    assert '<fieldset disabled' not in page.text
    response=client.post('/kibo?branch=2',data=form)
    assert 'Đăng ký thành công!' in response.text
    assert raw.execute("SELECT start FROM shifts WHERE staff_id=2 AND work_date='2026-11-16'").fetchone()[0]=='09:00'
    form['staff_id']=1
    response=client.post('/kibo?branch=1',data=form)
    assert 'Đăng ký thành công!' not in response.text
    assert raw.execute("SELECT count(*) FROM shifts WHERE staff_id=1 AND work_date='2026-11-16'").fetchone()[0]==0


def test_manager_view_shows_unlimited_without_extension_controls(unlimited):
    _,client,_=unlimited
    page=client.get('/?branch=2',base_url=BASE)
    assert 'Kibo mở 24/24' in page.text
    assert 'id="extendKibo"' not in page.text
    assert 'id="deadlineForm"' not in page.text
    page=client.get('/?branch=1',base_url=BASE)
    assert 'Hạn Kibo:' in page.text and 'id="extendKibo"' in page.text


def test_unlimited_keeps_approved_shifts_and_allows_resubmission(unlimited):
    _,client,raw=unlimited
    form={'staff_id':2,'week':'2026-11-09','start_0':'10:00','end_0':'19:00','start_1':'11:00','end_1':'16:00'}
    assert 'Đăng ký thành công!' in client.post('/kibo?branch=2',data=form).text
    form['start_1']='12:00'
    assert 'Đăng ký thành công!' in client.post('/kibo?branch=2',data=form).text
    assert raw.execute("SELECT start,is_kibo FROM shifts WHERE staff_id=2 AND work_date='2026-11-09'").fetchone()[:]==('12:00',0)
    assert raw.execute("SELECT start FROM shifts WHERE staff_id=2 AND work_date='2026-11-10'").fetchone()[0]=='12:00'
    assert client.post('/kibo/deadline?branch=2',json={'week':'2026-11-09','deadline':'2026-11-07T22:00'},headers=HEADERS,base_url=BASE).status_code==409

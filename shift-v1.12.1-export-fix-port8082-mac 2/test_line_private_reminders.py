"""No real recipients: test time windows, audience filters and retry identity."""
from datetime import datetime, timedelta
import json
import pytest
import line_schedule as line
from test_line_schedule import state, webhook
from test_manager_auth import env, setup, csrf

UID1='U'+'a'*32
UID2='U'+'b'*32


@pytest.fixture
def private_state(state):
    app,raw,service,calls,env=state
    raw.execute('CREATE TABLE kibo_submissions(staff_id INTEGER,week_start TEXT,submitted_at TEXT,PRIMARY KEY(staff_id,week_start))')
    raw.execute('DELETE FROM shifts')
    raw.execute("UPDATE line_private_config SET enabled=1")
    raw.execute("UPDATE line_identity_config SET channel_id='2012000011',liff_id='2012000011-testapp'")
    raw.execute('INSERT INTO line_staff_links VALUES(?,?,?)',(UID1,1,'2026-11-10'))
    raw.execute('INSERT INTO line_staff_links VALUES(?,?,?)',(UID2,2,'2026-11-10'));raw.commit()
    return state


def at(day=12,hour=18,minute=0):return datetime(2026,11,day,hour,minute,tzinfo=line.JST)


def test_four_hours_once_and_off_day_submission(private_state):
    _,raw,service,calls,_=private_state
    raw.execute("INSERT INTO kibo_submissions VALUES(2,'2026-11-16','2026-11-10')");raw.commit()
    service.tick(at(hour=17,minute=59));assert not calls
    service.tick(at());service.tick(at(minute=1))
    assert len(calls)==1 and calls[0][2]['to']==UID1
    msg=calls[0][2]['messages'][0]
    assert 'Lân' in msg['altText'] and 'Sương' not in msg['altText']
    assert '12/11/2026 lúc 22:00' in msg['altText']
    assert raw.execute('SELECT count(*) FROM line_private_deliveries').fetchone()[0]==1


def test_extension_moves_trigger_and_no_second_reminder(private_state):
    _,raw,service,calls,_=private_state
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-16',at(day=13,hour=23).isoformat()));raw.commit()
    service.tick(at());service.tick(at(day=13,hour=18,minute=59));assert not calls
    service.tick(at(day=13,hour=19));assert len(calls)==2
    assert all('13/11/2026 lúc 23:00' in c[2]['messages'][0]['altText'] for c in calls)
    raw.execute('UPDATE kibo_deadlines SET deadline=?',(at(day=14,hour=23).isoformat(),));raw.commit()
    service.tick(at(day=14,hour=19));assert len(calls)==2


def test_archived_unlinked_legacy_and_blocked_are_skipped(private_state):
    app,raw,service,calls,_=private_state
    raw.execute('UPDATE staff SET active=0 WHERE id=1')
    raw.execute("INSERT INTO shifts(staff_id,work_date,start,\"end\",is_kibo) VALUES(2,'2026-11-16','17:00','22:00',1)");raw.commit()
    service.tick(at());assert not calls
    raw.execute('UPDATE staff SET active=1');raw.execute('DELETE FROM shifts');raw.execute('DELETE FROM line_staff_links WHERE staff_id=1');raw.commit()
    e={'type':'unfollow','timestamp':200,'source':{'type':'user','userId':UID2}}
    webhook(app.test_client(),[e]);service.tick(at());assert not calls
    # A delayed old follow event cannot override the newer block.
    webhook(app.test_client(),[{**e,'type':'follow','timestamp':100}]);service.tick(at());assert not calls
    webhook(app.test_client(),[{**e,'type':'follow','timestamp':300}]);service.tick(at());assert len(calls)==1


def test_retry_keeps_key_and_stops_when_submitted(private_state,monkeypatch):
    _,raw,service,calls,_=private_state
    raw.execute('DELETE FROM line_staff_links WHERE staff_id=2');raw.commit()
    attempts=[]
    def fail(*args):attempts.append(args);raise RuntimeError('timeout')
    monkeypatch.setattr(line,'line_request',fail)
    service.tick(at());service.tick(at(minute=1));assert len(attempts)==1
    service.tick(at(minute=5));assert attempts[0]==attempts[1]
    raw.execute("INSERT INTO kibo_submissions VALUES(1,'2026-11-16','2026-11-12')");raw.commit()
    service.tick(at(minute=10));assert len(attempts)==2
    service.tick(at(hour=22));assert len(attempts)==2


def test_disabled_and_owner_control_require_csrf(private_state):
    app,raw,service,calls,env=private_state
    raw.execute('UPDATE line_private_config SET enabled=0');raw.commit()
    service.tick(at());assert not calls
    anonymous=app.test_client()
    assert anonymous.post('/admin/line/private',data={'action':'enable'},base_url='https://localhost').status_code==401
    owner=setup(env)
    assert owner.post('/admin/line/private',data={'action':'enable'},base_url='https://localhost').status_code==403
    token=csrf(owner,'/admin/line/private')
    r=owner.post('/admin/line/private',data={'action':'enable','csrf_token':token},base_url='https://localhost')
    assert r.status_code==200 and UID1.encode() not in r.data
    assert raw.execute('SELECT enabled FROM line_private_config').fetchone()[0]==1
    raw.execute('UPDATE line_schedule_config SET enabled=0');raw.commit()
    service.tick(at());assert not calls


def test_restart_after_closed_and_extended_across_week(private_state):
    _,raw,service,calls,_=private_state
    service.tick(at(hour=22));assert not calls
    # Even when extension crosses Monday, the older target week remains eligible.
    deadline=datetime(2026,11,17,22,tzinfo=line.JST)
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-16',deadline.isoformat()));raw.commit()
    service.tick(deadline-timedelta(hours=3));assert len(calls)==2


def test_pending_extension_never_reuses_key_for_changed_payload(private_state,monkeypatch):
    _,raw,service,calls,_=private_state
    raw.execute('DELETE FROM line_staff_links WHERE staff_id=2');raw.commit()
    attempts=[]
    def fail(*args):attempts.append(args);raise RuntimeError('timeout')
    monkeypatch.setattr(line,'line_request',fail)
    service.tick(at());assert len(attempts)==1
    raw.execute('INSERT INTO kibo_deadlines VALUES(?,?)',('2026-11-16',at(day=13,hour=22).isoformat()));raw.commit()
    service.tick(at(day=13));assert len(attempts)==1
    assert raw.execute('SELECT stopped FROM line_private_deliveries').fetchone()[0]==1

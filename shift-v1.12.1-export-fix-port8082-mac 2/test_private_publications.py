import json
from datetime import date,datetime,timedelta
import pytest
import line_schedule as line
from test_line_schedule import state
from test_manager_auth import env

WEEK=date(2026,11,16)
NOW=datetime(2026,11,16,12,tzinfo=line.JST)
U1='U'+'a'*32
U2='U'+'b'*32

@pytest.fixture
def linked(state):
    app,raw,service,calls,environment=state
    raw.execute('INSERT INTO line_staff_links(line_user_id,staff_id) VALUES(?,?)',(U1,1))
    raw.execute('INSERT INTO line_staff_links(line_user_id,staff_id) VALUES(?,?)',(U2,2))
    raw.execute("INSERT INTO staff(id,name,active,sort_order,hourly_rate,branch_id) VALUES(3,'Other Branch',1,0,88888,2)")
    raw.execute('INSERT INTO line_staff_links(line_user_id,staff_id) VALUES(?,3)',('U'+'c'*32,))
    raw.commit()
    return state


def publish(state,now=NOW):
    _,_,service,_,_=state
    _,_,version=line.publication_snapshot(service.connect(),WEEK,'Cstaff')
    return service.publish(WEEK,version,now=now)


def test_publish_sends_group_then_private_only_own_schedule_without_wages(linked):
    app,raw,service,calls,_=linked
    result,status=publish(linked)
    assert status==200 and result['private']['total']==2
    assert len(calls)==1 and calls[0][2]['to']=='Cstaff'
    app.extensions['line_private_publications'].tick(NOW)
    assert len(calls)==3
    personal={call[2]['to']:call[2]['messages'][0]['text'] for call in calls[1:]}
    assert set(personal)=={U1,U2}
    assert 'Lân' in personal[U1] and 'Sương' not in personal[U1]
    assert 'Sương' in personal[U2] and 'Lân' not in personal[U2]
    assert '07:30–19:30' in personal[U1] and '12:00–12:45' in personal[U1]
    assert '17:00–22:00' in personal[U2]
    assert 'T3 17/11: —' in personal[U1]
    assert all('99999' not in text and 'Other Branch' not in text for text in personal.values())
    app.extensions['line_private_publications'].tick(NOW+timedelta(minutes=6))
    assert len(calls)==3
    assert publish(linked)[0]['duplicate'] and len(calls)==3


def test_partial_failure_retries_only_failed_recipient_with_same_key(linked,monkeypatch):
    app,_,service,calls,_=linked
    publish(linked)
    attempts=[]
    def send(token,path,payload,key):
        attempts.append((payload['to'],key))
        if payload['to']==U2 and sum(uid==U2 for uid,_ in attempts)==1:raise RuntimeError('offline')
    queue=app.extensions['line_private_publications'];queue.send=send
    queue.tick(NOW)
    queue.tick(NOW+timedelta(minutes=1))
    assert len(attempts)==2
    queue.tick(NOW+timedelta(minutes=6))
    assert [uid for uid,_ in attempts]==[U1,U2,U2]
    assert attempts[1][1]==attempts[2][1]
    assert len(calls)==1


def test_group_failure_never_sends_private_until_group_accepted(linked,monkeypatch):
    app,_,service,calls,_=linked
    original=line.line_request
    monkeypatch.setattr(line,'line_request',lambda *args,**kw: (_ for _ in ()).throw(RuntimeError('offline')))
    assert publish(linked)[1]==502
    queue=app.extensions['line_private_publications'];queue.tick(NOW)
    assert calls==[]
    monkeypatch.setattr(line,'line_request',original)
    assert publish(linked,now=NOW+timedelta(minutes=1))[1]==200
    queue.tick(NOW+timedelta(minutes=1))
    assert len(calls)==3 and calls[0][2]['to']=='Cstaff'


def test_removed_link_or_blocked_user_not_sent(linked):
    app,raw,_,calls,_=linked
    publish(linked)
    raw.execute('DELETE FROM line_staff_links WHERE staff_id=1')
    raw.execute('INSERT INTO line_private_follows(line_user_id,active,event_ts) VALUES(?,0,1)',(U2,));raw.commit()
    app.extensions['line_private_publications'].tick(NOW)
    assert len(calls)==1
    assert raw.execute('SELECT sum(stopped) FROM line_private_publications').fetchone()[0]==2


def test_updated_publication_supersedes_pending_old_private_jobs(linked):
    app,raw,_,calls,_=linked
    publish(linked)
    raw.execute("UPDATE shifts SET start='08:00' WHERE staff_id=1");raw.commit()
    assert publish(linked,now=NOW+timedelta(minutes=1))[1]==200
    app.extensions['line_private_publications'].tick(NOW+timedelta(minutes=1))
    assert len(calls)==4
    assert '08:00–19:30' in calls[2][2]['messages'][0]['text']
    assert raw.execute('SELECT sum(stopped) FROM line_private_publications').fetchone()[0]==2


def test_expiry_and_explicit_private_only_send_for_existing_group_version(linked):
    app,raw,_,calls,_=linked
    publish(linked)
    app.extensions['line_private_publications'].tick(NOW+timedelta(days=1))
    assert len(calls)==1
    raw.execute('DELETE FROM line_private_publications');raw.commit()
    assert publish(linked)[0]['duplicate']
    assert raw.execute('SELECT count(*) FROM line_private_publications').fetchone()[0]==2
    assert len(calls)==1
    app.extensions['line_private_publications'].tick(NOW)
    assert len(calls)==3

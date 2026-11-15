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
    personal={call[2]['to']:call[2]['messages'][0]['altText'] for call in calls[1:]}
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
    assert '08:00–19:30' in calls[2][2]['messages'][0]['altText']
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


def test_update_only_notifies_changed_employee_and_shows_old_shift(linked):
    app,raw,_,calls,_=linked
    publish(linked)
    queue=app.extensions['line_private_publications'];queue.tick(NOW)
    raw.execute("UPDATE shifts SET start='08:00' WHERE staff_id=1");raw.commit()
    result,status=publish(linked,now=NOW+timedelta(minutes=1))
    assert status==200 and result['private']['total']==1
    queue.tick(NOW+timedelta(minutes=1))
    assert len(calls)==5 and calls[-1][2]['to']==U1
    card=calls[-1][2]['messages'][0]
    assert card['type']=='flex'
    text=json.dumps(card,ensure_ascii=False)
    assert 'Trước:' not in text and '07:30–19:30' not in text and '08:00–19:30' in text
    assert 'Đã đổi' in text and 'Xem lịch của tôi' in text
    assert '/staff/schedule?mine=1&week=2026-11-16' in text
    assert 'Sương' not in text and '99999' not in text


def test_rest_change_and_deleted_shift_notify_only_affected_person(linked):
    app,raw,_,calls,_=linked
    publish(linked);queue=app.extensions['line_private_publications'];queue.tick(NOW)
    raw.execute("UPDATE shifts SET break_end='13:00' WHERE staff_id=1")
    raw.execute('DELETE FROM shifts WHERE staff_id=2');raw.commit()
    result,status=publish(linked,now=NOW+timedelta(minutes=1))
    assert status==200 and result['private']['total']==2
    queue.tick(NOW+timedelta(minutes=1))
    assert {x[2]['to'] for x in calls[-2:]}=={U1,U2}
    assert '17:00–22:00' not in json.dumps(calls[-1][2],ensure_ascii=False)
    assert 'Đã đổi' in json.dumps(calls[-1][2],ensure_ascii=False)


def test_net_hours_two_breaks_overnight_and_other_staff_excluded():
    from line_private_publications import net_minutes,private_card
    rows=[dict(staff_id=1,work_date='2026-11-16',start='22:00',end='06:00',
        break_start='23:30',break_end='00:30',break2_start='03:00',break2_end='03:30')]
    assert net_minutes(rows)==390
    card=private_card(WEEK,{'id':1,'name':'Lân'},rows+[dict(rows[0],staff_id=2)],schedule_url='https://example.com/staff/schedule?mine=1')
    assert 'Tổng giờ: 6 giờ 30 phút' in json.dumps(card,ensure_ascii=False)
    assert 'Trước:' not in json.dumps(card,ensure_ascii=False)


def test_owner_private_card_test_is_single_recipient_idempotent_and_not_publish(linked):
    import re
    from test_manager_auth import setup,csrf
    app,raw,service,calls,environment=linked
    publish(linked)
    client=setup(environment)
    path='/admin/line/private-test?staff_id=1&week=2026-11-16'
    page=client.get(path,base_url='https://localhost')
    assert page.status_code==200
    fields=dict((key,value) for key,value in re.findall(r'name="([^"]+)" value="([^"]*)"',page.data.decode()))
    before=len(calls)
    result=client.post(path,data=fields,base_url='https://localhost')
    assert result.status_code==200 and 'LINE đã tiếp nhận'.encode() in result.data
    assert len(calls)==before+1 and calls[-1][2]['to']==U1
    assert calls[-1][2]['messages'][0]['type']=='flex'
    assert calls[-1][2]['messages'][0]['altText'].startswith('GỬI THỬ')
    assert raw.execute('SELECT count(*) FROM staff_schedule_publications').fetchone()[0]==1
    assert client.post(path,data=fields,base_url='https://localhost').status_code==200
    assert len(calls)==before+1
    assert client.post(path,data={},base_url='https://localhost').status_code==403
    assert app.test_client().post(path,data=fields,base_url='https://localhost').status_code==401
    assert client.get('/admin/line/private-test?staff_id=3&week=2026-11-16',base_url='https://localhost').status_code==200
    assert len(calls)==before+1

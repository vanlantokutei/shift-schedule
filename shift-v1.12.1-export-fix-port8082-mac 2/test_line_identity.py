"""Token verification and employee association; never uses real LINE accounts."""
import io
import json
import time
from urllib.parse import parse_qs

import pytest
import line_identity as identity
from test_line_schedule import state, webhook
from test_manager_auth import env, setup, csrf

UID1='U'+'a'*32
UID2='U'+'b'*32


@pytest.fixture
def linked_state(state,monkeypatch):
    app,raw,service,calls,manager_env=state
    raw.execute("UPDATE line_identity_config SET channel_id='2012000011',liff_id='2012000011-testapp'");raw.commit()
    def verify(token,channel):
        assert channel=='2012000011'
        if token not in {'valid-one','valid-two'}: raise ValueError('invalid')
        return (UID1 if token=='valid-one' else UID2),time.time()+3600
    monkeypatch.setattr(identity,'verify_line_token',verify)
    return state


def post(client,path,body,origin=None):
    headers={'Origin':origin} if origin else {}
    return client.post(path,json=body,headers=headers,base_url='https://localhost')


@pytest.mark.parametrize('change',[{'aud':'other'},{'iss':'wrong'},{'exp':0},{'sub':'forged'},{}])
def test_server_verifies_raw_token_audience_issuer_and_expiry(monkeypatch,change):
    data={'aud':'2012000011','iss':'https://access.line.me','exp':time.time()+3600,'sub':UID1}
    data.update(change)
    requests=[]
    def response(request,timeout):
        requests.append(parse_qs(request.data.decode()))
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(identity,'urlopen',response)
    if change:
        with pytest.raises(ValueError):identity.verify_line_token('raw-id-token-'+'x'*30,'2012000011')
    else:
        assert identity.verify_line_token('raw-id-token-'+'x'*30,'2012000011')[0]==UID1
    assert requests[0]['client_id']==['2012000011']
    assert requests[0]['id_token']==['raw-id-token-'+'x'*30]


def test_first_selection_is_remembered_across_browsers(linked_state):
    app,raw,_,_,_=linked_state
    one=app.test_client()
    profile=post(one,'/line/identity',{'id_token':'valid-one','userId':UID2}).get_json()
    assert profile['linked'] is None and len(profile['staff'])==2
    response=post(one,'/line/identity/link',{'id_token':'valid-one','staff_id':1})
    assert response.status_code==200 and response.get_json()['linked']['name']=='Lân'
    other_browser=app.test_client()
    profile=post(other_browser,'/line/identity',{'id_token':'valid-one'}).get_json()
    assert profile['linked']['id']==1
    assert UID1 not in json.dumps(profile) and UID2 not in json.dumps(profile)
    remembered=other_browser.get('/kibo?week=2026-11-16',base_url='https://localhost')
    assert remembered.status_code==302 and 'staff_id=1' in remembered.headers['Location']
    assert 'week=2026-11-16' in remembered.headers['Location']
    assert other_browser.post('/kibo',data={'staff_id':2},base_url='https://localhost').status_code==409
    assert other_browser.post('/kibo/fixed',json={'staff_id':2},base_url='https://localhost').status_code==409
    assert other_browser.get('/kibo?staff_id=1',base_url='https://localhost').status_code==200


def test_no_duplicate_name_claims_and_self_change_preserves_schedules(linked_state):
    app,raw,_,_,_=linked_state
    one=app.test_client();two=app.test_client()
    before=[tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]
    assert post(one,'/line/identity/link',{'id_token':'valid-one','staff_id':1}).status_code==200
    assert post(two,'/line/identity/link',{'id_token':'valid-two','staff_id':1}).status_code==409
    assert post(one,'/line/identity/link',{'id_token':'valid-one','staff_id':2}).status_code==200
    assert raw.execute('SELECT count(*) FROM line_staff_links').fetchone()[0]==1
    assert raw.execute('SELECT staff_id FROM line_staff_links').fetchone()[0]==2
    assert post(two,'/line/identity/link',{'id_token':'valid-two','staff_id':1}).status_code==200
    raw.execute('UPDATE staff SET active=0 WHERE id=1');raw.commit()
    assert post(two,'/line/identity',{'id_token':'valid-two'}).get_json()['linked'] is None
    assert post(two,'/line/identity/link',{'id_token':'valid-two','staff_id':1}).status_code==409
    assert [tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]==before


def test_unverified_identity_and_cross_origin_cannot_link(linked_state):
    app,raw,_,_,_=linked_state;c=app.test_client()
    assert post(c,'/line/identity/link',{'id_token':'fake','staff_id':1,'sub':UID1}).status_code==401
    assert post(c,'/line/identity/link',{'id_token':'valid-one','staff_id':1},'https://evil.example').status_code==403
    assert c.post('/line/identity/link',data={'staff_id':1},base_url='https://localhost').status_code==400
    assert raw.execute('SELECT count(*) FROM line_staff_links').fetchone()[0]==0
    assert c.get('/line/privacy',base_url='https://localhost').status_code==200


def test_only_owner_can_configure_or_unlink_and_reminder_uses_liff(linked_state):
    app,raw,service,calls,manager_env=linked_state
    anonymous=app.test_client()
    assert anonymous.get('/admin/line/identity',base_url='https://localhost').status_code==302
    post(anonymous,'/line/identity/link',{'id_token':'valid-one','staff_id':1})
    owner=setup(manager_env);token=csrf(owner,'/admin/line/identity')
    assert owner.post('/admin/line/identity',data={'action':'unlink','staff_id':1},base_url='https://localhost').status_code==403
    before=raw.execute('SELECT count(*) FROM shifts').fetchone()[0]
    response=owner.post('/admin/line/identity',data={'action':'unlink','staff_id':1,'csrf_token':token},base_url='https://localhost')
    assert response.status_code==200 and raw.execute('SELECT count(*) FROM line_staff_links').fetchone()[0]==0
    assert raw.execute('SELECT count(*) FROM shifts').fetchone()[0]==before
    from datetime import datetime
    service.tick(datetime(2026,11,16,10,tzinfo=identity.JST))
    assert 'https://liff.line.me/2012000011-testapp?week=2026-11-23' in calls[-1][2]['messages'][0]['text']


def test_feature_is_inactive_without_liff_config(state):
    app,raw,_,_,_=state;c=app.test_client()
    assert post(c,'/line/identity',{'id_token':'valid-one'}).status_code==503
    assert 'đang được thiết lập'.encode() in c.get('/kibo/line',base_url='https://localhost').data
    assert c.get('/kibo',base_url='https://localhost').status_code==200


def test_joining_paired_group_gets_identity_button_only_after_setup(linked_state):
    app,raw,_,calls,_=linked_state;c=app.test_client()
    event={'type':'memberJoined','source':{'type':'group','groupId':'Cstaff'},
           'replyToken':'test-reply-token','joined':{'members':[{'type':'user','userId':UID1}]}}
    assert webhook(c,[{**event,'source':{'type':'group','groupId':'Cother'}}]).status_code==200
    assert not calls
    assert webhook(c,[event]).status_code==200
    assert calls[-1][1]=='/message/reply'
    message=calls[-1][2]['messages'][0]
    assert message['type']=='template' and message['template']['actions'][0]['uri'].startswith('https://liff.line.me/')
    assert raw.execute('SELECT count(*) FROM line_staff_links').fetchone()[0]==0

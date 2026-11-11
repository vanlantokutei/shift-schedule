"""Exercise branch boundaries through actual schedule routes."""
import pytest
from branches import install_branches
from test_kibo_deadlines import env

BASE = 'https://localhost'
HEADERS = {'X-CSRF-Token':'test-csrf'}

@pytest.fixture
def tenants(env):
    app, client, raw, connect = env
    raw.executescript('''
        CREATE TABLE branches(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
        INSERT INTO branches VALUES(1,'ベト屋築地店'),(2,'デリメシ店');
        CREATE TABLE branch_kibo_deadlines(branch_id INTEGER,week_start TEXT,deadline TEXT,PRIMARY KEY(branch_id,week_start));
        INSERT INTO staff VALUES(2,'Deli Staff',1,0,'baito','hourly',1400,0,2);
        INSERT INTO shifts VALUES(1,'2026-11-09','08:00','12:00',0,NULL,NULL,NULL,NULL,0);
        INSERT INTO shifts VALUES(2,'2026-11-09','12:00','17:00',0,NULL,NULL,NULL,NULL,0);
    ''')
    raw.execute("UPDATE manager_accounts SET display_name='Admin' WHERE id=1")
    raw.commit()
    install_branches(app, connect)
    return app,client,raw


def test_owner_sees_only_selected_branch_week_month_and_draft(tenants):
    _,client,_ = tenants
    for route in ['/?week=2026-11-09', '/month?year=2026&month=11']:
        deli=client.get(route+'&branch=2',base_url=BASE)
        tsukiji=client.get(route+'&branch=1',base_url=BASE)
        assert deli.status_code==tsukiji.status_code==200
        assert 'Deli Staff' in deli.text and 'Lân' not in deli.text
        assert 'Lân' in tsukiji.text and 'Deli Staff' not in tsukiji.text
        assert 'デリメシ店' in deli.text
    assert client.get('/draft-preview?week=2026-11-09&branch=2',base_url=BASE).status_code==200


def test_wrong_branch_cannot_modify_staff_shift_or_fixed_kibo(tenants):
    _,client,raw=tenants
    for path,payload in [('/shift',dict(staff_id=1,date='2026-11-09',start='10:00',end='19:00')),
                         ('/staff',dict(action='rename',id=1,name='Wrong')),
                         ('/staff',dict(action='archive',id=1)),
                         ('/kibo/fixed',dict(staff_id=1,entries=[]))]:
        response=client.post(path+'?branch=2',json=payload,headers=HEADERS,base_url=BASE)
        assert response.status_code in (404,409)
    assert raw.execute('SELECT name FROM staff WHERE id=1').fetchone()[0]=='Lân'
    assert raw.execute('SELECT start FROM shifts WHERE staff_id=1').fetchone()[0]=='08:00'
    client.post('/staff/reorder?branch=2',json={'order':[1]},headers=HEADERS,base_url=BASE)
    assert raw.execute('SELECT sort_order FROM staff WHERE id=1').fetchone()[0]==0


def test_tencho_pinned_and_owner_directory_protected(tenants):
    _,client,raw=tenants
    raw.execute("UPDATE manager_accounts SET role='manager',branch_id=2 WHERE id=1");raw.commit()
    page=client.get('/?week=2026-11-09',base_url=BASE)
    assert page.status_code==200 and 'Deli Staff' in page.text and 'Lân' not in page.text
    assert client.get('/?branch=1',base_url=BASE).status_code==403
    assert client.get('/admin/branches',base_url=BASE).status_code==403
    assert client.get('/admin/accounts',base_url=BASE).status_code==403


def test_public_kibo_is_scoped_and_rejects_other_branch_id(tenants):
    app,_,_=tenants
    client=app.test_client()
    page=client.get('/kibo?branch=2&week=2026-11-09')
    assert page.status_code==200 and 'Deli Staff' in page.text and 'Lân' not in page.text
    assert client.get('/kibo?branch=2&staff_id=1').status_code==409
    assert client.get('/kibo/prefill?branch=2&staff_id=1&week=2026-11-09').status_code==409


def test_line_cannot_publish_or_read_tsukiji_from_another_branch(tenants):
    app,client,_=tenants
    app.add_url_rule('/line/publish',endpoint='fake_publish',view_func=lambda:'MUST NOT RUN',methods=['POST'])
    app.add_url_rule('/staff/schedule',endpoint='staff_schedule',view_func=lambda:'PRIVATE LINE SNAPSHOT')
    assert client.post('/line/publish?branch=2',json={},headers=HEADERS,base_url=BASE).status_code==409
    assert client.get('/staff/schedule?branch=2',base_url=BASE).status_code==409


def test_duplicate_branch_no_creation_and_csrf_required(tenants):
    _,client,raw=tenants
    assert client.get('/admin/branches',base_url=BASE).status_code==200
    assert client.post('/admin/branches',data={'name':'Other'},base_url=BASE).status_code==403
    response=client.post('/admin/branches',data={'name':'デリメシ店','csrf_token':'test-csrf'},base_url=BASE)
    assert 'đã tồn tại' in response.text
    assert raw.execute('SELECT count(*) FROM branches').fetchone()[0]==2


def test_add_branch_and_employee_starts_empty(tenants):
    _,client,raw=tenants
    response=client.post('/admin/branches',data={'name':'New Store','csrf_token':'test-csrf'},base_url=BASE)
    assert response.status_code==302 and 'branch=3' in response.location
    page=client.get(response.location,base_url=BASE)
    assert page.status_code==200 and 'Deli Staff' not in page.text and 'Lân' not in page.text
    response=client.post('/staff?branch=3',json={'action':'add','name':'New Person'},headers=HEADERS,base_url=BASE)
    assert response.status_code==200
    assert raw.execute("SELECT branch_id FROM staff WHERE name='New Person'").fetchone()[0]==3


def test_branch_deadline_does_not_extend_tsukiji(tenants):
    _,client,raw=tenants
    response=client.post('/kibo/deadline?branch=2',json={'week':'2026-11-09','deadline':'2026-11-07T22:00'},headers=HEADERS,base_url=BASE)
    assert response.status_code==200
    assert raw.execute('SELECT count(*) FROM kibo_deadlines').fetchone()[0]==0
    assert raw.execute('SELECT branch_id FROM branch_kibo_deadlines').fetchone()[0]==2
    assert '07/11/2026 lúc 22:00' in client.get('/kibo?branch=2&week=2026-11-09').text
    assert 'data-close="2026-11-05T22:00:00+09:00"' in client.get('/kibo?branch=1&week=2026-11-09').text

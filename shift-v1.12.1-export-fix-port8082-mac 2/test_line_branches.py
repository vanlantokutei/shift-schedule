"""Cross-store LINE boundaries through real routes and real PostgreSQL DDL."""
from datetime import date, datetime
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess

from flask import Flask
import pytest
import line_schedule as line
from line_branches import LineDatabase, TABLES, branch_scope, branch_url
from branches import install_branches
from test_line_schedule import state, Connection
from test_manager_auth import env, setup

BASE = 'https://localhost'
DELI = {'id': 2, 'name': 'デリメシ店'}


class TenantConnection(Connection):
    """SQLite equivalent of PostgreSQL search_path, only for route tests."""
    def __init__(self, raw):
        super().__init__(raw)
        self.tenant = False

    def execute(self, sql, args=()):
        if 'set_config' in sql:
            self.tenant = args == ('line_branch_2,public',)
            return super().execute('SELECT 1')
        if self.tenant:
            sql = re.sub(r'\b(' + '|'.join(TABLES) + r')\b', lambda m: 'deli_' + m[0], sql)
        return super().execute(sql, args)


@pytest.fixture
def tenants(state, monkeypatch):
    app, raw, service, calls, env_ = state
    for table in TABLES:
        ddl = raw.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        raw.execute(ddl.replace(table, 'deli_' + table))
    for table in ('line_schedule_config', 'line_identity_config', 'line_private_config'):
        raw.execute(f'INSERT INTO deli_{table}(id) VALUES(1)')
    raw.execute("INSERT INTO staff VALUES(3,'Deli Staff',1,0,1500,2)")
    raw.execute("INSERT INTO shifts VALUES(3,'2026-11-16','22:00','06:00',NULL,NULL,NULL,NULL,1)")
    raw.execute('CREATE TABLE branch_kibo_deadlines(branch_id INTEGER,week_start TEXT,deadline TEXT)')
    raw.execute('CREATE TABLE branch_kibo_settings(branch_id INTEGER,always_open INTEGER,full_day_hours INTEGER)')
    raw.commit()
    monkeypatch.setattr(service.connect, 'prepare', lambda number: None)
    service.connect.base_connect = lambda: TenantConnection(raw)
    install_branches(app, service.connect.base_connect)
    return app, raw, service, calls, setup(env_)


def test_separate_bot_credentials_and_group_pairing(tenants, monkeypatch):
    app, raw, service, calls, client = tenants
    headers = {'X-CSRF-Token': ''}
    with client.session_transaction() as s:
        headers['X-CSRF-Token'] = s['csrf']
    original = raw.execute('SELECT credentials,group_id FROM line_schedule_config').fetchone()
    # A Tsukiji token must never be accepted for Deli.
    response = client.post('/admin/line?branch=2', data={'csrf_token': headers['X-CSRF-Token'],
        'action': 'credentials', 'token': 'd'*50, 'secret': 'z'*32}, base_url=BASE)
    assert response.status_code == 200 and 'Hãy dùng token của bot @829gakri' in response.text
    monkeypatch.setattr(line, 'line_request', lambda *a, **kw: {'basicId':'@829gakri', 'groupName':'Deli group'})
    response = client.post('/admin/line?branch=2', data={'csrf_token': headers['X-CSRF-Token'],
        'action': 'credentials', 'token': 'd'*50, 'secret': 'z'*32}, base_url=BASE)
    assert response.status_code == 200 and 'Đã lưu kết nối bot' in response.text
    assert 'webhook?branch=2' in response.text
    client.post('/admin/line?branch=2', data={'csrf_token':headers['X-CSRF-Token'],'action':'pair'}, base_url=BASE)
    with client.session_transaction() as s:
        code = s['line_pair_code_2']
    data = json.dumps({'events':[{'type':'message','source':{'type':'group','groupId':'Cdeli'},
        'message':{'type':'text','text':'/lich '+code}}]}).encode()
    signature = base64.b64encode(hmac.new(b'z'*32, data, hashlib.sha256).digest()).decode()
    anonymous = app.test_client()
    assert anonymous.post('/line/webhook?branch=1', data=data, headers={'X-Line-Signature':signature},base_url=BASE).status_code==403
    assert anonymous.post('/line/webhook?branch=2', data=data, headers={'X-Line-Signature':signature},base_url=BASE).status_code==200
    assert raw.execute('SELECT group_id FROM deli_line_schedule_config').fetchone()[0]=='Cdeli'
    assert tuple(raw.execute('SELECT credentials,group_id FROM line_schedule_config').fetchone())==tuple(original)


def test_publish_and_private_worker_only_deliver_selected_branch(tenants):
    app,raw,service,calls,client=tenants
    sealed=service.cipher.encrypt(json.dumps({'token':'d'*50,'secret':'z'*32}).encode()).decode()
    raw.execute("UPDATE deli_line_schedule_config SET credentials=?,group_id='Cdeli',group_name='Deli group',public_origin='https://localhost'",(sealed,))
    raw.execute("INSERT INTO deli_line_staff_links(line_user_id,staff_id) VALUES('Udeli',3)")
    raw.execute("INSERT INTO line_staff_links(line_user_id,staff_id) VALUES('Utsukiji',1)")
    raw.commit()
    response=client.get('/line/publish-preview?branch=2&week=2026-11-16',base_url=BASE)
    preview=response.get_json()
    assert response.status_code==200 and preview['shifts']==1 and preview['private_recipients']==1
    assert 'デリメシ店' in preview['message_preview'] and 'Tsukiji' not in preview['message_preview']
    with client.session_transaction() as s: csrf=s['csrf']
    result=client.post('/line/publish?branch=2',json={'week':'2026-11-16','version':preview['version']},headers={'X-CSRF-Token':csrf},base_url=BASE)
    assert result.status_code==200
    assert len(calls)==1 and calls[0][2]['to']=='Cdeli' and calls[0][0]=='d'*50
    image_url=calls[0][2]['messages'][1]['originalContentUrl']
    assert image_url.endswith('?branch=2')
    assert raw.execute('SELECT is_kibo FROM shifts WHERE staff_id=3').fetchone()[0]==0
    assert raw.execute('SELECT is_kibo FROM shifts WHERE staff_id=2').fetchone()[0]==1
    with branch_scope(DELI):
        app.extensions['line_private_publications'].tick()
    assert len(calls)==2 and calls[1][2]['to']=='Udeli' and calls[1][0]=='d'*50
    assert '22:00–06:00 (+1 ngày)' in calls[1][2]['messages'][0]['text']
    assert 'Lân' not in calls[1][2]['messages'][0]['text']
    # A repeat accepts the same publication without sending it again.
    result=client.post('/line/publish?branch=2',json={'week':'2026-11-16','version':preview['version']},headers={'X-CSRF-Token':csrf},base_url=BASE)
    assert result.get_json()['duplicate'] and len(calls)==2
    assert app.test_client().get(image_url.replace('https://localhost',''),base_url=BASE).status_code==200
    assert app.test_client().get(image_url.replace('https://localhost','').replace('branch=2','branch=1'),base_url=BASE).status_code==404
    raw.execute("UPDATE manager_accounts SET role='manager',branch_id=2 WHERE id=1");raw.commit()
    assert client.get('/line/publish-preview?branch=1&week=2026-11-16',base_url=BASE).status_code==403
    assert client.get('/line/publish-preview?branch=2&week=2026-11-16',base_url=BASE).status_code==200


def test_worker_context_and_urls_restore_after_exception():
    assert branch_url('/staff/schedule')=='/staff/schedule'
    with pytest.raises(RuntimeError):
        with branch_scope(DELI):
            assert branch_url('/staff/schedule?mine=1')=='/staff/schedule?mine=1&branch=2'
            raise RuntimeError()
    assert branch_url('/staff/schedule')=='/staff/schedule'


def test_postgresql_schema_migration_preserves_tsukiji_and_is_empty_for_deli(tmp_path):
    """Run actual PostgreSQL single-user mode (no sockets or production DB)."""
    pgserver=pytest.importorskip('pgserver')
    bin_dir=Path(pgserver.__file__).parent/'pginstall/bin'
    pgdata=tmp_path/'pg'
    subprocess.run([str(bin_dir/'initdb'),'-D',str(pgdata),'--auth=trust','--no-locale'],check=True,capture_output=True)
    statements=[]
    class Recorder:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,args=()):
            for arg in args:
                sql=sql.replace('%s',str(arg) if isinstance(arg,int) else "'"+str(arg).replace("'","''")+"'",1)
            statements.append(' '.join(sql.split())+';')
            return self
    app=Flask(__name__);app.secret_key='migration-test'
    line.install_line_schedule(app,Recorder,start_worker=False)
    statements.append("UPDATE public.line_schedule_config SET credentials='existing-tsukiji';")
    db=LineDatabase(Recorder);db.prepare(2)
    statements.append("DO $$ BEGIN IF (SELECT credentials FROM public.line_schedule_config WHERE id=1)<>'existing-tsukiji' OR (SELECT credentials FROM line_branch_2.line_schedule_config WHERE id=1) IS NOT NULL THEN RAISE EXCEPTION 'credential isolation failed'; END IF; END $$;")
    statements.append("INSERT INTO public.staff_schedule_publications(publication_key,week_start,content) VALUES('same-key','2026-11-16','tsukiji');")
    statements.append("SET search_path=line_branch_2,public; INSERT INTO staff_schedule_publications(publication_key,week_start,content) VALUES('same-key','2026-11-16','deli'); COMMIT;")
    statements.append("DO $$ BEGIN IF (SELECT content FROM staff_schedule_publications WHERE publication_key='same-key')<>'deli' OR (SELECT content FROM public.staff_schedule_publications WHERE publication_key='same-key')<>'tsukiji' THEN RAISE EXCEPTION 'snapshot isolation failed'; END IF; END $$;")
    result=subprocess.run([str(bin_dir/'postgres'),'--single','-D',str(pgdata),'postgres'],input='\n'.join(statements)+'\n',text=True,capture_output=True)
    assert result.returncode==0 and 'ERROR:' not in result.stderr and 'FATAL:' not in result.stderr,result.stderr


def test_liff_state_and_identity_cannot_link_other_branch_staff(tenants, monkeypatch):
    app,raw,service,calls,client=tenants
    raw.execute("UPDATE deli_line_identity_config SET channel_id='2012000011',liff_id='2012000011-shared'")
    raw.commit()
    public=app.test_client()
    page=public.get('/kibo/line?liff.state=%3Fbranch%3D2%26week%3D2026-11-16',base_url=BASE)
    assert page.status_code==200 and 'data-branch="2"' in page.text and 'デリメシ店' in page.text
    import line_identity
    monkeypatch.setattr(line_identity,'verify_line_token',lambda *a:('U'+'a'*32,9999999999))
    profile=public.post('/line/identity?branch=2',json={'id_token':'fake'},base_url=BASE).get_json()
    assert [p['id'] for p in profile['staff']]==[3]
    assert public.post('/line/identity/link?branch=2',json={'id_token':'fake','staff_id':1},base_url=BASE).status_code==409
    assert public.post('/line/identity/link?branch=2',json={'id_token':'fake','staff_id':3},base_url=BASE).status_code==200
    assert raw.execute('SELECT count(*) FROM line_staff_links').fetchone()[0]==0
    with branch_scope(DELI), service.connect() as c:
        url=app.extensions['line_identity']['registration_url'](c,date(2026,11,16))
        assert 'branch=2' in url

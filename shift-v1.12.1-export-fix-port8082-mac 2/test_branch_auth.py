"""Invitation tokens determine branch display; login authority stays account-bound."""
import hashlib
from urllib.parse import urlsplit, parse_qs
import re
import pytest
from branches import install_branches
from test_manager_auth import env, setup, csrf, post

BASE='https://localhost'

@pytest.fixture
def branch_auth(env):
    app,raw,_=env
    install_branches(app,lambda: __import__('test_manager_auth').LocalConnection(raw))
    raw.executescript("CREATE TABLE staff(id INTEGER,branch_id INTEGER,active INTEGER); INSERT INTO staff VALUES(1,1,1),(2,2,1);")
    owner=setup(env)
    token=csrf(owner,'/admin/accounts?branch=2')
    response=post(owner,'/admin/accounts?branch=2',dict(csrf_token=token,username='son.derimeshi',display_name='Sơn'))
    assert response.status_code==200
    invite=re.search(r'aria-label="Link kích hoạt" readonly value="([^"]+)"',response.text).group(1)
    assert parse_qs(urlsplit(invite).query)['branch']==['2']
    return app,raw,owner,urlsplit(invite).fragment


def info(client,token,path='/admin/activate'):
    security=csrf(client,path)
    return client.post('/admin/invite-info',json={'invite_token':token},headers={'X-CSRF-Token':security},base_url=BASE)


def test_old_fragment_only_invite_resolves_correct_branch_even_with_wrong_url_hint(branch_auth):
    app,_,_,token=branch_auth
    client=app.test_client()
    page=client.get('/admin/activate?branch=1',base_url=BASE)
    assert 'ベト屋築地店' not in page.text
    assert 'Betoya' in page.text
    response=info(client,token)
    assert response.status_code==200
    assert response.json=={'ok':True,'branch':{'id':2,'name':'デリメシ店'}}
    assert token not in response.text


def test_activation_and_generic_login_redirect_to_assigned_branch(branch_auth):
    app,raw,_,token=branch_auth
    client=app.test_client()
    security=csrf(client,'/admin/activate?branch=1')
    response=post(client,'/admin/activate?branch=1',dict(csrf_token=security,invite_token=token,password='test-password-123',confirm_password='test-password-123'))
    assert response.status_code==302 and response.location=='/admin/login?branch=2'
    page=client.get(response.location,base_url=BASE)
    assert 'デリメシ店' in page.text and 'ベト屋築地店' not in page.text
    response=post(client,'/admin/login?branch=1',dict(csrf_token=csrf(client),username='son.derimeshi',password='test-password-123'))
    assert response.status_code==302 and response.location=='/?branch=2'
    with client.session_transaction(base_url=BASE) as session:
        assert session['branch_id']==2
    assert client.get('/?branch=1',base_url=BASE).status_code==403
    assert client.get('/admin/branches',base_url=BASE).status_code==403
    assert client.get('/admin/login?branch=1',base_url=BASE).location=='/?branch=2'
    assert raw.execute("SELECT branch_id FROM manager_accounts WHERE username='son.derimeshi'").fetchone()[0]==2


def test_bad_password_keeps_verified_branch_and_cannot_change_assignment(branch_auth):
    app,raw,_,token=branch_auth
    client=app.test_client()
    response=post(client,'/admin/activate?branch=1',dict(csrf_token=csrf(client,'/admin/activate'),invite_token=token,password='short',confirm_password='short',branch=1))
    assert response.status_code==400
    assert 'デリメシ店' in response.text and 'ベト屋築地店' not in response.text
    assert raw.execute("SELECT password_hash FROM manager_accounts WHERE username='son.derimeshi'").fetchone()[0] is None


@pytest.mark.parametrize('change',["invite_expires='2000-01-01'",'active=0','invite_hash=NULL'])
def test_unusable_invites_reveal_no_branch(branch_auth,change):
    app,raw,_,token=branch_auth
    raw.execute('UPDATE manager_accounts SET '+change+" WHERE username='son.derimeshi'");raw.commit()
    client=app.test_client()
    response=info(client,token)
    assert response.status_code==400 and 'branch' not in response.json


def test_super_admin_still_switches_all_branches_and_labels_are_clear(branch_auth):
    _,_,owner,_=branch_auth
    assert owner.get('/?branch=1',base_url=BASE).status_code==200
    assert owner.get('/?branch=2',base_url=BASE).status_code==200
    assert owner.get('/admin/branches',base_url=BASE).status_code==200
    page=owner.get('/admin/accounts?branch=2',base_url=BASE)
    assert 'Super Admin · Tất cả chi nhánh' in page.text
    assert 'Tencho · デリメシ店' in page.text


def test_invite_lookup_requires_csrf_and_generic_login_is_neutral(branch_auth):
    app,_,_,token=branch_auth
    client=app.test_client()
    assert 'ベト屋築地店' not in client.get('/admin/login',base_url=BASE).text
    response=client.post('/admin/invite-info',json={'invite_token':token},base_url=BASE)
    assert response.status_code==403

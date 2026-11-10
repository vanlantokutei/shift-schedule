from datetime import date, datetime, timedelta
import time
from test_line_schedule import state
from test_line_identity import linked_state, post, UID1
from test_manager_auth import env, setup, csrf
import line_schedule as line


def link(app):
    c=app.test_client()
    assert post(c,'/line/identity/link',{'id_token':'valid-one','staff_id':1}).status_code==200
    return c


def publish(raw, service, now=None):
    week=date(2026,11,16)
    _,_,version=line.publication_snapshot(service.connect(),week,'Cstaff')
    return service.publish(week,version,now=now or datetime(2026,11,10,23,tzinfo=line.JST))


def get(c,path='/staff/schedule?week=2026-11-16'):
    return c.get(path,base_url='https://localhost')


def test_viewer_requires_verified_active_link_even_for_images(linked_state):
    app,raw,service,_,_=linked_state
    anon=app.test_client()
    assert get(anon).status_code==302
    assert '/kibo/line?view=schedule' in get(anon).location
    c=link(app)
    assert get(c).status_code==200
    with c.session_transaction() as s:s['line_staff_exp']=time.time()-1
    assert get(c).status_code==302
    c=link(app)
    raw.execute('UPDATE staff SET active=0 WHERE id=1');raw.commit()
    assert get(c,'/staff/schedule-image?week=2026-11-16').status_code==302


def test_viewer_is_immutable_ignores_drafts_and_has_no_payroll(linked_state):
    app,raw,service,_,_=linked_state;c=link(app)
    assert 'Chưa có lịch'.encode() in get(c).data
    assert publish(raw,service)[1]==200
    raw.execute("UPDATE shifts SET start='09:00',break_start='10:00'");raw.commit()
    all=get(c).data.decode()
    assert '07:30–19:30' in all and '12:00–12:45' in all and '16:00–16:15' in all
    assert '09:00' not in all and '99999' not in all
    one=get(c,'/staff/schedule?week=2026-11-16&staff_id=2').data.decode()
    assert 'Lịch của Sương' in one and '17:00–22:00' in one and '07:30–19:30' not in one
    mine=get(c,'/staff/schedule?week=2026-11-16&mine=1').data.decode()
    assert 'Lịch của Lân' in mine
    # Historical finalized data survives the delivery queue cleanup.
    raw.execute('DELETE FROM line_schedule_deliveries');raw.commit()
    assert '07:30–19:30' in get(c).data.decode()


def test_failed_send_not_published_and_retry_publishes_once(linked_state,monkeypatch):
    app,raw,service,calls,_=linked_state;c=link(app)
    def fail(*a,**k):raise RuntimeError('offline')
    original=line.line_request;monkeypatch.setattr(line,'line_request',fail)
    assert publish(raw,service)[1]==502
    assert 'Chưa có lịch'.encode() in get(c).data
    monkeypatch.setattr(line,'line_request',original)
    assert publish(raw,service)[1]==200
    assert '07:30–19:30' in get(c).data.decode()
    assert raw.execute('SELECT COUNT(*) FROM staff_schedule_publications').fetchone()[0]==1


def test_latest_update_and_week_navigation_empty(linked_state):
    app,raw,service,_,_=linked_state;c=link(app)
    now=datetime(2026,11,10,23,tzinfo=line.JST)
    publish(raw,service,now)
    raw.execute("UPDATE shifts SET start='09:00' WHERE staff_id=1");raw.commit()
    publish(raw,service,now+timedelta(minutes=1))
    assert '09:00–19:30' in get(c).data.decode() and '07:30–19:30' not in get(c).data.decode()
    assert 'Chưa có lịch'.encode() in get(c,'/staff/schedule?week=2026-11-23').data
    assert get(c,'/staff/schedule?week=2026-11-17').status_code==400


def test_legacy_image_fallback_does_not_invent_personal_schedule(linked_state):
    app,raw,service,_,_=linked_state;c=link(app)
    publish(raw,service)
    raw.execute('DELETE FROM staff_schedule_publications');raw.commit()
    page=get(c).data.decode()
    assert 'ảnh lịch đã gửi' in page and 'staff_id=2' not in page
    image=get(c,'/staff/schedule-image?week=2026-11-16')
    assert image.status_code==200 and image.data.startswith(b'\x89PNG')
    assert get(c,'/staff/schedule').status_code==200


def sync(owner):
    import re
    page=get(owner,'/admin/line/schedule-sync?week=2026-11-16')
    version=re.search(rb'name="version" value="([^"]+)"',page.data).group(1).decode()
    token=csrf(owner,'/admin/line/schedule-sync?week=2026-11-16')
    return owner.post('/admin/line/schedule-sync?week=2026-11-16',data={'version':version,'csrf_token':token},base_url='https://localhost')


def test_sync_only_approved_shifts_keeps_wishes_and_never_sends_messages(linked_state):
    app,raw,service,calls,manager_env=linked_state
    owner=setup(manager_env);employee=link(app)
    before=[tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]
    response=sync(owner)
    assert response.status_code==200 and 'Đã đồng bộ 1 ca'.encode() in response.data
    page=get(employee).data.decode()
    assert '07:30–19:30' in page and '17:00–22:00' not in page and '99999' not in page
    assert 'được quản lý đồng bộ' in page
    assert [tuple(r) for r in raw.execute('SELECT * FROM shifts').fetchall()]==before
    assert not calls
    assert sync(owner).status_code==200
    assert raw.execute('SELECT COUNT(*) FROM staff_schedule_publications').fetchone()[0]==1


def test_sync_requires_owner_csrf_and_rejects_stale_preview(linked_state):
    app,raw,service,calls,manager_env=linked_state
    employee=link(app)
    assert get(employee,'/admin/line/schedule-sync').status_code==302
    owner=setup(manager_env)
    assert owner.post('/admin/line/schedule-sync',base_url='https://localhost').status_code==403
    import re
    page=get(owner,'/admin/line/schedule-sync?week=2026-11-16')
    version=re.search(rb'name="version" value="([^"]+)"',page.data).group(1).decode()
    token=csrf(owner,'/admin/line/schedule-sync?week=2026-11-16')
    raw.execute("UPDATE shifts SET start='08:00' WHERE staff_id=1");raw.commit()
    response=owner.post('/admin/line/schedule-sync?week=2026-11-16',data={'version':version,'csrf_token':token},base_url='https://localhost')
    assert response.status_code==409 and raw.execute('SELECT COUNT(*) FROM staff_schedule_publications').fetchone()[0]==0
    raw.execute("UPDATE manager_accounts SET role='manager'");raw.commit()
    assert get(owner,'/admin/line/schedule-sync').status_code==403


def test_line_manager_button_is_pinned_and_does_not_grant_editing(linked_state):
    app,raw,service,calls,manager_env=linked_state
    owner=setup(manager_env);lan=link(app)
    staff=app.test_client()
    assert post(staff,'/line/identity/link',{'id_token':'valid-two','staff_id':2}).status_code==200
    assert 'Quản lý lịch'.encode() not in get(lan).data
    token=csrf(owner,'/admin/line/identity')
    response=owner.post('/admin/line/identity',data={'csrf_token':token,'action':'manager_menu_enable','staff_id':1},base_url='https://localhost')
    assert response.status_code==200
    assert 'Quản lý lịch'.encode() in get(lan).data
    assert 'Quản lý lịch'.encode() not in get(staff).data
    assert get(lan,'/staff/manage').location.endswith('/admin/login')
    assert lan.post('/shift',json={},base_url='https://localhost').status_code==401
    assert get(staff,'/staff/manage').status_code==403
    assert staff.post('/admin/line/identity',data={'action':'manager_menu_enable','staff_id':2},base_url='https://localhost').status_code==401
    # Reassigning the same staff name to a different LINE account cannot inherit the button.
    raw.execute('DELETE FROM line_staff_links');raw.commit()
    post(staff,'/line/identity/link',{'id_token':'valid-two','staff_id':1})
    assert 'Quản lý lịch'.encode() not in get(staff).data
    assert get(staff,'/staff/manage').status_code==403


def test_manager_button_revocation_and_existing_owner_login(linked_state):
    app,raw,service,calls,manager_env=linked_state
    owner=setup(manager_env);lan=link(app)
    token=csrf(owner,'/admin/line/identity')
    for action in ['manager_menu_enable','manager_menu_disable']:
        assert owner.post('/admin/line/identity',data={'csrf_token':token,'action':action,'staff_id':1},base_url='https://localhost').status_code==200
    assert 'Quản lý lịch'.encode() not in get(lan).data
    assert get(lan,'/staff/manage').status_code==403
    assert get(owner,'/staff/manage').location=='/'

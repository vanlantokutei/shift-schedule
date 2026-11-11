"""Archive/restore through the real routes, retaining history and payroll."""
import re

import pytest
from flask import template_rendered
from test_kibo_deadlines import env, extend


def post(client, action, **data):
    return client.post('/staff', json=dict(action=action, id=2, **data),
                       headers={'X-CSRF-Token': 'test-csrf'}, base_url='https://localhost')


def seed(raw):
    raw.execute("INSERT INTO staff VALUES(2,'Nhân viên cũ',1,1,'baito','hourly',1280,0,1)")
    raw.execute('''INSERT INTO shifts(staff_id,work_date,start,"end",break_min,break_start,break_end,is_kibo)
                   VALUES(2,'2026-11-02','09:00','17:00',0,'12:00','13:00',0)''')
    raw.execute("INSERT INTO fixed_kibo VALUES(2,0,'09:00','17:00')")
    raw.execute("INSERT INTO kibo_submissions(staff_id,week_start) VALUES(2,'2026-11-09')")
    raw.commit()


def snapshot(raw):
    return {table: [tuple(r) for r in raw.execute(f'SELECT * FROM {table}')]
            for table in ('shifts', 'fixed_kibo', 'kibo_submissions')}


def page_context(app, client, url):
    contexts = []
    def capture(sender, template, context, **extra): contexts.append(context)
    with template_rendered.connected_to(capture, app):
        response = client.get(url, base_url='https://localhost')
    assert response.status_code == 200
    return response, contexts[-1]


@pytest.mark.parametrize('archive_action', ['archive', 'delete'])
def test_archive_preserves_history_payroll_and_can_restore(env, archive_action):
    app, client, raw, _ = env
    seed(raw)
    before = snapshot(raw)
    original_staff = tuple(raw.execute('SELECT * FROM staff WHERE id=2').fetchone())
    assert post(client, archive_action).json == dict(ok=True, id=2)
    assert raw.execute('SELECT active FROM staff WHERE id=2').fetchone()[0] == 0
    assert snapshot(raw) == before
    response, context = page_context(app, client, '/?week=2026-11-02')
    assert [s['id'] for s in context['staff']] == [1, 2]
    assert [s['id'] for s in context['active_staff']] == [1]
    assert context['totals'][2] == context['monthly_totals'][2] == 7
    assert context['pay'][2] == context['baito_monthly_pay'] == 8960
    assert context['monthly_all_hours'] == 7
    assert 2 not in context['kibo_status']
    assert 'Nhân viên đã nghỉ (1)' in response.text
    assert 'Xem lịch cũ' in response.text and 'Khôi phục' in response.text
    assert 'data-active="0"' in response.text
    _, context = page_context(app, client, '/month?year=2026&month=11')
    assert context['totals'][2] == 7
    # Historical-only staff do not clutter months without any of their shifts.
    _, context = page_context(app, client, '/?week=2026-12-07')
    assert [s['id'] for s in context['staff']] == [1]
    assert [s['id'] for s in context['archived_staff']] == [2]
    _, context = page_context(app, client, '/month?year=2026&month=12')
    assert [s['id'] for s in context['staff']] == [1]
    kibo = client.get('/kibo').text
    assert not re.search(r'<option value="2"', kibo)
    assert post(client, 'restore').json == dict(ok=True, id=2)
    assert tuple(raw.execute('SELECT * FROM staff WHERE id=2').fetchone()) == original_staff
    assert snapshot(raw) == before
    assert re.search(r'<option value="2"', client.get('/kibo').text)
    # Reinstated people get their saved fixed schedule back under the same ID.
    prefill = client.get('/kibo/prefill?staff_id=2&week=2026-11-09').json
    assert prefill['entries']['0'] == dict(start='09:00', end='17:00')
    assert extend(client).status_code == 200
    response = client.post('/kibo', data=dict(staff_id=2, week='2026-11-09', start_0='10:00', end_0='15:00'))
    assert 'Đăng ký thành công!' in response.text
    assert raw.execute("SELECT start FROM shifts WHERE staff_id=2 AND work_date='2026-11-09'").fetchone()[0] == '10:00'


def test_archived_employee_cannot_change_history_or_submit_as_another_person(env):
    _, client, raw, _ = env
    seed(raw)
    post(client, 'archive')
    before = snapshot(raw)
    assert extend(client).status_code == 200
    for path, method, data in [
        ('/kibo', 'POST', dict(staff_id=2, week='2026-11-09', start_0='10:00', end_0='16:00')),
        ('/kibo?staff_id=2', 'GET', None),
        ('/kibo/prefill?staff_id=2', 'GET', None),
        ('/kibo/fixed', 'POST', dict(staff_id=2, entries=[])),
        ('/kibo/fixed?staff_id=2', 'DELETE', None),
    ]:
        kw = {'data': data} if path == '/kibo' else {'json': data} if data else {}
        assert client.open(path, method=method, **kw).status_code == 409
    for start, end in [('10:00', '16:00'), ('', '')]:
        response = client.post('/shift', json=dict(staff_id=2, date='2026-11-02', start=start, end=end),
                               headers={'X-CSRF-Token': 'test-csrf'}, base_url='https://localhost')
        assert response.status_code == 409
    assert post(client, 'rename', name='Changed').status_code == 409
    assert snapshot(raw) == before
    assert raw.execute('SELECT name FROM staff WHERE id=2').fetchone()[0] == 'Nhân viên cũ'


def test_restore_requires_manager_and_csrf_and_keeps_existing_identity(env):
    app, client, raw, _ = env
    seed(raw)
    post(client, 'archive')
    assert app.test_client().post('/staff', json=dict(action='restore', id=2)).status_code == 401
    assert client.post('/staff', json=dict(action='restore', id=2), base_url='https://localhost').status_code == 403
    assert raw.execute('SELECT active FROM staff WHERE id=2').fetchone()[0] == 0
    assert client.post('/staff', json=dict(action='restore', id=999),
                       headers={'X-CSRF-Token': 'test-csrf'}, base_url='https://localhost').status_code == 404
    assert post(client, 'restore').status_code == 200
    assert post(client, 'restore').status_code == 200
    assert raw.execute('SELECT count(*) FROM staff').fetchone()[0] == 2

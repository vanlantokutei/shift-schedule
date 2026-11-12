"""Employee schedule viewer; reads immutable publications approved by managers."""
from datetime import datetime, timedelta
import hashlib
import hmac
from jp_holidays import holiday_context
import json
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from line_branches import branch_number, branch_name, branch_url
from flask import abort, g, redirect, render_template, request, Response, session


def install_staff_schedule(app, connect, linked_staff):
    def authorize(c):
        uid = session.get('line_staff_user')
        person = linked_staff(c, uid) if uid and session.get('line_staff_exp', 0) > time.time() else None
        if person or getattr(g, 'manager', None):
            return dict(person) if person else None, None
        return None, redirect(branch_url('/kibo/line?' + urlencode({'view': 'schedule', 'mine': request.args.get('mine', '0')})))

    def can_manage(c):
        if getattr(g, 'manager', None):
            return True
        uid = session.get('line_staff_user')
        return bool(uid and session.get('line_staff_exp', 0) > time.time()
                    and app.extensions['line_identity']['manager_menu_allowed'](c, uid))

    @app.get('/staff/manage')
    def staff_manage_entry():
        with connect() as c:
            if not can_manage(c):
                abort(403, description='Tài khoản này không có nút quản lý lịch.')
        # LINE identity only controls visibility. Editing always requires the
        # existing manager password/session and the existing CSRF protection.
        return redirect(branch_url('/' if getattr(g, 'manager', None) else '/admin/login'))

    def selected_week():
        value = request.args.get('week')
        if not value:
            today = datetime.now(ZoneInfo('Asia/Tokyo')).date()
            return today - timedelta(days=today.weekday())
        try:
            week = datetime.strptime(value, '%Y-%m-%d').date()
            if week.weekday() or week.isoformat() != value or not 2000 <= week.year <= 2100:
                raise ValueError()
            return week
        except (ValueError, TypeError):
            abort(400)

    def publication(c, week):
        return c.execute('''SELECT content FROM staff_schedule_publications WHERE week_start=%s
            AND published_at IS NOT NULL ORDER BY published_at DESC,publication_key DESC LIMIT 1''', (str(week),)).fetchone()

    def old_publication(c, week):
        return c.execute('''SELECT png FROM line_schedule_deliveries WHERE week_start LIKE %s
            AND accepted_at IS NOT NULL ORDER BY accepted_at DESC,week_start DESC LIMIT 1''',
            ('published:' + str(week) + ':%',)).fetchone()

    def sync_snapshot(c, week):
        rows = c.execute(f'''SELECT staff_id,work_date,start,"end",break_start,break_end,
            break2_start,break2_end,is_kibo FROM shifts WHERE staff_id IN (SELECT id FROM staff WHERE branch_id={branch_number()}) AND work_date BETWEEN %s AND %s
            AND start IS NOT NULL AND "end" IS NOT NULL ORDER BY staff_id,work_date''',
            (str(week), str(week + timedelta(days=6)))).fetchall()
        approved = [r for r in rows if not r['is_kibo']]
        ids = {r['staff_id'] for r in approved}
        staff = c.execute(f'SELECT id,name,active FROM staff WHERE branch_id={branch_number()} ORDER BY sort_order,id').fetchall()
        content = {'source': 'manager-import', 'people': [{'id': p['id'], 'name': p['name']}
                   for p in staff if p['active'] or p['id'] in ids],
                   'shifts': [{k: v for k, v in r.items() if k != 'is_kibo'} for r in approved]}
        version = hashlib.sha256(json.dumps({'week': str(week), 'content': content},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return content, len(rows)-len(approved), version

    @app.route('/admin/line/schedule-sync', methods=['GET', 'POST'])
    def staff_schedule_sync():
        if not g.manager or g.manager['role'] != 'owner':
            abort(403)
        week = selected_week()
        notice = None
        status = 200
        with connect() as c:
            if request.method == 'POST':
                c.execute('LOCK TABLE staff IN SHARE MODE')
                c.execute('LOCK TABLE shifts IN SHARE MODE')
            content, wishes, version = sync_snapshot(c, week)
            if request.method == 'POST':
                expected = request.form.get('version', '')
                if not expected.isascii() or not hmac.compare_digest(expected, version):
                    notice, status = 'Lịch vừa thay đổi. Kiểm tra bản xem trước rồi bấm đồng bộ lại.', 409
                elif not content['shifts']:
                    notice, status = 'Tuần này chưa có ca đã xếp để đồng bộ.', 400
                else:
                    key = 'imported:' + str(week) + ':' + version
                    exists = c.execute('SELECT publication_key FROM staff_schedule_publications WHERE publication_key=%s', (key,)).fetchone()
                    if exists:
                        notice = 'Bản lịch này đã được đồng bộ. Không tạo bản trùng.'
                    else:
                        c.execute('''INSERT INTO staff_schedule_publications
                            (publication_key,week_start,content,published_at) VALUES(%s,%s,%s,%s)''',
                            (key, str(week), json.dumps(content, ensure_ascii=False), datetime.now(ZoneInfo('Asia/Tokyo'))))
                        notice = f"Đã đồng bộ {len(content['shifts'])} ca vào trang Xem lịch. Không gửi tin vào nhóm."
        return render_template('staff_schedule_sync.html', week=week, content=content,
            wishes=wishes, version=version, notice=notice), status

    @app.get('/staff/schedule')
    def staff_schedule():
        week = selected_week()
        with connect() as c:
            me, response = authorize(c)
            if response is not None:
                return response
            show_manager_menu = can_manage(c)
            saved = publication(c, week)
            old = old_publication(c, week) if not saved else None
            # First visit opens this week if available, otherwise the latest publication.
            if not request.args.get('week') and not saved and not old:
                recent = c.execute('''SELECT week_start FROM staff_schedule_publications
                    WHERE published_at IS NOT NULL ORDER BY week_start DESC LIMIT 1''').fetchone()
                if recent:
                    week = datetime.strptime(recent['week_start'], '%Y-%m-%d').date()
                    saved = publication(c, week)
                else:
                    recent = c.execute("SELECT week_start FROM line_schedule_deliveries WHERE week_start LIKE %s AND accepted_at IS NOT NULL ORDER BY accepted_at DESC LIMIT 1", ('published:%',)).fetchone()
                    if recent:
                        week = datetime.strptime(recent['week_start'].split(':')[1], '%Y-%m-%d').date()
                        old = old_publication(c, week)
            content = json.loads(saved['content']) if saved else {'people': [], 'shifts': []}
        days = [week + timedelta(days=i) for i in range(7)]
        selected = None
        try:
            wanted = me['id'] if request.args.get('mine') == '1' and me else int(request.args.get('staff_id', '0'))
        except ValueError:
            abort(400)
        if wanted:
            selected = next((p for p in content['people'] if p['id'] == wanted), None)
        people = [selected] if selected else content['people']
        cells = {(r['staff_id'], str(r['work_date'])): r for r in content['shifts']}
        return render_template('staff_schedule.html', **holiday_context(days), week=week, days=days, people=people,
            selected=selected, me=me, cells=cells, saved=bool(saved), old=bool(old),
            imported=content.get('source') == 'manager-import', show_manager_menu=show_manager_menu,
            previous=week-timedelta(days=7), following=week+timedelta(days=7),
            labels=['Thứ 2','Thứ 3','Thứ 4','Thứ 5','Thứ 6','Thứ 7','Chủ nhật'])

    @app.get('/staff/schedule-image')
    def staff_schedule_image():
        week = selected_week()
        with connect() as c:
            _, response = authorize(c)
            if response is not None:
                return response
            old = old_publication(c, week)
        if not old:
            abort(404)
        return Response(bytes(old['png']), mimetype='image/png')

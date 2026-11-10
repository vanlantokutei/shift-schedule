"""Employee schedule viewer; reads immutable, accepted publications only."""
from datetime import datetime, timedelta
import json
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from flask import abort, g, redirect, render_template, request, Response, session


def install_staff_schedule(app, connect, linked_staff):
    def authorize(c):
        uid = session.get('line_staff_user')
        person = linked_staff(c, uid) if uid and session.get('line_staff_exp', 0) > time.time() else None
        if person or getattr(g, 'manager', None):
            return dict(person) if person else None, None
        return None, redirect('/kibo/line?' + urlencode({'view': 'schedule', 'mine': request.args.get('mine', '0')}))

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

    @app.get('/staff/schedule')
    def staff_schedule():
        week = selected_week()
        with connect() as c:
            me, response = authorize(c)
            if response is not None:
                return response
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
        return render_template('staff_schedule.html', week=week, days=days, people=people,
            selected=selected, me=me, cells=cells, saved=bool(saved), old=bool(old),
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

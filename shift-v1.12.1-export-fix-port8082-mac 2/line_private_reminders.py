"""One private Kibo reminder per employee/week, during the final four hours only."""
from datetime import date, datetime, timedelta
import json
import re
import uuid
from zoneinfo import ZoneInfo

from line_branches import branch_number, branch_name, branch_url
from flask import abort, g, render_template, request
from kibo_deadlines import window
from line_design import reminder_card

JST = ZoneInfo('Asia/Tokyo')
LOCK = 72905471


def as_time(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class PrivateReminders:
    def __init__(self, app, connect, service, send):
        self.app, self.connect, self.service, self.send = app, connect, service, send
        with connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            c.execute('''CREATE TABLE IF NOT EXISTS line_private_config (
                id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0)''')
            c.execute('INSERT INTO line_private_config(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            c.execute('''CREATE TABLE IF NOT EXISTS line_private_follows (
                line_user_id TEXT PRIMARY KEY, active INTEGER NOT NULL, event_ts BIGINT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS line_private_deliveries (
                week_start TEXT NOT NULL, staff_id INTEGER NOT NULL, line_user_id TEXT NOT NULL,
                deadline TIMESTAMPTZ NOT NULL, payload TEXT NOT NULL, retry_key TEXT NOT NULL,
                first_attempt TIMESTAMPTZ, accepted_at TIMESTAMPTZ,
                next_attempt TIMESTAMPTZ NOT NULL, stopped INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, PRIMARY KEY(week_start,staff_id))''')

    def follow_event(self, c, event):
        source = event.get('source') or {}
        uid = source.get('userId', '')
        if (source.get('type') != 'user' or event.get('type') not in ('follow', 'unfollow')
                or not isinstance(uid, str) or not re.fullmatch(r'U[0-9a-fA-F]{32}', uid)):
            return
        stamp = event.get('timestamp')
        if not isinstance(stamp, int):
            return
        c.execute('''INSERT INTO line_private_follows(line_user_id,active,event_ts) VALUES(%s,%s,%s)
            ON CONFLICT(line_user_id) DO UPDATE SET active=excluded.active,event_ts=excluded.event_ts
            WHERE excluded.event_ts>=line_private_follows.event_ts''',
            (uid, int(event['type'] == 'follow'), stamp))

    def candidates(self, c, week):
        return c.execute(f'''SELECT s.id,s.name,l.line_user_id FROM staff s
            JOIN line_staff_links l ON l.staff_id=s.id
            LEFT JOIN line_private_follows f ON f.line_user_id=l.line_user_id
            WHERE s.active=1 AND s.branch_id={branch_number()} AND (f.active IS NULL OR f.active=1)
            AND NOT EXISTS(SELECT 1 FROM kibo_submissions k WHERE k.staff_id=s.id AND k.week_start=%s)
            AND NOT EXISTS(SELECT 1 FROM shifts sh WHERE sh.staff_id=s.id
                AND sh.work_date BETWEEN %s AND %s AND sh.is_kibo=1)
            ORDER BY s.id''', (str(week), str(week), str(week + timedelta(days=6)))).fetchall()

    def tick(self, now):
        now = now.astimezone(JST)
        with self.connect() as c:
            if not c.execute('SELECT pg_try_advisory_lock(%s) AS locked', (LOCK,)).fetchone()['locked']:
                return
            config = self.service.config(c)
            if (not config['enabled'] or not config['credentials'] or
                    not c.execute('SELECT enabled FROM line_private_config WHERE id=1').fetchone()['enabled']):
                return
            weeks = {now.date() - timedelta(days=now.weekday()) + timedelta(days=7)}
            for row in (c.execute('SELECT week_start FROM kibo_deadlines WHERE deadline>%s', (now,)) if branch_number()==1 else c.execute('SELECT week_start FROM branch_kibo_deadlines WHERE branch_id=%s AND deadline>%s', (branch_number(), now))).fetchall():
                try:
                    week = date.fromisoformat(row['week_start'])
                    if not week.weekday():
                        weeks.add(week)
                except (ValueError, TypeError):
                    pass
            token = self.service.credentials(config)['token']
            for week in sorted(weeks):
                opens, deadline = window(c, week)
                if not opens <= now < deadline or now < deadline - timedelta(hours=4):
                    continue
                for person in self.candidates(c, week):
                    job = c.execute('SELECT * FROM line_private_deliveries WHERE week_start=%s AND staff_id=%s',
                                    (str(week), person['id'])).fetchone()
                    if job and (job['accepted_at'] or job['stopped'] or as_time(job['next_attempt']) > now):
                        continue
                    changed = job and (job['line_user_id'] != person['line_user_id'] or as_time(job['deadline']) != deadline)
                    if changed and not job['first_attempt']:
                        job = None
                    if job and (job['line_user_id'] != person['line_user_id'] or
                                as_time(job['deadline']) != deadline or
                                (job['first_attempt'] and now - as_time(job['first_attempt']) >= timedelta(hours=23))):
                        # A timeout may already have delivered. Never change a pending
                        # recipient/payload or reuse a retry key after its 24h lifetime.
                        c.execute('UPDATE line_private_deliveries SET stopped=1,last_error=%s WHERE week_start=%s AND staff_id=%s',
                                  ('Đã dừng vì liên kết hoặc hạn đăng ký thay đổi.', str(week), person['id']))
                        continue
                    if not job:
                        signup = self.app.extensions['line_identity']['registration_url'](c, week)
                        if not signup:
                            continue
                        text = (f'Chào {person["name"]}! Bạn chưa gửi kibo tuần {week:%d/%m}–'
                                f'{week + timedelta(days=6):%d/%m/%Y}.\n'
                                f'Hạn đăng ký: {deadline:%d/%m/%Y lúc %H:%M} (giờ Nhật).\n'
                                'Bạn gửi lịch rảnh giúp quán nhé. Cảm ơn bạn!')
                        message = reminder_card(text, signup, week, deadline)
                        message['contents']['body']['contents'][1]['text'] = 'Bạn chưa gửi kibo'
                        message['contents']['body']['contents'][4]['text'] = f'{person["name"]} ơi, gửi lịch rảnh giúp quán nhé!'
                        payload = json.dumps({'to': person['line_user_id'], 'messages': [message]}, ensure_ascii=False)
                        c.execute('''INSERT INTO line_private_deliveries
                            (week_start,staff_id,line_user_id,deadline,payload,retry_key,next_attempt)
                            VALUES(%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(week_start,staff_id) DO UPDATE SET line_user_id=excluded.line_user_id,
                            deadline=excluded.deadline,payload=excluded.payload,retry_key=excluded.retry_key,
                            next_attempt=excluded.next_attempt WHERE line_private_deliveries.first_attempt IS NULL''',
                            (str(week),person['id'],person['line_user_id'],deadline,payload,str(uuid.uuid4()),now))
                        c.commit()
                        job = c.execute('SELECT * FROM line_private_deliveries WHERE week_start=%s AND staff_id=%s',
                                        (str(week),person['id'])).fetchone()
                    # Fresh reads before each HTTP call: someone may have submitted,
                    # archived, unlinked or extended the deadline during this scan.
                    if ((person['id'],person['line_user_id']) not in {(p['id'],p['line_user_id']) for p in self.candidates(c, week)} or
                            window(c, week)[1] != deadline or
                            not c.execute('SELECT enabled FROM line_private_config WHERE id=1').fetchone()['enabled'] or
                            not self.service.config(c)['enabled']):
                        continue
                    c.execute('''UPDATE line_private_deliveries SET first_attempt=COALESCE(first_attempt,%s)
                        WHERE week_start=%s AND staff_id=%s''', (now,str(week),person['id']))
                    c.commit()
                    try:
                        self.send(token, '/message/push', json.loads(job['payload']), job['retry_key'])
                    except RuntimeError:
                        c.execute('''UPDATE line_private_deliveries SET next_attempt=%s,last_error=%s
                            WHERE week_start=%s AND staff_id=%s''',
                            (now+timedelta(minutes=5),'LINE chưa nhận yêu cầu; sẽ thử lại trước hạn.',str(week),person['id']))
                    else:
                        c.execute('UPDATE line_private_deliveries SET accepted_at=%s,last_error=NULL WHERE week_start=%s AND staff_id=%s',
                                  (now,str(week),person['id']))
                    c.commit()


def install_private_reminders(app, connect, service, send):
    private = PrivateReminders(app, connect, service, send)
    app.extensions['line_private_reminders'] = private

    @app.route('/admin/line/private', methods=['GET','POST'])
    def line_private_settings():
        if g.manager['role'] != 'owner':
            abort(403)
        notice = None
        with connect() as c:
            if request.method == 'POST':
                action = request.form.get('action')
                if action not in ('enable','disable'):
                    abort(400)
                c.execute('UPDATE line_private_config SET enabled=%s WHERE id=1', (int(action=='enable'),))
                notice = ('Đã bật nhắc riêng trước hạn 4 tiếng.' if action=='enable' else 'Đã tắt nhắc riêng.')
            settings = c.execute('SELECT enabled FROM line_private_config WHERE id=1').fetchone()
            master = service.config(c)
            now = datetime.now(JST)
            week = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)
            _, deadline = window(c, week)
            people = c.execute(f'''SELECT s.id,s.name,l.staff_id AS linked,f.active AS follows
                FROM staff s LEFT JOIN line_staff_links l ON l.staff_id=s.id
                LEFT JOIN line_private_follows f ON f.line_user_id=l.line_user_id
                WHERE s.active=1 AND s.branch_id={branch_number()} ORDER BY s.sort_order,s.id''').fetchall()
            recent = c.execute('''SELECT d.week_start,s.name,d.accepted_at,d.last_error FROM line_private_deliveries d
                LEFT JOIN staff s ON s.id=d.staff_id ORDER BY d.next_attempt DESC LIMIT 10''').fetchall()
        return render_template('line_private_settings.html',settings=settings,master=master,notice=notice,
                               week=week,deadline=deadline,remind_at=deadline-timedelta(hours=4),people=people,recent=recent)
    return private

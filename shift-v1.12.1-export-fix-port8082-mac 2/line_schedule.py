"""Weekly LINE registration reminders, opt-in and limited to one paired group."""
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import threading
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import uuid

from cryptography.fernet import Fernet
from flask import Response, abort, g, jsonify, render_template, request, session
from PIL import Image, ImageDraw, ImageFont
from kibo_deadlines import window as kibo_window

JST = ZoneInfo('Asia/Tokyo')
DELIVERY_LOCK = 72905439
API = 'https://api.line.me/v2/bot'


def scheduled_week(now):
    """Monday 10:00 JST, with same-day recovery after restarts."""
    now = now.astimezone(JST)
    return now.date() if now.weekday() == 0 and now.hour >= 10 else None


def registration_reminder(connection, current_week, now, test=False):
    """Match the employee Kibo week and its current deadline, including extensions."""
    target = current_week + timedelta(days=7)
    opens, deadline = kibo_window(connection, target)
    if not opens <= now < deadline:
        return None
    label = 'GỬI THỬ · ' if test else ''
    return (
        f'{label}📢 ベト屋築地店 — Nhắc đăng ký 希望シフト\n\n'
        f'Mọi người vui lòng đăng ký lịch rảnh cho tuần '
        f'{target:%d/%m}–{target + timedelta(days=6):%d/%m/%Y}.\n'
        f'⏰ Hạn đăng ký: {deadline:%d/%m/%Y lúc %H:%M} (giờ Nhật).\n\n'
        f'📅 Đăng ký tại đây:\n'
        f'https://shift-schedule-1fk2.onrender.com/kibo?week={target.isoformat()}\n\n'
        'Cảm ơn mọi người!'
    )


def signature_valid(secret, body, signature):
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return bool(signature) and hmac.compare_digest(expected, signature)


def line_request(token, path, payload=None, retry_key=None):
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    if retry_key:
        headers['X-Line-Retry-Key'] = retry_key
    req = Request(API + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers, method='POST' if payload is not None else 'GET')
    try:
        with urlopen(req, timeout=20) as response:
            return json.loads(response.read() or b'{}')
    except HTTPError as error:
        # Only this specific 409 means LINE already accepted the same retry key.
        if error.code == 409 and retry_key and error.headers.get('x-line-accepted-request-id'):
            return {}
        raise RuntimeError('LINE trả về mã HTTP ' + str(error.code)) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError('Không kết nối được LINE. Hệ thống sẽ thử lại.') from None


def schedule_image(week, staff, rows):
    """Render work and both breaks only; never render wages or contact data."""
    days = [week + timedelta(days=i) for i in range(7)]
    cells = {(r['staff_id'], str(r['work_date'])): r for r in rows}
    width, name_width, col_width, row_height = 1800, 260, 220, 96
    image = Image.new('RGB', (width, 180 + max(len(staff), 1) * row_height), 'white')
    draw = ImageDraw.Draw(image)
    font_path = str(Path(__file__).with_name('fonts') / 'DejaVuSans.ttf')
    title_font, font, small = [ImageFont.truetype(font_path, n) for n in (32, 23, 18)]
    draw.text((24, 15), 'BETOYA TSUKIJI - LỊCH LÀM VIỆC', font=title_font, fill='#10764e')
    draw.text((24, 63), f'{week:%d/%m/%Y} - {days[-1]:%d/%m/%Y}', font=font, fill='#334155')
    draw.rectangle((0, 110, width, 175), fill='#edf5ef')
    draw.text((16, 128), 'Nhân viên', font=font, fill='#334155')
    for i, d in enumerate(days):
        draw.text((name_width + i * col_width + 15, 128),
                  f'{["T2","T3","T4","T5","T6","T7","CN"][i]} {d:%d/%m}', font=font, fill='#334155')
    for index, person in enumerate(staff):
        y = 175 + index * row_height
        draw.rectangle((0, y, width, y + row_height), fill='#f7faf8' if index % 2 else 'white')
        name = person['name']
        while name and draw.textlength(name, font=font) > name_width - 34:
            name = name[:-1]
        if name != person['name']:
            name = name[:-1] + '…'
        draw.text((16, y + 30), name, font=font, fill='#263a31')
        for i, d in enumerate(days):
            x = name_width + i * col_width
            r = cells.get((person['id'], str(d)))
            draw.rectangle((x, y, x + col_width, y + row_height), outline='#dce5df')
            draw.text((x + 12, y + 8), f'{r["start"]}–{r["end"]}' if r else '—', font=font, fill='#263a31')
            if r:
                for j, (left, right) in enumerate([('break_start', 'break_end'), ('break2_start', 'break2_end')]):
                    if r.get(left) and r.get(right):
                        draw.text((x + 12, y + 40 + j * 23), f'Nghỉ {r[left]}–{r[right]}', font=small, fill='#8a613b')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    data = buffer.getvalue()
    # Same URL is used for the preview, whose LINE limit is 1 MB.
    if len(data) > 1_000_000:
        raise ValueError('Ảnh lịch quá lớn. Cần giảm số nhân viên trước khi gửi.')
    return data


class LineSchedule:
    def __init__(self, app, connect):
        self.app, self.connect = app, connect
        key = hashlib.sha256(('line-credentials:' + app.secret_key).encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def init(self):
        with self.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(%s)', (DELIVERY_LOCK,))
            c.execute('''CREATE TABLE IF NOT EXISTS line_schedule_config (
                id INTEGER PRIMARY KEY, credentials TEXT, enabled INTEGER NOT NULL DEFAULT 0,
                group_id TEXT, group_name TEXT, public_origin TEXT,
                pair_hash TEXT, pair_expires TIMESTAMPTZ, last_error TEXT)''')
            c.execute('INSERT INTO line_schedule_config(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            c.execute('''CREATE TABLE IF NOT EXISTS line_schedule_deliveries (
                week_start TEXT PRIMARY KEY, image_id TEXT UNIQUE NOT NULL, png BYTEA NOT NULL,
                payload TEXT NOT NULL, retry_key TEXT NOT NULL,
                accepted_at TIMESTAMPTZ, next_attempt TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL)''')

    def config(self, c, lock=False):
        return c.execute('SELECT * FROM line_schedule_config WHERE id=1' + (' FOR UPDATE' if lock else '')).fetchone()

    def credentials(self, config):
        return json.loads(self.cipher.decrypt(config['credentials'].encode())) if config['credentials'] else {}

    def tick(self, now=None):
        now = now or datetime.now(JST)
        week = scheduled_week(now)
        if week:
            return self.deliver(week, now, 'reminder:' + str(week))

    def deliver(self, week, now, delivery_key, test=False):
        with self.connect() as c:
            if not c.execute('SELECT pg_try_advisory_lock(%s) AS locked', (DELIVERY_LOCK,)).fetchone()['locked']:
                return 'not_ready'
            # Connection is closed by the context manager, releasing the session lock.
            config = self.config(c)
            if (not test and not config['enabled']) or not config['group_id'] or not config['credentials']:
                return 'not_ready'
            job = c.execute('SELECT * FROM line_schedule_deliveries WHERE week_start=%s', (delivery_key,)).fetchone()
            if job and job['accepted_at']:
                return 'sent'
            if job and job['next_attempt'] > now:
                return 'pending'
            if job and json.loads(job['payload'])['to'] != config['group_id']:
                c.execute('UPDATE line_schedule_config SET last_error=%s WHERE id=1',
                          ('Nhóm nhận đã đổi. Lần gửi đang chờ của tuần này đã được dừng.',))
                return 'group_changed'
            if not job:
                text = registration_reminder(c, week, now, test=test)
                if text is None:
                    c.execute('UPDATE line_schedule_config SET last_error=%s WHERE id=1',
                              ('Đăng ký 希望シフト cho tuần tới chưa mở hoặc đã đóng. Chưa gửi nhắc.',))
                    return 'closed'
                image_id = secrets.token_urlsafe(32)
                payload = {'to': config['group_id'], 'messages': [{'type': 'text', 'text': text}]}
                c.execute('''INSERT INTO line_schedule_deliveries
                    (week_start,image_id,png,payload,retry_key,next_attempt,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                    (delivery_key, image_id, b'', json.dumps(payload),
                     str(uuid.uuid4()), now, now + timedelta(days=7)))
                # Persist the exact reminder and retry key before calling LINE.
                c.commit()
                job = c.execute('SELECT * FROM line_schedule_deliveries WHERE week_start=%s', (delivery_key,)).fetchone()
            try:
                token = self.credentials(config)['token']
                line_request(token, '/message/push', json.loads(job['payload']), job['retry_key'])
            except (RuntimeError, KeyError):
                c.execute('UPDATE line_schedule_config SET last_error=%s WHERE id=1',
                          (('Gửi thử chưa thành công. Bấm gửi thử lại sau 5 phút.' if test else 'Gửi LINE chưa thành công. Tự thử lại sau 5 phút trong ngày thứ Hai.'),))
                c.execute('UPDATE line_schedule_deliveries SET next_attempt=%s WHERE week_start=%s',
                          (now + timedelta(minutes=5), delivery_key))
            else:
                c.execute('UPDATE line_schedule_deliveries SET accepted_at=%s WHERE week_start=%s', (now, delivery_key))
                c.execute('UPDATE line_schedule_config SET last_error=NULL WHERE id=1')
                c.execute('DELETE FROM line_schedule_deliveries WHERE expires_at<%s', (now,))
                return 'sent'
            c.execute('DELETE FROM line_schedule_deliveries WHERE expires_at<%s', (now,))

            return 'failed'

    def loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                # Do not log payloads, group messages or credentials.
                self.app.logger.error('LINE weekly delivery check failed; will retry.')
            threading.Event().wait(30)


def install_line_schedule(app, connect, start_worker=True):
    service = LineSchedule(app, connect)
    service.init()
    app.extensions['line_schedule'] = service

    @app.route('/admin/line', methods=['GET', 'POST'])
    def line_settings():
        if g.manager['role'] != 'owner':
            abort(403)
        notice = None
        if request.method == 'GET':
            session['line_test_key'] = str(uuid.uuid4())
        with connect() as c:
            config = service.config(c, lock=request.method == 'POST')
            if request.method == 'POST':
                action = request.form.get('action')
                if action == 'credentials':
                    token = request.form.get('token', '').strip()
                    secret = request.form.get('secret', '').strip()
                    if not (32 <= len(token) <= 1000 and len(secret) == 32):
                        notice = 'Kiểm tra lại Channel secret và Channel access token.'
                    else:
                        try:
                            bot = line_request(token, '/info')
                            if bot.get('basicId') != '@323txucc':
                                raise RuntimeError('Hãy dùng token của bot @323txucc.')
                        except RuntimeError as error:
                            notice = str(error)
                        else:
                            sealed = service.cipher.encrypt(json.dumps({'secret': secret, 'token': token}).encode()).decode()
                            # Explicit fixed origin prevents Host-header-controlled image URLs.
                            origin = 'https://shift-schedule-1fk2.onrender.com'
                            c.execute('''UPDATE line_schedule_config SET credentials=%s,public_origin=%s,
                                enabled=0,group_id=NULL,group_name=NULL,pair_hash=NULL,pair_expires=NULL,last_error=NULL WHERE id=1''', (sealed, origin))
                            notice = 'Đã lưu kết nối bot. Tiếp theo tạo mã ghép nhóm.'
                elif action == 'pair':
                    if config['credentials']:
                        code = secrets.token_hex(4).upper()
                        c.execute('UPDATE line_schedule_config SET pair_hash=%s,pair_expires=%s WHERE id=1',
                                  (hashlib.sha256(code.encode()).hexdigest(), datetime.now(JST) + timedelta(minutes=30)))
                        session['line_pair_code'] = code
                        notice = 'Mã ghép nhóm có hiệu lực 30 phút và chỉ dùng một lần.'
                elif action == 'enable':
                    if config['credentials'] and config['group_id']:
                        c.execute('UPDATE line_schedule_config SET enabled=1,last_error=NULL WHERE id=1')
                        notice = 'Đã bật nhắc đăng ký 希望シフト thứ Hai lúc 10:00, giờ Nhật.'
                    else:
                        notice = 'Cần kết nối bot và ghép nhóm trước khi bật.'
                elif action == 'test':
                    test_key = request.form.get('test_key', '')
                    if not test_key or not hmac.compare_digest(session.get('line_test_key', ''), test_key):
                        abort(403)
                    # Release the config row lock before delivery opens its own connection.
                    c.commit()
                    now = datetime.now(JST)
                    week = now.date() - timedelta(days=now.weekday())
                    result = service.deliver(week, now, 'reminder-test:' + test_key, test=True)
                    notice = {
                        'sent': 'LINE đã nhận tin nhắc đăng ký 希望シフト gửi thử.',
                        'closed': 'Đăng ký 希望シフト cho tuần tới chưa mở hoặc đã đóng. Chưa gửi nhắc.',
                        'not_ready': 'Chưa sẵn sàng. Kiểm tra kết nối bot và nhóm nhận.',
                        'pending': 'Lần gửi thử đang chờ. Thử lại sau 5 phút.',
                        'group_changed': 'Nhóm nhận đã đổi. Tải lại trang để tạo lần thử mới.',
                    }.get(result, 'Gửi thử chưa thành công. Vui lòng thử lại sau 5 phút.')
                elif action == 'disable':
                    c.execute('UPDATE line_schedule_config SET enabled=0 WHERE id=1')
                    notice = 'Đã tắt nhắc đăng ký tự động.'
                c.commit()
                config = service.config(c)
            recent = c.execute('SELECT week_start,accepted_at FROM line_schedule_deliveries ORDER BY next_attempt DESC LIMIT 5').fetchall()
            now = datetime.now(JST)
            current_week = now.date() - timedelta(days=now.weekday())
            reminder_preview = registration_reminder(c, current_week, now)
        return render_template('line_settings.html', config=config, notice=notice, recent=recent,
                               pair_code=session.get('line_pair_code') if config['pair_hash'] else None,
                               test_key=session.get('line_test_key', ''), reminder_preview=reminder_preview)

    @app.get('/admin/line/preview')
    def line_preview():
        if g.manager['role'] != 'owner':
            abort(403)
        today = datetime.now(JST).date()
        week = today - timedelta(days=today.weekday())
        with connect() as c:
            staff = c.execute('SELECT id,name,active FROM staff ORDER BY sort_order,id').fetchall()
            rows = c.execute('''SELECT staff_id,work_date,start,"end",break_start,break_end,break2_start,break2_end
                FROM shifts WHERE work_date BETWEEN %s AND %s AND COALESCE(is_kibo,0)=0''',
                (str(week), str(week + timedelta(days=6)))).fetchall()
        ids = {r['staff_id'] for r in rows}
        return Response(schedule_image(week, [s for s in staff if s['active'] or s['id'] in ids], rows), mimetype='image/png')

    @app.post('/line/webhook')
    def line_webhook():
        body = request.get_data(cache=False)
        if len(body) > 262144:
            abort(413)
        with connect() as c:
            config = service.config(c, lock=True)
            credentials = service.credentials(config)
            if not credentials or not signature_valid(credentials['secret'], body, request.headers.get('X-Line-Signature', '')):
                abort(403)
            try:
                data = json.loads(body)
                events = data['events']
                if not isinstance(events, list):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                abort(400)
            for event in events:
                if not isinstance(event, dict):
                    continue
                source = event.get('source') or {}
                if not isinstance(source, dict):
                    continue
                group_id = source.get('groupId') if source.get('type') == 'group' else None
                if not group_id:
                    continue
                if event.get('type') == 'leave' and group_id == config['group_id']:
                    c.execute('UPDATE line_schedule_config SET enabled=0,group_id=NULL,group_name=NULL WHERE id=1')
                message = event.get('message') or {}
                if not isinstance(message, dict):
                    continue
                text = message.get('text', '') if message.get('type') == 'text' else ''
                if event.get('type') != 'message' or not isinstance(text, str) or not text.startswith('/lich '):
                    continue
                code = text[6:].strip().upper()
                if not config['pair_hash'] or not config['pair_expires'] or config['pair_expires'] <= datetime.now(JST):
                    continue
                if not hmac.compare_digest(config['pair_hash'], hashlib.sha256(code.encode()).hexdigest()):
                    continue
                # Do not store other messages or sender profiles. Pair exactly the group holding the one-time code.
                group_name = 'Nhóm nhân viên'
                try:
                    group_name = line_request(credentials['token'], '/group/' + group_id + '/summary').get('groupName', group_name)
                except RuntimeError:
                    pass
                c.execute('''UPDATE line_schedule_config SET group_id=%s,group_name=%s,enabled=0,
                    pair_hash=NULL,pair_expires=NULL,last_error=NULL WHERE id=1''', (group_id, group_name))
                config = service.config(c)
        return jsonify(ok=True)

    @app.get('/line/schedule/<image_id>.png')
    def line_schedule_image(image_id):
        if len(image_id) != 43:
            abort(404)
        with connect() as c:
            image = c.execute('SELECT png FROM line_schedule_deliveries WHERE image_id=%s AND expires_at>%s',
                              (image_id, datetime.now(JST))).fetchone()
        if not image or not image['png']:
            abort(404)
        return Response(bytes(image['png']), mimetype='image/png')

    if start_worker and not os.environ.get('LINE_SCHEDULER_DISABLED'):
        threading.Thread(target=service.loop, name='line-weekly-schedule', daemon=True).start()
    return service

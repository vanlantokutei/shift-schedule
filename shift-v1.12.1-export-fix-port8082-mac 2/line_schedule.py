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
from line_design import reminder_card, welcome_card

JST = ZoneInfo('Asia/Tokyo')
DELIVERY_LOCK = 72905439
API = 'https://api.line.me/v2/bot'


def scheduled_week(now):
    """Monday 10:00 JST, with same-day recovery after restarts."""
    now = now.astimezone(JST)
    return now.date() if now.weekday() == 0 and now.hour >= 10 else None


def registration_reminder(connection, current_week, now, test=False, signup_url=None):
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
        f'{signup_url or "https://shift-schedule-1fk2.onrender.com/kibo?week=" + target.isoformat()}\n\n'
        'Cảm ơn mọi người!'
    )


def publication_text(week, updated=False):
    """Vietnamese notice accompanying the official schedule image."""
    label = 'Lịch cập nhật' if updated else 'Lịch đã chốt'
    intro = ('Lịch làm việc đã được điều chỉnh. Đây là bản mới nhất, thay cho bản đã gửi trước.'
             if updated else 'Lịch làm việc tuần này đã được chốt.')
    return (f'📅 Betoya Tsukiji — {label}\n'
            f'Tuần {week:%d/%m}–{week + timedelta(days=6):%d/%m/%Y}\n\n'
            f'{intro}\n'
            'Mọi người vui lòng kiểm tra ca làm và giờ nghỉ trong ảnh đính kèm.\n'
            'Nếu có thắc mắc hoặc cần đổi ca, hãy báo lại cho quản lý.\n\n'
            'Cảm ơn mọi người!')


def signature_valid(secret, body, signature):
    expected = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return bool(signature) and hmac.compare_digest(expected, signature)


def publish_week(value):
    try:
        week = datetime.strptime(value, '%Y-%m-%d').date()
        if week.weekday() or week.isoformat() != value:
            raise ValueError()
        return week
    except (TypeError, ValueError):
        abort(400, description='Chọn đúng ngày thứ Hai của tuần cần chốt.')


def publication_snapshot(connection, week, group_id):
    rows = connection.execute('''SELECT staff_id,work_date,start,"end",break_start,break_end,
        break2_start,break2_end,is_kibo FROM shifts WHERE work_date BETWEEN %s AND %s
        AND start IS NOT NULL AND "end" IS NOT NULL ORDER BY staff_id,work_date''',
        (str(week), str(week + timedelta(days=6)))).fetchall()
    people = connection.execute('SELECT id,name,active FROM staff ORDER BY sort_order,id').fetchall()
    ids = {r['staff_id'] for r in rows}
    people = [p for p in people if p['active'] or p['id'] in ids]
    # Wish approval does not change the displayed image, so it must not change
    # the idempotency key. Names, row order, breaks, week and recipient do.
    content = {'week': str(week), 'group': group_id,
               'people': [{'id': p['id'], 'name': p['name']} for p in people],
               'shifts': [{k: v for k, v in r.items() if k != 'is_kibo'} for r in rows]}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return people, rows, version


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
        if path == '/message/validate/push' and error.code == 400:
            try:
                details = json.loads(error.read(16384)).get('details', [])
                summary = '; '.join(str(d.get('property', '')) + ': ' + str(d.get('message', ''))
                                    for d in details if isinstance(d, dict))[:600]
            except (ValueError, TypeError, AttributeError):
                summary = ''
            raise RuntimeError('Thẻ LINE chưa hợp lệ. ' + summary) from None
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

            c.execute("""CREATE TABLE IF NOT EXISTS staff_schedule_publications (
                publication_key TEXT PRIMARY KEY, week_start TEXT NOT NULL,
                content TEXT NOT NULL, published_at TIMESTAMPTZ)""")

    def config(self, c, lock=False):
        return c.execute('SELECT * FROM line_schedule_config WHERE id=1' + (' FOR UPDATE' if lock else '')).fetchone()

    def credentials(self, config):
        return json.loads(self.cipher.decrypt(config['credentials'].encode())) if config['credentials'] else {}

    def tick(self, now=None):
        now = now or datetime.now(JST)
        week = scheduled_week(now)
        result = self.deliver(week, now, 'reminder:' + str(week)) if week else None
        private = self.app.extensions.get('line_private_reminders')
        if private:
            private.tick(now)
        return result

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
                identity = self.app.extensions.get('line_identity')
                signup_url = identity['registration_url'](c, week + timedelta(days=7)) if identity else None
                text = registration_reminder(c, week, now, test=test, signup_url=signup_url)
                if text is None:
                    c.execute('UPDATE line_schedule_config SET last_error=%s WHERE id=1',
                              ('Đăng ký 希望シフト cho tuần tới chưa mở hoặc đã đóng. Chưa gửi nhắc.',))
                    return 'closed'
                image_id = secrets.token_urlsafe(32)
                signup_url = signup_url or 'https://shift-schedule-1fk2.onrender.com/kibo?week=' + str(week + timedelta(days=7))
                payload = {'to': config['group_id'], 'messages': [reminder_card(text, signup_url, week + timedelta(days=7), kibo_window(c, week + timedelta(days=7))[1])]}
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

    def publish(self, week, expected_version, now=None):
        now = now or datetime.now(JST)
        with self.connect() as c:
            if not c.execute('SELECT pg_try_advisory_lock(%s) AS locked', (DELIVERY_LOCK,)).fetchone()['locked']:
                return {'ok': False, 'error': 'Đang gửi một tin LINE khác. Vui lòng thử lại.'}, 409
            # Hold table locks only while confirming and persisting the snapshot.
            # This includes new shifts inserted while the preview was open.
            c.execute('LOCK TABLE staff IN SHARE MODE')
            c.execute('LOCK TABLE shifts IN SHARE ROW EXCLUSIVE MODE')
            config = self.config(c, lock=True)
            if not config['credentials'] or not config['group_id']:
                return {'ok': False, 'error': 'Cần kết nối bot và ghép nhóm nhận trong phần Nhắc đăng ký lịch qua LINE.'}, 409
            people, rows, version = publication_snapshot(c, week, config['group_id'])
            if not rows:
                return {'ok': False, 'error': 'Tuần đang xem chưa có ca làm để chốt.'}, 400
            if (not isinstance(expected_version, str) or not expected_version.isascii()
                    or not hmac.compare_digest(version, expected_version)):
                return {'ok': False, 'error': 'Lịch hoặc nhóm nhận vừa thay đổi. Hãy mở lại bản xem trước.'}, 409
            key = 'published:' + str(week) + ':' + version
            job = c.execute('SELECT * FROM line_schedule_deliveries WHERE week_start=%s', (key,)).fetchone()
            if job and job['accepted_at']:
                return {'ok': True, 'duplicate': True, 'message': 'Phiên bản lịch này đã được LINE tiếp nhận. Không gửi lặp.'}, 200
            if job and job['expires_at'] - timedelta(days=6) <= now:
                return {'ok': False, 'error': 'Lần gửi trước đã quá 24 giờ. Kiểm tra nhóm LINE trước khi gửi lại.'}, 409
            if not job:
                previous = c.execute('''SELECT week_start FROM line_schedule_deliveries
                    WHERE week_start LIKE %s AND accepted_at IS NOT NULL LIMIT 1''',
                    ('published:' + str(week) + ':%',)).fetchone()
                image_id = secrets.token_urlsafe(32)
                image_url = config['public_origin'] + '/line/schedule/' + image_id + '.png'
                payload = {'to': config['group_id'], 'messages': [
                    {'type': 'text', 'text': publication_text(week, updated=bool(previous))},
                    {'type': 'image', 'originalContentUrl': image_url, 'previewImageUrl': image_url}]}
                c.execute('''INSERT INTO line_schedule_deliveries
                    (week_start,image_id,png,payload,retry_key,next_attempt,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                    (key, image_id, schedule_image(week, people, rows), json.dumps(payload),
                     str(uuid.uuid4()), now, now + timedelta(days=7)))
            content = {'people': [{'id': p['id'], 'name': p['name']} for p in people],
                       'shifts': [{k: v for k, v in r.items() if k != 'is_kibo'} for r in rows]}
            c.execute("""INSERT INTO staff_schedule_publications(publication_key,week_start,content)
                VALUES(%s,%s,%s) ON CONFLICT(publication_key) DO NOTHING""",
                (key, str(week), json.dumps(content, ensure_ascii=False)))
            c.execute('''UPDATE shifts SET is_kibo=0 WHERE work_date BETWEEN %s AND %s
                AND start IS NOT NULL AND "end" IS NOT NULL''',
                (str(week), str(week + timedelta(days=6))))
            # Release schedule locks and make the immutable image fetchable by LINE.
            c.commit()
            job = c.execute('SELECT * FROM line_schedule_deliveries WHERE week_start=%s', (key,)).fetchone()
            try:
                line_request(self.credentials(config)['token'], '/message/push',
                             json.loads(job['payload']), job['retry_key'])
            except RuntimeError:
                return {'ok': False, 'confirmed': True,
                        'error': 'Đã chốt ca nhưng LINE chưa xác nhận gửi. Bấm gửi lại để thử lại cùng phiên bản.'}, 502
            c.execute('UPDATE line_schedule_deliveries SET accepted_at=%s WHERE week_start=%s', (now, key))
            c.execute('UPDATE staff_schedule_publications SET published_at=%s WHERE publication_key=%s', (now, key))
            return {'ok': True, 'duplicate': False,
                    'message': 'Đã chốt lịch. LINE đã tiếp nhận ảnh gửi vào nhóm ' + config['group_name'] + '.'}, 200


def install_line_schedule(app, connect, start_worker=True):
    service = LineSchedule(app, connect)
    service.init()
    app.extensions['line_schedule'] = service

    @app.get('/line/publish-preview')
    def line_publish_preview():
        week = publish_week(request.args.get('week'))
        with connect() as c:
            config = service.config(c)
            if not config['credentials'] or not config['group_id']:
                return jsonify(ok=False, error='Cần kết nối bot và ghép nhóm nhận trước.'), 409
            people, rows, version = publication_snapshot(c, week, config['group_id'])
            key = 'published:' + str(week) + ':' + version
            job = c.execute('SELECT accepted_at FROM line_schedule_deliveries WHERE week_start=%s', (key,)).fetchone()
            previous = c.execute('''SELECT week_start FROM line_schedule_deliveries
                WHERE week_start LIKE %s AND accepted_at IS NOT NULL LIMIT 1''',
                ('published:' + str(week) + ':%',)).fetchone()
        return jsonify(ok=True, week=str(week), end=str(week + timedelta(days=6)),
                       group_name=config['group_name'], shifts=len(rows),
                       wishes=sum(bool(r['is_kibo']) for r in rows), version=version,
                       already_sent=bool(job and job['accepted_at']), has_previous=bool(previous),
                       message_preview=publication_text(week, updated=bool(previous)))

    @app.get('/line/publish-preview.png')
    def line_publish_preview_image():
        week = publish_week(request.args.get('week'))
        with connect() as c:
            config = service.config(c)
            people, rows, version = publication_snapshot(c, week, config['group_id'])
        if request.args.get('version') != version:
            abort(409)
        return Response(schedule_image(week, people, rows), mimetype='image/png')

    @app.post('/line/publish')
    def line_publish():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error='Dữ liệu chốt lịch không hợp lệ.'), 400
        week = publish_week(data.get('week'))
        result, status = service.publish(week, data.get('version'))
        return jsonify(result), status

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
                elif action == 'design_check':
                    now = datetime.now(JST)
                    week = now.date() - timedelta(days=now.weekday())
                    target = week + timedelta(days=7)
                    identity = app.extensions.get('line_identity')
                    signup_url = identity['registration_url'](c, target) if identity else None
                    signup_url = signup_url or 'https://shift-schedule-1fk2.onrender.com/kibo?week=' + str(target)
                    text = registration_reminder(c, week, now, signup_url=signup_url)
                    if not config['credentials'] or text is None:
                        notice = 'Cần kết nối bot và mở đăng ký tuần tới để kiểm tra thẻ.'
                    else:
                        try:
                            line_request(service.credentials(config)['token'], '/message/validate/push',
                                         {'messages': [reminder_card(text, signup_url, target, kibo_window(c, target)[1])]})
                            notice = 'LINE xác nhận thẻ nhắc hợp lệ. Không gửi tin nhắn vào nhóm.'
                        except RuntimeError as error:
                            notice = str(error)
                elif action == 'disable':
                    c.execute('UPDATE line_schedule_config SET enabled=0 WHERE id=1')
                    notice = 'Đã tắt nhắc đăng ký tự động.'
                c.commit()
                config = service.config(c)
            recent = c.execute('SELECT week_start,accepted_at FROM line_schedule_deliveries ORDER BY next_attempt DESC LIMIT 5').fetchall()
            now = datetime.now(JST)
            current_week = now.date() - timedelta(days=now.weekday())
            identity = app.extensions.get('line_identity')
            signup_url = identity['registration_url'](c, current_week + timedelta(days=7)) if identity else None
            reminder_preview = registration_reminder(c, current_week, now, signup_url=signup_url)
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
                app.extensions['line_private_reminders'].follow_event(c, event)
                group_id = source.get('groupId') if source.get('type') == 'group' else None
                # Rich-menu action in a private chat. Only a linked, active employee
                # can retrieve an already-published schedule; never expose drafts.
                if source.get('type') == 'user' and event.get('type') == 'message' and event.get('replyToken'):
                    message = event.get('message') or {}
                    if isinstance(message, dict) and message.get('type') == 'text' and message.get('text') in ('Xem lịch', 'Liên hệ Tencho'):
                        user_id = source.get('userId', '')
                        linked = c.execute('SELECT s.id FROM line_staff_links l JOIN staff s ON s.id=l.staff_id '
                                           'WHERE l.line_user_id=%s AND s.active=1', (user_id,)).fetchone()
                        identity = app.extensions.get('line_identity')
                        today = datetime.now(JST).date()
                        week = today - timedelta(days=today.weekday()) + timedelta(days=7)
                        signup_url = identity['registration_url'](c, week) if identity else None
                        messages = [{'type': 'text', 'text': 'Tencho chưa gửi lịch đã chốt. Bạn xem lại trong nhóm nhân viên nhé.'}]
                        if message.get('text') == 'Liên hệ Tencho':
                            messages = [{'type': 'text', 'text': 'Bạn cần hỗ trợ hoặc đổi ca? Nhắn Tencho Lân trong nhóm nhân viên nhé.\n\nベト屋築地店\nĐiện thoại quán: 03-6264-1370\nĐịa chỉ: 東京都中央区築地3-11-6 1階\nhttps://betoya.jp/store/tsukiji/'}]
                        elif not linked:
                            messages = [welcome_card(signup_url)] if signup_url else [{'type': 'text', 'text': 'Vui lòng liên hệ Tencho để liên kết tên trước khi xem lịch.'}]
                        else:
                            url = signup_url.split('?')[0] + '?view=schedule' if signup_url else config['public_origin'] + '/staff/schedule'
                            messages = [{'type': 'template', 'altText': 'Xem lịch đã chốt của quán và lịch của bạn',
                                'template': {'type': 'buttons', 'text': 'Chọn lịch muốn xem. Bấm tên trong bảng để mở lịch riêng.',
                                    'actions': [{'type': 'uri', 'label': 'Lịch cả quán', 'uri': url},
                                                {'type': 'uri', 'label': 'Lịch của tôi', 'uri': url + ('&' if '?' in url else '?') + 'mine=1'}]}}]
                        try:
                            line_request(credentials['token'], '/message/reply',
                                         {'replyToken': event['replyToken'], 'messages': messages})
                        except RuntimeError:
                            pass
                if not group_id:
                    continue
                if event.get('type') == 'memberJoined' and group_id == config['group_id'] and event.get('replyToken'):
                    identity = app.extensions.get('line_identity')
                    today = datetime.now(JST).date()
                    week = today - timedelta(days=today.weekday()) + timedelta(days=7)
                    signup_url = identity['registration_url'](c, week) if identity else None
                    if signup_url:
                        try:
                            line_request(credentials['token'], '/message/reply', {
                                'replyToken': event['replyToken'], 'messages': [welcome_card(signup_url)]})
                        except RuntimeError:
                            pass
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

    from line_identity import install_line_identity
    install_line_identity(app, connect)
    from line_private_reminders import install_private_reminders
    install_private_reminders(app, connect, service, lambda *args, **kwargs: line_request(*args, **kwargs))
    if start_worker and not os.environ.get('LINE_SCHEDULER_DISABLED'):
        threading.Thread(target=service.loop, name='line-weekly-schedule', daemon=True).start()
    return service

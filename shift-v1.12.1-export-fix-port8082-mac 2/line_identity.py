"""Verify LINE identity server-side and remember one employee per LINE account."""
from datetime import datetime, timedelta
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from flask import abort, g, jsonify, redirect, render_template, request, session

JST = ZoneInfo('Asia/Tokyo')
IDENTITY_LOCK = 72905449


def verify_line_token(token, channel_id):
    """Do not trust client-provided profile objects or decode unsigned claims."""
    if not isinstance(token, str) or not 20 <= len(token) <= 10000:
        raise ValueError('Mở lại trang từ LINE để xác nhận tài khoản.')
    req = Request('https://api.line.me/oauth2/v2.1/verify',
                  data=urlencode({'id_token': token, 'client_id': channel_id}).encode(),
                  headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=15) as response:
            claims = json.loads(response.read())
        if (claims.get('iss') != 'https://access.line.me' or str(claims.get('aud')) != channel_id
                or not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= time.time()
                or not re.fullmatch(r'U[0-9a-fA-F]{32}', claims.get('sub', ''))):
            raise ValueError()
        return claims['sub'], claims['exp']
    except (HTTPError, URLError, OSError, ValueError, TypeError, AttributeError):
        raise ValueError('Không xác nhận được tài khoản LINE. Vui lòng mở lại từ LINE.') from None


def install_line_identity(app, connect):
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)', (IDENTITY_LOCK,))
        c.execute('''CREATE TABLE IF NOT EXISTS line_identity_config (
            id INTEGER PRIMARY KEY, channel_id TEXT, liff_id TEXT)''')
        c.execute('INSERT INTO line_identity_config(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        c.execute('''CREATE TABLE IF NOT EXISTS line_staff_links (
            line_user_id TEXT PRIMARY KEY, staff_id INTEGER NOT NULL UNIQUE,
            linked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def config(c):
        return c.execute('SELECT channel_id,liff_id FROM line_identity_config WHERE id=1').fetchone()

    def registration_url(c, week):
        settings = config(c)
        if settings['channel_id'] and settings['liff_id']:
            return 'https://liff.line.me/' + settings['liff_id'] + '?week=' + str(week)
        return None

    app.extensions['line_identity'] = {'registration_url': registration_url}

    def requested_week():
        today = datetime.now(JST).date()
        default = today - timedelta(days=today.weekday()) + timedelta(days=7)
        value = request.args.get('week')
        try:
            week = datetime.strptime(value, '%Y-%m-%d').date() if value else default
            if week.weekday():
                raise ValueError()
            return str(week)
        except (TypeError, ValueError):
            return str(default)

    def verified_user(c, data):
        settings = config(c)
        if not settings['channel_id'] or not settings['liff_id']:
            abort(503, description='Kết nối LINE đang được thiết lập.')
        try:
            uid, expiry = verify_line_token(data.get('id_token'), settings['channel_id'])
        except ValueError as error:
            abort(401, description=str(error))
        session['line_staff_user'] = uid
        session['line_staff_exp'] = expiry
        return uid

    def json_input():
        # These endpoints use a fresh verified LINE token, not manager authority.
        # JSON and same-origin checks prevent login/link CSRF without relying on
        # a public name-picker session or accepting profile data from the client.
        if request.content_length and request.content_length > 16000:
            abort(413)
        origin = request.headers.get('Origin')
        if origin and origin != 'https://shift-schedule-1fk2.onrender.com':
            abort(403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        return data

    def linked_staff(c, uid):
        return c.execute('''SELECT s.id,s.name FROM line_staff_links l
            JOIN staff s ON s.id=l.staff_id WHERE l.line_user_id=%s AND s.active=1''', (uid,)).fetchone()

    @app.get('/kibo/line')
    def line_kibo_entry():
        with connect() as c:
            settings = config(c)
        return render_template('line_kibo.html', liff_id=settings['liff_id'] or '', week=requested_week())

    @app.post('/line/identity')
    def line_identity_profile():
        data = json_input()
        with connect() as c:
            uid = verified_user(c, data)
            link = linked_staff(c, uid)
            people = c.execute('''SELECT s.id,s.name,l.line_user_id FROM staff s
                LEFT JOIN line_staff_links l ON l.staff_id=s.id WHERE s.active=1 ORDER BY s.sort_order,s.id''').fetchall()
        return jsonify(ok=True, linked=dict(link) if link else None,
                       staff=[{'id': p['id'], 'name': p['name'],
                               'taken': bool(p['line_user_id'] and p['line_user_id'] != uid)} for p in people])

    @app.post('/line/identity/link')
    def line_identity_link():
        data = json_input()
        try:
            staff_id = int(data.get('staff_id'))
        except (TypeError, ValueError):
            return jsonify(ok=False, error='Vui lòng chọn tên của bạn.'), 400
        with connect() as c:
            uid = verified_user(c, data)
            c.execute('SELECT pg_advisory_xact_lock(%s)', (IDENTITY_LOCK,))
            person = c.execute('SELECT id,name FROM staff WHERE id=%s AND active=1', (staff_id,)).fetchone()
            if not person:
                return jsonify(ok=False, error='Tên này không còn hoạt động. Liên hệ quản lý.'), 409
            occupant = c.execute('SELECT line_user_id FROM line_staff_links WHERE staff_id=%s', (staff_id,)).fetchone()
            if occupant and occupant['line_user_id'] != uid:
                return jsonify(ok=False, error='Tên này đã liên kết với một tài khoản LINE khác. Liên hệ quản lý nếu cần sửa.'), 409
            c.execute('''INSERT INTO line_staff_links(line_user_id,staff_id) VALUES(%s,%s)
                ON CONFLICT(line_user_id) DO UPDATE SET staff_id=excluded.staff_id,linked_at=CURRENT_TIMESTAMP''',
                (uid, staff_id))
        return jsonify(ok=True, linked=dict(person))

    @app.before_request
    def remember_line_staff():
        g.line_staff = None
        uid = session.get('line_staff_user')
        if not uid or session.get('line_staff_exp', 0) <= time.time():
            return
        if request.endpoint not in {'kibo', 'kibo_prefill', 'kibo_fixed', 'kibo_fixed_delete'}:
            return
        with connect() as c:
            person = linked_staff(c, uid)
        if not person:
            return
        g.line_staff = dict(person)
        if request.endpoint == 'kibo' and request.method == 'GET':
            if request.args.get('staff_id') != str(person['id']):
                return redirect('/kibo?' + urlencode({'staff_id': person['id'], 'week': requested_week()}))
        if request.method != 'GET' or request.endpoint != 'kibo':
            data = request.get_json(silent=True) if request.is_json else request.values
            if str((data or {}).get('staff_id', '')) != str(person['id']):
                return jsonify(ok=False, error='Hãy đăng ký bằng tên đã liên kết với LINE của bạn.'), 409

    @app.context_processor
    def line_kibo_context():
        if request.endpoint != 'kibo':
            return {}
        with connect() as c:
            settings = config(c)
        return {'line_identity_available': bool(settings['channel_id'] and settings['liff_id']),
                'line_linked_name': g.line_staff['name'] if getattr(g, 'line_staff', None) else None}

    @app.route('/admin/line/identity', methods=['GET', 'POST'])
    def line_identity_settings():
        if g.manager['role'] != 'owner':
            abort(403)
        notice = None
        with connect() as c:
            if request.method == 'POST':
                c.execute('SELECT pg_advisory_xact_lock(%s)', (IDENTITY_LOCK,))
                if request.form.get('action') == 'unlink':
                    try:
                        staff_id = int(request.form.get('staff_id'))
                    except (ValueError, TypeError):
                        abort(400)
                    c.execute('DELETE FROM line_staff_links WHERE staff_id=%s', (staff_id,))
                    notice = 'Đã gỡ liên kết. Nhân viên có thể chọn lại tên lần mở LINE tiếp theo.'
                else:
                    channel = request.form.get('channel_id', '').strip()
                    liff = request.form.get('liff_id', '').strip()
                    if not re.fullmatch(r'[0-9]{5,20}', channel) or not re.fullmatch(r'[0-9]{5,20}-[A-Za-z0-9_-]{2,80}', liff):
                        notice = 'Kiểm tra LINE Login Channel ID và LIFF ID.'
                    else:
                        old = config(c)
                        count = c.execute('SELECT count(*) AS n FROM line_staff_links').fetchone()['n']
                        if count and old['channel_id'] != channel:
                            notice = 'Đã có nhân viên liên kết. Không đổi Channel ID khi chưa xử lý các liên kết hiện có.'
                        else:
                            c.execute('UPDATE line_identity_config SET channel_id=%s,liff_id=%s WHERE id=1', (channel, liff))
                            notice = 'Đã lưu kết nối LINE. Nhân viên mở link LIFF để chọn tên lần đầu.'
                c.commit()
            settings = config(c)
            links = c.execute('''SELECT s.id,s.name,s.active,l.linked_at FROM line_staff_links l
                JOIN staff s ON s.id=l.staff_id ORDER BY s.sort_order,s.id''').fetchall()
        return render_template('line_identity_settings.html', settings=settings, links=links, notice=notice)

    from staff_schedule import install_staff_schedule
    install_staff_schedule(app, connect, linked_staff)

    @app.get('/line/privacy')
    def line_identity_privacy():
        return render_template('line_identity_privacy.html')

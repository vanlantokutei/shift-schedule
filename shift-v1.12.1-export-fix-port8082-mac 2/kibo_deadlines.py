"""Per-week Kibo deadlines, always interpreted in Japan time."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import jsonify, request

JST = ZoneInfo('Asia/Tokyo')


def window(connection, week):
    opens = datetime.combine(week - timedelta(days=7), datetime.min.time(), JST)
    deadline = opens + timedelta(days=3, hours=22)
    row = connection.execute('SELECT deadline FROM kibo_deadlines WHERE week_start=%s',
                             (str(week),)).fetchone()
    if row:
        saved = row['deadline']
        if isinstance(saved, str):
            saved = datetime.fromisoformat(saved)
        deadline = max(deadline, saved.astimezone(JST))
    return opens, deadline


def install_deadline_route(app, connect):
    @app.post('/kibo/deadline')
    def extend_kibo_deadline():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(ok=False, error='Dữ liệu không hợp lệ.'), 400
        try:
            week = datetime.strptime(data.get('week', ''), '%Y-%m-%d').date()
            deadline = datetime.strptime(data.get('deadline', ''), '%Y-%m-%dT%H:%M').replace(tzinfo=JST)
            if week.weekday() != 0:
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(ok=False, error='Vui lòng chọn ngày và giờ gia hạn hợp lệ.'), 400
        now = datetime.now(JST)
        with connect() as connection:
            # Serialize concurrent extensions for the same week.
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (week.toordinal(),))
            opens, current = window(connection, week)
            if now < opens:
                return jsonify(ok=False, error='Tuần này chưa mở đăng ký Kibo.'), 400
            if deadline <= now or deadline <= current:
                return jsonify(ok=False, error='Hạn mới phải sau thời điểm hiện tại và hạn đang áp dụng.'), 400
            connection.execute("""INSERT INTO kibo_deadlines(week_start,deadline) VALUES(%s,%s)
                ON CONFLICT(week_start) DO UPDATE SET deadline=excluded.deadline""",
                (str(week), deadline))
        return jsonify(ok=True, deadline=deadline.isoformat())

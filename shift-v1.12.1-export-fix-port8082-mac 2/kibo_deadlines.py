"""Per-week Kibo deadlines, always interpreted in Japan time."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import jsonify, request
from branches import branch_id

JST = ZoneInfo('Asia/Tokyo')


def always_open(connection):
    if branch_id()==1:
        return False
    row=connection.execute('SELECT always_open FROM branch_kibo_settings WHERE branch_id=%s', (branch_id(),)).fetchone()
    return bool(row and row['always_open'])


def window(connection, week):
    opens = datetime.combine(week - timedelta(days=7), datetime.min.time(), JST)
    deadline = opens + timedelta(days=3, hours=22)
    row = (connection.execute('SELECT deadline FROM kibo_deadlines WHERE week_start=%s', (str(week),)) if branch_id()==1 else connection.execute('SELECT deadline FROM branch_kibo_deadlines WHERE branch_id=%s AND week_start=%s', (branch_id(),str(week)))).fetchone()
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
            if always_open(connection):
                return jsonify(ok=False, error='Chi nhánh này đang mở Kibo 24/24, không cần gia hạn.'),409
            # Serialize concurrent extensions for the same week.
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (week.toordinal(),))
            opens, current = window(connection, week)
            if now < opens:
                return jsonify(ok=False, error='Tuần này chưa mở đăng ký Kibo.'), 400
            if deadline <= now or deadline <= current:
                return jsonify(ok=False, error='Hạn mới phải sau thời điểm hiện tại và hạn đang áp dụng.'), 400
            if branch_id()!=1:
                connection.execute("""INSERT INTO branch_kibo_deadlines(branch_id,week_start,deadline) VALUES(%s,%s,%s) ON CONFLICT(branch_id,week_start) DO UPDATE SET deadline=excluded.deadline""", (branch_id(),str(week),deadline))
                return jsonify(ok=True, deadline=deadline.isoformat())
            connection.execute("""INSERT INTO kibo_deadlines(week_start,deadline) VALUES(%s,%s)
                ON CONFLICT(week_start) DO UPDATE SET deadline=excluded.deadline""",
                (str(week), deadline))
        return jsonify(ok=True, deadline=deadline.isoformat())

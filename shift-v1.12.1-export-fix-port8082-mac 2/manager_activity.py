"""Branch-scoped editing presence and transactional shift audit trail."""
import json
from flask import g, has_request_context, jsonify, request
from branches import branch_id


def identify_writer(connection):
    manager = getattr(g, 'manager', None) if has_request_context() else None
    if manager and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        connection.execute("SELECT set_config('betoya.writer', %s, true)",
                           (json.dumps({'id': manager['id'], 'name': manager['display_name']}),))
    return connection


def install_manager_activity(app, connect):
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(72905439)')
        c.execute('''CREATE TABLE IF NOT EXISTS manager_presence (
            manager_id INTEGER NOT NULL, branch_id INTEGER NOT NULL,
            tab TEXT NOT NULL, state TEXT NOT NULL, seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(manager_id,branch_id,tab))''')
        c.execute('''CREATE TABLE IF NOT EXISTS shift_audit (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            branch_id INTEGER NOT NULL, manager_id INTEGER NOT NULL,
            manager_name TEXT NOT NULL, staff_name TEXT NOT NULL,
            work_date TEXT NOT NULL, before_shift JSONB, after_shift JSONB,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        c.execute('CREATE INDEX IF NOT EXISTS shift_audit_branch_latest ON shift_audit(branch_id,id DESC)')
        c.execute('''CREATE OR REPLACE FUNCTION betoya_audit_shift() RETURNS trigger AS $$
        DECLARE actor JSONB; previous JSONB; following JSONB; employee INTEGER; store INTEGER; person TEXT;
        BEGIN
          actor := nullif(current_setting('betoya.writer',true),'')::jsonb;
          IF actor IS NULL THEN RETURN NULL; END IF;
          IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD) - 'id'; END IF;
          IF TG_OP <> 'DELETE' THEN following := to_jsonb(NEW) - 'id'; END IF;
          IF previous IS NOT DISTINCT FROM following THEN RETURN NULL; END IF;
          employee := coalesce(following->>'staff_id', previous->>'staff_id')::integer;
          SELECT branch_id,name INTO store,person FROM staff WHERE id=employee;
          INSERT INTO shift_audit(branch_id,manager_id,manager_name,staff_name,work_date,before_shift,after_shift)
            VALUES(store,(actor->>'id')::integer,actor->>'name',person,
                   coalesce(following->>'work_date',previous->>'work_date'),previous,following);
          RETURN NULL;
        END; $$ LANGUAGE plpgsql''')
        c.execute('DROP TRIGGER IF EXISTS betoya_shift_audit ON shifts')
        c.execute('''CREATE TRIGGER betoya_shift_audit AFTER INSERT OR UPDATE OR DELETE ON shifts
                     FOR EACH ROW EXECUTE FUNCTION betoya_audit_shift()''')

    @app.post('/admin/schedule-presence')
    def schedule_presence():
        data = request.get_json(silent=True) or {}
        tab = data.get('tab')
        state = data.get('state')
        if not isinstance(tab, str) or not 1 <= len(tab) <= 80 or state not in {'viewing', 'editing', 'away'}:
            return jsonify(ok=False), 400
        with connect() as c:
            c.execute("DELETE FROM manager_presence WHERE seen_at < now() - interval '1 day'")
            if state == 'away':
                c.execute('DELETE FROM manager_presence WHERE manager_id=%s AND branch_id=%s AND tab=%s',
                          (g.manager['id'], branch_id(), tab))
            else:
                c.execute('''INSERT INTO manager_presence(manager_id,branch_id,tab,state) VALUES(%s,%s,%s,%s)
                    ON CONFLICT(manager_id,branch_id,tab) DO UPDATE SET state=excluded.state,seen_at=now()''',
                          (g.manager['id'], branch_id(), tab, state))
        return jsonify(ok=True)

    @app.get('/admin/schedule-activity')
    def schedule_activity():
        owner = g.manager['role'] == 'owner'
        with connect() as c:
            stores = c.execute('SELECT id,name FROM branches WHERE %s OR id=%s ORDER BY id',
                               (owner, branch_id())).fetchall()
            result = []
            for store in stores:
                people = c.execute('''SELECT a.display_name AS name,
                    CASE WHEN bool_or(p.state='editing') THEN 'editing' ELSE 'viewing' END AS state
                    FROM manager_presence p JOIN manager_accounts a ON a.id=p.manager_id
                    WHERE p.branch_id=%s AND p.seen_at>now()-interval '45 seconds' AND a.active=1
                    AND (a.role='owner' OR a.branch_id=p.branch_id)
                    GROUP BY a.id,a.display_name ORDER BY a.display_name''', (store['id'],)).fetchall()
                changes = c.execute('''SELECT manager_name,staff_name,work_date,before_shift,after_shift,
                    to_char(changed_at AT TIME ZONE 'Asia/Tokyo','DD/MM/YYYY HH24:MI:SS') AS at
                    FROM shift_audit WHERE branch_id=%s ORDER BY id DESC LIMIT 30''', (store['id'],)).fetchall()
                result.append(dict(store, people=people, changes=changes))
        return jsonify(branches=result)

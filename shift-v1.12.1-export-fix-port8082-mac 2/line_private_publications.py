"""Durable, per-employee delivery of explicitly published schedules."""
from line_branches import branch_number, branch_name, branch_url
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCK=72905441
JST=ZoneInfo('Asia/Tokyo')


def private_text(week,person,rows):
    own={r['work_date']:r for r in rows if r['staff_id']==person['id']}
    lines=[f"📅 Lịch làm của {person['name']} · {branch_name()}",f"Tuần {week:%d/%m}–{week+timedelta(days=6):%d/%m/%Y}"]
    for i in range(7):
        day=week+timedelta(days=i);row=own.get(str(day));label=['T2','T3','T4','T5','T6','T7','CN'][i]
        text=f"{label} {day:%d/%m}: —"
        if row:
            text=f"{label} {day:%d/%m}: {row['start']}–{row['end']}"
            if row['end']<row['start']:text+=' (+1 ngày)'
            rests=[f"{row[a]}–{row[b]}" for a,b in [('break_start','break_end'),('break2_start','break2_end')] if row.get(a) and row.get(b)]
            if rests:text+=' · Nghỉ '+', '.join(rests)
        lines.append(text)
    lines.append('Đây là lịch đã chốt. Cần đổi ca, vui lòng liên hệ Tencho.')
    return '\n'.join(lines)


def own_schedule(rows, staff_id):
    # Compare only actual shifts/rests, never names, wages or approval flags.
    fields=('work_date','start','end','break_start','break_end','break2_start','break2_end')
    return sorted(tuple(str(r.get(k) or '') for k in fields)
                  for r in rows if r['staff_id']==staff_id)


def net_minutes(rows):
    from shift_times import shift_bounds, break_bounds
    total=0
    for row in rows:
        left,right=shift_bounds(row['start'],row['end'],overnight=True)
        rest=0
        for a,b in [('break_start','break_end'),('break2_start','break2_end')]:
            if row.get(a) and row.get(b):
                begin,finish=break_bounds(row[a],row[b],left,right,overnight=True)
                rest+=finish-begin
        total+=max(0,right-left-rest)
    return total


def private_card(week,person,rows,previous=None,schedule_url=None):
    own={r['work_date']:r for r in rows if r['staff_id']==person['id']}
    old={r['work_date']:r for r in (previous or []) if r['staff_id']==person['id']}
    def text(value,size='sm',color='#075B40',bold=False):
        result={'type':'text','text':value,'size':size,'color':color,'wrap':True}
        if bold:result['weight']='bold'
        return result
    days=[];changes=0
    for i in range(7):
        day=week+timedelta(days=i);row=own.get(str(day));before=old.get(str(day))
        changed=previous is not None and own_schedule([row] if row else [],person['id'])!=own_schedule([before] if before else [],person['id'])
        changes+=int(changed)
        hours='—' if not row else row['start']+'–'+row['end']+(' (+1 ngày)' if row['end']<row['start'] else '')
        detail=[text(hours,'lg','#075B40' if row else '#A0AAA5',True)]
        if row:
            rests=[row[a]+'–'+row[b] for a,b in [('break_start','break_end'),('break2_start','break2_end')] if row.get(a) and row.get(b)]
            if rests:detail.append(text('Nghỉ '+ ' · '.join(rests),'xs','#64736B'))
        datebox={'type':'box','layout':'vertical','flex':0,'width':'54px','paddingAll':'6px','cornerRadius':'8px','backgroundColor':'#F0F2F1','contents':[
            dict(text(['T2','T3','T4','T5','T6','T7','CN'][i],bold=True),align='center'),
            dict(text(f'{day:%d/%m}','xs','#64736B'),align='center')]}
        columns=[datebox,{'type':'box','layout':'vertical','flex':1,'spacing':'xs','contents':detail}]
        if changed:columns.append({'type':'box','layout':'vertical','flex':0,'paddingAll':'5px','cornerRadius':'12px','backgroundColor':'#FFF0DC','contents':[text('Đã đổi','xxs','#B66A16',True)]})
        days.append({'type':'box','layout':'horizontal','spacing':'md','alignItems':'center','paddingAll':'8px','cornerRadius':'8px',
                     'backgroundColor':'#FFFAF1' if changed else '#FFFFFF','contents':columns})
        if i<6:days.append({'type':'separator','color':'#E8EDE9'})
    minutes=net_minutes(own.values());hours,mins=divmod(minutes,60)
    total=f'Tổng giờ: {hours} giờ'+(f' {mins} phút' if mins else '')
    header=[text(branch_name(),'xs','#D8EADF'),
            text(('Lịch cập nhật · ' if previous is not None else 'Lịch đã chốt · ')+person['name'],'xl','#FFFFFF',True),
            text(f'{week:%d/%m} – {week+timedelta(days=6):%d/%m/%Y}','sm','#D8EADF'),
            {'type':'box','layout':'vertical','paddingAll':'8px','cornerRadius':'12px','backgroundColor':'#DFF1E6','contents':[dict(text(total,'md',bold=True),align='center')]},
            dict(text('(Đã trừ giờ nghỉ)','xs','#D8EADF'),align='center')]
    if changes:days.insert(0,{'type':'box','layout':'vertical','paddingAll':'8px','cornerRadius':'12px','backgroundColor':'#EDF7F0','contents':[text(f'● {changes} ngày có thay đổi','sm','#B66A16',True)]})
    footer=[]
    if schedule_url:footer.append({'type':'button','style':'primary','color':'#075B40','action':{'type':'uri','label':'Xem lịch của tôi','uri':schedule_url}})
    footer.append(dict(text('Cần đổi ca, liên hệ Tencho.','xs','#64736B'),align='center'))
    return {'type':'flex','altText':private_text(week,person,rows)[:1500], 'contents':{
        'type':'bubble','size':'mega',
        'header':{'type':'box','layout':'vertical','backgroundColor':'#075B40','paddingAll':'20px','spacing':'sm','contents':header},
        'body':{'type':'box','layout':'vertical','spacing':'sm','paddingAll':'12px','contents':days},
        'footer':{'type':'box','layout':'vertical','paddingAll':'16px','spacing':'sm','contents':footer}}}


class PrivatePublications:
    def __init__(self,connect,service,send):
        self.connect,self.service,self.send=connect,service,send
        with connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
            c.execute('''CREATE TABLE IF NOT EXISTS line_private_publications (
                publication_key TEXT NOT NULL,staff_id INTEGER NOT NULL,line_user_id TEXT NOT NULL,
                payload TEXT NOT NULL,retry_key TEXT NOT NULL,accepted_at TIMESTAMPTZ,
                next_attempt TIMESTAMPTZ NOT NULL,expires_at TIMESTAMPTZ NOT NULL,
                stopped INTEGER NOT NULL DEFAULT 0,last_error TEXT,
                PRIMARY KEY(publication_key,staff_id))''')

    def recipients(self,c):
        return c.execute(f'''SELECT s.id,s.name,l.line_user_id FROM staff s
            JOIN line_staff_links l ON l.staff_id=s.id
            LEFT JOIN line_private_follows f ON f.line_user_id=l.line_user_id
            WHERE s.branch_id={branch_number()} AND s.active=1 AND (f.active IS NULL OR f.active=1)
            ORDER BY s.id''').fetchall()

    def enqueue(self,c,key,week,rows,now):
        for person in self.recipients(c):
            last=c.execute("""SELECT p.content FROM line_private_publications d
                JOIN staff_schedule_publications p ON p.publication_key=d.publication_key
                WHERE d.staff_id=%s AND d.line_user_id=%s AND d.accepted_at IS NOT NULL
                AND p.week_start=%s AND d.publication_key<>%s
                ORDER BY d.accepted_at DESC,p.published_at DESC LIMIT 1""",
                (person['id'],person['line_user_id'],str(week),key)).fetchone()
            previous=json.loads(last['content'])['shifts'] if last else None
            if previous is not None and own_schedule(previous,person['id'])==own_schedule(rows,person['id']):
                continue
            origin=self.service.config(c)['public_origin']
            schedule_url=branch_url(origin+'/staff/schedule?mine=1&week='+str(week))
            payload={'to':person['line_user_id'],'messages':[private_card(week,person,rows,previous,schedule_url)]}
            c.execute('''INSERT INTO line_private_publications
                (publication_key,staff_id,line_user_id,payload,retry_key,next_attempt,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(publication_key,staff_id) DO NOTHING''',
                (key,person['id'],person['line_user_id'],json.dumps(payload,ensure_ascii=False),str(uuid.uuid4()),now,now+timedelta(hours=23)))
        return self.stats(c,key)

    def stats(self,c,key):
        rows=c.execute('SELECT accepted_at,stopped FROM line_private_publications WHERE publication_key=%s',(key,)).fetchall()
        return {'total':len(rows),'accepted':sum(bool(r['accepted_at']) for r in rows),
                'pending':sum(not r['accepted_at'] and not r['stopped'] for r in rows),
                'stopped':sum(bool(r['stopped']) for r in rows)}

    def tick(self,now=None):
        now=now or datetime.now(JST)
        with self.connect() as c:
            if not c.execute('SELECT pg_try_advisory_lock(%s) AS locked',(LOCK,)).fetchone()['locked']:return
            config=self.service.config(c)
            if not config['credentials']:return
            jobs=c.execute('''SELECT d.*,p.week_start,p.published_at,g.payload AS group_payload
                FROM line_private_publications d JOIN staff_schedule_publications p ON p.publication_key=d.publication_key
                JOIN line_schedule_deliveries g ON g.week_start=d.publication_key
                WHERE d.accepted_at IS NULL AND d.stopped=0 AND d.next_attempt<=%s
                AND p.published_at IS NOT NULL AND g.accepted_at IS NOT NULL
                ORDER BY d.next_attempt,d.staff_id LIMIT 20''',(now,)).fetchall()
            for job in jobs:
                key,sid=job['publication_key'],job['staff_id']
                expires=job['expires_at']
                if isinstance(expires,str):expires=datetime.fromisoformat(expires)
                valid=c.execute(f'''SELECT s.id FROM staff s JOIN line_staff_links l ON l.staff_id=s.id
                    LEFT JOIN line_private_follows f ON f.line_user_id=l.line_user_id
                    WHERE s.id=%s AND s.branch_id={branch_number()} AND s.active=1 AND l.line_user_id=%s
                    AND (f.active IS NULL OR f.active=1)''',(sid,job['line_user_id'])).fetchone()
                newer=c.execute('''SELECT publication_key FROM staff_schedule_publications
                    WHERE week_start=%s AND published_at>%s LIMIT 1''',(job['week_start'],job['published_at'])).fetchone()
                if not valid or newer or now>=expires or json.loads(job['group_payload'])['to']!=config['group_id']:
                    c.execute('UPDATE line_private_publications SET stopped=1,last_error=%s WHERE publication_key=%s AND staff_id=%s',
                              ('Đã dừng: liên kết thay đổi, lịch mới hơn hoặc hết thời gian thử lại.',key,sid));c.commit();continue
                # Persist the next attempt before calling LINE, and reuse its idempotency key on every retry.
                c.execute('UPDATE line_private_publications SET next_attempt=%s WHERE publication_key=%s AND staff_id=%s',
                          (now+timedelta(minutes=5),key,sid));c.commit()
                try:self.send(self.service.credentials(config)['token'],'/message/push',json.loads(job['payload']),job['retry_key'])
                except RuntimeError:
                    c.execute('UPDATE line_private_publications SET last_error=%s WHERE publication_key=%s AND staff_id=%s',
                              ('LINE chưa xác nhận; sẽ thử lại.',key,sid))
                else:
                    c.execute('UPDATE line_private_publications SET accepted_at=%s,last_error=NULL WHERE publication_key=%s AND staff_id=%s',
                              (now,key,sid))
                c.commit()

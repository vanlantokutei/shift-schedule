from flask import Flask, render_template, request, jsonify
from datetime import date, timedelta, datetime
import sqlite3
app=Flask(__name__); DB='shift.db'
DEFAULT_STAFF=['LÂN','SƯƠNG','Thoa','MINH ANH','HƯNG','THẢO','THƯ','Xuân','Nhi','Hiền','Ngân','HIỀN Mới','Trang']
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER DEFAULT 1,staff_type TEXT DEFAULT 'baito',pay_type TEXT DEFAULT 'hourly',hourly_rate INTEGER DEFAULT 1250,monthly_salary INTEGER DEFAULT 0,sort_order INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY,staff_id INTEGER,work_date TEXT,start TEXT,end TEXT,break_min INTEGER DEFAULT 0,break_start TEXT,break_end TEXT,UNIQUE(staff_id,work_date));''')
 cols=[r['name'] for r in c.execute('pragma table_info(staff)')]
 for name,sql in [('staff_type',"alter table staff add column staff_type TEXT DEFAULT 'baito'"),('pay_type',"alter table staff add column pay_type TEXT DEFAULT 'hourly'"),('hourly_rate',"alter table staff add column hourly_rate INTEGER DEFAULT 1250"),('monthly_salary',"alter table staff add column monthly_salary INTEGER DEFAULT 0"),('sort_order',"alter table staff add column sort_order INTEGER DEFAULT 0")]:
  if name not in cols:c.execute(sql)
 sc=[r['name'] for r in c.execute('pragma table_info(shifts)')]
 if 'break_start' not in sc:c.execute('alter table shifts add column break_start TEXT')
 if 'break_end' not in sc:c.execute('alter table shifts add column break_end TEXT')
 if c.execute('select count(*) n from staff').fetchone()['n']==0:c.executemany('insert into staff(name) values(?)',[(x,) for x in DEFAULT_STAFF])
 # Initialize stable display order for old databases
 rows=c.execute('select id,sort_order from staff order by id').fetchall()
 if rows and all((r['sort_order'] or 0)==0 for r in rows):
  for i,r in enumerate(rows): c.execute('update staff set sort_order=? where id=?',(i,r['id']))
 c.commit();c.close()
def monday(s=None):
 d=datetime.strptime(s,'%Y-%m-%d').date() if s else date.today();return d-timedelta(days=d.weekday())
def hdiff(a,b):
 if not a or not b:return 0
 x=datetime.strptime(a,'%H:%M');y=datetime.strptime(b,'%H:%M');return max(0,(y-x).seconds/3600)
def hours(r):
 if not r:return 0
 br=hdiff(r['break_start'],r['break_end']) if r['break_start'] and r['break_end'] else 0
 return max(0,hdiff(r['start'],r['end'])-br)
@app.route('/')
def index():
 m=monday(request.args.get('week'));days=[m+timedelta(days=i) for i in range(7)];c=db();staff=c.execute('select * from staff where active=1 order by sort_order,id').fetchall();rows=c.execute('select * from shifts where work_date between ? and ?',(str(days[0]),str(days[-1]))).fetchall();# Monthly totals for the month containing the week start.
 month_start=m.replace(day=1)
 if m.month==12: month_end=date(m.year+1,1,1)-timedelta(days=1)
 else: month_end=date(m.year,m.month+1,1)-timedelta(days=1)
 month_rows=c.execute('select * from shifts where work_date between ? and ?',(str(month_start),str(month_end))).fetchall()
 c.close();sm={(r['staff_id'],r['work_date']):r for r in rows}
 totals={s['id']:sum(hours(sm.get((s['id'],str(d)))) for d in days) for s in staff}
 monthly_totals={s['id']:sum(hours(r) for r in month_rows if r['staff_id']==s['id']) for s in staff}
 pay={s['id']:(round(monthly_totals[s['id']]*(s['hourly_rate'] or 0)) if s['pay_type']=='hourly' else (s['monthly_salary'] or 0)) for s in staff}
 baito_monthly_pay=sum(round(monthly_totals[s['id']]*(s['hourly_rate'] or 0)) for s in staff if s['staff_type']=='baito')
 daily_totals={str(d):sum(hours(sm.get((s['id'],str(d)))) for s in staff) for d in days}
 monthly_all_hours=sum(monthly_totals.values())
 return render_template('index.html',staff=staff,days=days,sm=sm,totals=totals,monthly_totals=monthly_totals,pay=pay,baito_monthly_pay=baito_monthly_pay,daily_totals=daily_totals,monthly_all_hours=monthly_all_hours,prev=m-timedelta(days=7),nxt=m+timedelta(days=7),current_month=m.month,current_year=m.year)
@app.post('/shift')
def shift():
 x=request.get_json();c=db()
 if not x.get('start') or not x.get('end'):c.execute('delete from shifts where staff_id=? and work_date=?',(x['staff_id'],x['date']))
 else:c.execute('''insert into shifts(staff_id,work_date,start,end,break_start,break_end,break_min) values(?,?,?,?,?,?,0) on conflict(staff_id,work_date) do update set start=excluded.start,end=excluded.end,break_start=excluded.break_start,break_end=excluded.break_end''',(x['staff_id'],x['date'],x['start'],x['end'],x.get('break_start') or None,x.get('break_end') or None))
 c.commit();c.close();return jsonify(ok=True)
@app.post('/staff')
def staff_action():
 x=request.get_json();c=db();a=x.get('action')
 if a=='add' and x.get('name','').strip():
  mx=c.execute('select coalesce(max(sort_order),-1)+1 n from staff').fetchone()['n'];cur=c.execute('insert into staff(name,sort_order) values(?,?)',(x['name'].strip(),mx));sid=cur.lastrowid
 elif a=='rename' and x.get('name','').strip(): c.execute('update staff set name=? where id=?',(x['name'].strip(),x['id']));sid=x['id']
 elif a=='delete': c.execute('update staff set active=0 where id=?',(x['id'],));sid=x['id']
 elif a=='update':
  c.execute('update staff set staff_type=?,pay_type=?,hourly_rate=?,monthly_salary=? where id=?',(x.get('staff_type','baito'),x.get('pay_type','hourly'),int(x.get('hourly_rate') or 0),int(x.get('monthly_salary') or 0),x['id']));sid=x['id']
 else:c.close();return jsonify(ok=False),400
 c.commit();c.close();return jsonify(ok=True,id=sid)


@app.post('/move-week')
def move_week():
 x=request.get_json() or {}
 try:
  src=monday(x.get('source_week'))
  raw=x.get('target_date')
  if not raw: return jsonify(ok=False,error='missing target date'),400
  target=monday(raw)
 except Exception:
  return jsonify(ok=False,error='invalid date'),400
 if src==target: return jsonify(ok=True,target=str(target),moved=0)
 c=db()
 src_end=src+timedelta(days=6); target_end=target+timedelta(days=6)
 source_rows=c.execute('select * from shifts where work_date between ? and ?',(str(src),str(src_end))).fetchall()
 conflicts=c.execute('select count(*) n from shifts where work_date between ? and ?',(str(target),str(target_end))).fetchone()['n']
 if conflicts and not x.get('force'):
  c.close(); return jsonify(ok=False,conflict=True,count=conflicts,target=str(target)),409
 try:
  c.execute('begin')
  if conflicts: c.execute('delete from shifts where work_date between ? and ?',(str(target),str(target_end)))
  # Move through temporary dates first so UNIQUE(staff_id,work_date) can never collide.
  temp_base=date(2099,1,4)
  moved=[]
  for i,r in enumerate(source_rows):
   old=datetime.strptime(r['work_date'],'%Y-%m-%d').date(); offset=(old-src).days
   tmp=temp_base+timedelta(days=offset)
   c.execute('update shifts set work_date=? where id=?',(str(tmp),r['id']))
   moved.append((r['id'],str(target+timedelta(days=offset))))
  for rid,newdate in moved: c.execute('update shifts set work_date=? where id=?',(newdate,rid))
  c.commit()
 except Exception as e:
  c.rollback(); c.close(); return jsonify(ok=False,error=str(e)),500
 c.close(); return jsonify(ok=True,target=str(target),moved=len(source_rows))

@app.post('/staff/reorder')
def staff_reorder():
 x=request.get_json() or {}; order=x.get('order') or []
 c=db()
 for i,sid in enumerate(order): c.execute('update staff set sort_order=? where id=?',(i,int(sid)))
 c.commit();c.close();return jsonify(ok=True)

if __name__=='__main__':init();app.run(host='127.0.0.1',port=8082,debug=False)

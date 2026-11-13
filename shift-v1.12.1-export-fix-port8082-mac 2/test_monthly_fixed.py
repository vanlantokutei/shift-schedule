from test_kibo_deadlines import env


def test_month_boundary_and_cancel(env):
    app,client,raw,connect=env
    raw.execute("INSERT INTO fixed_kibo VALUES(1,0,'08:00','12:00')")
    payload={'staff_id':1,'month':'2026-11','entries':[{'weekday':0,'start':'09:00','end':'17:00'},{'weekday':1,'start':'10:00','end':'18:00'}]}
    assert client.post('/kibo/fixed',json=payload).json['saved']==2
    entries=client.get('/kibo/prefill?staff_id=1&week=2026-11-30').json['entries']
    assert entries=={'0':{'start':'09:00','end':'17:00'}}
    assert client.get('/kibo/prefill?staff_id=1&week=2026-12-07').json['entries']['0']['start']=='08:00'
    assert raw.execute('SELECT count(*) FROM shifts').fetchone()[0]==0
    assert raw.execute('SELECT count(*) FROM kibo_submissions').fetchone()[0]==0
    assert client.delete('/kibo/fixed?staff_id=1&month=2026-11').json['ok']
    assert raw.execute('SELECT count(*) FROM fixed_kibo').fetchone()[0]==1
    assert raw.execute('SELECT count(*) FROM monthly_fixed_kibo').fetchone()[0]==0


def test_invalid_month_and_foreign_staff(env):
    _,client,raw,_=env
    for month in ['2026-13','2026-1',42]:
        assert client.post('/kibo/fixed',json={'staff_id':1,'month':month,'entries':[]}).status_code==400
    assert client.post('/kibo/fixed',json={'staff_id':999,'month':'2026-11','entries':[]}).status_code==409

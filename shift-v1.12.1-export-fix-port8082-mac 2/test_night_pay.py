import pytest
from shift_times import night_minutes
from test_branches import tenants, BASE
from test_kibo_deadlines import env

@pytest.mark.parametrize('start,end,rests,expected',[
    ('10:00','24:00',[],120),
    ('22:00','06:00',[('23:30','00:30'),('03:00','04:00')],300),
    ('00:00','08:00',[],300),
    ('21:00','05:00',[('21:30','22:30'),('04:30','05:00')],360),
    ('05:00','22:00',[],0),
    ('00:00','24:00',[],420),
    ('04:00','03:00',[],360),
    ('22:00','06:00',[('23:00','24:00'),('23:30','00:30')],330),
])
def test_night_minutes(start,end,rests,expected):
    row=dict(start=start,end=end)
    for pair,keys in zip(rests,[('break_start','break_end'),('break2_start','break2_end')]):
        row.update(zip(keys,pair))
    assert night_minutes(row)==expected


def test_branch2_pay_includes_night_and_subtracts_break(tenants):
    _,client,raw=tenants
    raw.execute('DELETE FROM shifts')
    for sid in [1,2]:
        raw.execute("UPDATE staff SET pay_type='hourly',staff_type='baito',hourly_rate=1280 WHERE id=?",(sid,))
        raw.execute("INSERT INTO shifts VALUES(?,'2026-11-17','20:00','24:00',0,'22:30','23:00',NULL,NULL,0)",(sid,))
    raw.commit()
    # 3.5h base + 1.5h night × 25% = 4480 + 480 = 4960.
    deli=client.get('/?branch=2&week=2026-11-16',base_url=BASE)
    assert deli.status_code==200
    assert '¥4,960' in deli.text and '+¥480.00' in deli.text and 'Đêm 1.50h' in deli.text
    tsukiji=client.get('/?branch=1&week=2026-11-16',base_url=BASE)
    assert tsukiji.status_code==200 and '¥4,480' in tsukiji.text
    assert 'Đêm 1.50h' not in tsukiji.text

"""Validate local clock times, allowing one midnight crossing when configured."""
from datetime import datetime


def minute(value):
    if value=='24:00':
        return 1440
    parsed=datetime.strptime(value,'%H:%M')
    if parsed.strftime('%H:%M')!=value:
        raise ValueError('Invalid clock time')
    return parsed.hour*60+parsed.minute


def shift_bounds(start,end,overnight=False):
    left,right=minute(start),minute(end)
    if left==1440:
        raise ValueError('Start must be before 24:00')
    if overnight and right<left:
        right+=1440
    if right<=left:
        raise ValueError('End must follow start')
    return left,right


def break_bounds(start,end,left,right,overnight=False):
    begin,finish=minute(start),minute(end)
    if overnight and begin<left:
        begin+=1440
    if overnight and finish<left:
        finish+=1440
    if overnight and finish<begin:
        finish+=1440
    if not left<=begin<finish<=right:
        raise ValueError('Break must be inside shift')
    return begin,finish


def night_minutes(row):
    """Net minutes in 22:00–05:00, including shifts crossing midnight."""
    if not row or not row.get('start') or not row.get('end'):
        return 0
    left,right=shift_bounds(row['start'],row['end'],overnight=True)
    rests=[]
    for a,b in [('break_start','break_end'),('break2_start','break2_end')]:
        if row.get(a) and row.get(b):
            rests.append(break_bounds(row[a],row[b],left,right,overnight=True))
    # Merge rests so legacy overlapping records cannot subtract twice.
    merged=[]
    for begin,finish in sorted(rests):
        if merged and begin<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(merged[-1][1],finish))
        else:
            merged.append((begin,finish))
    total=0
    for begin,finish in [(0,300),(1320,1740),(2760,3180)]:
        a,b=max(left,begin),min(right,finish)
        if b>a:
            total+=b-a
            total-=sum(max(0,min(b,d)-max(a,c)) for c,d in merged)
    return total

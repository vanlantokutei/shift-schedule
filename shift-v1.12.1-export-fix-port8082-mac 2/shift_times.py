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

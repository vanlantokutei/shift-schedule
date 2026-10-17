"""Published Japanese holidays, including substitute and citizens' holidays.

Source: https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv
Snapshot: 2026-10-17. Replace the UTF-8 CSV when new years are published.
No network requests are made while rendering the schedule.
"""
import csv
from datetime import date
from pathlib import Path

with Path(__file__).with_suffix('.csv').open(encoding='utf-8', newline='') as source:
    reader = csv.reader(source)
    next(reader)
    HOLIDAYS = {
        date(*(int(part) for part in day.split('/'))): name
        for day, name in reader if day
    }
PUBLISHED_YEARS = {day.year for day in HOLIDAYS}


def holiday_context(days):
    return {
        'holidays': HOLIDAYS,
        'holiday_data_available': all(day.year in PUBLISHED_YEARS for day in days),
    }

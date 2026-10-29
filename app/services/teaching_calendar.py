"""Week one contains term_start; subsequent teaching weeks begin on Monday."""
from datetime import date, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TeachingCalendar(BaseModel):
    model_config = ConfigDict(extra='forbid')
    term_name: str | None = Field(None, max_length=100)
    term_start: date | None = None
    term_end: date | None = None
    start_week: int | None = Field(None, ge=1, le=60)
    end_week: int | None = Field(None, ge=1, le=60)
    week_pattern: Literal['all', 'odd', 'even'] = 'all'

    @model_validator(mode='after')
    def valid_calendar(self):
        if (self.term_start is None) != (self.term_end is None):
            raise ValueError('学期起止日期须同时提供')
        if self.term_start and self.term_start > self.term_end:
            raise ValueError('学期日期颠倒')
        if (self.start_week or self.end_week or self.week_pattern != 'all') and not self.term_start:
            raise ValueError('教学周规则需要学期起止日期')
        if self.start_week and self.end_week and self.start_week > self.end_week:
            raise ValueError('教学周区间颠倒')
        return self


def calendar_match(row, on_date):
    if not on_date or not row['term_start']:
        return True, None
    anchor = row['term_start'] - timedelta(days=row['term_start'].weekday())
    week = (on_date - anchor).days // 7 + 1
    matches = (row['term_start'] <= on_date <= row['term_end']
               and (row['start_week'] is None or week >= row['start_week'])
               and (row['end_week'] is None or week <= row['end_week'])
               and (row['week_pattern'] == 'all' or week % 2 == (1 if row['week_pattern'] == 'odd' else 0)))
    return matches, week

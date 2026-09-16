"""Shared exact-money and file utilities. No labels are loaded by the solver."""
from __future__ import annotations
import calendar
import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ZERO = Decimal('0')
CENT = Decimal('0.01')
OUTPUT_COLUMNS = ['request_id', 'amount_safe_to_pay', 'affordability_status',
                  'recommended_payment_method', 'payment_plan',
                  'earliest_date_for_full_payment', 'spending_changes_needed',
                  'decision_explanation']
INPUT_COLUMNS = ['request_id', 'user_id', 'request_date', 'request_type',
                 'requested_amount', 'desired_completion_date',
                 'allows_partial_payment', 'request_text']

def money(value):
    if value is None or str(value).strip() == '':
        raise ValueError('Missing monetary amount; evidence extraction is required')
    number = Decimal(str(value).replace(',', '').strip())
    if not number.is_finite():
        raise ValueError('Non-finite monetary amount')
    return number

def rounded(value):
    return money(value).quantize(CENT, rounding=ROUND_HALF_UP)

def fmt(value):
    return format(rounded(value), '.2f')

def day(value):
    return date.fromisoformat(value)

def add_months(value, months, anchor=None):
    y, m = divmod(value.year * 12 + value.month - 1 + months, 12)
    return date(y, m + 1, min(anchor or value.day, calendar.monthrange(y, m + 1)[1]))

def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='raise')
        writer.writeheader()
        writer.writerows(rows)

def pipe(value):
    return set(filter(None, value.split('|')))

def quantile(values, q):
    """Linear interpolation, implemented with Decimal for reproducibility."""
    v = sorted(values)
    if not v:
        raise ValueError('No observations for forecast')
    index = Decimal(str(q)) * (len(v)-1)
    lower = int(index)
    return v[lower] + (v[min(lower+1, len(v)-1)]-v[lower]) * (index-lower)

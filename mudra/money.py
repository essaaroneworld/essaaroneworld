"""Money helpers.

All monetary values are stored as integer paise so that double-entry totals
always balance exactly. The API speaks rupees (decimal numbers).
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .errors import ValidationError


def to_paise(value):
    if value is None or value == "":
        return 0
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValidationError(f"Invalid amount: {value!r}")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_rupees(paise):
    if paise is None:
        return 0.0
    return round(paise / 100, 2)


def round_half_up(value):
    """Round a Decimal/float to the nearest integer (paise), half away from zero."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def to_qty(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(Decimal(str(value)))
    except InvalidOperation:
        raise ValidationError(f"Invalid quantity: {value!r}")

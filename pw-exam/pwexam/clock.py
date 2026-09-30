"""Single source of time so tests can move the clock."""
import time

_offset = 0


def now():
    return int(time.time()) + _offset


def advance(seconds):
    """Test helper: move the clock forward."""
    global _offset
    _offset += int(seconds)


def reset():
    global _offset
    _offset = 0


def fmt(ts):
    """Human date-time in the exam centre's timezone (PWEXAM_TIMEZONE, default Asia/Kolkata)."""
    import datetime as dt
    import os
    tzname = os.environ.get("PWEXAM_TIMEZONE", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname)
    except Exception:  # no tz database on this host: fall back to IST
        tz, tzname = dt.timezone(dt.timedelta(hours=5, minutes=30)), "IST"
    d = dt.datetime.fromtimestamp(int(ts), tz)
    return d.strftime("%d %b %Y, %I:%M %p") + ("" if tzname == "IST" else f" ({d.tzname()})")

from datetime import date


def month_window(as_of: date, months: int) -> tuple[date, date]:
    start = as_of.replace(day=1)
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    return start, date(year, month + 1, 1)

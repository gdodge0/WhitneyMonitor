import calendar
import re
from datetime import date

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_BITMASK_RE = re.compile(r"[01]{7}$")


def validate_ranges(date_ranges):
    """
    Validate a list of [start, end, bitmask] items.

    Rules
    -----
    • Each item is an iterable of exactly three strings.
    • Dates follow yyyy-mm-dd and parse with datetime.date.
    • start_date ≤ end_date
    • Bitmask is exactly 7 chars of '0' or '1'.

    Raises
    ------
    ValueError  – with a message describing the first problem encountered.
    """
    if not isinstance(date_ranges, (list, tuple)):
        raise ValueError("The top‑level value must be a list/tuple of ranges")

    for idx, triple in enumerate(date_ranges):
        # Structural check
        if not (isinstance(triple, (list, tuple)) and len(triple) == 3):
            raise ValueError(f"Range #{idx} should be [start, end, bitmask]")

        start_s, end_s, mask = triple

        # Date format / parse-ability
        for label, s in (("start", start_s), ("end", end_s)):
            if not _DATE_RE.fullmatch(s):
                raise ValueError(f"Range #{idx} – {label} date '{s}' is not yyyy-mm-dd")
        try:
            start = date.fromisoformat(start_s)
            end = date.fromisoformat(end_s)
        except ValueError as e:
            raise ValueError(f"Range #{idx} – invalid calendar date: {e}")

        if start > end:
            raise ValueError(f"Range #{idx} – start date {start_s} is after end date {end_s}")

        # Bitmask
        if not _BITMASK_RE.fullmatch(mask):
            raise ValueError(f"Range #{idx} – bitmask '{mask}' must be exactly 7 chars of 0/1")

    # If we get here everything passed
    return None


def complete_months(date_ranges):
    """
    For every [start_date, end_date, bitmask] triple, collect every *full* month
    touched by the range.  Return a deduplicated, chronologically‑sorted list
    where each item is [month_start, month_end] in yyyy‑mm‑dd format.

    Parameters
    ----------
    date_ranges : list[list[str]]
        e.g. [["2025-06-14", "2025-08-03", "1111111"], …]

    Returns
    -------
    list[list[str, str]]
        e.g. [["2025-06-01", "2025-06-30"],
              ["2025-07-01", "2025-07-31"],
              ["2025-08-01", "2025-08-31"]]
    """
    months = set()

    for start_s, end_s, _ in date_ranges:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)

        y, m = start.year, start.month
        while True:
            m_start = date(y, m, 1)
            m_end = date(y, m, calendar.monthrange(y, m)[1])

            if m_end >= start and m_start <= end:
                months.add((m_start, m_end))

            # move to the 1st of the next month
            if (y, m) >= (end.year, end.month):
                break
            m = m + 1 if m < 12 else 1
            y = y if m != 1 else y + 1

    # dedupe & sort, then stringify
    return [[s.isoformat(), e.isoformat()] for s, e in sorted(months)]


def date_matches(day_s, date_ranges):
    """
    Check whether a single day falls within **any** range *and*
    the range’s enabled‑day bitmask.

    Parameters
    ----------
    day_s : str
        Date string "yyyy-mm-dd".
    date_ranges : list[list[str]]
        Same structure as above.  Bitmask is 7 chars, Monday = index.

    Returns
    -------
    bool
    """
    d = date.fromisoformat(day_s)
    # Python: Monday=0 … Sunday=6  ⇒  Sunday should map to bit 0
    bit_idx = d.weekday()

    for start_s, end_s, mask in date_ranges:
        if mask and mask[bit_idx] == "1":
            if date.fromisoformat(start_s) <= d <= date.fromisoformat(end_s):
                return True
    return False

# Example:
#
# ranges = [["2025-06-14", "2025-08-03", "1111111"], ["2025-08-04", "2025-08-15", "0111111"]]
#
# print(complete_months(ranges))
# -> [['2025-06-01', '2025-06-30'],
#     ['2025-07-01', '2025-07-31'],
#     ['2025-08-01', '2025-08-31']]
#
# print(date_matches("2025-06-15", ranges))  # True  (within range, bit=1)
# -> True
# print(date_matches("2025-08-04", ranges))  # False (outside range)
# -> False

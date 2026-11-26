"""Bill identity and calendar helpers (no HA dependencies)."""
from datetime import date
from .tiers import number


def bill_month(value):
    try:
        value = str(value)
        if len(value) != 7:
            return None
        parsed = date.fromisoformat(value + "-01")
        return parsed.strftime("%Y-%m")
    except (ValueError, TypeError):
        return None


def read_date(value):
    try:
        return date.fromisoformat(str(value).strip().replace("/", "-")).isoformat()
    except (ValueError, TypeError):
        return "未知"


def identifier(value):
    return str(value).strip() if value is not None else ""


def valid_bill(row):
    return (isinstance(row, dict) and bill_month(row.get("billYm"))
            and (identifier(row.get("id")) or identifier(row.get("applicationNo")))
            and number(row.get("gasAmt")) is not None
            and number(row.get("billAmt")) is not None)


def same_bill(old, new):
    old_id, new_id = identifier(old.get("id")), identifier(new.get("id"))
    if old_id and new_id:
        return old_id == new_id
    old_app, new_app = identifier(old.get("applicationNo")), identifier(new.get("applicationNo"))
    return bool(old_app and new_app and old_app == new_app
                and bill_month(old.get("billYm")) == bill_month(new.get("billYm")))


def merge_bill(records, row, timestamp):
    """Upgrade legacy app-number records in place; never merge distinct official IDs."""
    if not valid_bill(row):
        return "invalid"
    matches = [i for i, old in enumerate(records) if same_bill(old, row)]
    merged = {}
    for i in matches:
        merged.update(records[i])
    known_id = identifier(merged.get("id"))
    merged.update(row)
    merged["id"] = identifier(row.get("id")) or known_id
    # An empty application number is legitimate; retain a known alias for deduplication.
    merged["applicationNo"] = (identifier(row.get("applicationNo"))
                               or next((identifier(records[i].get("applicationNo"))
                                        for i in matches if identifier(records[i].get("applicationNo"))), ""))
    for key in ("gasAmt", "billAmt", "penaltyAmt", "revblAmt"):
        merged[key] = number(merged.get(key))
    merged["timestamp"] = timestamp
    if matches:
        records[matches[0]] = merged
        for i in reversed(matches[1:]):
            del records[i]
        return "updated"
    records.append(merged)
    return "new"


def previous_month(ym):
    ym = bill_month(ym)
    if not ym:
        return None
    year, month = map(int, ym.split("-"))
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def previous_usage(rows, ym, chart=None, current_year=None):
    target = previous_month(ym)
    if not target:
        return None
    matching = [r for r in rows if isinstance(r, dict) and r.get("billYm") == target]
    if matching:
        values = [number(r.get("gasAmt")) for r in matching]
        if all(v is not None for v in values):
            return sum(values)
    if isinstance(chart, dict) and current_year is not None:
        year, month = map(int, target.split("-"))
        key = "thisGas" if year == current_year else "lastGas" if year == current_year - 1 else None
        values = chart.get(key) if key else None
        if isinstance(values, list) and len(values) >= month:
            return number(values[month - 1])
    return None


def monthly_totals(records):
    months = {}
    for row in records:
        if not valid_bill(row):
            continue
        ym = row["billYm"]
        gas, cost = months.get(ym, (0, 0))
        months[ym] = (round(gas + number(row["gasAmt"]), 6),
                      round(cost + number(row["billAmt"]), 2))
    return months

def select_detail(details, expected_id):
    """Prefer the matching official bill; never substitute another bill's detail."""
    if not isinstance(details, list):
        return None
    rows = [row for row in details if isinstance(row, dict)]
    expected_id = identifier(expected_id)
    if expected_id:
        for row in rows:
            if identifier(row.get("id")) == expected_id:
                return row
    if len(rows) == 1 and (not expected_id or not identifier(rows[0].get("id"))):
        return rows[0]
    return None


def detail_complete(detail):
    return (isinstance(detail, dict)
            and all(number(detail.get(key)) is not None
                    for key in ("thisRead", "lastRead", "gasUsed"))
            and all(read_date(detail.get(key)) != "未知"
                    for key in ("thisReadTime", "lastReadTime")))

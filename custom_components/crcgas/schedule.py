"""Refresh schedule: calendar rules, legacy compatibility and timer lifecycle."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
MODES = ("hour", "day", "week", "month")
SCHEDULE_KEY = "refresh_schedule"
DEFAULT_TIME = "07:07:00"


@dataclass(frozen=True)
class Schedule:
    mode: str
    hours: int = 6
    weekday: int = 1
    day: int = 1
    at: str = DEFAULT_TIME
    legacy_seconds: int = 0


def _integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("invalid integer")
    return value


def parse_schedule(value):
    """Validate v2.1.5 data without guessing missing fields."""
    if not isinstance(value, dict) or value.get("mode") not in MODES:
        raise ValueError("invalid refresh mode")
    mode = value["mode"]
    if mode == "hour":
        return Schedule(mode, hours=_integer(value.get("hours"), 1, 24))
    at = time.fromisoformat(value.get("at", ""))
    if at.tzinfo is not None or at.second or at.microsecond:
        raise ValueError("time must be local HH:MM")
    return Schedule(mode, at=at.isoformat(),
                    weekday=_integer(value.get("weekday"), 1, 7) if mode == "week" else 1,
                    day=_integer(value.get("day"), 1, 31) if mode == "month" else 1)


def serialize(spec):
    data = {"mode": spec.mode}
    if spec.mode == "hour":
        data["hours"] = spec.hours
    else:
        data["at"] = spec.at
        if spec.mode == "week":
            data["weekday"] = spec.weekday
        if spec.mode == "month":
            data["day"] = spec.day
    return data


def effective_schedule(data, options):
    # Options override initial data. Legacy choices retain their actual old
    # interval until the user explicitly saves a new schedule.
    if SCHEDULE_KEY in options:
        return parse_schedule(options[SCHEDULE_KEY])
    if SCHEDULE_KEY in data:
        return parse_schedule(data[SCHEDULE_KEY])
    mode = options.get("scan_interval_unit", data.get("scan_interval_unit", "hour"))
    value = int(options.get("scan_interval", data.get("scan_interval", 1)))
    if mode == "hour":
        return Schedule("hour", hours=max(1, value))
    seconds = {"day": 86400, "week": 604800, "month": 2592000}.get(mode, 3600) * max(1, value)
    return Schedule(mode if mode in MODES else "hour", legacy_seconds=seconds)


def form_defaults(data, options):
    spec = effective_schedule(data, options)
    if not spec.legacy_seconds:
        return spec
    # Suggest original advertised calendar meaning, only applied on save.
    value = int(options.get("scan_interval", data.get("scan_interval", 1)))
    if spec.mode == "day":
        return Schedule("day", at=f"{min(23, max(0, value)):02d}:00:00")
    if spec.mode == "week":
        return Schedule("week", weekday=min(7, max(1, value)))
    if spec.mode == "month":
        return Schedule("month", day=min(31, max(1, value)))
    return Schedule("hour")


def _local_instant(day, at, zone):
    """First valid wall-clock occurrence; advance through a DST gap."""
    naive = datetime.combine(day, time.fromisoformat(at))
    for minutes in range(24 * 60):
        wall = naive + timedelta(minutes=minutes)
        if wall.date() != day:
            return None
        valid = []
        for fold in (0, 1):
            utc = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == wall:
                valid.append(utc)
        if valid:
            return min(valid)
    return None


def next_run(spec, now, zone, previous_due=None):
    """Return a strictly future UTC instant; manual refresh never changes it."""
    if now.tzinfo is None:
        raise ValueError("timezone-aware now required")
    now = now.astimezone(UTC)
    if spec.legacy_seconds or spec.mode == "hour":
        interval = timedelta(seconds=spec.legacy_seconds or spec.hours * 3600)
        if previous_due is None:
            return now + interval
        due = previous_due.astimezone(UTC)
        return due + interval * max(1, (now - due) // interval + 1)
    if isinstance(zone, str):
        zone = ZoneInfo(zone)
    today = now.astimezone(zone).date()
    for offset in range(370):
        day = today + timedelta(days=offset)
        if spec.mode == "week" and day.isoweekday() != spec.weekday:
            continue
        if spec.mode == "month" and day.day != min(spec.day, monthrange(day.year, day.month)[1]):
            continue
        candidate = _local_instant(day, spec.at, zone)
        if candidate is not None and candidate > now:
            return candidate
    raise ValueError("no next refresh date")


class RefreshTimer:
    """One cancellable timer. Scheduling is independent from data refreshes."""
    def __init__(self, spec, clock, zone, arm, refresh):
        self.spec = spec
        self.clock = clock
        self.zone = zone
        self.arm = arm
        self.refresh = refresh
        self.cancel = None
        self.running = False
        self.generation = 0
        self.due = None

    def start(self):
        if not self.running:
            self.running = True
            self._schedule()

    def stop(self):
        self.running = False
        self.generation += 1
        if self.cancel:
            self.cancel()
        self.cancel = None
        self.due = None

    def configure(self, spec):
        if spec == self.spec:
            return
        was_running = self.running
        self.stop()
        self.spec = spec
        if was_running:
            self.start()

    def _schedule(self, previous_due=None):
        self.due = next_run(self.spec, self.clock(), self.zone(), previous_due)
        due = self.due
        generation = self.generation

        async def fired(_now):
            if not self.running or generation != self.generation or self.due != due:
                return
            self.cancel = None
            # Arm next calendar slot independently of failures or refresh latency.
            self._schedule(due)
            await self.refresh()

        self.cancel = self.arm(due, fired)

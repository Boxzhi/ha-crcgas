import importlib.util
from pathlib import Path
import unittest
import sys
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("crcgas_schedule", Path(__file__).resolve().parents[1] / "custom_components/crcgas/schedule.py")
schedule = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = schedule
spec.loader.exec_module(schedule)
S = schedule.Schedule
UTC = timezone.utc


def dt(value):
    return datetime.fromisoformat(value).astimezone(UTC)


class CalendarTests(unittest.TestCase):
    def test_hour_anchors(self):
        now = dt("2026-11-20T07:00:00+08:00")
        due = schedule.next_run(S("hour", hours=6), now, "Asia/Shanghai")
        self.assertEqual(due, dt("2026-11-20T13:00:00+08:00"))
        self.assertEqual(schedule.next_run(S("hour", hours=6), dt("2026-11-20T20:00:00+08:00"), "Asia/Shanghai", due),
                         dt("2026-11-21T01:00:00+08:00"))

    def test_day_week_and_month(self):
        now = dt("2026-11-20T07:08:00+08:00")
        self.assertEqual(schedule.next_run(S("day"), now, "Asia/Shanghai"), dt("2026-11-21T07:07:00+08:00"))
        self.assertEqual(schedule.next_run(S("week", weekday=1), now, "Asia/Shanghai"), dt("2026-11-23T07:07:00+08:00"))
        self.assertEqual(schedule.next_run(S("month", day=1), now, "Asia/Shanghai"), dt("2026-12-01T07:07:00+08:00"))

    def test_month_end_and_leap_year(self):
        for year, day in [(2027, 28), (2028, 29)]:
            now = dt(f"{year}-02-01T00:00:00+08:00")
            self.assertEqual(schedule.next_run(S("month", day=31), now, "Asia/Shanghai"), dt(f"{year}-02-{day}T07:07:00+08:00"))

    def test_year_boundary_and_exact_slot(self):
        now = dt("2026-12-31T07:07:00+08:00")
        self.assertEqual(schedule.next_run(S("month", day=31), now, "Asia/Shanghai"), dt("2027-01-31T07:07:00+08:00"))

    def test_dst_gap_and_fold(self):
        self.assertEqual(schedule.next_run(S("day", at="02:30:00"), dt("2026-03-08T00:00:00-05:00"), "America/New_York"), dt("2026-03-08T03:00:00-04:00"))
        self.assertEqual(schedule.next_run(S("day", at="01:30:00"), dt("2026-11-01T00:00:00-04:00"), "America/New_York"), dt("2026-11-01T01:30:00-04:00"))
        self.assertEqual(schedule.next_run(S("day", at="01:30:00"), dt("2026-11-01T01:35:00-04:00"), "America/New_York"), dt("2026-11-02T01:30:00-05:00"))

    def test_legacy_and_options_precedence(self):
        for mode, seconds in [("day", 6*86400), ("week", 6*604800), ("month", 6*2592000)]:
            spec = schedule.effective_schedule({"scan_interval": 6, "scan_interval_unit":mode}, {})
            self.assertEqual(spec.legacy_seconds, seconds)
            self.assertEqual(schedule.effective_schedule({}, {"refresh_schedule":{"mode":"hour","hours":4}}).hours, 4)
        self.assertEqual(schedule.form_defaults({"scan_interval": 6, "scan_interval_unit":"day"},{}).at,"06:00:00")

    def test_validation(self):
        for value in [{"mode":"hour","hours":0},{"mode":"hour","hours":25},{"mode":"day","at":"24:00"},{"mode":"day","at":"07:07:01"},{"mode":"week","weekday":8,"at":"07:07"},{"mode":"month","day":32,"at":"07:07"}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                schedule.parse_schedule(value)
        self.assertEqual(schedule.serialize(schedule.parse_schedule({"mode":"month","day":31,"at":"07:07"})),{"mode":"month","day":31,"at":"07:07:00"})


class TimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconfigure_manual_and_cancel(self):
        clock = [dt("2026-11-20T00:00:00+08:00")]
        arms, cancelled, refreshes = [], [], []
        def arm(due, action):
            arms.append((due, action))
            return lambda: cancelled.append(due)
        async def refresh():
            refreshes.append(clock[0])
        timer = schedule.RefreshTimer(S("hour", hours=6), lambda:clock[0], lambda:"Asia/Shanghai", arm, refresh)
        timer.start()
        first_due, first_callback = arms[-1]
        await refresh()  # user-triggered refresh does not change timer
        self.assertEqual(timer.due, first_due)
        timer.configure(S("hour", hours=6))
        self.assertEqual(len(arms), 1)
        timer.configure(S("day"))
        self.assertEqual(cancelled, [first_due])
        await first_callback(clock[0])  # cancelled callback must be ignored
        self.assertEqual(len(refreshes), 1)
        clock[0], cb = arms[-1]
        await cb(clock[0])
        self.assertEqual(len(refreshes), 2)
        self.assertEqual(timer.due, dt("2026-11-21T07:07:00+08:00"))
        _, stale = arms[-1]
        timer.stop()
        await stale(clock[0])
        self.assertEqual(len(refreshes), 2)
        self.assertIsNone(timer.due)

    async def test_refresh_failure_does_not_cancel_future_schedule(self):
        now = [dt("2026-11-20T00:00:00+08:00")]
        arms = []
        def arm(due, action):
            arms.append((due, action))
            return lambda:None
        async def fail():
            raise RuntimeError()
        timer = schedule.RefreshTimer(S("day"), lambda:now[0], lambda:"Asia/Shanghai", arm, fail)
        timer.start()
        now[0], cb = arms[-1]
        with self.assertRaises(RuntimeError):
            await cb(now[0])
        self.assertEqual(timer.due, dt("2026-11-21T07:07:00+08:00"))

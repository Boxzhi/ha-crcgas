"""Per-entry payment snapshot, independent of bill history and Recorder."""
import hashlib
import logging
from datetime import datetime

from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .payments import CHINA_TZ, fetch_payments, local_year, normalize_payment, summarize, summarize_year

_LOGGER = logging.getLogger(__name__)
PAYMENT_KEYS = (
    "last_pay_time", "last_pay_amount", "last_pay_channel", "annual_pay_count",
    "annual_gas_pay_amount", "annual_business_pay_amount",
    "payment_history_status", "payment_history_updated_at",
)


class PaymentHistory:
    def __init__(self, hass, entry_id, cons_no):
        self._store = Store(hass, 1, f"{DOMAIN}_payments_{entry_id}")
        self._account = hashlib.sha256(str(cons_no).encode()).hexdigest()
        self._snapshot = None
        self._status = "unavailable"

    async def async_load(self):
        try:
            saved = await self._store.async_load()
            if not isinstance(saved, dict) or saved.get("account") != self._account:
                return
            year = saved.get("year")
            updated_at = saved.get("updated_at")
            records = saved.get("records")
            if not isinstance(year, int) or not isinstance(records, list) or not isinstance(updated_at, str):
                return
            updated = datetime.fromisoformat(updated_at)
            if updated.tzinfo is None or updated.astimezone(CHINA_TZ).year != year:
                return
            rows = [normalize_payment(row) for row in records]
            self._snapshot = {**saved, "records": rows}
            self._status = "stale"
        except Exception:
            _LOGGER.warning("缴费历史缓存读取失败（将重新采集）")

    async def async_refresh(self, api, cons_no):
        """Replace cache atomically only once all pages succeed."""
        year = local_year()
        try:
            records = await fetch_payments(api, cons_no, year)
            # Refuse a cross-year result; next refresh will read the new window.
            now = datetime.now(CHINA_TZ)
            if now.year != year:
                raise ValueError("payment query crossed year boundary")
            snapshot = {
                "account": self._account,
                "year": year,
                "updated_at": now.isoformat(),
                "records": records,
            }
            await self._store.async_save(snapshot)
        except Exception:
            self._status = "stale" if self._snapshot is not None else "unavailable"
            raise
        self._snapshot = snapshot
        self._status = "fresh"

    def _records(self):
        year = local_year()
        if self._snapshot is None:
            return []
        return [row for row in self._snapshot["records"]
                if year - 2 <= int(row["payTime"][:4]) <= year]

    def sensor_data(self):
        data = dict.fromkeys(PAYMENT_KEYS)
        data["payment_history_status"] = self._status
        if self._snapshot is not None:
            year = local_year()
            data.update(summarize(self._records(), year))
            if self._snapshot["year"] != year:
                # Old year's successful snapshot cannot prove zero payments in the new year.
                for key in ("annual_pay_count", "annual_gas_pay_amount", "annual_business_pay_amount"):
                    data[key] = None
                data["payment_history_status"] = "stale"
            data["payment_history_updated_at"] = self._snapshot["updated_at"]
        return data

    def query(self, year=None, category="all", offset=0, limit=100):
        current = local_year()
        records = self._records()
        covered = [] if self._snapshot is None else [
            y for y in range(current, current - 3, -1)
            if self._snapshot["year"] - 2 <= y <= self._snapshot["year"]
        ]
        selected = [r for r in records if (year is None or r["payTime"].startswith(str(year) + "-"))
                    and (category == "all" or r["category"] == category)]
        return {
            "status": self.sensor_data()["payment_history_status"],
            "updated_at": self.sensor_data()["payment_history_updated_at"],
            "available_years": covered,
            "summaries": [summarize_year(records, y) for y in covered],
            "total": len(selected) if self._snapshot is not None and (year is None or year in covered) else None,
            "offset": offset,
            "records": selected[offset:offset + limit],
            "has_more": offset + limit < len(selected),
        }

    async def async_remove(self):
        await self._store.async_remove()

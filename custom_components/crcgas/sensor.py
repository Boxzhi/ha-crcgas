"""华润燃气 传感器平台 - v1.2.7+

基于v1.2.7 | 新增: 三档用气量/气价, 上期读数/时间, 结算状态, 燃气表总读数(能源面板),
保持上次有效值, 历史数据注入(HA统计+SQLite), 燃气累计用量传感器
"""

import asyncio
import json
import logging
from math import isfinite
import sqlite3
import time as time_mod
from datetime import timedelta, datetime, timezone
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.util import dt as dt_util
from homeassistant.helpers import entity_registry as er

try:
    from homeassistant.components.sensor import SensorEntity as BaseSensor
except ImportError:
    from homeassistant.helpers.entity import Entity as BaseSensor

from homeassistant.components.sensor.const import SensorStateClass as _SSC

from .api import HuarunGasApi, SessionTimeoutError
from .tiers import TIER_KEYS, parse_tiers, parse_bill_steps, number
from .payment_storage import PAYMENT_KEYS, PaymentHistory
from .schedule import RefreshTimer, effective_schedule
from .const import (
    CONF_BO_TOKEN,
    CONF_CONS_NO,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL_UNIT,
    CONF_WX_CODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    INTEGRATION_STATUS,
    SENSOR_TYPES,
    TOKEN_EXPIRE_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)


def _numeric_value(value):
    """Preserve signed values and genuine zero; missing/invalid is unknown."""
    if value is None or isinstance(value, bool):
        return None
    try:
        val = float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None
    return val if isfinite(val) else None


def _first_number(*values):
    for value in values:
        val = _numeric_value(value)
        if val is not None:
            return val
    return None


_STATE_CLASS_MAP = {
    "this_read": _SSC.TOTAL_INCREASING,
    "total_gas_consumption": _SSC.TOTAL_INCREASING,
    "total_gas_cost": _SSC.TOTAL_INCREASING,
    "monthly_gas_used": _SSC.TOTAL_INCREASING,
    "step1_gas_used": _SSC.TOTAL_INCREASING,
    "step2_gas_used": _SSC.TOTAL_INCREASING,
    "step3_gas_used": _SSC.TOTAL_INCREASING,
    "annual_pay_count": _SSC.TOTAL,
    "annual_gas_pay_amount": _SSC.TOTAL,
    "annual_business_pay_amount": _SSC.TOTAL,
    "gas_energy_dashboard": _SSC.TOTAL_INCREASING,
    "account_balance": _SSC.MEASUREMENT,
    "arrears": _SSC.MEASUREMENT,
    "bill_amount": _SSC.MEASUREMENT,
    "estimated_gas_bill_amount": _SSC.MEASUREMENT,
    "gas_price_step1": _SSC.MEASUREMENT,
    "gas_price_step2": _SSC.MEASUREMENT,
    "gas_price_step3": _SSC.MEASUREMENT,
    "last_month_gas": _SSC.TOTAL,
    "last_pay_amount": _SSC.MEASUREMENT,
    "penalties": _SSC.MEASUREMENT,
    "penalty_amount": _SSC.MEASUREMENT,
    "revbl_amount": _SSC.MEASUREMENT,
    "step1_gas_limit": _SSC.MEASUREMENT,
    "step1_gas_sum": _SSC.TOTAL,
    "step1_remain": _SSC.MEASUREMENT,
    "step2_gas_limit": _SSC.MEASUREMENT,
    "step2_gas_sum": _SSC.TOTAL,
    "step2_remain": _SSC.MEASUREMENT,
    "step3_gas_sum": _SSC.TOTAL,
    "step3_remain": _SSC.MEASUREMENT,
    "this_gas_used": _SSC.TOTAL,
    "year_avg_gas": _SSC.TOTAL,
    "annual_gas_used": _SSC.TOTAL,

}

_DEVICE_CLASS_MAP = {
    "this_read": "gas",
    "total_gas_consumption": "gas",
    "total_gas_cost": "monetary",
    "monthly_gas_used": "gas",
    "step1_gas_used": "gas",
    "step2_gas_used": "gas",
    "step3_gas_used": "gas",
    "gas_energy_dashboard": "gas",
}


class HuarunGasSensor(BaseSensor):
    """华润燃气传感器基类"""

    def __init__(self, coordinator: DataUpdateCoordinator, sensor_type: str):
        self.coordinator = coordinator
        self.sensor_type = sensor_type
        self._attr_unique_id = f"{DOMAIN}_{sensor_type}"
        self._attr_name = SENSOR_TYPES[sensor_type]["name"]
        self._attr_icon = SENSOR_TYPES[sensor_type].get("icon")
        self._attr_native_unit_of_measurement = SENSOR_TYPES[sensor_type].get("unit")
        self._attr_state_class = _STATE_CLASS_MAP.get(sensor_type)
        self._attr_device_class = _DEVICE_CLASS_MAP.get(sensor_type)
        self._last_good_value = None

    @property
    def device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)},
            "name": "华润燃气",
            "manufacturer": "华润燃气",
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        """添加到 Home Assistant"""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        if self.coordinator.data is not None:
            self.async_write_ha_state()

    @property
    def native_value(self):
        """获取传感器值"""
        data = self.coordinator.data
        if not data:
            if self.sensor_type in ("total_gas_consumption", "total_gas_cost"):
                return self._last_good_value
            return None

        if self.sensor_type in PAYMENT_KEYS:
            return data.get(self.sensor_type)
        if self.sensor_type == "tier_data_status":
            return data.get("tier_data_status", "unavailable")
        if self.sensor_type in TIER_KEYS or self.sensor_type in (
            "step1_gas_used", "step2_gas_used", "step3_gas_used",
            "annual_gas_used", "estimated_gas_bill_amount",
        ):
            return number(data.get(self.sensor_type))

        if self.sensor_type == "arrears":
            v = data.get("arrears")
            return _numeric_value(v)

        elif self.sensor_type == "account_balance":
            v = data.get("account_balance")
            return _numeric_value(v)

        elif self.sensor_type == "last_pay_time":
            v = data.get("last_pay_time")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "last_pay_amount":
            v = data.get("last_pay_amount")
            return _numeric_value(v)

        elif self.sensor_type == "annual_pay_count":
            v = data.get("annual_pay_count")
            return _numeric_value(v)

        elif self.sensor_type == "this_read":
            v = data.get("this_read")
            val = number(v)
            if val is not None:
                self._last_good_value = val
                return val
            return self._last_good_value

        elif self.sensor_type == "total_gas_consumption":
            v = data.get("this_read")
            val = number(v)
            if val is not None:
                self._last_good_value = val
                return val
            return self._last_good_value

        elif self.sensor_type == "total_gas_cost":
            v = data.get("total_gas_cost")
            val = number(v)
            if val is not None:
                self._last_good_value = val
                return val
            return self._last_good_value

        elif self.sensor_type == "this_read_time":
            v = data.get("this_read_time")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "this_gas_used":
            v = data.get("this_gas_used")
            return _numeric_value(v)

        elif self.sensor_type == "bill_amount":
            v = data.get("bill_amount")
            return _numeric_value(v)

        elif self.sensor_type == "penalty_amount":
            v = data.get("penalty_amount")
            return _numeric_value(v)

        elif self.sensor_type == "last_read":
            v = data.get("last_read")
            return _numeric_value(v)

        elif self.sensor_type == "last_read_time":
            v = data.get("last_read_time")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "settle_flag":
            v = data.get("settle_flag")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "cons_addr":
            v = data.get("cons_addr")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "org_name":
            v = data.get("org_name")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "gas_nature":
            v = data.get("gas_nature")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "purchase_style":
            v = data.get("purchase_style")
            style_map = {"01": "IC卡", "02": "物联网表", "03": "普通表"}
            return style_map.get(v, v) if v else "未知"

        elif self.sensor_type == "last_month_gas":
            v = data.get("last_month_gas")
            return _numeric_value(v)

        elif self.sensor_type == "year_avg_gas":
            v = data.get("year_avg_gas")
            return _numeric_value(v)

        elif self.sensor_type == "integration_status":
            v = data.get("integration_status")
            return INTEGRATION_STATUS.get(v, v) if v else "未知"

        elif self.sensor_type == "monthly_gas_used":
            v = data.get("monthly_gas_used")
            return _numeric_value(v)

        elif self.sensor_type == "gas_energy_dashboard":
            v = data.get("this_read")
            val = number(v)
            if val is not None:
                self._last_good_value = val
                return val
            return self._last_good_value

        elif self.sensor_type == "cons_name":
            v = data.get("cons_name")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "mobile":
            v = data.get("mobile")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "meter_no":
            v = data.get("meter_no")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "area_name":
            v = data.get("area_name")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "account_status":
            v = data.get("account_status")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "revbl_amount":
            v = data.get("revbl_amount")
            return _numeric_value(v)

        elif self.sensor_type == "penalty_date":
            v = data.get("penalty_date")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "last_fetch_time":
            v = data.get("last_fetch_time")
            return v if v and v != "未知" else "未知"

        elif self.sensor_type == "bill_month":
            v = data.get("bill_month")
            return v if v and v != "未知" else "未知"

        return None


    # === 补充传感器类型（数据从API现有字段提取） ===
    def _is_missing_sensor(self):
        return self.sensor_type in ("cons_name", "mobile", "meter_no", "area_name",
                                     "account_status", "revbl_amount", "penalty_date",
                                     "last_fetch_time", "bill_month")


class HuarunGasMeterHistorySensor(BaseSensor):
    """燃气累计用量传感器 — 用于能源面板显示完整历史数据"""

    def __init__(self, coordinator, entry_id):
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_gas_meter_history"
        self._attr_name = "燃气累计用量"
        self._attr_icon = "mdi:counter"
        self._attr_native_unit_of_measurement = "m\u00b3"
        self._attr_state_class = _SSC.TOTAL_INCREASING
        self._attr_device_class = "gas"
        self._last_good_value = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": "华润燃气",
            "manufacturer": "华润燃气",
        }

    @property
    def available(self):
        return self._coordinator.last_update_success

    @property
    def native_value(self):
        data = self._coordinator.data
        if data:
            v = data.get("this_read")
            val = float(v) if v is not None else None
            if val is not None and val > 0:
                self._last_good_value = val
                return val
        return self._last_good_value

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
        if self._coordinator.data is not None:
            self.async_write_ha_state()

def _import_history_statistics(hass, entry, bill_history, current_reading=None):
    """将历史账单数据导入 HA 统计系统，形成趋势图（增量：从DB已有累计值继续）"""
    try:
        from homeassistant.components.recorder.statistics import (
            async_add_external_statistics,
            StatisticMetaData,
            StatisticMeanType,
        )
    except ImportError:
        _LOGGER.warning("recorder 组件不可用，无法导入历史统计")
        return

    sorted_bills = sorted(bill_history, key=lambda b: b.get("billYm", ""))
    if not sorted_bills:
        return

    from datetime import datetime, timezone
    import sqlite3, os

    db_path = os.path.join(hass.config.path(), "home-assistant_v2.db")
    last_gas_sum = 0.0
    last_bill_sum = 0.0
    existing_months = set()
    last_ts = 0

    try:
        db_conn = sqlite3.connect(db_path)
        c = db_conn.cursor()

        # Read existing data
        meta_gas = c.execute("SELECT id FROM statistics_meta WHERE statistic_id='crcgas:monthly_gas_usage'").fetchone()
        if meta_gas:
            existing = c.execute("SELECT start_ts, sum FROM statistics WHERE metadata_id=?", (meta_gas[0],)).fetchall()
            for ts, s in existing:
                existing_months.add(datetime.fromtimestamp(ts).strftime("%Y-%m"))
                if ts > last_ts:
                    last_gas_sum = s
                    last_ts = ts

        meta_bill = c.execute("SELECT id FROM statistics_meta WHERE statistic_id='crcgas:monthly_bill_amount'").fetchone()
        if meta_bill:
            existing_bills = c.execute("SELECT start_ts, sum FROM statistics WHERE metadata_id=? ORDER BY start_ts DESC LIMIT 1", (meta_bill[0],)).fetchone()
            if existing_bills:
                last_bill_sum = existing_bills[1]

        db_conn.close()
    except Exception:
        pass

    # Find the last gas cumulative from the existing data
    if existing_months:
        # Use the existing last gas sum
        pass  # last_gas_sum already set above

    new_gas_stats = []
    new_bill_stats = []
    cumulative_gas = last_gas_sum
    cumulative_bill = last_bill_sum

    for bill in sorted_bills:
        ym = bill.get("billYm", "")
        if not ym or len(ym) < 7:
            continue
        if ym in existing_months:
            # Skip existing months (already in statistics)
            continue
        year, month = int(ym[:4]), int(ym[5:7])
        gas_amt = number(bill.get("gasAmt"))
        bill_amt = number(bill.get("billAmt"))
        if gas_amt is None or bill_amt is None:
            continue
        cumulative_gas += gas_amt
        cumulative_bill += bill_amt
        start = datetime(year, month, 1, tzinfo=timezone.utc)

        new_gas_stats.append({
            "start": start, "state": gas_amt, "sum": cumulative_gas,
            "min": gas_amt, "max": gas_amt, "mean": 0.0, "mean_weight": 0.0,
            "last_reset": start,
        })
        new_bill_stats.append({
            "start": start, "state": bill_amt, "sum": cumulative_bill,
            "min": bill_amt, "max": bill_amt, "mean": 0.0, "mean_weight": 0.0,
            "last_reset": start,
        })

    # Only inject if we have data
    if new_gas_stats:
        gas_metadata = StatisticMetaData(
            has_mean=False, has_sum=True,
            name="历史月度用气量", source=DOMAIN,
            statistic_id=f"{DOMAIN}:monthly_gas_usage",
            unit_of_measurement="m³",
            mean_type=StatisticMeanType.NONE,
        )
        async_add_external_statistics(hass, gas_metadata, new_gas_stats)
        _LOGGER.info("已导入 %d 条用气量统计（累计从 %.1f 开始）", len(new_gas_stats), last_gas_sum)

    if new_bill_stats:
        bill_metadata = StatisticMetaData(
            has_mean=False, has_sum=True,
            name="历史月度燃气费", source=DOMAIN,
            statistic_id=f"{DOMAIN}:monthly_bill_amount",
            unit_of_measurement="CNY",
            mean_type=StatisticMeanType.NONE,
        )
        async_add_external_statistics(hass, bill_metadata, new_bill_stats)
        _LOGGER.info("已导入 %d 条燃气费统计（累计从 %.1f 开始）", len(new_bill_stats), last_bill_sum)


async def _import_meter_history_to_entity(hass, entry, bill_history, only_missing=False):
    """通过 sqlite3 直接写入历史累计统计到 recorder 数据库
    only_missing=True: 只检查数据库中是否有记录，无记录时才注入（启动时用，不删除已有数据）
    only_missing=False: 先删后插（按钮触发时用）
    """
    import sqlite3
    from datetime import datetime, timezone
    import time as time_mod

    history_unique_id = f"{DOMAIN}_total_gas_consumption"
    entity_reg = er.async_get(hass)
    entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, history_unique_id)
    if not entity_id:
        entity_id = "sensor.hua_run_ran_qi_ran_qi_zong_xiao_hao_liang"
        _LOGGER.info("使用 fallback entity_id: %s", entity_id)

    # 同时获取累计燃气费用传感器的ID
    cost_history_unique_id = f"{DOMAIN}_total_gas_cost"
    cost_entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, cost_history_unique_id)
    # fallback: 如果 registry 还没注册，直接构造已知的 entity_id
    if not cost_entity_id:
        cost_entity_id = "sensor.hua_run_ran_qi_lei_ji_ran_qi_fei_yong"

    sorted_bills = sorted(bill_history, key=lambda b: b.get("billYm", ""))
    stats_data = []
    cost_stats_data = []
    cumulative = 0.0
    cumulative_cost = 0.0
    for bill in sorted_bills:
        ym = bill.get("billYm", "")
        if not ym or len(ym) < 7:
            continue
        year, month = int(ym[:4]), int(ym[5:7])
        gas_amt = number(bill.get("gasAmt"))
        bill_amt = number(bill.get("billAmt"))
        if gas_amt is None or bill_amt is None:
            continue
        cumulative += gas_amt
        start_ts = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
        stats_data.append((start_ts, cumulative))
        # 费用累计
        cumulative_cost += bill_amt
        cost_stats_data.append((start_ts, cumulative_cost))

    if not stats_data:
        _LOGGER.warning("无账单数据，跳过历史累计注入")
        return

    db_path = hass.config.path("home-assistant_v2.db")
    now = time_mod.time()

    # 计算统一的 last_reset_ts：取最早的月份开始时间
    common_reset_ts = stats_data[0][0] if stats_data else now

    async def _inject_cost():
        if not cost_entity_id or not cost_stats_data:
            return

        def _do_cost_inject():
            conn2 = None
            try:
                conn2 = sqlite3.connect(db_path, timeout=10)
                conn2.execute("PRAGMA journal_mode=WAL")
                conn2.execute("BEGIN IMMEDIATE")

                # 清理旧的 source=crcgas 残留
                for old_id in [
                    r[0] for r in conn2.execute(
                        "SELECT id FROM statistics_meta WHERE statistic_id = ? AND source = ?",
                        (cost_entity_id, DOMAIN),
                    ).fetchall()
                ]:
                    conn2.execute("DELETE FROM statistics WHERE metadata_id = ?", (old_id,))
                    conn2.execute("DELETE FROM statistics_meta WHERE id = ?", (old_id,))

                conn2.execute(
                    "INSERT OR IGNORE INTO statistics_meta "
                    "(statistic_id, source, unit_of_measurement, has_mean, has_sum, name) "
                    "VALUES (?, 'recorder', ?, 0, 1, '累计燃气费用')",
                    (cost_entity_id, "¥"),
                )
                cost_row = conn2.execute(
                    "SELECT id FROM statistics_meta WHERE statistic_id = ? AND source = 'recorder'",
                    (cost_entity_id,),
                ).fetchone()
                if cost_row:
                    cost_mid = cost_row[0]
                    conn2.execute("DELETE FROM statistics WHERE metadata_id = ?", (cost_mid,))
                    conn2.execute("DELETE FROM statistics_short_term WHERE metadata_id = ?", (cost_mid,))
                    cost_rows = [(cost_mid, ts, val, val, val, val, 0.0, common_reset_ts, now) for ts, val in cost_stats_data]
                    conn2.executemany(
                        "INSERT INTO statistics "
                        "(metadata_id, start_ts, state, sum, min, max, mean, last_reset_ts, created_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        cost_rows,
                    )
                    _LOGGER.info("已注入 %d 条历史费用到 %s", len(cost_stats_data), cost_entity_id)
                conn2.commit()
            except sqlite3.Error as e:
                _LOGGER.error("SQLite写入费用统计失败: %s", e)
            except Exception as e:
                _LOGGER.error("注入费用统计意外错误: %s", e)
            finally:
                if conn2:
                    conn2.close()

        await hass.async_add_executor_job(_do_cost_inject)

    def _do_inject():
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")

            # === 注入燃气累计用量（总读数）===
            if only_missing:
                row = conn.execute(
                    "SELECT id FROM statistics_meta WHERE statistic_id = ? AND source = 'recorder'",
                    (entity_id,),
                ).fetchone()
                if row:
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM statistics WHERE metadata_id = ?",
                        (row[0],),
                    ).fetchone()
                    if existing and existing[0] >= 10:
                        _LOGGER.info("已有 %d 条历史累计记录(气量)，跳过", existing[0])
                        conn.rollback()
                        return
                _LOGGER.info("未发现历史累计记录，执行首次自动注入")

            # 清理旧的 source=crcgas 残留
            for old_id in [
                r[0] for r in conn.execute(
                    "SELECT id FROM statistics_meta WHERE statistic_id = ? AND source = ?",
                    (entity_id, DOMAIN),
                ).fetchall()
            ]:
                conn.execute("DELETE FROM statistics WHERE metadata_id = ?", (old_id,))
                conn.execute("DELETE FROM statistics_meta WHERE id = ?", (old_id,))

            conn.execute(
                "INSERT OR IGNORE INTO statistics_meta "
                "(statistic_id, source, unit_of_measurement, has_mean, has_sum, name) "
                "VALUES (?, 'recorder', ?, 0, 1, '燃气表总读数')",
                (entity_id, "m³"),
            )

            row = conn.execute(
                "SELECT id FROM statistics_meta WHERE statistic_id = ? AND source = 'recorder'",
                (entity_id,),
            ).fetchone()
            if row is None:
                _LOGGER.error("无法获取 statistics_meta id（statistic_id=%s）", entity_id)
                conn.rollback()
                return

            mid = row[0]
            conn.execute("DELETE FROM statistics WHERE metadata_id = ?", (mid,))
            conn.execute("DELETE FROM statistics_short_term WHERE metadata_id = ?", (mid,))

            rows = [(mid, ts, val, val, val, val, 0.0, common_reset_ts, now) for ts, val in stats_data]
            conn.executemany(
                "INSERT INTO statistics "
                "(metadata_id, start_ts, state, sum, min, max, mean, last_reset_ts, created_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

            conn.commit()
            _LOGGER.info("已注入 %d 条历史累计到 %s", len(stats_data), entity_id)
        except sqlite3.Error as e:
            _LOGGER.error("SQLite 写入历史累计失败: %s", e)
            if conn:
                conn.rollback()
        except Exception as e:
            _LOGGER.error("注入历史累计时发生意外错误: %s", e)
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    await hass.async_add_executor_job(_do_inject)
    await _inject_cost()




async def async_setup_entry(

    hass: HomeAssistant,

    config_entry: ConfigEntry,

    async_add_entities,

):

    """设置传感器"""

    cons_no = config_entry.data.get(CONF_CONS_NO, "")



    # 复用 __init__.py 中创建的 API 实例（共享 Token 刷新状态）

    api = hass.data[DOMAIN].get(f"{config_entry.entry_id}_api")

    if api is None:

        # 兜底：如果 __init__ 未创建（不应发生），本地创建

        _LOGGER.warning("未找到共享API实例，本地创建（Token刷新可能不同步）")

        refresh_token = config_entry.data[CONF_REFRESH_TOKEN]

        bo_token = config_entry.data[CONF_BO_TOKEN]

        wx_code = config_entry.data[CONF_WX_CODE]



        async def on_token_refresh(new_refresh_token: str, new_bo_token: str):

            """Token刷新后保存"""

            _LOGGER.info("保存新Token到config_entry")

            new_data = {**config_entry.data}

            new_data[CONF_REFRESH_TOKEN] = new_refresh_token

            new_data[CONF_BO_TOKEN] = new_bo_token

            hass.config_entries.async_update_entry(config_entry, data=new_data)



        session = async_get_clientsession(hass)

        api = HuarunGasApi(refresh_token, bo_token, wx_code, on_token_refresh, session=session)



    # Explicit schedule owns the refresh timer, separate from manual refreshes.
    spec = effective_schedule(config_entry.data, config_entry.options)

    # ========== 2. 数据更新协调器 ==========

    last_valid_tiers = {}
    payment_cache = PaymentHistory(hass, config_entry.entry_id, cons_no)
    await payment_cache.async_load()
    hass.data[DOMAIN][f"{config_entry.entry_id}_payment_cache"] = payment_cache

    async def async_update_data():

        """

        更新数据 - 各接口独立容错，任一失败不影响其他。

        """

        result = {

            "arrears": None,

            "account_balance": None,

            "last_pay_time": "未知",

            "last_pay_amount": None,

            "annual_pay_count": None,

            "this_read": None,

            "this_read_time": "未知",

            "step1_gas_used": None,

            "step2_gas_used": None,

            "this_gas_used": None,

            "bill_amount": None,

            "step1_remain": None,

            "step2_remain": None,
            "step3_remain": None,

            "penalty_amount": None,
            "last_read": None,
            "last_read_time": "未知",
            "settle_flag": "未知",

            "cons_addr": "未知",

            "org_name": "未知",

            "gas_nature": "未知",

            "purchase_style": "未知",

            "last_month_gas": None,

            "year_avg_gas": None,

            "integration_status": "unknown",

            "monthly_gas_used": None,

            "cons_name": "未知",
            "mobile": "未知",
            "meter_no": "未知",
            "area_name": "未知",
            "account_status": "未知",
            "revbl_amount": None,
            "penalty_date": "未知",
            "last_fetch_time": "未知",
            "bill_month": "未知",

        }

        result.update(last_valid_tiers)
        result["tier_data_status"] = "stale" if last_valid_tiers else "unavailable"
        session_timeout_count = 0

        total_api_calls = 6



        # 检查token是否即将过期

        if api.is_token_expiring_soon(threshold_seconds=int(TOKEN_EXPIRE_THRESHOLD.total_seconds())):

            _LOGGER.warning(f"Token即将过期，强制刷新...")

            try:

                await api.async_refresh_token()

            except Exception as e:

                _LOGGER.error(f"强制刷新Token失败: {e}")



        # 1. 获取欠费信息

        try:

            arrears_data = await api.async_query_arrears(cons_no)

            if arrears_data:

                data = arrears_data.get("dataResult", {})

                if isinstance(data, dict):

                    result["arrears"] = _numeric_value(data.get("totalAmt"))

                    result["account_balance"] = _numeric_value(data.get("totalBal"))

        except Exception as e:

            _LOGGER.error(f"获取欠费信息失败: {e}")

            if "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):

                session_timeout_count += 1



        # 2. 获取缴费历史：所有成功页面构成独立快照，失败保留上次有效数据。
        try:
            await payment_cache.async_refresh(api, cons_no)
        except Exception as e:
            _LOGGER.warning("缴费历史更新失败，使用有效缓存（%s）", type(e).__name__)
            if isinstance(e, SessionTimeoutError) or "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):
                session_timeout_count += 1
        result.update(payment_cache.sensor_data())

        # 3. 获取账单列表（获取抄表日期和阶梯剩余气量）

        try:

            bill_data = await api.async_get_gas_bill_list(cons_no, page=1, page_num=6)

            if bill_data and bill_data.get("success"):

                data_result = bill_data.get("dataResult", {})

                bills = data_result.get("data", []) if isinstance(data_result, dict) else []

                try:
                    tier_data = parse_tiers(data_result)
                except ValueError as err:
                    _LOGGER.warning("阶梯信息无效，保留上次有效数据: %s", err)
                else:
                    last_valid_tiers.clear()
                    last_valid_tiers.update(tier_data)
                    for key in TIER_KEYS:
                        result.pop(key, None)
                    result.update(tier_data)
                    result["tier_data_status"] = "fresh"

                if bills:

                    last_bill = bills[0]

                    result["_last_bill_ym"] = last_bill.get("billYm", "")

                    result["_last_app_no"] = last_bill.get("applicationNo", "")

                    result["last_bill_gas_amt"] = _numeric_value(last_bill.get("gasAmt"))

                    result["last_bill_penalty"] = _numeric_value(last_bill.get("penaltyAmt"))

                    result["settle_flag"] = last_bill.get("settleFlag", "未知")
                    result["revbl_amount"] = _numeric_value(last_bill.get("revblAmt"))
                    result["penalty_date"] = last_bill.get("penaltyDate", "未知")
                    result["bill_month"] = last_bill.get("billYm", "未知")

                    # 保存新账单到历史存储（自动更新统计）
                    try:
                        from .history_storage import CRCGasHistoryStorage
                        hs = CRCGasHistoryStorage(hass, config_entry.entry_id)
                        await hs.async_load()
                        existing_app_nos = {b["applicationNo"] for b in hs.get_bill_history(limit=999) if b.get("applicationNo")}
                        new_bills = [b for b in bills if b.get("applicationNo") and b["applicationNo"] not in existing_app_nos
                                     and number(b.get("gasAmt")) is not None and number(b.get("billAmt")) is not None]
                        if new_bills:
                            for nb in new_bills:
                                await hs.async_add_bill_record(nb)
                            _LOGGER.info(f"账单历史更新: 新增{len(new_bills)}条新账单")
                            # 增量注入：只追加新月份
                            try:
                                from homeassistant.components.recorder.statistics import (
                                    async_add_external_statistics, StatisticMetaData, StatisticMeanType)
                                import sqlite3, os
                                db_path = os.path.join(hass.config.path(), "home-assistant_v2.db")
                                db_conn = sqlite3.connect(db_path)
                                c = db_conn.cursor()
                                
                                # 获取最新累计值
                                meta_gas = c.execute("SELECT id FROM statistics_meta WHERE statistic_id='crcgas:monthly_gas_usage'").fetchone()
                                meta_bill = c.execute("SELECT id FROM statistics_meta WHERE statistic_id='crcgas:monthly_bill_amount'").fetchone()
                                last_gas_sum = float(c.execute("SELECT sum FROM statistics WHERE metadata_id=? ORDER BY start_ts DESC LIMIT 1", (meta_gas[0],)).fetchone()[0]) if meta_gas else 0
                                last_bill_sum = float(c.execute("SELECT sum FROM statistics WHERE metadata_id=? ORDER BY start_ts DESC LIMIT 1", (meta_bill[0],)).fetchone()[0]) if meta_bill else 0
                                db_conn.close()
                                
                                for nb in new_bills:
                                    ym = nb.get("billYm", "")
                                    if len(ym) < 7: continue
                                    year, month = int(ym[:4]), int(ym[5:7])
                                    gas_amt = number(nb.get("gasAmt"))
                                    bill_amt = number(nb.get("billAmt"))
                                    start = datetime(year, month, 1, tzinfo=timezone.utc)
                                    
                                    last_gas_sum += gas_amt
                                    last_bill_sum += bill_amt
                                    
                                    # 用气量
                                    async_add_external_statistics(hass, StatisticMetaData(
                                        has_mean=False, has_sum=True,
                                        name="历史月度用气量", source="crcgas",
                                        statistic_id="crcgas:monthly_gas_usage",
                                        unit_of_measurement="m\u00b3",
                                        mean_type=StatisticMeanType.NONE,
                                    ), [{
                                        "start": start, "state": gas_amt, "sum": last_gas_sum,
                                        "min": gas_amt, "max": gas_amt, "mean": 0.0, "mean_weight": 0.0,
                                        "last_reset": start,
                                    }])
                                    
                                    # 费用
                                    async_add_external_statistics(hass, StatisticMetaData(
                                        has_mean=False, has_sum=True,
                                        name="历史月度燃气费", source="crcgas",
                                        statistic_id="crcgas:monthly_bill_amount",
                                        unit_of_measurement="CNY",
                                        mean_type=StatisticMeanType.NONE,
                                    ), [{
                                        "start": start, "state": bill_amt, "sum": last_bill_sum,
                                        "min": bill_amt, "max": bill_amt, "mean": 0.0, "mean_weight": 0.0,
                                        "last_reset": start,
                                    }])
                                    _LOGGER.info(f"增量注入: {ym} 用气={gas_amt}m\u00b3 累计={last_gas_sum}m\u00b3")
                            except Exception as e_inc:
                                _LOGGER.warning(f"增量注入失败: {e_inc}")
                    except Exception as e_h:
                        _LOGGER.debug(f"账单历史更新跳过: {e_h}")

        except Exception as e:

            _LOGGER.error(f"获取账单列表失败: {e}")

            if "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):

                session_timeout_count += 1



        # 4. 获取月度用气图表

        try:

            chart_data = await api.async_get_gas_bill_list4chart(cons_no)

            if chart_data and chart_data.get("success"):

                dr = chart_data.get("dataResult", {})

                if isinstance(dr, dict):

                    last_gas = dr.get("lastGas", [])

                    if len(last_gas) > 1:

                        result["last_month_gas"] = number(last_gas[1])

                    all_gas = [n for g in last_gas if (n := number(g)) is not None]

                    if all_gas:

                        result["year_avg_gas"] = round(sum(all_gas) / len(all_gas), 1)

                    # 统计本年累计用气（thisGas 前几个非空值之和）
                    this_gas = dr.get("thisGas", [])
                    valid_this = [n for g in this_gas if (n := number(g)) is not None]
                    if valid_this:
                        result["annual_gas_used"] = sum(valid_this)
                    else:
                        result["annual_gas_used"] = None

        except Exception as e:

            _LOGGER.error(f"获取月度用气图表失败: {e}")

            if "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):

                session_timeout_count += 1



        # 5. 获取账单详情（本期读数、用气量、账单金额）

        try:

            bill_ym = result.get("_last_bill_ym", "")

            app_no = result.get("_last_app_no", "")

            if bill_ym and app_no:

                detail_data = await api.async_get_bill_detail(cons_no, bill_ym, app_no)

                if detail_data and detail_data.get("success"):

                    details = detail_data.get("dataResult", [])

                    if details and isinstance(details, list):

                        detail = details[0]

                        result["this_read"] = number(detail.get("thisRead"))

                        result["this_read_time"] = detail.get("thisReadTime", "未知")

                        result["last_read"] = number(detail.get("lastRead"))

                        result["last_read_time"] = detail.get("lastReadTime", "未知")

                        result["this_gas_used"] = _first_number(detail.get("gasUsed"), result.get("last_bill_gas_amt"))

                        result["bill_amount"] = _first_number(detail.get("totalAmount"), detail.get("billAmount"), result.get("revbl_amount"))

                        result["penalty_amount"] = number(detail.get("penaltyAmount"))

                        result.update(parse_bill_steps(
                            detail.get("gasStepList"), result["this_gas_used"]
                        ))
                    else:

                        result["this_gas_used"] = result.get("last_bill_gas_amt")

                else:

                    result["this_gas_used"] = result.get("last_bill_gas_amt")

                    result["penalty_amount"] = result.get("last_bill_penalty")

            else:

                _LOGGER.warning(f"缺少账单信息: bill_ym={bill_ym}, app_no={app_no}")

        except Exception as e:

            _LOGGER.error(f"获取账单详情失败: {e}")

            if "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):

                session_timeout_count += 1

            result["this_gas_used"] = result.get("last_bill_gas_amt")



        # 6. 获取绑定信息

        try:

            binding_data = await api.async_get_binding_cons()

            if binding_data and binding_data.get("success"):

                cons_list = binding_data.get("dataResult", [])

                if isinstance(cons_list, list) and cons_list:

                    cons_info = cons_list[0]

                    result["cons_addr"] = cons_info.get("consAddr", "未知")

                    result["org_name"] = cons_info.get("orgName", "未知")

                    result["gas_nature"] = cons_info.get("gasNature") or "未知"

                    result["purchase_style"] = cons_info.get("purchaseGasStyle", "未知")
                    result["cons_name"] = cons_info.get("consName", "未知")
                    result["mobile"] = cons_info.get("mobile", "未知")

        except Exception as e:

            _LOGGER.error(f"获取绑定信息失败: {e}")

            if "SESSION_TIMEOUT" in str(e) or "会话超时" in str(e):

                session_timeout_count += 1



        if session_timeout_count >= total_api_calls:

            _LOGGER.critical(f"全部{total_api_calls}个API返回会话超时！Token已完全失效。")

            result["integration_status"] = "token_expired"

        else:

            # 简单状态判断

            if session_timeout_count > 0:

                result["integration_status"] = "token_expired"

            else:

                result["integration_status"] = "normal"



        # 设置月度累计用气量（暂时用本期用气量代替）

        result["monthly_gas_used"] = result.get("this_gas_used")



        # 最新账单与当前阶梯周期可能不同，不用当前余量覆盖历史账单明细。
        for tier in (1, 2, 3):
            key = f"gas_price_step{tier}"
            if result.get(key) is None:
                result[key] = result.get(f"_bill_price_step{tier}")

        # 所有实际发生的阶梯都有有效单价时才计算费用（含第三阶梯）。
        usages = [number(result.get(f"step{i}_gas_used")) for i in (1, 2, 3)]
        prices = [number(result.get(f"gas_price_step{i}")) for i in (1, 2, 3)]
        if all(u is not None and (u == 0 or (p is not None and p > 0))
               for u, p in zip(usages, prices)):
            result["estimated_gas_bill_amount"] = round(
                sum(u * (p or 0) for u, p in zip(usages, prices)), 2
            )
        else:
            result["estimated_gas_bill_amount"] = None

        # 累计燃气费用 = 历史账单总和
        try:
            from .history_storage import CRCGasHistoryStorage
            cost_store = CRCGasHistoryStorage(hass, config_entry.entry_id)
            await cost_store.async_load()
            cost_history = cost_store.get_bill_history(limit=999)
            known_costs = [number(b.get("billAmt")) for b in cost_history]
            result["total_gas_cost"] = (round(sum(known_costs), 2)
                                        if known_costs and all(v is not None for v in known_costs) else None)
        except Exception:
            result["total_gas_cost"] = result.get("total_gas_cost")

        _LOGGER.info(f"数据更新完成: 欠费¥{result['arrears']}, 读数{result['this_read']}, 状态={result['integration_status']}")

        result["last_fetch_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # bill_amount fallback: 仅在详情缺失时使用有效账单金额，保留真实零值
        if result.get("bill_amount") is None and result.get("revbl_amount") is not None:
            result["bill_amount"] = result["revbl_amount"]
            _LOGGER.info("bill_amount 使用 revbl_amount 兜底: %s", result["bill_amount"])

        return result



    update_lock = asyncio.Lock()

    async def async_locked_update():
        async with update_lock:
            return await async_update_data()

    coordinator = DataUpdateCoordinator(

        hass,

        _LOGGER,

        name=DOMAIN,
        config_entry=config_entry,

        update_method=async_locked_update,

        update_interval=None,

    )



    async def scheduled_refresh():
        try:
            await coordinator.async_request_refresh()
        except Exception:
            _LOGGER.exception("定时数据更新失败")

    def arm_refresh(due, action):
        _LOGGER.debug("下次燃气数据更新: %s", due)
        return async_track_point_in_utc_time(hass, action, due)

    timer = RefreshTimer(
        spec, dt_util.utcnow, lambda: dt_util.get_time_zone(hass.config.time_zone),
        arm_refresh, scheduled_refresh,
    )

    async def schedule_changed(_hass, entry):
        # Token persistence also fires update listeners; identical schedule is a no-op.
        timer.configure(effective_schedule(entry.data, entry.options))

    config_entry.async_on_unload(timer.stop)
    config_entry.async_on_unload(config_entry.add_update_listener(schedule_changed))

    # 立即存储 coordinator 和 api（供 button.py 使用）

    # 必须在创建传感器之前存储，这样即使平台并行加载，按钮也能获取到

    hass.data[DOMAIN][f"{config_entry.entry_id}_coordinator"] = coordinator

    hass.data[DOMAIN][f"{config_entry.entry_id}_api"] = api

    hass.data[DOMAIN][f"{config_entry.entry_id}_cons_no"] = cons_no



    # 创建传感器

    entities = [

        HuarunGasSensor(coordinator, sensor_type)

        for sensor_type in SENSOR_TYPES

    ]

    async_add_entities(entities)



    # 异步执行首次刷新 + 自动抓取历史记录

    def _fix_statis_sum():
        """修正短期统计冷启动 sum=0 问题"""
        sensor_ids = [
            "sensor.hua_run_ran_qi_ran_qi_zong_xiao_hao_liang",
            "sensor.hua_run_ran_qi_lei_ji_ran_qi_fei_yong",
        ]
        db_path = hass.config.path("home-assistant_v2.db")
        try:
            with sqlite3.connect(db_path) as conn:
                c = conn.cursor()
                meta_fixed = 0
                sum_fixed = 0
                for sid in sensor_ids:
                    meta = c.execute(
                        "SELECT id, has_mean, has_sum FROM statistics_meta WHERE statistic_id = ?",
                        (sid,),
                    ).fetchone()
                    if not meta:
                        continue
                    mid, hm, hs = meta[0], meta[1], meta[2]

                    # ① 修复 statistics_meta 字段（has_mean/has_sum 不应为 null）
                    if hm is None or hs is None:
                        new_hm = 0 if hm is None else hm
                        new_hs = 1 if hs is None else hs
                        c.execute(
                            "UPDATE statistics_meta SET has_mean=?, has_sum=? WHERE id=?",
                            (new_hm, new_hs, mid),
                        )
                        meta_fixed += 1
                        _LOGGER.info(
                            "修正 statistics_meta: %s has_mean=%s→%s has_sum=%s→%s",
                            sid, hm, new_hm, hs, new_hs,
                        )

                    # ② 修复 short_term sum
                    latest = c.execute(
                        "SELECT sum, state FROM statistics_short_term "
                        "WHERE metadata_id = ? ORDER BY start_ts DESC LIMIT 1",
                        (mid,),
                    ).fetchone()
                    if not latest:
                        continue
                    short_sum, state_val = float(latest[0]), float(latest[1])
                    if state_val > 0 and abs(short_sum - state_val) > 0.01:
                        c.execute(
                            "UPDATE statistics_short_term SET sum = state WHERE metadata_id = ?",
                            (mid,),
                        )
                        rows = c.rowcount
                        sum_fixed += rows
                        _LOGGER.info(
                            "修正短期统计 sum: %s %.1f→%.1f (%d条)",
                            sid, short_sum, state_val, rows,
                        )

                if meta_fixed or sum_fixed:
                    conn.commit()
                    if meta_fixed:
                        _LOGGER.info("statistics_meta 修正完成: %d 条", meta_fixed)
                    if sum_fixed:
                        _LOGGER.info("短期统计 sum 修正完成: %d 条", sum_fixed)
        except Exception as e:
            _LOGGER.warning("统计修正异常: %s", e)

    async def async_initial_setup():

        """首次初始化：先刷新传感器数据，再自动抓取历史记录"""

        try:

            await coordinator.async_config_entry_first_refresh()

            _LOGGER.info("首次传感器数据刷新完成")

        except Exception as e:

            _LOGGER.error(f"首次传感器数据刷新失败: {e}")

            return
        finally:
            if not asyncio.current_task().cancelling():
                timer.start()

        # 修正短期统计 sum 冷启动问题（立即执行，可能无记录）
        try:
            await hass.async_add_executor_job(_fix_statis_sum)
        except Exception as e:
            _LOGGER.warning("短期统计 sum 修正异常: %s", e)

        # 5分钟后延时修正（等 recorder 编译完成后再查一次）
        async def _fix_statis_deferred(_now):
            _LOGGER.info("延时修正短期统计 sum（recorder 编译后）...")
            await hass.async_add_executor_job(_fix_statis_sum)

        config_entry.async_on_unload(async_call_later(hass, timedelta(seconds=300), _fix_statis_deferred))



        # 导入历史数据到能源面板
        try:
            from .history_storage import CRCGasHistoryStorage
            store = CRCGasHistoryStorage(hass, config_entry.entry_id)
            await store.async_load()
            bill_history = store.get_bill_history(limit=999)
            if bill_history:
                _LOGGER.info(f"历史数据注入: {len(bill_history)} 条账单")
                await _import_meter_history_to_entity(hass, config_entry, bill_history, only_missing=True)
                _LOGGER.info("历史数据注入完成")
        except Exception as e:
            _LOGGER.warning(f"历史数据注入失败（按钮可补救）: {e}")

        # 自动抓取历史记录（仅限首次安装/无历史数据时）

        try:

            from .history_storage import async_setup_history_storage

            storage = await async_setup_history_storage(hass, config_entry.entry_id)



            # 检查是否已有历史数据

            bill_history = storage.get_bill_history()

            if bill_history:

                _LOGGER.debug(f"已有 {len(bill_history)} 条历史账单，跳过自动抓取")

                return



            _LOGGER.info("首次启动，自动抓取历史记录...")

            result = await storage.async_fetch_all_bills(api, cons_no)

            _LOGGER.info(

                f"首次自动抓取完成: "

                f"新增{result['new_bills']}条, "

                f"更新{result['updated_bills']}条, "

                f"总计{result['total_stored']}条"

            )
            # 抓取完成后立即注入统计（无需重启）
            if result.get("total_stored", 0) > 0:
                bill_history = storage.get_bill_history(limit=999)
                if bill_history:
                    try:
                        await _import_meter_history_to_entity(hass, config_entry, bill_history, only_missing=True)
                        _import_history_statistics(hass, config_entry, bill_history)
                        _LOGGER.info(f"首次统计注入完成: {len(bill_history)}条账单")
                    except Exception as e_inj:
                        _LOGGER.warning(f"首次统计注入失败: {e_inj}")

        except Exception as e:

            _LOGGER.warning(f"首次自动抓取历史记录失败（按钮可补救）: {e}")



    initial_task = hass.async_create_task(async_initial_setup())
    config_entry.async_on_unload(initial_task.cancel)

    

    # 注册服务：抓取历史记录

    async def async_fetch_history_service(call):

        """服务：抓取所有历史账单"""

        _LOGGER.info("服务触发: 抓取所有历史记录")

        from .history_storage import async_setup_history_storage

        

        storage = await async_setup_history_storage(hass, config_entry.entry_id)

        try:

            result = await storage.async_fetch_all_bills(api, cons_no)

            _LOGGER.info(

                f"历史记录抓取完成: "

                f"新增{result['new_bills']}条, "

                f"更新{result['updated_bills']}条, "

                f"总计{result['total_stored']}条"

            )

            return {"success": True, "result": result}

        except Exception as e:

            _LOGGER.error(f"抓取历史记录失败: {e}")

            return {"success": False, "error": str(e)}

    

    hass.services.async_register(DOMAIN, "fetch_history", async_fetch_history_service)

    _LOGGER.info(f"已注册服务: {DOMAIN}.fetch_history")



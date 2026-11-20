"""HA-native multi-step configuration and refresh options."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    TimeSelector, TextSelector, TextSelectorConfig, TextSelectorType,
)
from .const import (
    API_GET_BINDING_CONS, BASE_URL, CONF_AREA, CONF_BO_TOKEN,
    CONF_CONS_ADDR, CONF_CONS_NAME, CONF_CONS_NO, CONF_MOBILE,
    CONF_REFRESH_TOKEN, CONF_WX_CODE, DOMAIN, SCAN_INTERVAL_UNITS,
)
from .schedule import (
    SCHEDULE_KEY, Schedule, effective_schedule, form_defaults, parse_schedule, serialize,
)

_LOGGER = logging.getLogger(__name__)
WEEKDAYS = {1: "星期一", 2: "星期二", 3: "星期三", 4: "星期四", 5: "星期五", 6: "星期六", 7: "星期日"}
AUTH_KEYS = (CONF_REFRESH_TOKEN, CONF_BO_TOKEN, CONF_WX_CODE)


def _auth_schema(defaults=None, mode=None):
    defaults = defaults or {}
    fields = {}
    for key in AUTH_KEYS:
        marker = vol.Required(key, default=defaults[key]) if key in defaults else vol.Required(key)
        fields[marker] = TextSelector(TextSelectorConfig(
            type=TextSelectorType.PASSWORD if key != CONF_WX_CODE else TextSelectorType.TEXT
        ))
    if mode is not None:
        fields[vol.Required("mode", default=mode)] = vol.In(SCAN_INTERVAL_UNITS)
    return vol.Schema(fields)


def _mode_schema(mode):
    return vol.Schema({vol.Required("mode", default=mode): vol.In(SCAN_INTERVAL_UNITS)})


def _detail_schema(mode, defaults):
    fields = {}
    if mode == "hour":
        fields[vol.Required("hours", default=defaults.hours)] = NumberSelector(
            NumberSelectorConfig(min=1, max=24, step=1, mode=NumberSelectorMode.BOX))
    else:
        if mode == "week":
            fields[vol.Required("weekday", default=defaults.weekday)] = vol.In(WEEKDAYS)
        if mode == "month":
            fields[vol.Required("day", default=defaults.day)] = NumberSelector(
                NumberSelectorConfig(min=1, max=31, step=1, mode=NumberSelectorMode.BOX))
        fields[vol.Required("at", default=defaults.at)] = TimeSelector()
    return vol.Schema(fields)


def _validated_detail(mode, user_input):
    value = {"mode": mode, **user_input}
    for key in ("hours", "day", "weekday"):
        if key in value:
            original = value[key]
            converted = int(original)
            if isinstance(original, bool) or float(original) != converted:
                raise ValueError("integer required")
            value[key] = converted
    return serialize(parse_schedule(value))


class ScheduleSteps:
    """Shared second-step forms; Home Assistant selects schema on Next."""
    async def _schedule_step(self, mode, user_input):
        errors = {}
        if user_input is not None:
            try:
                selected = _validated_detail(mode, user_input)
            except (ValueError, TypeError, OverflowError):
                errors["base"] = "invalid_schedule"
            else:
                return await self._save_schedule(selected)
        defaults = self._schedule_defaults
        if defaults.mode != mode:
            defaults = Schedule(mode)
        return self.async_show_form(
            step_id=f"schedule_{mode}",
            data_schema=self.add_suggested_values_to_schema(_detail_schema(mode, defaults), user_input or {}),
            errors=errors,
        )

    async def async_step_schedule_hour(self, user_input=None):
        return await self._schedule_step("hour", user_input)

    async def async_step_schedule_day(self, user_input=None):
        return await self._schedule_step("day", user_input)

    async def async_step_schedule_week(self, user_input=None):
        return await self._schedule_step("week", user_input)

    async def async_step_schedule_month(self, user_input=None):
        return await self._schedule_step("month", user_input)


class ConfigFlow(ScheduleSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2

    async def _credentials(self, user_input):
        credentials = {key: user_input[key].strip() for key in AUTH_KEYS}
        if not all(credentials.values()):
            return None, "auth_failed"
        result = await self._validate_and_get_cons(
            credentials[CONF_REFRESH_TOKEN], credentials[CONF_BO_TOKEN], credentials[CONF_WX_CODE])
        if not result.get("success"):
            return None, result.get("error", "auth_failed")
        info = result["cons_info"]
        return {
            **credentials, CONF_CONS_NO: info.get("consNo", ""),
            CONF_CONS_NAME: info.get("consName", ""), CONF_CONS_ADDR: info.get("consAddr", ""),
            CONF_MOBILE: info.get("mobile", ""), CONF_AREA: info.get("area", ""),
        }, None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._pending_data, error = await self._credentials(user_input)
            if error is None:
                mode = user_input["mode"]
                self._schedule_defaults = Schedule(mode)
                return await self._schedule_step(mode, None)
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=_auth_schema(mode="hour"), errors=errors,
        )

    async def _save_schedule(self, selected):
        data = {**self._pending_data, SCHEDULE_KEY: selected}
        return self.async_create_entry(
            title=f"华润燃气 ({data.get(CONF_CONS_NO, '未知户号')})", data=data,
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            data, error = await self._credentials(user_input)
            if error is None:
                if data[CONF_CONS_NO] != entry.data.get(CONF_CONS_NO):
                    errors["base"] = "account_changed"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=data)
            else:
                errors["base"] = error
        # Credential reconfiguration must not silently overwrite schedule options.
        return self.async_show_form(
            step_id="reconfigure", data_schema=_auth_schema(entry.data), errors=errors,
        )

    async def _validate_and_get_cons(self, refresh_token, bo_token, wx_code):
        """验证 tokens 并获取户号信息"""
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "refresh-token": refresh_token,
            "bo-token": bo_token,
            "wxCode": wx_code,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 "
                "Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI"
            ),
            "xweb_xhr": "1",
            "Sec-Fetch-Site": "cross-site",
            "Referer": "https://servicewechat.com/wx34991921b0f92df7/46/page-frame.html",
        }

        url = f"{BASE_URL}{API_GET_BINDING_CONS}"

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status != 200:
                    _LOGGER.warning(f"绑定查询HTTP错误: {response.status}")
                    return {"success": False, "error": "network_error"}

                data = await response.json()

                msg = data.get("msg", "")
                status_code = data.get("statusCode", "")

                if "会话超时" in msg or "SESSION_TIMEOUT" in str(status_code).upper():
                    _LOGGER.warning(f"Token验证返回会话超时: {msg}")
                    return {"success": False, "error": "session_timeout"}

                if data.get("success") and data.get("statusCode") == "200":
                    cons_list = data.get("dataResult", [])
                    if isinstance(cons_list, list) and cons_list:
                        return {"success": True, "cons_info": cons_list[0]}
                    else:
                        _LOGGER.info("无绑定户号")
                        return {"success": False, "error": "no_binding"}
                else:
                    _LOGGER.warning(f"Token验证失败: success={data.get('success')}, msg={msg}, statusCode={status_code}")
                    return {"success": False, "error": "auth_failed"}

        except TimeoutError:
            _LOGGER.error("连接华润燃气服务器超时")
            return {"success": False, "error": "network_error"}
        except Exception as e:
            _LOGGER.exception(f"验证请求异常: {type(e).__name__}: {e}")
            return {"success": False, "error": "network_error"}


    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(ScheduleSteps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        self._schedule_defaults = form_defaults(self.config_entry.data, self.config_entry.options)
        if user_input is not None:
            return await self._schedule_step(user_input["mode"], None)
        current = effective_schedule(self.config_entry.data, self.config_entry.options)
        description = "旧版实际轮询周期仍保留，保存新设置后切换为日历定时。" if current.legacy_seconds else "选择更新模式，下一步配置具体时间。"
        return self.async_show_form(
            step_id="init", data_schema=_mode_schema(self._schedule_defaults.mode),
            description_placeholders={"legacy_notice": description},
        )

    async def _save_schedule(self, selected):
        options = {**self.config_entry.options, SCHEDULE_KEY: selected}
        options.pop("scan_interval", None)
        options.pop("scan_interval_unit", None)
        return self.async_create_entry(title="", data=options)

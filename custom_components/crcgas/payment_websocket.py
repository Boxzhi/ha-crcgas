"""Authenticated, read-only payment history endpoint."""
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


@websocket_api.websocket_command({
    vol.Required("type"): "crcgas/payment_history",
    vol.Optional("entry_id"): str,
    vol.Optional("year"): vol.All(int, vol.Range(min=2000, max=9999)),
    vol.Optional("category", default="all"): vol.In(("all", "gas", "business", "unknown")),
    vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
    vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=200)),
})
@callback
def websocket_payment_history(hass, connection, msg):
    domain_data = hass.data.get(DOMAIN, {})
    registry = er.async_get(hass)
    def can_read(entry):
        if connection.user.is_admin:
            return True
        return any(
            entity.unique_id == f"{DOMAIN}_last_pay_amount"
            and connection.user.permissions.check_entity(entity.entity_id, POLICY_READ)
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        )
    entries = [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if domain_data.get(f"{entry.entry_id}_payment_cache") is not None and can_read(entry)
    ]
    entry_id = msg.get("entry_id")
    if entry_id is None:
        if len(entries) != 1:
            connection.send_error(msg["id"], "entry_required", "请选择有读取权限的燃气配置项")
            return
        entry_id = entries[0].entry_id
    if not any(entry.entry_id == entry_id for entry in entries):
        connection.send_error(msg["id"], "not_found", "燃气配置项不可用或无读取权限")
        return
    cache = domain_data[f"{entry_id}_payment_cache"]
    result = cache.query(msg.get("year"), msg["category"], msg["offset"], msg["limit"])
    result["entry_id"] = entry_id
    connection.send_result(msg["id"], result)

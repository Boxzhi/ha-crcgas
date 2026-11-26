"""Per-update health and last valid balance, independent of HA."""
from math import isfinite


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        val = float(str(value).replace(",", ""))
    except (ValueError, TypeError, OverflowError):
        return None
    return val if isfinite(val) else None


class BalanceSnapshot:
    """Remember only validated monetary fields within one account/runtime."""
    def __init__(self):
        self.values = {"arrears": None, "account_balance": None}

    def update(self, response):
        data = response.get("dataResult") if isinstance(response, dict) and response.get("success") is True else None
        if not isinstance(data, dict):
            return False
        complete = True
        for key, source in (("arrears", "totalAmt"), ("account_balance", "totalBal")):
            value = money(data.get(source))
            if value is None:
                complete = False
            else:
                self.values[key] = value
        return complete


def integration_status(outcomes, session_dead=False):
    """Classify update outcomes; callers mark missing required data as api error."""
    values = list(outcomes.values())
    if values and all(v == "ok" for v in values):
        return "normal"
    if "ok" in values:
        return "partial_error"
    if values and all(v == "auth" for v in values) and session_dead:
        return "token_expired"
    if values and all(v == "network" for v in values):
        return "network_error"
    return "api_error"

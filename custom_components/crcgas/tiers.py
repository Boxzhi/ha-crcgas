"""Normalize CRC Gas tier data without Home Assistant dependencies."""

from math import isfinite, isclose

RULES = {"1": 1, "2": 2, "3": 3, "0201": 1, "0202": 2, "0203": 3}
TIER_KEYS = tuple(
    key
    for tier in (1, 2, 3)
    for key in (
        f"step{tier}_gas_limit", f"step{tier}_gas_sum",
        f"step{tier}_remain", f"gas_price_step{tier}",
    )
)


def number(value):
    """Return a nonnegative finite number, preserving zero and missing separately."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) and result >= 0 else None


def parse_tiers(data_result):
    """Normalize cumulative/independent limits using the official used + remain.

    Reject incomplete or inconsistent snapshots instead of inventing zero/defaults.
    Old regions may return independent limits even for legacy rule codes.
    """
    info = data_result.get("consPrcInfo") if isinstance(data_result, dict) else None
    rows = info.get("consPrcDetailList") if isinstance(info, dict) else None
    if not isinstance(rows, list):
        raise ValueError("missing consPrcDetailList")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tier = RULES.get(str(row.get("ruleCode", "")).strip())
        if tier is not None:
            if tier in indexed:
                raise ValueError("duplicate tier")
            indexed[tier] = row
    if not indexed or set(indexed) != set(range(1, max(indexed) + 1)):
        raise ValueError("missing tier or unrecognized rule codes")
    result = {}
    previous = 0.0
    for tier, row in sorted(indexed.items()):
        raw, used, remain = (number(row.get(k)) for k in ("levGq", "levGqSum", "levGqRemain"))
        if raw is None or used is None or remain is None or raw <= 0:
            raise ValueError(f"missing or invalid tier {tier} values")
        independent = used + remain
        if isclose(raw - previous, independent, rel_tol=0, abs_tol=0.001) and raw > previous:
            limit = raw - previous
        elif isclose(raw, independent, rel_tol=0, abs_tol=0.001):
            limit = raw
        else:
            raise ValueError(f"inconsistent tier {tier} limit/used/remain")
        previous += limit
        result[f"step{tier}_gas_limit"] = limit
        result[f"step{tier}_gas_sum"] = used
        result[f"step{tier}_remain"] = remain
        price = number(row.get("catPrc"))
        result[f"gas_price_step{tier}"] = price if price and price > 0 else None
    return result


def parse_bill_steps(rows, total):
    """Use the bill's actual split; do not infer past billing from today's tier."""
    if not isinstance(rows, list) or not rows:
        return {}
    total = number(total)
    amounts = {tier: 0.0 for tier in (1, 2, 3)}
    costs = {tier: 0.0 for tier in (1, 2, 3)}
    complete_cost = {tier: True for tier in (1, 2, 3)}
    for row in rows:
        if not isinstance(row, dict):
            return {}
        label = str(row.get("stepType", ""))
        tier = next((n for n, name in enumerate(("一阶", "二阶", "三阶"), 1) if name in label), None)
        used = number(row.get("gasUsed"))
        if tier is None or used is None:
            return {}
        amounts[tier] += used
        cost = number(row.get("stepAmt"))
        if cost is None:
            complete_cost[tier] = False
        else:
            costs[tier] += cost
    if total is None or not isclose(sum(amounts.values()), total, rel_tol=0, abs_tol=0.001):
        return {}
    result = {f"step{tier}_gas_used": used for tier, used in amounts.items()}
    for tier, used in amounts.items():
        if used > 0 and complete_cost[tier] and costs[tier] > 0:
            result[f"_bill_price_step{tier}"] = round(costs[tier] / used, 6)
    return result

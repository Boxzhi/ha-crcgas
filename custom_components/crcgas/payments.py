"""Pure payment history parsing and bounded pagination."""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

CHANNELS = {
    "0406": "微网厅支付", "0409": "其它", "0201": "银行代收",
    "0403": "支付宝支付", "0101": "柜台收费",
}
FEE_TYPES = {"01": "gas", "02": "business"}
PAGE_SIZE = 6
MAX_PAGES = 100
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def local_year():
    return datetime.now(CHINA_TZ).year


def normalize_payment(row):
    """Preserve each real transaction; the API provides no unique transaction ID."""
    if not isinstance(row, dict):
        raise ValueError("invalid payment record")
    try:
        timestamp = datetime.strptime(str(row["payTime"]), "%Y-%m-%d %H:%M:%S")
        amount = Decimal(str(row["payAmount"]))
        if not amount.is_finite():
            raise ValueError("invalid payment amount")
        amount = amount.quantize(Decimal("0.01"))
    except (KeyError, ValueError, TypeError, InvalidOperation) as err:
        raise ValueError("invalid payment time or amount") from err
    state = str(row.get("payState") or "").strip()
    if not state:
        raise ValueError("missing payment state")
    channel = str(row.get("payType") or "").strip()
    charge = str(row.get("chargeType") or "").strip()
    fee = str(row.get("feeType") or "").strip()
    return {
        "payTime": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "payAmount": str(amount),
        "payState": state,
        "payType": channel,
        "payTypeName": CHANNELS.get(channel) or (f"未知渠道（{channel}）" if channel else "未知渠道"),
        "chargeType": charge,
        "chargeTypeName": str(row.get("chargeTypeName") or "").strip(),
        "feeType": fee,
        "category": FEE_TYPES.get(charge, "unknown"),
    }


def _fingerprint(rows):
    # Multiset comparison ignores ordering, preserves duplicate multiplicity.
    return Counter(tuple(sorted(row.items())) for row in rows)


async def fetch_payments(api, cons_no, year=None):
    """Return only a completed snapshot. Never append partial or repeated pages."""
    year = local_year() if year is None else year
    collected = []
    fingerprints = []
    for page in range(1, MAX_PAGES + 1):
        response = await api.async_query_pay_history(
            cons_no, page=page, page_num=PAGE_SIZE,
            start_ym="2000-01", end_ym="2100-12",
        )
        if not isinstance(response, dict) or response.get("success") is not True:
            raise ValueError("payment request unsuccessful")
        raw = response.get("dataResult")
        if not isinstance(raw, list):
            raise ValueError("invalid payment response")
        if not raw:
            break
        rows = [normalize_payment(row) for row in raw]
        fingerprint = _fingerprint(rows)
        if fingerprint in fingerprints:
            # The captured endpoint returns more than pageNum records. A repeated
            # first oversized page confirms this server does not honor pagination.
            if page == 2 and len(collected) > PAGE_SIZE and fingerprint == fingerprints[0]:
                break
            raise ValueError("ambiguous repeated payment page")
        if fingerprints and any(fingerprint & previous for previous in fingerprints):
            # No transaction ID: overlapping non-identical pages cannot be
            # reconciled without potentially dropping a real identical payment.
            raise ValueError("overlapping payment pages")
        fingerprints.append(fingerprint)
        collected.extend(rows)
        if len(raw) < PAGE_SIZE:
            break
    else:
        raise ValueError("payment pagination limit reached")
    return sorted(
        (row for row in collected if year - 2 <= int(row["payTime"][:4]) <= year),
        key=lambda row: row["payTime"], reverse=True,
    )


def summarize(records, year):
    """Successful payments only; business fees are separate from gas payments."""
    gas = [r for r in records if r["category"] == "gas" and r["payState"] == "缴费成功"]
    business = [r for r in records if r["category"] == "business" and r["payState"] == "缴费成功"]
    gas_year = [r for r in gas if r["payTime"].startswith(str(year) + "-")]
    business_year = [r for r in business if r["payTime"].startswith(str(year) + "-")]
    latest = max(gas, key=lambda row: row["payTime"], default=None)
    def total(rows):
        return float(sum((Decimal(r["payAmount"]) for r in rows), Decimal("0")))
    return {
        "last_pay_time": latest["payTime"] if latest else None,
        "last_pay_amount": float(latest["payAmount"]) if latest else None,
        "last_pay_channel": latest["payTypeName"] if latest else None,
        "annual_pay_count": len(gas_year),
        "annual_gas_pay_amount": total(gas_year),
        "annual_business_pay_amount": total(business_year),
    }


def summarize_year(records, year):
    selected = [r for r in records if r["payTime"].startswith(str(year) + "-")]
    result = summarize(selected, year)
    return {
        "year": year,
        "record_count": len(selected),
        "gas_count": result["annual_pay_count"],
        "gas_amount": result["annual_gas_pay_amount"],
        "business_count": sum(r["category"] == "business" and r["payState"] == "缴费成功" for r in selected),
        "business_amount": result["annual_business_pay_amount"],
        "unknown_fee_count": sum(r["category"] == "unknown" for r in selected),
    }

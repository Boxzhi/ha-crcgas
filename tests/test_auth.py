"""API concurrency regressions without HA or real network credentials."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components/crcgas"
PKG = "_crcgas_auth_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
const = types.ModuleType(PKG + ".const")
names = ("DO_REFRESH_TOKEN", "GET_BINDING_CONS", "GET_BILL_DETAIL",
         "GET_BO_TOKEN", "GET_GAS_BILL_LIST", "GET_GAS_BILL_LIST_4_CHART",
         "GET_LOGIN_INFO", "QUERY_ARREARS", "QUERY_PAY_HISTORY")
for name in names:
    setattr(const, "API_" + name, "/" + name)
const.BASE_URL = "https://example.invalid"
fake_aiohttp = types.ModuleType("aiohttp")
fake_aiohttp.ClientError = type("ClientError", (Exception,), {})
fake_aiohttp.ContentTypeError = type("ContentTypeError", (fake_aiohttp.ClientError,), {})
fake_aiohttp.ClientTimeout = lambda **kw: kw
with patch.dict(sys.modules, {PKG: pkg, PKG + ".const": const, "aiohttp": fake_aiohttp}):
    from _crcgas_auth_test import api, health


def success(data):
    return {"success": True, "dataResult": data}


TIMEOUT = {"success": True, "msg": "会话超时", "statusCode": "SESSION_TIMEOUT"}
TOKENS = success({"refreshToken": "new-r", "boToken": "new-b", "wxCode": "new-w"})


class Response:
    status = 200
    def __init__(self, data, gate=None):
        self.data, self.gate = data, gate
    async def __aenter__(self):
        if self.gate:
            await self.gate.wait()
        return self
    async def __aexit__(self, *args):
        return False
    async def json(self):
        return self.data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def request(self, method, url, **kwargs):
        self.calls.append((url.rsplit("/", 1)[-1], dict(kwargs["headers"])))
        return self.responses.pop(0)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def make(self, responses, callback=None):
        session = Session(responses)
        return api.HuarunGasApi("old-r", "old-b", "old-w", callback, session), session

    async def test_refresh_blocks_business_and_coalesces(self):
        gate = asyncio.Event()
        saved = []
        async def persist(*tokens):
            saved.append(tokens)
        client, session = self.make([Response(TOKENS, gate), Response(success({"totalBal":108.88,"totalAmt":0}))], persist)
        first = asyncio.create_task(client.async_refresh_token())
        await asyncio.sleep(0)
        second = asyncio.create_task(client.async_refresh_token())
        balance = asyncio.create_task(client.async_query_arrears("test"))
        await asyncio.sleep(0)
        self.assertEqual(len(session.calls), 1)
        gate.set()
        await asyncio.gather(first, second, balance)
        self.assertEqual([c[0] for c in session.calls], ["DO_REFRESH_TOKEN","QUERY_ARREARS"])
        self.assertEqual(session.calls[1][1]["wxCode"], "new-w")
        self.assertEqual(saved, [("new-r","new-b","new-w")])

    async def test_session_timeout_recovers_and_retries_once(self):
        client, session = self.make([Response(TIMEOUT), Response(TOKENS),
                                     Response(success({"totalBal":108.88,"totalAmt":0}))])
        result = await client.async_query_arrears("test")
        self.assertEqual(result["dataResult"]["totalBal"], 108.88)
        self.assertEqual([c[0] for c in session.calls], ["QUERY_ARREARS","DO_REFRESH_TOKEN","QUERY_ARREARS"])
        self.assertEqual(session.calls[-1][1]["bo-token"], "new-b")

    async def test_queued_refresh_after_business_timeout(self):
        gate = asyncio.Event()
        client, session = self.make([Response(TIMEOUT, gate), Response(TOKENS), Response(success({}))])
        business = asyncio.create_task(client.async_get_binding_cons())
        await asyncio.sleep(0)
        rotate = asyncio.create_task(client.async_refresh_token())
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(business, rotate)
        self.assertEqual(len(session.calls), 3)

    async def test_expired_refresh_throttles_recovery(self):
        client, session = self.make([Response(TIMEOUT), Response(TIMEOUT), Response(TIMEOUT)])
        with self.assertRaises(api.SessionTimeoutError):
            await client.async_query_arrears("test")
        self.assertTrue(client.session_dead)
        with self.assertRaises(api.SessionTimeoutError):
            await client.async_get_binding_cons()
        self.assertEqual(len(session.calls), 3)

    async def test_busy_is_not_auth(self):
        client, session = self.make([Response({"success":True,"msg":"系统繁忙","dataResult":"123456789012345"})])
        with self.assertRaises(api.ApiResponseError):
            await client.async_query_arrears("test")
        self.assertFalse(client.session_dead)
        self.assertEqual(len(session.calls), 1)

    async def test_same_credentials_no_retry(self):
        client, session = self.make([Response(TIMEOUT), Response(success({"refreshToken":"old-r","boToken":"old-b"}))])
        with self.assertRaises(api.SessionTimeoutError):
            await client.async_get_binding_cons()
        self.assertEqual(len(session.calls), 2)

    async def test_retry_does_not_loop(self):
        client, session = self.make([Response(TIMEOUT), Response(TOKENS), Response(TIMEOUT)])
        with self.assertRaises(api.SessionTimeoutError):
            await client.async_get_binding_cons()
        self.assertEqual(len(session.calls), 3)

    async def test_overlapping_failed_refresh_coalesces(self):
        gate = asyncio.Event()
        client, session = self.make([Response(TIMEOUT, gate)])
        first = asyncio.create_task(client.async_refresh_token())
        await asyncio.sleep(0)
        second = asyncio.create_task(client.async_refresh_token())
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(e, api.SessionTimeoutError) for e in results))
        self.assertEqual(len(session.calls), 1)


class HealthTests(unittest.TestCase):
    def test_balance_missing_and_real_zero(self):
        b = health.BalanceSnapshot()
        self.assertFalse(b.update(success({})))
        self.assertIsNone(b.values["account_balance"])
        self.assertTrue(b.update(success({"totalBal":"108.88","totalAmt":"0.00"})))
        self.assertFalse(b.update({"success":False,"dataResult":{"totalBal":0,"totalAmt":1}}))
        self.assertEqual(b.values, {"account_balance":108.88,"arrears":0.0})
        self.assertFalse(b.update(success({"totalBal":0})))
        self.assertEqual(b.values, {"account_balance":0.0,"arrears":0.0})
        self.assertFalse(b.update(success({"totalBal":"NaN","totalAmt":True})))
        self.assertEqual(b.values, {"account_balance":0.0,"arrears":0.0})

    def test_health_classification(self):
        self.assertEqual(health.integration_status({"balance":"auth","payments":"ok"}, True),"partial_error")
        self.assertEqual(health.integration_status({"balance":"ok","payments":"ok"}),"normal")
        self.assertEqual(health.integration_status({"balance":"auth","bills":"auth"}, True),"token_expired")
        self.assertEqual(health.integration_status({"balance":"auth"}, False),"api_error")
        self.assertEqual(health.integration_status({"balance":"network","bills":"network"}),"network_error")
        self.assertEqual(health.integration_status({"balance":"api","bills":"network"}),"api_error")

    def test_startup_order_and_persistence_wiring(self):
        init = (ROOT / "__init__.py").read_text()
        self.assertLess(init.index("await _async_refresh_token_hourly()"), init.index("await hass.config_entries.async_forward_entry_setups"))
        self.assertIn("new_data[CONF_WX_CODE] = new_wx_code", init)
        self.assertNotIn("hass.async_create_task(_async_refresh_token_hourly())", init)
        self.assertNotIn("刷新Token原始响应", (ROOT / "api.py").read_text())

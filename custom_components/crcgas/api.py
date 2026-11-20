"""华润燃气 API封装 - v1.0.8

修复: httpx → aiohttp (HA内置，零外部依赖)
"""

import base64
import json
import logging
import time
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional, Callable, Awaitable

import aiohttp

from .const import (
    API_DO_REFRESH_TOKEN,
    API_GET_BINDING_CONS,
    API_GET_BILL_DETAIL,
    API_GET_BO_TOKEN,
    API_GET_GAS_BILL_LIST,
    API_GET_GAS_BILL_LIST_4_CHART,
    API_GET_LOGIN_INFO,
    API_QUERY_ARREARS,
    API_QUERY_PAY_HISTORY,
    BASE_URL,
)

_LOGGER = logging.getLogger(__name__)


class SessionTimeoutError(Exception):
    """会话超时/Token失效异常"""
    pass


class TransportError(Exception):
    """HTTP or network transport failure."""


class ApiResponseError(Exception):
    """Server returned an invalid or unsuccessful response."""


class HuarunGasApi:
    """华润燃气 API客户端 - 使用 aiohttp（HA内置）"""

    def __init__(
        self,
        refresh_token: str,
        bo_token: str,
        wx_code: str,
        on_token_refresh: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
        session=None,  # 外部注入aiohttp session（从HA获取）
    ):
        self.refresh_token = refresh_token
        self.bo_token = bo_token
        self.wx_code = wx_code
        self._on_token_refresh = on_token_refresh
        self._session = session  # aiohttp.ClientSession
        # All cloud I/O using these rotating credentials shares one lock.
        self._auth_lock = asyncio.Lock()
        self._generation = 0
        self._refresh_attempt = 0
        self._refresh_error = None
        self._refresh_result = {}
        self._last_recovery = float("-inf")

        # --- 日志节流（2026-11-01 加）---
        # 「会话超时」是**不可自愈**的终态：refresh-token 已失效，只能用户重新登录。
        # 原先每分钟的紧急定时器都会打一条 ERROR，一小时 60+ 条，把 HA 的
        # 100 行日志窗口完全占满，导致其它集成的真实故障（如 huawei_home_storage
        # 的 401）被冲掉、无从排查。
        #
        # 现在：同一种失败只在**首次**和**每 30 分钟**报一次 ERROR，
        # 其余降为 DEBUG。故障状态用 _session_dead 记录，恢复后自动重置。
        self._session_dead = False
        self._dead_logged_at: float = 0.0
        self._dead_count = 0

    def _log_session_dead(self, message: str) -> None:
        """会话失效的节流日志：首次 + 每 30 分钟一次 ERROR，其余 DEBUG。"""
        now = time.time()
        self._dead_count += 1
        self._session_dead = True
        # 首次，或距上次 ERROR 超过 30 分钟
        if self._dead_logged_at == 0.0 or now - self._dead_logged_at >= 1800:
            _LOGGER.error(
                "%s（累计 %d 次，30 分钟内不再重复报警；请重新登录以恢复）",
                message,
                self._dead_count,
            )
            self._dead_logged_at = now
        else:
            _LOGGER.debug("%s（已节流，第 %d 次）", message, self._dead_count)

    def _mark_session_alive(self) -> None:
        """刷新成功时复位节流状态。"""
        if self._session_dead:
            _LOGGER.info("Token 已恢复（此前累计失败 %d 次）", self._dead_count)
        self._session_dead = False
        self._dead_logged_at = 0.0
        self._dead_count = 0

    def _decode_jwt_payload(self, token: str) -> Optional[Dict[str, Any]]:
        """解码JWT payload获取过期时间"""
        try:
            parts = token.split('.')
            if len(parts) != 3:
                return None
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            decoded = base64.urlsafe_b64decode(payload)
            return json.loads(decoded)
        except Exception as e:
            _LOGGER.warning(f"JWT解码失败: {e}")
            return None

    def get_token_remaining_seconds(self, token: str = None) -> Optional[int]:
        """获取token剩余有效时间（秒）"""
        token = token or self.bo_token
        payload = self._decode_jwt_payload(token)
        if not payload:
            return None
        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return max(0, exp - int(time.time()))

    def is_token_expiring_soon(self, threshold_seconds: int = 300) -> bool:
        """检查token是否即将过期（默认5分钟阈值）"""
        remaining = self.get_token_remaining_seconds()
        if remaining is None:
            return False
        return remaining < threshold_seconds

    @staticmethod
    def _is_session_timeout(data: Dict[str, Any]) -> bool:
        """
        检测华润API的特殊错误格式。
        
        华润API的错误响应：
          {"success": true, "msg": "会话超时", "statusCode": "SESSION_TIMEOUT", "dataResult": "2048458744932626432"}
        
        成功响应：
          {"success": true, "msg": "操作成功", "dataResult": {dict or list}}
        """
        msg = str(data.get("msg") or "")
        status_code = data.get("statusCode", "")
        return "会话超时" in msg or "SESSION_TIMEOUT" in str(status_code).upper()

    def _get_headers(self) -> dict:
        """获取请求头（动态更新token）"""
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "refresh-token": self.refresh_token,
            "bo-token": self.bo_token,
            "wxCode": self.wx_code,
            # 微信小程序必需请求头
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 "
                "Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI"
            ),
            "xweb_xhr": "1",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://servicewechat.com/wx34991921b0f92df7/46/page-frame.html",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    @property
    def session_dead(self):
        """Only an explicit refresh rejection establishes this flag."""
        return self._session_dead

    async def _exchange(self, method, endpoint, **kwargs):
        """Single HTTP exchange. Caller owns the credential lock."""
        url = f"{BASE_URL}{endpoint}"
        try:
            async with self._session.request(
                method, url, headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=30), **kwargs,
            ) as response:
                if response.status != 200:
                    raise TransportError(f"HTTP {response.status}: {endpoint}")
                try:
                    data = await response.json()
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise ApiResponseError(f"Invalid JSON: {endpoint}") from exc
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise TransportError(f"{type(exc).__name__}: {endpoint}") from exc
        if not isinstance(data, dict):
            raise ApiResponseError(f"Invalid response: {endpoint}")
        if self._is_session_timeout(data):
            raise SessionTimeoutError(f"SESSION_TIMEOUT: {endpoint}")
        if "系统繁忙" in str(data.get("msg") or ""):
            raise ApiResponseError(f"Server busy: {endpoint}")
        return data

    async def _request(self, method, endpoint, **kwargs):
        """Keep refresh and business exchanges mutually exclusive.
        
        Retry a read-only business query once, only after credential rotation.
        Recovery refresh attempts are globally throttled per API instance.
        """
        async with self._auth_lock:
            generation = self._generation
            try:
                data = await self._exchange(method, endpoint, **kwargs)
            except SessionTimeoutError:
                pass
            else:
                if data.get("success") is True:
                    self._mark_session_alive()
                return data

        # Release the lock so an already queued refresh can complete first.
        async with self._auth_lock:
            if self._generation == generation and time.monotonic() - self._last_recovery >= 60:
                self._last_recovery = time.monotonic()
                try:
                    await self._refresh_locked()
                except (SessionTimeoutError, TransportError, ApiResponseError) as exc:
                    _LOGGER.debug("认证恢复未完成: %s", type(exc).__name__)
            if self._generation == generation:
                raise SessionTimeoutError(f"SESSION_TIMEOUT: {endpoint}")
            data = await self._exchange(method, endpoint, **kwargs)
            if data.get("success") is True:
                self._mark_session_alive()
            return data

    async def async_refresh_token(self) -> Dict[str, Any]:
        """Coalesce overlapping refresh calls, including failed attempts."""
        attempt = self._refresh_attempt
        async with self._auth_lock:
            if attempt != self._refresh_attempt:
                if self._refresh_error is not None:
                    raise self._refresh_error("Previous simultaneous refresh failed")
                return dict(self._refresh_result)
            return await self._refresh_locked()

    async def _refresh_locked(self):
        """Refresh under the same lock used by all business requests."""
        self._refresh_attempt += 1
        self._refresh_error = None
        self._refresh_result = {}
        try:
            try:
                data = await self._exchange("GET", API_DO_REFRESH_TOKEN)
            except SessionTimeoutError:
                self._log_session_dead("Token刷新失败(会话超时)")
                raise
            if data.get("success") is not True:
                raise ApiResponseError("Token refresh unsuccessful")
            result = data.get("dataResult")
            if not isinstance(result, dict):
                raise ApiResponseError("Invalid token refresh response")
            new_rt = result.get("refresh-token") or result.get("refreshToken")
            new_bt = result.get("bo-token") or result.get("boToken")
            new_wx = result.get("wxCode") or result.get("wxcode") or self.wx_code
            if not all(isinstance(value, str) and value for value in (new_rt, new_bt, new_wx)):
                raise ApiResponseError("Token refresh response missing credentials")
            if (new_rt, new_bt, new_wx) != (self.refresh_token, self.bo_token, self.wx_code):
                self._generation += 1
            self.refresh_token, self.bo_token, self.wx_code = new_rt, new_bt, new_wx
            self._mark_session_alive()
            if self._on_token_refresh:
                await self._on_token_refresh(new_rt, new_bt, new_wx)
            self._refresh_result = {"refresh-token": new_rt, "bo-token": new_bt}
            _LOGGER.info("Token刷新成功")
            return dict(self._refresh_result)
        except (SessionTimeoutError, TransportError, ApiResponseError) as exc:
            self._refresh_error = type(exc)
            raise
        except Exception as exc:
            self._refresh_error = ApiResponseError
            raise ApiResponseError("Token refresh or persistence failed") from exc

    async def async_get_login_info(self) -> Dict[str, Any]:
        """获取登录信息"""
        return await self._request("GET", API_GET_LOGIN_INFO)

    async def async_get_binding_cons(self) -> Dict[str, Any]:
        """获取绑定的用户信息"""
        return await self._request("GET", API_GET_BINDING_CONS)

    async def async_get_gas_bill_list(self, cons_no: str, page: int = 1, page_num: int = 6) -> Dict[str, Any]:
        """获取账单列表"""
        params = {"consNo": cons_no, "page": page, "pageNum": page_num}
        return await self._request("GET", API_GET_GAS_BILL_LIST, params=params)

    async def async_get_bill_detail(self, cons_no: str, bill_ym: str, application_no: str) -> Dict[str, Any]:
        """获取账单详情 - 参数: billYm + consNo + applicationNo"""
        params = {"consNo": cons_no, "billYm": bill_ym, "applicationNo": application_no}
        return await self._request("GET", API_GET_BILL_DETAIL, params=params)

    async def async_get_gas_bill_list4chart(self, cons_no: str) -> Dict[str, Any]:
        """获取月度用气量图表数据"""
        params = {"consNo": cons_no}
        return await self._request("GET", API_GET_GAS_BILL_LIST_4_CHART, params=params)

    async def async_query_arrears(self, cons_no: str) -> Dict[str, Any]:
        """查询欠费 - POST请求，JSON body"""
        order_time = datetime.now().strftime("%Y%m%d%H%M%S")
        payload = {
            "busiType": 1,
            "consNo": cons_no,
            "orderTime": order_time,
            "onlyQuery": 1
        }
        return await self._request("POST", API_QUERY_ARREARS, json=payload)

    async def async_get_bo_token(self) -> Dict[str, Any]:
        """获取 BO Token"""
        return await self._request("GET", API_GET_BO_TOKEN)

    async def async_query_pay_history(self, cons_no: str, page: int = 1, page_num: int = 6, start_ym: str = "2000-01", end_ym: str = "2100-12") -> Dict[str, Any]:
        """查询缴费历史"""
        params = {"consNo": cons_no, "page": page, "pageNum": page_num, "startYm": start_ym, "endYm": end_ym}
        return await self._request("GET", API_QUERY_PAY_HISTORY, params=params)

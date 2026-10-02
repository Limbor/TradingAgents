"""Narrow HTTP client for StockManager's authoritative strategy paper ledger."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import requests


class PaperServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _base_url(config: dict[str, Any]) -> str:
    url = str(config.get("stockmanager_web_url", "http://127.0.0.1:8787")).rstrip("/")
    parsed = urlsplit(url)
    # This bridge has no StockManager credentials. Only a local service is
    # allowed; preventing arbitrary hosts also avoids server-side requests to
    # addresses controlled by a client who can edit app settings.
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if parsed.scheme != "http" or not loopback or parsed.path or parsed.query or parsed.fragment:
        raise PaperServiceError("StockManager Web 地址必须是本机 HTTP 服务", 503)
    return url


async def paper_request(
    config: dict[str, Any], method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call a fixed route chosen by our API. Never accept raw paths from users."""
    url = _base_url(config) + path

    def send() -> requests.Response:
        timeout = max(2.0, min(float(config.get("stockmanager_web_timeout", 90.0)), 300.0))
        return requests.request(method, url, json=payload, timeout=(2, timeout), allow_redirects=False)

    try:
        response = await asyncio.to_thread(send)
    except requests.RequestException as exc:
        raise PaperServiceError("StockManager 模拟盘服务未连接", 503) from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise PaperServiceError("StockManager 返回了无效数据") from exc
    if not response.ok or not isinstance(body, dict) or body.get("ok") is False:
        detail = body.get("detail", "请求失败") if isinstance(body, dict) else "请求失败"
        raise PaperServiceError(str(detail), response.status_code if 400 <= response.status_code < 500 else 502)
    return body

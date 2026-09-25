"""Streamable-HTTP adapter for ArcReel's host-independent tools."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi.responses import PlainTextResponse
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.types import Receive, Scope, Send

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.script.source_loader import SourceLoader
from server.agent_toolset.remote import remote_tools
from server.agent_toolset.toolset import AGENT_TOOLSET
from server.auth import API_KEY_PREFIX, _verify_api_key
from server.services.project import workflow_planner
from server.tool_runtime import Services

# One decoded control byte may occupy six JSON bytes (``\u00XX``); leave 1 MiB for the MCP envelope.
_MAX_REQUEST_BODY_BYTES = SourceLoader.DEFAULT_MAX_BYTES * 6 + 1024 * 1024


class ArcApiKeyVerifier(TokenVerifier):
    """Bridge MCP Bearer auth to ArcReel's existing API Key verifier."""

    def __init__(self, verify_api_key: Callable[[str], Awaitable[dict[str, Any] | None]] = _verify_api_key) -> None:
        self._verify_api_key = verify_api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(API_KEY_PREFIX):
            return None
        payload = await self._verify_api_key(token)
        if payload is None:
            return None
        return AccessToken(token=token, client_id=payload["sub"], scopes=["arcreel"])


def _default_services(projects: ProjectManager) -> Services:
    return Services(
        projects=projects,
        workflow_planner=workflow_planner.get_workflow_planner(projects),
        capabilities=ConfigResolver(async_session_factory),
    )


def build_remote_mcp_server(
    *,
    projects: ProjectManager | None = None,
    services: Services | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastMCP:
    """Build one restart-safe MCP server instance for the host lifespan."""
    if services is not None:
        if projects is not None and projects.data_root.resolve() != services.projects.data_root.resolve():
            raise ValueError("projects 与 services.projects 必须属于同一项目根")
        projects = services.projects
    else:
        projects = projects or get_project_manager()
        services = _default_services(projects)

    # MCP_PUBLIC_URL 只喂 RFC 9728 protected-resource metadata 与 401 challenge：ArcReel 只认
    # 静态 arc- API Key，ArcApiKeyVerifier 返回的 AccessToken 不带 resource，不参与任何校验。
    # Bearer 直连的客户端不读这两处，故该变量对常规接入可缺省。
    public_url = AnyHttpUrl(os.environ.get("MCP_PUBLIC_URL", "http://localhost:1241/mcp"))
    return FastMCP(
        "arcreel",
        tools=remote_tools(AGENT_TOOLSET, projects=projects, services=services),
        token_verifier=token_verifier or ArcApiKeyVerifier(),
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=["arcreel"],
        ),
        stateless_http=True,
        streamable_http_path="/",
        json_response=False,
        max_request_body_size=_MAX_REQUEST_BODY_BYTES,
        # 端点每请求强制 arc- API Key，且该 Key 从不以 cookie / session 形式存在于浏览器，
        # 重绑定到本端点的请求拿不到凭证、只能收 401——Host 白名单在此不构成安全边界，
        # 只会拦下合法部署；Host 归属由反向代理与部署形态承担。浏览器型客户端的跨源防护由
        # 应用级 CORSMiddleware（CORS_ORIGINS，见 server/cors_config.py）单点承担。
        # 关闭该开关不影响 SDK 对 POST 的 Content-Type 校验。
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class RemoteMCPHost:
    """Stable ASGI mount whose one-shot SDK manager is rebuilt per host lifespan."""

    def __init__(self, server_factory: Callable[[], FastMCP] = build_remote_mcp_server) -> None:
        self._server_factory = server_factory
        self._app: Any | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            await PlainTextResponse("MCP server is not running", status_code=503)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        server = self._server_factory()
        child_app = server.streamable_http_app()
        async with server.session_manager.run():
            self._app = child_app
            try:
                yield
            finally:
                self._app = None


remote_mcp_host = RemoteMCPHost()


__all__ = [
    "ArcApiKeyVerifier",
    "RemoteMCPHost",
    "build_remote_mcp_server",
    "remote_mcp_host",
]

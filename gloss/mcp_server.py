"""Read-only MCP server exposing the glossary over Streamable HTTP.

Run with ``python -m gloss.mcp_server``. The Slack bot in ``app.py`` is a
separate process; both share the same database.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import __version__, mcp_tools
from .db import make_engine
from .mcp_auth import BearerAuthMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HOSTS = ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*")

engine = make_engine()
MCP_USER_NAME = os.getenv("MCP_USER_NAME", "mcp")

mcp = MCPServer(
    "glossary-bot",
    version=__version__,
    instructions=(
        "Read-only glossary of terms maintained by the Glossary Bot Slack app. "
        "Definitions may be aliases ('see X'); search is fuzzy."
    ),
)


@mcp.tool()
def lookup_term(term: str) -> dict:
    """Look up an exact glossary term (case-insensitive).

    Follows 'see' / 'see also' aliases and returns the canonical definition.
    If the term is not found, returns fuzzy suggestions.
    """
    with Session(engine) as session:
        return mcp_tools.lookup_term(session, term, user_name=MCP_USER_NAME)


@mcp.tool()
def search_terms(query: str, limit: int = 20) -> dict:
    """Search the glossary by term name (substring) and definition text (fuzzy).

    Returns matching terms with their definitions. ``limit`` is capped at 50.
    """
    with Session(engine) as session:
        return mcp_tools.search_terms(session, query, limit=limit)


@mcp.tool()
def list_terms(
    limit: int = 50,
    offset: int = 0,
    sort: Literal["recent", "alphabetical"] = "recent",
) -> dict:
    """List glossary entries, newest first (``recent``) or alphabetically.

    Supports limit/offset pagination; ``limit`` is capped at 50.
    """
    with Session(engine) as session:
        return mcp_tools.list_terms(session, limit=limit, offset=offset, sort=sort)


@mcp.tool()
def glossary_stats() -> dict:
    """Return the number of terms, distinct definers, and total lookups."""
    with Session(engine) as session:
        return mcp_tools.glossary_stats(session)


def allowed_hosts_from_env() -> list[str]:
    hosts = [
        host.strip()
        for host in os.getenv("MCP_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if not hosts:
        logger.warning(
            "MCP_ALLOWED_HOSTS is not set; only localhost hosts will be accepted. "
            "Set it to the public hostname when deployed."
        )
        return list(DEFAULT_ALLOWED_HOSTS)
    return hosts


def auth_token_from_env() -> str:
    token = os.getenv("MCP_AUTH_TOKEN", "")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN must be set to serve the MCP endpoint")
    return token


async def health(request):
    return JSONResponse({"status": "ok"})


def create_app():
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts_from_env()
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        # Mounting suppresses the MCP app's own lifespan, so start the session
        # manager here.
        async with mcp.session_manager.run():
            yield

    host = Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    return BearerAuthMiddleware(
        host, token=auth_token_from_env(), exempt_paths={"/healthz"}
    )


app = create_app()


def main():
    port = int(os.getenv("PORT") or os.getenv("MCP_PORT") or "8080")
    logger.info("Starting glossary MCP server on port %s", port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )


if __name__ == "__main__":
    main()

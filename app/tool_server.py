"""Bind authenticated Phonic tool callbacks to the bot launched for each meeting."""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request

from app.agent import TOOL_SPECS, native_config
from app.config import require_env, session_timeout
from app.meeting_tools import MeetingState, MeetingTools, ToolError
from app.recall_client import RecallClient, RecallError

logger = logging.getLogger("phonic-demo")
tool_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
TOOL_ARGUMENTS = {name: parameter[0] if parameter else None for name, _, parameter in TOOL_SPECS}


@dataclass
class MeetingSession:
    client: RecallClient
    expires_at: float
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    tools: MeetingTools | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expiration: asyncio.Task | None = None
    closed: bool = False


sessions: dict[str, MeetingSession] = {}


async def close_session(session: MeetingSession, *, leave_bot: bool) -> None:
    async with session.lock:
        if session.closed:
            return
        session.closed = True
        session.ready.set()
        if session.expiration is not None and session.expiration is not asyncio.current_task():
            session.expiration.cancel()
        try:
            if leave_bot and session.tools is not None and not session.tools.state.is_leaving:
                try:
                    await session.client.leave_call(session.tools.bot_id)
                except RecallError:
                    logger.warning("Could not confirm bot departure; check the meeting or Recall dashboard.")
        finally:
            try:
                await session.client.aclose()
            finally:
                sessions.pop(session.token, None)


async def expire_session(session: MeetingSession) -> None:
    await asyncio.sleep(max(0, session.expires_at - time.monotonic()))
    await close_session(session, leave_bot=True)


async def launch_session(meeting_url: str) -> str:
    client = RecallClient(require_env("RECALL_API_KEY"), require_env("RECALL_API_BASE_URL"))
    session = MeetingSession(client, time.monotonic() + session_timeout())
    configuration = native_config(session.token)
    await client.__aenter__()
    sessions[session.token] = session
    session.expiration = asyncio.create_task(expire_session(session))
    try:
        async with session.lock:
            bot_id = await client.create_bot(meeting_url, configuration)
            session.tools = MeetingTools(client, bot_id, MeetingState())
            session.ready.set()
        return bot_id
    except BaseException:
        await close_session(session, leave_bot=True)
        raise


async def close_all_sessions() -> None:
    await asyncio.gather(
        *(close_session(session, leave_bot=True) for session in list(sessions.values())),
    )


async def read_tool_arguments(request: Request, tool_name: str) -> dict[str, str]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, "Tool request is too large.")
    try:
        data = json.loads(body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Provide a JSON object.") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Provide a JSON object.")
    # Phonic adds call_info beside the tool's top-level request-body parameters.
    call_info = data.pop("call_info", {})
    if not isinstance(call_info, dict):
        raise HTTPException(400, "Invalid Phonic call information.")
    parameter = TOOL_ARGUMENTS[tool_name]
    if set(data) != ({parameter} if parameter else set()):
        raise HTTPException(400, "Provide only the documented tool arguments.")
    if parameter and not isinstance(data[parameter], str):
        raise HTTPException(400, "The tool argument must be text.")
    return data


@tool_app.post("/tools/{tool_name}")
async def run_tool(tool_name: str, request: Request):
    token = request.headers.get("X-Session-Token", "")
    session = sessions.get(token)
    if session is None or session.closed or time.monotonic() >= session.expires_at:
        raise HTTPException(401, "Missing, invalid, or expired meeting session.")
    if tool_name not in TOOL_ARGUMENTS:
        raise HTTPException(404, "Unknown meeting tool.")
    try:
        # Bound body reading and lock contention as well as external requests.
        async with asyncio.timeout(25):
            arguments = await read_tool_arguments(request, tool_name)
            await session.ready.wait()
            async with session.lock:
                if session.closed or time.monotonic() >= session.expires_at or session.tools is None:
                    raise HTTPException(401, "The meeting session has ended.")
                # Reserved Recall variables identify the bot, but the token is the
                # authority. Never let callback headers select a new bot or region.
                expected_origin = session.client.base_url.removesuffix("/api/v1")
                if (request.headers.get("X-Recall-Bot-Id") != session.tools.bot_id
                        or request.headers.get("X-Recall-Api-Base-Url", "").rstrip("/") != expected_origin):
                    raise HTTPException(403, "The Recall bot context does not match this session.")
                try:
                    result = await getattr(session.tools, tool_name)(**arguments)
                    response = {"status": "success", "message": result}
                except ToolError as error:
                    response = {"status": "error", "message": str(error)}
                leaving = session.tools.state.is_leaving
    except TimeoutError:
        return {
            "status": "error",
            "message": "Could not confirm the action. Check the meeting; do not retry automatically.",
        }
    if leaving:
        await close_session(session, leave_bot=False)
    return response

"""Run the localhost meeting launcher and separately tunneled tool callbacks."""

import asyncio
import signal
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.config import BOT_NAME, ROOT, validate_runtime_settings
from app.recall_client import RecallError
from app.tool_server import close_all_sessions, launch_session, tool_app


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        await close_all_sessions()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
pending_meetings: set[str] = set()


class MeetingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    meeting_url: str


@app.get("/")
async def index():
    return FileResponse(ROOT / "app" / "templates" / "index.html")


@app.post("/api/bots", status_code=201)
async def create_meeting_agent(request: MeetingRequest):
    if request.meeting_url in pending_meetings:
        raise HTTPException(409, "An agent request for this meeting is already in progress.")
    pending_meetings.add(request.meeting_url)
    try:
        validate_runtime_settings()
        bot_id = await launch_session(request.meeting_url)
        return {
            "bot_id": bot_id,
            "message": f"Agent requested. Admit {BOT_NAME} if prompted.",
        }
    except ValueError as error:
        raise HTTPException(503, str(error)) from None
    except RecallError as error:
        raise HTTPException(502, str(error)) from None
    finally:
        pending_meetings.discard(request.meeting_url)


class LocalServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # Both servers share one shutdown handler.
        yield


async def serve() -> None:
    servers = [
        LocalServer(uvicorn.Config(app, host="127.0.0.1", port=8006, access_log=False)),
        LocalServer(uvicorn.Config(
            tool_app, host="127.0.0.1", port=7866, access_log=False, log_level="warning",
        )),
    ]
    loop = asyncio.get_running_loop()

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop)
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_all_sessions()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    asyncio.run(serve())

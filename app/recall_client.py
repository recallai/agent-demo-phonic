"""Recall requests used by the meeting launcher and webhook tools."""

from urllib.parse import urlsplit
from uuid import UUID

import aiohttp

from app.config import BOT_NAME


class RecallError(Exception):
    """A safe description of a rejected or unconfirmed Recall request."""


class RecallClient:
    def __init__(self, api_key: str, base_url: str):
        parsed = urlsplit(base_url)
        regions = {"us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1"}
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {f"{region}.recall.ai" for region in regions}
            or parsed.path.rstrip("/") != "/api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("RECALL_API_BASE_URL must be a Recall regional HTTPS /api/v1 URL.")
        if not api_key.strip():
            raise ValueError("Set RECALL_API_KEY before starting the demo.")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Token {self._api_key}"},
            timeout=aiohttp.ClientTimeout(total=8),
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(
        self, method: str, path: str, action: str, payload: dict | None = None,
        *, read_json: bool = False,
    ) -> dict | None:
        if self._session is None:
            raise RuntimeError("Use RecallClient inside an async with block.")
        try:
            async with self._session.request(
                method, f"{self.base_url}/{path}", json=payload, allow_redirects=False,
            ) as response:
                if response.status >= 500:
                    raise RecallError(
                        f"Recall could not confirm {action} (HTTP {response.status}). "
                        "Check the meeting before trying again."
                    )
                if not 200 <= response.status < 300:
                    hint = "Check the request settings and Recall workspace configuration."
                    if response.status in (401, 403):
                        hint = "Check the Recall key, region, and workspace access."
                    elif response.status == 429:
                        hint = "Wait before trying again."
                    raise RecallError(f"Recall rejected {action} (HTTP {response.status}). {hint}")
                if read_json:
                    data = await response.json()
                    if not isinstance(data, dict):
                        raise ValueError("Unexpected response format")
                    return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise RecallError(
                f"Could not confirm {action}. It may have succeeded; "
                "check the meeting before trying again."
            ) from error
        return None

    @staticmethod
    def _bot_path(bot_id: str, endpoint: str) -> str:
        return f"bot/{UUID(bot_id)}/{endpoint}/"

    async def create_bot(self, meeting_url: str, phonic_config: dict) -> str:
        """Connect a Phonic voice session using the launcher's configuration."""
        payload = {
            "meeting_url": meeting_url,
            "bot_name": BOT_NAME,
            "variant": {
                "zoom": "web_4_core",
                "google_meet": "web_4_core",
                "microsoft_teams": "web_4_core",
                "webex": "web_4_core",
            },
            "output_media": {"audio": {"kind": "phonic", "config": phonic_config}},
        }
        host = urlsplit(meeting_url).hostname or ""
        if host != "webex.com" and not host.endswith(".webex.com"):
            payload["chat"] = {
                "on_bot_join": {
                    "send_to": "everyone",
                    "message": (
                        "Start requests with 'Assistant, [command]'.\n\n"
                        "Try 'Assistant, show https://example.com' or "
                        "'Assistant, send the shared webpage link in chat'."
                    ),
                    "pin": False,
                },
            }
        result = await self._request(
            "POST", "bot/", "the meeting agent request", payload, read_json=True,
        )
        try:
            if result is None or not isinstance(result.get("id"), str):
                raise ValueError("Missing bot ID")
            return str(UUID(result["id"]))
        except ValueError as error:
            raise RecallError(
                "Recall accepted the request but returned no valid bot ID. "
                "Check the meeting before trying again."
            ) from error

    async def start_screenshare(self, bot_id: str, url: str) -> None:
        await self._request(
            "POST", self._bot_path(bot_id, "output_media"), "starting the screen share",
            {"screenshare": {"kind": "webpage", "config": {"url": url}}},
        )

    async def send_chat_message(self, bot_id: str, message: str) -> None:
        await self._request(
            "POST", self._bot_path(bot_id, "send_chat_message"), "the chat message",
            {"message": message, "to": "everyone", "pin": False},
        )

    async def stop_screenshare(self, bot_id: str) -> None:
        await self._request(
            "DELETE", self._bot_path(bot_id, "output_media"), "stopping the screen share",
            {"screenshare": True},
        )

    async def leave_call(self, bot_id: str) -> None:
        await self._request("POST", self._bot_path(bot_id, "leave_call"), "leaving the meeting")

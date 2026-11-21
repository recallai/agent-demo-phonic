"""Five meeting actions and the state they share within one meeting."""

import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from app.recall_client import RecallClient, RecallError

logger = logging.getLogger("meeting-demo")
CHAT_CHARACTER_LIMIT = 500


class ToolError(Exception):
    """An action failed or needs different input; safe to report to the participant."""


@dataclass
class MeetingState:
    current_page_url: str | None = None
    is_leaving: bool = False


def public_webpage_url(value: str) -> str:
    """Reject credentials and local addresses before passing a URL to Recall."""
    url = value.strip()
    if len(url) > CHAT_CHARACTER_LIMIT:
        raise ToolError(f"Use a page URL of at most {CHAT_CHARACTER_LIMIT} characters so it fits in chat.")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii")
        hostname = hostname.rstrip(".").lower()
        valid = (
            parsed.scheme == "https"
            and hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and "\\" not in url
            and "%" not in parsed.netloc
            and not any(character.isspace() for character in url)
            and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
        )
    except (ValueError, UnicodeError):
        valid = False
        hostname = ""
    if not valid:
        raise ToolError("Provide a public HTTPS webpage URL without login credentials.")
    try:
        is_public = ip_address(hostname).is_global
    except ValueError:
        labels = hostname.split(".")
        # Browsers also accept shortened, octal, and hexadecimal IPv4 addresses.
        # Require numeric hosts to use the canonical form checked above.
        is_public = (
            len(labels) > 1
            and len(hostname) <= 253
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
            and not re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", labels[-1])
            and labels[-1] not in {"localhost", "local", "internal"}
        )
    if not is_public:
        raise ToolError("Use a public website, not a local or private network address.")
    return url


class MeetingTools:
    def __init__(self, client: RecallClient, bot_id: str, state: MeetingState):
        self.client = client
        self.bot_id = bot_id
        self.state = state

    def _require_active(self) -> None:
        if self.state.is_leaving:
            raise ToolError("The agent is already leaving the meeting.")

    async def _post_status(self, message: str) -> None:
        try:
            await self.client.send_chat_message(self.bot_id, message)
        except RecallError:
            # Feedback failure must not undo or repeat the original action.
            logger.warning("Could not post tool status in chat; not retrying")

    async def _call_recall(self, request: Awaitable[None]) -> None:
        try:
            await request
        except RecallError as error:
            await self._post_status(str(error))
            raise ToolError(str(error)) from error

    async def start_screenshare(self, url: str) -> str:
        """Show a public webpage without reading it or automatically posting its URL."""
        self._require_active()
        try:
            url = public_webpage_url(url)
        except ToolError as error:
            await self._post_status(str(error))
            raise
        await self._call_recall(self.client.start_screenshare(self.bot_id, url))
        self.state.current_page_url = url
        await self._post_status("Screen share requested. It may take a few seconds to appear.")
        return "Recall accepted the screen share request. Page visibility is unverified."

    async def send_chat_message(self, message: str) -> str:
        """Post requested text to everyone. Use the link tool for the current page."""
        self._require_active()
        if not message.strip() or len(message) > CHAT_CHARACTER_LIMIT:
            await self._post_status(f"Message not sent: use 1 to {CHAT_CHARACTER_LIMIT} characters.")
            raise ToolError(f"Google Meet messages must contain 1 to {CHAT_CHARACTER_LIMIT} characters.")
        await self._call_recall(self.client.send_chat_message(self.bot_id, message))
        return "Recall accepted the chat message."

    async def send_current_page_link(self) -> str:
        """Post the exact last accepted page URL, including after sharing stops."""
        self._require_active()
        if self.state.current_page_url is None:
            message = "No webpage has been selected yet. Ask me to show one first."
            await self._post_status(message)
            raise ToolError(message)
        return await self.send_chat_message(self.state.current_page_url)

    async def stop_screenshare(self) -> str:
        """Stop sharing while preserving conversation and the last page URL."""
        self._require_active()
        await self._call_recall(self.client.stop_screenshare(self.bot_id))
        await self._post_status("Stopped sharing.")
        return "Recall accepted the request to stop sharing."

    async def leave_call(self) -> str:
        """Post a departure message and remove only this bot from the meeting."""
        self._require_active()
        await self._post_status("Leaving the call.")
        await self._call_recall(self.client.leave_call(self.bot_id))
        self.state.is_leaving = True
        return "Recall accepted the request for this bot to leave."

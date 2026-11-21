"""Native Phonic instructions and one-time webhook tool provisioning."""

import argparse
import asyncio
import json
from urllib.parse import quote

import aiohttp

from app.config import (
    project_name,
    require_env,
    session_timeout,
    tools_origin,
    voice_id,
)

PHONIC_API_URL = "https://api.phonic.ai/v1"
TOOL_OWNER = "Recall.ai Phonic meeting sample."

INSTRUCTIONS = """You are Assistant, also called phonic demo, in a meeting.
Join silently. The app posts your introduction where meeting chat is available.

Only respond when someone addresses Assistant or phonic demo, or clearly continues
a request they already directed to you. For conversation between other people,
use choose_not_to_respond. Do not offer unsolicited commentary or check-ins.
Answer questions aloud, briefly. If a request is unclear, ask one short question.

Act only on explicit participant requests. Use the meeting tools for actions;
never claim an action succeeded before its tool result. Perform each requested
action once. Do not retry a failed or uncertain action unless the participant
checks the meeting and explicitly asks to try again.

You can show a public HTTPS webpage, send requested text to meeting chat, send
the current page link, stop sharing, or leave the meeting. You cannot inspect,
read, click, scroll, or summarize a displayed page. Do not pretend to see it.
Use the requested URL. Ask for a URL when one is missing or ambiguous;
do not invent or guess an address.
Sharing a page does not authorize posting its URL in chat. When asked to send
the current page link, call send_current_page_link; never reconstruct the URL.

The tools post concise chat feedback. Requested text and links are their own
feedback. Do not add spoken acknowledgments or duplicate chat messages for
tool actions. An accepted screen share request does not prove the page is
visible; do not claim you verified it. Stop sharing only when requested, and
leave only when explicitly asked to leave. Leaving removes this bot, not others.
This demo has no mute, listening controls, avatar, browser interaction, memory
across meetings, or external application integrations. Explain that briefly if
asked for an unsupported action.
"""

TOOL_SPECS = (
    (
        "start_screenshare",
        ("Show a public HTTPS webpage when explicitly requested. This displays the "
         "page without reading it or sending its URL to chat."),
        ("url", "The requested public HTTPS webpage URL, at most 500 characters."),
    ),
    (
        "send_chat_message",
        ("Send explicitly requested text to everyone in meeting chat. For the "
         "current page link, use send_current_page_link instead."),
        ("message", "The requested chat message, from 1 to 500 characters."),
    ),
    (
        "send_current_page_link",
        ("Send the exact last accepted page URL to chat, only when requested. "
         "The app remembers the URL even after sharing stops."),
        None,
    ),
    (
        "stop_screenshare",
        ("Stop sharing only when requested. Keep the bot in the meeting and "
         "preserve the last accepted page URL."),
        None,
    ),
    (
        "leave_call",
        ("Remove only this bot from the meeting when explicitly asked to leave. "
         "Do not use for an ordinary goodbye between other participants."),
        None,
    ),
)


def native_config(session_token: str) -> dict:
    """Pass native settings through Recall; Recall supplies bot ID and API origin."""
    duration = session_timeout()
    return {
        "project": project_name(),
        "system_prompt": INSTRUCTIONS,
        "voice_id": voice_id(),
        "default_language": "en",
        "generate_welcome_message": False,
        "welcome_message": "",
        "enable_assistant_backchannel": False,
        "no_input_poke_sec": None,
        "generate_no_input_poke_text": False,
        "no_input_end_conversation_sec": duration,
        "max_conversation_duration_sec": duration,
        "detect_voicemail_tone": False,
        "template_variables": {"session_token": session_token},
        "tools": [
            *(name for name, _, _ in TOOL_SPECS),
            {
                "type": "built_in",
                "name": "choose_not_to_respond",
                "tool_config": {"respond_after_sec": None},
            },
        ],
    }


def webhook_tools() -> list[dict]:
    """Saved webhook tools are referenced by name in the native configuration."""
    definitions = []
    for name, description, parameter in TOOL_SPECS:
        parameters = []
        if parameter:
            parameters.append({
                "name": parameter[0],
                "description": parameter[1],
                "type": "string",
                "is_required": True,
                "location": "request_body",
            })
        definitions.append({
            "name": name,
            "description": f"{TOOL_OWNER} {description}",
            "type": "custom_webhook",
            "execution_mode": "sync",
            "parameters": parameters,
            "endpoint_method": "POST",
            "endpoint_url": f"{tools_origin()}/tools/{name}",
            "endpoint_headers": {
                "X-Session-Token": "{{session_token}}",
                "X-Recall-Bot-Id": "{{recall_bot_id}}",
                "X-Recall-Api-Base-Url": "{{recall_api_base_url}}",
            },
            "endpoint_timeout_ms": 30000,
            "require_speech_before_tool_call": False,
            "forbid_speech_after_tool_call": True,
            "allow_tool_chaining": True,
            "uninterruptible": True,
            "can_run_during_user_speech": False,
        })
    return definitions


class SetupError(Exception):
    """A setup failure that does not expose credentials or response bodies."""


async def phonic_request(
    client: aiohttp.ClientSession, method: str, path: str, payload: dict | None = None,
) -> dict | None:
    try:
        async with client.request(
            method, f"{PHONIC_API_URL}/{path}", params={"project": project_name()},
            json=payload, allow_redirects=False,
        ) as response:
            if method == "GET" and response.status == 404:
                return None
            if not 200 <= response.status < 300:
                raise SetupError(
                    f"Phonic tool setup returned HTTP {response.status}. "
                    "Check your Phonic key, project, and tools before running setup again."
                )
            result = await response.json()
            if not isinstance(result, dict):
                raise SetupError("Phonic returned an unexpected setup response; check your tools before retrying.")
            if method == "PATCH" and result.get("success") is not True:
                raise ValueError("Update was not confirmed")
            if method == "POST" and (
                not isinstance(result.get("id"), str)
                or not result["id"]
                or result.get("name") != payload["name"]
            ):
                raise ValueError("Creation was not confirmed")
            return result
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise SetupError(
            "Could not confirm the Phonic setup request. No automatic retry was made. "
            "Check your Phonic tools before running setup again."
        ) from error


async def setup_tools(*, update: bool) -> None:
    """Check every name before writing; never overwrite an unrelated saved tool."""
    definitions = webhook_tools()
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {require_env('PHONIC_API_KEY')}"},
        timeout=aiohttp.ClientTimeout(total=20), trust_env=False,
    ) as client:
        existing_tools = {}
        for definition in definitions:
            name = definition["name"]
            result = await phonic_request(client, "GET", f"tools/{name}")
            if result is None:
                continue
            existing = result.get("tool")
            if not isinstance(existing, dict):
                raise SetupError("Phonic returned an unexpected tool response; nothing changed.")
            if (
                existing.get("type") != "custom_webhook"
                or not str(existing.get("description", "")).startswith(f"{TOOL_OWNER} ")
            ):
                raise SetupError(f"Tool {name} already exists and is not owned by this sample.")
            if not update:
                raise SetupError(
                    f"Sample tool {name} already exists. Use --setup-tools --update "
                    "to refresh sample-owned tools, including a changed tunnel URL."
                )
            tool_id = existing.get("id")
            if not isinstance(tool_id, str) or not tool_id:
                raise SetupError("Phonic returned no tool ID; nothing changed.")
            existing_tools[name] = tool_id

        for definition in definitions:
            name = definition["name"]
            if name in existing_tools:
                # The update endpoint cannot change a tool's type.
                payload = {key: value for key, value in definition.items() if key != "type"}
                tool_id = quote(existing_tools[name], safe="")
                await phonic_request(client, "PATCH", f"tools/{tool_id}", payload)
                print(f"Updated {name}.")
            else:
                await phonic_request(client, "POST", "tools", definition)
                print(f"Created {name}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--dry-run", action="store_true", help="Print configuration without API calls.")
    command.add_argument("--setup-tools", action="store_true", help="Create saved Phonic webhook tools.")
    parser.add_argument("--update", action="store_true", help="Allow updating this sample's existing tools.")
    args = parser.parse_args()
    if args.update and not args.setup_tools:
        parser.error("--update requires --setup-tools")
    try:
        if args.dry_run:
            print(json.dumps({
                "config": native_config("DRY_RUN_TOKEN"),
                "webhook_tools": webhook_tools(),
            }, indent=2))
        else:
            asyncio.run(setup_tools(update=args.update))
    except (SetupError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

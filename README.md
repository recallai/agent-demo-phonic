# Phonic meeting agent

Send an interactive meeting agent to Zoom, Google Meet, Microsoft Teams, or Webex. It answers questions aloud and takes spoken requests to share a webpage, post in chat, or leave the call.

Recall streams meeting audio directly to Phonic. This Python app launches the bot and handles meeting actions through webhooks. See [Recall's Phonic integration](https://docs.recall.ai/docs/phonic), [Recall meeting tools](https://docs.recall.ai/docs/meeting-agents-adding-tools-to-your-meeting-agents), and [Phonic webhook tools](https://docs.phonic.ai/docs/build/tools/webhook_tools).

## Behavior

Address the agent as **“Assistant”** or **“phonic demo”**, followed by a question or command. It joins silently. Where meeting chat is available, it posts an introduction and tool results. Requested text and links are their own confirmation.

| Action | Tool |
| --- | --- |
| Start or replace a screen share with a public webpage | `start_screenshare(url)` |
| Post text in meeting chat | `send_chat_message(message)` |
| Post the exact last accepted page URL | `send_current_page_link()` |
| Stop the screen share while keeping voice available | `stop_screenshare()` |
| Leave the meeting | `leave_call()` |

Phonic receives the meeting's mixed audio and uses the agent prompt to decide when to respond.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Recall workspace and its regional REST API key, with the Phonic integration available.
- A Phonic project and an API key that can create and update its tools.
- An HTTPS tunnel, such as cloudflared, for tool callbacks.
- A meeting link from one of the supported platforms.

## Quickstart

### 1. Install dependencies

From the repository root:

```sh
uv sync --locked
```

### 2. Configure the environment

```sh
cp .env.example .env
```

Fill in the root `.env`. It is ignored by Git and never sent to the browser. Existing process environment variables take precedence.

| Variable | Value |
| --- | --- |
| `RECALL_API_KEY` | REST API key for the Recall workspace. |
| `RECALL_API_BASE_URL` | Matching regional API URL, such as `https://us-east-1.recall.ai/api/v1`. |
| `PHONIC_API_KEY` | Phonic key with permission to configure tools in the selected project; used by setup only. |
| `PHONIC_PROJECT` | Existing Phonic project name; defaults to `main`. A separate demo project keeps its tools independent. |
| `PHONIC_VOICE_ID` | Voice available to that project; defaults to `sabrina`. |
| `PHONIC_TOOLS_ORIGIN` | The callback tunnel's public HTTPS origin, without a path. |
| `SESSION_TIMEOUT_SECONDS` | Session limit, from `60` to `3600` seconds; defaults to `600`. |

In the matching Recall region and workspace, open **Agents → Phonic** and save a Phonic API key with access to `PHONIC_PROJECT`. Recall uses that saved key for audio. The local key is only needed when configuring the hosted webhook tools.

The app supplies the agent instructions and voice settings in each Create Bot request. You do not need to create a saved Phonic agent. The five webhook tools must exist in the selected project before launching a bot.

### 3. Start an HTTPS tunnel

With [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installed:

```sh
cloudflared tunnel --url http://127.0.0.1:7866
```

Copy the HTTPS origin into `PHONIC_TOOLS_ORIGIN` and keep the tunnel running.

Only expose the callback server on **7866**. Keep the launcher on **8006** local. Meeting audio travels directly between Recall and Phonic, not through this app or tunnel.

### 4. Configure the Phonic tools

Preview the tool definitions and agent configuration without API calls or credentials:

```sh
uv run --locked python -m app.agent --dry-run
```

Create the five saved webhook tools:

```sh
uv run --locked python -m app.agent --setup-tools
```

If the tunnel origin or tool definitions change, explicitly refresh the sample's tools:

```sh
uv run --locked python -m app.agent --setup-tools --update
```

Use the same project for setup and the meeting. The setup command checks existing names before making changes and refuses to overwrite unrelated tools. After a partial setup failure, inspect the Phonic project before rerunning; successful changes are not rolled back automatically.

Restart the app after changing `.env`. A temporary tunnel may receive a different origin when restarted; update the saved tools before launching another bot.

### 5. Start the app

```sh
uv run --locked python -m app.web
```

Keep the app and tunnel running, then open [the launcher](http://127.0.0.1:8006).

### 6. Send the agent to a meeting

Join a meeting on any of the four supported platforms. For a quick test, start one at [meet.new](https://meet.new/). Paste the direct meeting link into the launcher and select **Send agent**. Admit **phonic demo** if prompted.

### 7. Try the agent

Try the chat commands in Zoom, Google Meet, or a Teams meeting with chat enabled. On Webex, try asking a question, sharing a page, stopping the share, and leaving. [Recall chat support](https://docs.recall.ai/docs/sending-chat-messages)

| Try saying |
| --- |
| “Assistant, what is two plus two?” |
| “Assistant, show https://example.com.” |
| “Assistant, send the shared webpage link in chat.” |
| “Assistant, post Hello from the demo in chat.” |
| “Assistant, show https://www.recall.ai.” |
| “Assistant, send that link in chat.” |
| “Assistant, stop sharing.” |
| “Assistant, leave the meeting.” |

Confirm that the new page replaces the previous share, its exact link appears in chat, and voice still works after sharing stops. API acceptance does not prove that the webpage finished loading or that participants saw it.

The session defaults to ten minutes, starting when the launcher requests the bot. Expiration invalidates the tool token and requests departure. Ctrl+C also requests departure for active sessions. If departure cannot be confirmed, check the meeting or Recall dashboard before closing the tunnel. After the bot leaves, stop the app and tunnel. Launch another bot to repeat the demo.

## Code map

| File | Responsibility |
| --- | --- |
| `app/web.py` | Local meeting form and the two server entrypoints. |
| `app/templates/index.html` | One meeting-URL input and launch status. |
| `app/agent.py` | Phonic instructions, native configuration, and saved webhook tool setup. |
| `app/tool_server.py` | Authenticate callbacks, bind them to the launched bot, serialize actions, and expire sessions. |
| `app/meeting_tools.py` | Five actions, chat feedback, and the last accepted page URL. |
| `app/recall_client.py` | Create Bot and the four Recall action endpoints. |
| `app/config.py` | Load and validate environment settings. |

`RecallClient.create_bot()` requests `web_4_core` for each supported platform and sends inline agent settings with `output_media.audio.kind = "phonic"`. Phonic calls the app's callback server to run meeting tools.

Each meeting gets an expiring `session_token` in Phonic's `template_variables`. The saved tools substitute that value into `X-Session-Token`. Recall also supplies `recall_bot_id` and `recall_api_base_url`; the tools forward them as headers. The server checks them against the bot and regional origin already associated with the token. A callback cannot select a different bot or redirect the Recall key.

Tool requests contain their parameters at the top level, alongside Phonic's `call_info`. The server returns a short JSON outcome for the agent. Chat feedback failures never repeat the original action. A failed share preserves the previous accepted URL, and stopping sharing leaves the voice connection available.

## Boundaries and troubleshooting

- If voice fails, check the Phonic key saved in Recall, the project name, and the selected voice. The local setup key does not configure Recall's saved credentials.
- If voice works but actions fail, confirm the app and tunnel are running, the tunnel targets **7866**, and setup used the same Phonic project. Refresh the saved tools after a tunnel change.
- Missing or mismatched session headers are rejected. A restart loses in-memory sessions; launch a new bot rather than reuse the old one.
- Share trusted public HTTPS pages that load without signing in. Check the screen share to confirm the page loaded.
- Messages and shared URLs have a 500-character limit so exact links fit in meeting chat. Stopping sharing preserves the most recently accepted URL.
- Recall and Phonic retain meeting data according to their respective settings.

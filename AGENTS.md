# Working on this sample

Keep this a small, runnable Google Meet demo with one local meeting-URL form.

- Use current Recall MCP documentation and official Phonic documentation to verify integration behavior. Record unsupported capabilities explicitly.
- Use Recall's native Phonic audio integration and saved Phonic webhook tools. Keep exactly five conversational meeting actions: `start_screenshare`, `send_chat_message`, `send_current_page_link`, `stop_screenshare`, and `leave_call`. The user selected this scope with no speaking avatar, mute, or listening controls.
- Join silently and post a short introduction in meeting chat. Requests addressed to Assistant or phonic demo are model instructions unless an enforced input filter is implemented.
- Actions require explicit requests. Post concise chat feedback; requested text and links are their own feedback. Never automatically post a shared page URL or retry uncertain actions.
- Display public HTTPS pages without reading or interacting with them. Preserve the last accepted page URL when a later request fails. Stop only the screen share; leave removes only the bot.
- Keep credentials in the ignored root `.env` or process environment. Never log secrets, callback tokens, request bodies, or meeting content. The model must not choose the Recall bot ID or API destination.
- Keep the launcher local. Authenticate public tool callbacks with an expiring per-meeting token bound to the bot created by this app.
- Use concrete names, keep related logic together, and review responsibilities when files approach 300 lines. Keep agent configuration, meeting actions, and HTTP requests easy to trace.
- Do not add evaluator code, test harnesses, verification instrumentation, or reports to this repository. Run checks externally; the user performs the live meeting demonstration.
- Keep the README aligned with the LiveKit and Pipecat examples: behavior, requirements, numbered quickstart, code map, and concise boundaries/troubleshooting.
- Report local validation separately from observed live meeting behavior.

"""Single Discord posting surface for v2 cron scripts. Bot-token only.

Use post_to_channel(channel_id, msg) from any v2 cron script. Reads the
DISCORD_TOKEN_ORCHESTRATOR env var (same token the v2 scripts already use
for direct bot posting). No webhook fallback — webhooks are decommissioned.

Returns True on a 2xx response, False otherwise (caller decides whether to log).
"""
import os
import requests

_BOT_TOKEN = os.environ.get("DISCORD_TOKEN_ORCHESTRATOR", "")


def post_to_channel(channel_id: str, msg: str) -> bool:
    if not _BOT_TOKEN or not channel_id:
        return False
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={
                "Authorization": f"Bot {_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"content": msg[:1900]},
            timeout=8,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False

"""
Publish the bot's command menus (Telegram setMyCommands).

Everyone sees the member commands; each admin's private chat additionally
lists the admin commands. Menus are only hints: authorization is enforced by
the webhook against SSM admin_ids, never by what a menu shows.

    python scripts/set_bot_menus.py            # preview
    python scripts/set_bot_menus.py --apply    # publish

Run after changing commands or the admin list. Reads the bot token and
admin_ids from SSM. API: https://core.telegram.org/bots/api#setmycommands
"""
from __future__ import annotations

import argparse
import os
import sys

import boto3
import httpx

SSM_PREFIX_DEFAULT = "/skatebot/prod"

MEMBER_COMMANDS = [
    ("start", "Open the bot; book using your session rental button"),
    ("cancel", "Cancel the form you are filling in"),
]
ADMIN_COMMANDS = MEMBER_COMMANDS + [
    ("sendpoll", "Create a new Tuesday session and post its group poll"),
    ("specialevent", "Create a one-off special event and post its sign-up"),
    ("sessions", "List sessions and IDs; add Events or a page number"),
    ("summary", "Show latest session attendance; add Events for events"),
    ("rentals", "Show rentals and remaining stock; optional session ID"),
    ("setsession", "Edit latest session: date, start, end, location"),
    ("skipsession", "Skip the latest session"),
    ("deletesession", "Preview deletion by session ID; CONFIRM to delete"),
    ("importmembers", "Import rules registry: attach XLSX with this caption"),
    ("linkmember", "Link account: Telegram user ID and registered handle"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish Telegram command menus.")
    ap.add_argument("--apply", action="store_true", help="Publish (default: preview only).")
    ap.add_argument("--ssm-prefix", default=os.environ.get("SSM_PREFIX", SSM_PREFIX_DEFAULT))
    args = ap.parse_args()

    ssm = boto3.client("ssm")
    get = lambda name, decrypt=False: ssm.get_parameter(  # noqa: E731
        Name=f"{args.ssm_prefix}/{name}", WithDecryption=decrypt)["Parameter"]["Value"]
    token = get("bot_token", decrypt=True)
    admins = [int(x) for x in get("admin_ids").split(",") if x.strip()]

    plan = [({"type": "default"}, MEMBER_COMMANDS)]
    plan += [({"type": "chat", "chat_id": uid}, ADMIN_COMMANDS) for uid in admins]
    for scope, commands in plan:
        print(f"{scope}: {', '.join('/' + c for c, _ in commands)}")
    if not args.apply:
        print("Preview only. Re-run with --apply to publish.")
        return 0

    # Both the language-neutral menu and the English one (English clients prefer it).
    for scope, commands in plan:
        for language in (None, "en"):
            body = {"scope": scope, "commands": [{"command": c, "description": d} for c, d in commands]}
            if language:
                body["language_code"] = language
            resp = httpx.post(f"https://api.telegram.org/bot{token}/setMyCommands", json=body,
                              timeout=15.0).json()
            if not resp.get("ok"):
                print(f"Telegram rejected {scope} {language or ''}: {resp}", file=sys.stderr)
                return 1
    print("Menus published. Users may need to reopen the chat to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

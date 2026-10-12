"""
Resolve the bot-admin allowlist (numeric Telegram user IDs) from handles.

Authorization uses numeric IDs, never handles. This looks the given handles
up among the SIT group's administrators (getChatAdministrators) and prints
the matching IDs; with --write it stores them in SSM `admin_ids`.

    python scripts/find_admin_ids.py
    python scripts/find_admin_ids.py --write

Reads the bot token from SSM, so run it after scripts/bootstrap-ssm.ps1.
API: https://core.telegram.org/bots/api#getchatadministrators
"""
from __future__ import annotations

import argparse
import os
import sys

import boto3
import httpx

SSM_PREFIX_DEFAULT = "/skatebot/prod"
DEFAULT_HANDLES = [
    "darylt_pc", "yunnnnnns", "Jjiilin", "Its_Joycelyn", "NotDrivingUnderInfluence",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve admin Telegram IDs from handles.")
    ap.add_argument("handles", nargs="*", default=DEFAULT_HANDLES)
    ap.add_argument("--ssm-prefix", default=os.environ.get("SSM_PREFIX", SSM_PREFIX_DEFAULT))
    ap.add_argument("--write", action="store_true", help="Store the IDs in SSM admin_ids.")
    args = ap.parse_args()

    ssm = boto3.client("ssm")
    get = lambda name, decrypt=False: ssm.get_parameter(  # noqa: E731
        Name=f"{args.ssm_prefix}/{name}", WithDecryption=decrypt)["Parameter"]["Value"]
    token = get("bot_token", decrypt=True)
    chat_id = get("group_chat_id")

    resp = httpx.post(f"https://api.telegram.org/bot{token}/getChatAdministrators",
                      json={"chat_id": int(chat_id)}, timeout=15.0).json()
    if not resp.get("ok"):
        print(f"Telegram error: {resp}", file=sys.stderr)
        return 1
    by_handle = {(m["user"].get("username") or "").lower(): m["user"] for m in resp["result"]}

    found, missing = {}, []
    for handle in args.handles:
        user = by_handle.get(handle.lstrip("@").lower())
        if user:
            found[handle] = user["id"]
        else:
            missing.append(handle)
    for handle, uid in found.items():
        print(f"  @{handle:<28} {uid}")
    if missing:
        print("Not admins of the group (resolve manually): " + ", ".join("@" + h for h in missing),
              file=sys.stderr)
        return 1

    ids = ",".join(str(uid) for uid in found.values())
    print(f"admin_ids = {ids}")
    if args.write:
        ssm.put_parameter(Name=f"{args.ssm_prefix}/admin_ids", Value=ids, Type="String", Overwrite=True)
        print("Written to SSM. Lambdas pick it up on their next cold start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

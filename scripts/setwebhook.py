"""
Register (or re-register) the webhook URL with Telegram.

Reads the Function URL from Terraform outputs, reads the webhook secret from
SSM, calls Telegram's setWebhook API. Run locally with AWS_PROFILE set:

    python scripts/setwebhook.py

Also accepts a --url flag if you want to point Telegram at a different URL
(useful for ngrok during local testing).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import boto3
import httpx

SSM_PREFIX_DEFAULT = "/skatebot/prod"


def tf_output(key: str, tf_dir: Path) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", key],
        cwd=str(tf_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def ssm_value(name: str, decrypt: bool = True) -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Register the webhook with Telegram.")
    ap.add_argument(
        "--url",
        help="Override webhook URL (default: read from terraform output webhook_function_url)",
    )
    ap.add_argument(
        "--ssm-prefix",
        default=os.environ.get("SSM_PREFIX", SSM_PREFIX_DEFAULT),
        help="SSM parameter prefix (default: /skatebot/prod)",
    )
    ap.add_argument(
        "--tf-dir",
        default=str(Path(__file__).parent.parent / "infra" / "terraform"),
        help="Path to the Terraform module dir (for reading outputs)",
    )
    args = ap.parse_args()

    url = args.url or tf_output("webhook_function_url", Path(args.tf_dir))
    secret = ssm_value(f"{args.ssm_prefix}/webhook_secret")
    token = ssm_value(f"{args.ssm_prefix}/bot_token")

    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": url,
            "secret_token": secret,
            "drop_pending_updates": True,
            "allowed_updates": ["message", "callback_query"],
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        print(f"Telegram rejected setWebhook: {body}", file=sys.stderr)
        return 1

    print(f"Webhook registered: {url}")
    print("Details:")
    info = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10.0).json()
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

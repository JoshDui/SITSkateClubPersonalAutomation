"""
Aggregate attendance data from the deployed Azure Cosmos DB into a single
Attendance.xlsx workbook on the local filesystem.

Why this exists: the original architecture POSTed attendance JSON from the
exporter Function to a Power Automate flow that wrote rows into a SharePoint
Excel file. That path is dead because the Power Automate HTTP-request
trigger requires a Premium licence the SIT student tenant does not grant,
and the Microsoft Graph alternative is blocked by tenant directory
permissions (`az ad app create` returns "Insufficient privileges").

Pivot: the Function App's exporter now uploads per-session CSVs to Blob
Storage, and this script consolidates the live Cosmos data into one
Excel workbook on demand. See azure/PROGRESS.md "Attendance export pivot"
for the full rationale.

Usage (after `az login`):

    python azure/scripts/build_attendance_report.py
    python azure/scripts/build_attendance_report.py --since 2026-01-01
    python azure/scripts/build_attendance_report.py --out report.xlsx --include-skipped

Auth: uses `DefaultAzureCredential`, which picks up your `az login` token.
The Cosmos data-plane role is granted to the Terraform-running user via
azure/infra/terraform/rbac.tf — if you see 403s on Cosmos reads, run
`terraform apply` to add yourself, or use the manual `az cosmosdb` snippet
documented in azure/PROGRESS.md.

Cosmos endpoint, key vault URL, and table names are auto-discovered from
the deployed Function App's app settings via the Azure CLI. Override with
--function-app / --resource-group, or by exporting COSMOS_ENDPOINT etc.
before running.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Defaults match the deployed prod resource names — see azure/PROGRESS.md.
DEFAULT_FUNCTION_APP = "skatebot-prod-azure-32441"
DEFAULT_RESOURCE_GROUP = "rg-skatebot-prod"

# Env keys the Function App's shared.config requires at import time. We
# inject anything missing from the discovered app settings before importing.
_REQUIRED_ENV = (
    "ENV", "TABLE_MEMBERS", "TABLE_SESSIONS", "TABLE_RESPONSES",
    "COSMOS_ENDPOINT", "KEY_VAULT_URL",
)


def _discover_app_settings(function_app: str, resource_group: str) -> dict[str, str]:
    """Fetch app settings from the deployed Function App via az CLI. Requires
    `az login` to have been run in this shell."""
    cmd = [
        "az", "functionapp", "config", "appsettings", "list",
        "--name", function_app, "--resource-group", resource_group,
        "--output", "json",
    ]
    # shell=True on Windows so `az` (a .cmd shim) resolves on PATH without
    # us having to find its full path. Inputs are not user-controlled.
    use_shell = os.name == "nt"
    result = subprocess.run(
        cmd if not use_shell else " ".join(cmd),
        capture_output=True, text=True, shell=use_shell,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`az functionapp config appsettings list` failed (exit "
            f"{result.returncode}). Stderr: {result.stderr.strip()}\n\n"
            f"Either run `az login` first, or pass --function-app / "
            f"--resource-group to point at the right deployment, or set "
            f"COSMOS_ENDPOINT / TABLE_* env vars manually."
        )
    return {entry["name"]: entry["value"] for entry in json.loads(result.stdout)}


def _ensure_env(function_app: str, resource_group: str) -> None:
    """Populate any of _REQUIRED_ENV that the user hasn't already set."""
    missing = [k for k in _REQUIRED_ENV if k not in os.environ]
    if not missing:
        return
    settings = _discover_app_settings(function_app, resource_group)
    for key in missing:
        if key not in settings:
            raise RuntimeError(
                f"Function App {function_app!r} has no {key!r} app setting. "
                f"Set the env var manually before re-running."
            )
        os.environ[key] = settings[key]


def _bootstrap_imports() -> None:
    """Make `shared.db` importable from the function app source tree."""
    script_dir = Path(__file__).resolve().parent  # azure/scripts/
    sys.path.insert(0, str(script_dir.parent / "functionapp"))


def _build_workbook(rows: list[list[object]]) -> "Workbook":  # noqa: F821 — late import
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"

    headers = [
        "Session Date", "Session Time", "Location",
        "Handle", "Full Name", "Student ID", "Course", "Cluster", "Year",
        "Is SIT Student", "Categories",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in rows:
        ws.append(row)

    # Reasonable column widths so the file opens looking sane in Excel.
    widths = [13, 16, 22, 22, 28, 12, 32, 14, 8, 14, 32]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(ord("A") + i - 1)].width = w

    return wb


def _build_attendees_for_session(session: dict, db) -> list[dict]:
    """Mirror of exporter._build_attendees, kept inline so the script stays
    a single-file utility. Schema must match exporter._CSV_HEADERS so the
    Blob CSVs and the Excel agree row-for-row."""
    responses = db.get_responses(session["id"])

    by_user: dict[int, dict] = {}
    for r in responses:
        tg_id = r["telegram_id"]
        entry = by_user.setdefault(
            tg_id,
            {"handle": r.get("username"), "categories": []},
        )
        cat = r.get("category")
        if cat and cat not in entry["categories"]:
            entry["categories"].append(cat)

    enriched = []
    for entry in by_user.values():
        handle = entry.get("handle")
        member = (db.get_member(handle) if handle else None) or {}
        enriched.append({
            "handle": handle,
            "full_name": member.get("full_name"),
            "student_id": member.get("student_id"),
            "full_course_name": member.get("full_course_name"),
            "cluster": member.get("cluster"),
            "year": member.get("year"),
            "is_sit_student": bool(member.get("is_sit_student", 0)),
            "categories": entry["categories"],
        })
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--out", default="./Attendance.xlsx",
        help="Output xlsx path (default: ./Attendance.xlsx)",
    )
    parser.add_argument(
        "--since",
        help="Only include sessions on/after this YYYY-MM-DD date "
             "(default: 90 days ago)",
    )
    parser.add_argument(
        "--include-skipped", action="store_true",
        help="Include sessions where /skipsession was run (default: filter out)",
    )
    parser.add_argument(
        "--function-app", default=os.environ.get("FUNCTION_APP", DEFAULT_FUNCTION_APP),
        help=f"Function App name for app-settings discovery (default: {DEFAULT_FUNCTION_APP})",
    )
    parser.add_argument(
        "--resource-group",
        default=os.environ.get("AZURE_RESOURCE_GROUP", DEFAULT_RESOURCE_GROUP),
        help=f"Resource group (default: {DEFAULT_RESOURCE_GROUP})",
    )
    args = parser.parse_args()

    if args.since:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
    else:
        since_date = (datetime.now(timezone.utc) - timedelta(days=90)).date()

    _ensure_env(args.function_app, args.resource_group)
    _bootstrap_imports()
    from shared import db  # depends on env vars + sys.path bootstrap above

    sessions = db.list_sessions_since(
        since_date.isoformat(), include_skipped=args.include_skipped,
    )
    if not sessions:
        print(
            f"No sessions found on/after {since_date.isoformat()} "
            f"(include_skipped={args.include_skipped}). Nothing to write."
        )
        return 0

    rows: list[list[object]] = []
    total_attendees = 0
    sessions_with_data = 0
    for session in sessions:
        attendees = _build_attendees_for_session(session, db)
        if not attendees:
            print(f"  skip {session['session_date']} {session['id']} — no responses.")
            continue
        sessions_with_data += 1
        total_attendees += len(attendees)
        session_time = f"{session['start_time']} - {session['end_time']}"
        for a in attendees:
            rows.append([
                session["session_date"],
                session_time,
                session["location"],
                a.get("handle") or "",
                a.get("full_name") or "",
                a.get("student_id") or "",
                a.get("full_course_name") or "",
                a.get("cluster") or "",
                a.get("year") or "",
                "Yes" if a.get("is_sit_student") else "No",
                ", ".join(a.get("categories") or []),
            ])

    workbook = _build_workbook(rows)
    out_path = Path(args.out).resolve()
    workbook.save(out_path)

    print(
        f"Wrote {out_path}: {sessions_with_data}/{len(sessions)} sessions "
        f"with attendance, {total_attendees} attendee-rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

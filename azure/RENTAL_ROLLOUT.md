# Rental bookings and rules registry

## Scope

Azure implementation only. AWS remains unchanged. New booking flow is controlled
by `BOOKINGS_ENABLED=true`, `BOT_USERNAME=SITSkateClubBot`, and
`RULES_FORM_URL=https://forms.cloud.microsoft/r/Q9fETRjdMq`.

The registry is a separate rules acknowledgement dataset, not the trial/freshies
registration sheet. Automatic SharePoint sync is not implemented. An admin must
refresh the workbook snapshot after new form responses before they can qualify.

## User flow

The group retains SIT, non-SIT, and rental choices. Attendance requires an
acknowledged, unambiguous registry match. The rental button opens the bot DM;
opening it does not register attendance. Rental eligibility requires a current
SIT student declaration for each session. The old student/alumni form field is
never used to authorize rentals. Alumni and external participants cannot rent.

EU35–45 buttons show exact and one-size-larger stock. Counts overlap and must not
be summed. Exact size is preferred, +1 requires acceptance, and other sizes go
to the admin review list without reserving stock. Guards are informational.
Confirmation reserves a pair and registers SIT attendance atomically. Failed
changes retain the old pair. Cancel rental preserves attendance; cancel
attendance releases the rental. Booking changes close at the session start.

Initial inventory (pairs): 35=2, 36=0, 37=8, 38=1, 39=10, 40=9, 41=9,
42=6, 43=6, 44=5, 45=4. Total60. Inventory is per session and assumes physical
rentals are returned between non-overlapping sessions. No automatic downsizing.

## Admin operations (private chat)

- `/sessions [PAGE_NUMBER]` lists session dates, statuses, and exact IDs (eight
  per page). Past and upcoming sessions can be selected; deleted sessions are hidden.
- `/deletesession SESSION_ID` previews one specific session, attendance/rental
  counts, and the consequences. It makes no changes. Send the displayed
  `/deletesession SESSION_ID CONFIRM` command to delete that exact session.
  There is deliberately no destructive `latest` shortcut or bulk-delete option.
  Deletion is recoverable within the database: records are retained with an
  audit marker and excluded from normal bot queries. Bookings are frozen under
  the same optimistic-concurrency protection as reservations, and queued exports
  stop. No member profiles or rules acknowledgements are removed.
  The bot attempts to delete the original poll; if Telegram refuses, it attempts
  a closed notice without buttons. If an old session lacks its original chat ID,
  remove that message manually: the bot will not guess a destructive target.
  Existing CSV/Excel reports remain unchanged; an export already running may
  finish. Deleted Telegram messages cannot be restored. A database restore
  requires developer assistance; there is no restore command in this release.
  Deleting an upcoming session allows the regular scheduler to create a replacement.
- Send the latest `.xlsx` file with `/importmembers` as its caption, or reply
  `/importmembers` to the uploaded document. Only configured admins can import.
- `/rentals [SESSION_ID]` lists requested/allocated sizes, guards, remaining
  pairs, and requests needing review. Without an ID it uses the latest session.
- `/linkmember TELEGRAM_USER_ID REGISTERED_HANDLE` links an existing acknowledged,
  unambiguous record to a verified Telegram user when their handle has changed.
  Existing claims cannot be transferred silently.
- Missing/invalid handles in the workbook are not imported. Ask those people to
  resubmit the form with their current valid handle and refresh the workbook.
  Do not treat a typed Telegram handle as proof of identity.
- Unusual size allocations remain a manual admin conversation; there is no
  admin inventory adjustment or forced-allocation command in this release.

Imports upsert records, not full snapshot replacement: absent rows are not
deleted/revoked. Changing a handle's name/student identity flags it as ambiguous
and blocks automatic use, including on later reimports, pending administrator
investigation. Full-name/ID spelling corrections can therefore need review.
The first Telegram account matching an unclaimed username can claim it; the
historical Excel form is not cryptographic proof of Telegram identity.

## Data and reliability

Booking stock, declarations and attendance share one ETag-protected document
per session in the existing sessions table's `BOOKING` partition. Concurrent
confirmations recheck stock. Update IDs suppress duplicates; menu versions
reject old confirmations. JSON payloads above60kB UTF-16 are rejected rather
than truncated. Member profiles and claims use separate member-table partitions.
Existing exported sessions remain unchanged; new sessions use the new flow.

The group poll refresh is retried through webhook503 responses if Telegram
editing fails. Public lists may truncate names to fit Telegram limits, but
counts stay complete. `/rentals` lists full rental details privately.

## Initial registry

Supplied file: `C:\UniPain\SkateClubStuff\Updated SIT Inline Skate Club Rules.xlsx`.
On2026-09-26:126 rows;118 unique usable acknowledged handles imported;8 invalid
rows skipped (Excel rows62,82,96,98,99,101,102,118). Source workbook unchanged.
No masked NRIC or email fields are stored by this importer.

## Verification and release

- Run `python -m pytest azure/tests -q -p no:cacheprovider` at repository root.
- `azure/scripts/registry_refresh.py WORKBOOK` validates; `--apply` imports.
  Requires authorized Azure credentials and non-secret Function App settings.
- `azure/scripts/booking_storage_probe.py` verifies actual ETag protection using
  one isolated booking row, which it removes afterward. No poll is sent.
- Publish from `azure/functionapp` with
  `func azure functionapp publish skatebot-prod-azure-32441 --python --build remote`.
- Enable settings only after registry and storage checks pass. Verify webhook
  rejects a missing secret and accepts an authenticated empty update.
- User acceptance still requires a real Telegram rental click, size confirmation,
  visible poll update, and cancellation. Do not send unsolicited test group polls.

After new bookings exist, do NOT simply disable `BOOKINGS_ENABLED`: legacy
attendance writes use a different responses table. Rollback must preserve/read
the booking documents and be planned against any active sessions. Never delete
booking or member data as a deployment rollback.

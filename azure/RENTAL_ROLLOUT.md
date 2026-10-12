# Rental bookings and rules registry

## Scope

Azure implementation only. AWS remains unchanged. New booking flow is controlled
by `BOOKINGS_ENABLED=true`, `BOT_USERNAME=SITSkateClubBot`, and
`RULES_FORM_URL=https://forms.cloud.microsoft/r/Q9fETRjdMq`.

The registry is a separate rules acknowledgement dataset, not the trial/freshies
registration sheet. Automatic SharePoint sync is not implemented. Imports now
provide optional report enrichment; they are not a participation requirement.

## User flow

Both groups retain SIT, non-SIT, and rental choices. As of 2026-09-28, attendance
and rules-form completion are accepted on declaration, without registry matching.
External participants must still complete the form; the bot does not verify it.
Missing usernames, missing entries and ambiguous entries do not block participation.
The rental button opens the bot DM;
opening it does not register attendance. Rental eligibility requires a current
SIT student declaration for each session. The old student/alumni form field is
never used to authorize rentals. Alumni and external participants cannot rent.
Selecting external attendance invalidates an older current-student declaration.
Historical rules links and Check again buttons provide guidance, not verification.
Existing open polls adopt this policy without recreation; new/refreshed poll
text explains it. No existing bookings or previously exported reports are rewritten.
New CSV and Excel reports derive student status from attendance/rental categories,
not the registry; without a registry name they use the Telegram display name.

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

### Two-group trial (2026-09-27)

- `/sendpoll` (or `/sendpoll SIT`) creates the upcoming Tuesday poll in the
  existing SIT topic. `/sendpoll 8 Wheel Clan` creates the upcoming Thursday
  poll in 8 Wheel Clan General. Names are case-insensitive; `8wheel` is an alias.
  Unknown names fail closed. Each invocation creates a new session; do not repeat
  the command to look up an existing booking. On the event day after its start
  time, the command selects the following week.
- Both use 18:30–21:30, SIT @ Punggol Coast, Asia/Singapore. Thursday posting
  is manual only; the weekly scheduler considers only SIT sessions.
- `/summary 8 Wheel Clan`, `/rentals 8 Wheel Clan`, `/skipsession 8 Wheel Clan`,
  and `/sessions 8 Wheel Clan` select the Thursday group. Without a name these
  select SIT. Pagination: `/sessions 8 Wheel Clan 2`. `/rentals SESSION_ID`
  and `/deletesession SESSION_ID [CONFIRM]` still address one exact session.
- To edit Thursday's latest session:
  `/setsession 2026-10-01 18:30 21:30 SIT @ Punggol Coast --group 8 Wheel Clan`.
  Existing `/setsession` syntax without `--group` remains SIT-only. This command
  saves details but does not immediately refresh the old poll text.
- Every new session snapshots group name/key, chat ID and topic (zero means
  General). Replies, updates, links and deletion use its stored poll reference.
  Historical sessions without a group key remain SIT; no bulk migration occurs.
- Configure `EIGHT_WHEEL_CHAT_ID=-1002958151210` to enable the verified destination.
  Leave it unset to disable new Thursday polls. Never repoint the existing SIT
  secrets to the Thursday group. General omits `message_thread_id` entirely.
- Attendance and reservations are independent per session; the same physical
  60 pairs are assumed returned between Tuesday and Thursday. Do not create
  overlapping events or duplicate polls that could independently reserve them.
  This trial does not implement cross-event stock allocation or return tracking.
- Declaration-based participation and the five-account admin allowlist are
  shared. Real Thursday attendance uses the existing per-session CSV export;
  CSV columns stay unchanged and each event has a distinct session ID/file.

All admin commands require a numeric Telegram user ID in the Key Vault
`admin-ids` allowlist and the sender's own private chat. Usernames, display
names, forwarded messages, and Telegram group-admin status do not grant access.
On 2026-09-26 the approved accounts are @darylt_pc, @yunnnnnns, @Jjiilin,
@Its_Joycelyn, and @NotDrivingUnderInfluence; their IDs were verified against
Telegram's live group-administrator records. A username change does not change
the account's access. Changing `admin-ids` requires restarting the Function App
because Key Vault values are cached per worker.

Admin command menus are scoped to those accounts' private chats. Public/default
menus contain only `/start`. Menus aid discovery, not authorization: manually
typed admin commands still go through the server-side allowlist. Ordinary
members retain `/start`, attendance buttons, and rental deep links. Admins must
start the bot privately before Telegram can show their private command menu.
Existing messages/screenshots cannot be made secret retroactively.

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
and blocks automatic identity linking, including on later reimports, pending
administrator investigation. It does not block attendance or rentals.
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
- Registry counts are informational, not eligibility gates. Verify webhook
  rejects a missing secret and accepts an authenticated empty update.
- User acceptance still requires a real Telegram rental click, size confirmation,
  visible poll update, and cancellation. Do not send unsolicited test group polls.

After new bookings exist, do NOT simply disable `BOOKINGS_ENABLED`: legacy
attendance writes use a different responses table. Rollback must preserve/read
the booking documents and be planned against any active sessions. Never delete
booking or member data as a deployment rollback.

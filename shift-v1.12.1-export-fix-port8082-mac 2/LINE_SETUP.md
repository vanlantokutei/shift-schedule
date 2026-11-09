# Weekly LINE Kibo registration reminder

## Publish a reviewed schedule

The weekly board also has a `Chốt lịch & gửi LINE` button for authenticated
managers. It opens a preview of the exact week being viewed and names the
paired recipient group. The dialog discloses the number of remaining Kibo
wish shifts; confirming approves these shifts as official and sends the PNG.
Wages and rates are never selected for the image. This action is independent
of the Monday registration reminder and its enabled/disabled state.

The server compares the preview's content hash before confirming. A changed
schedule or recipient requires a new preview. Confirmed snapshots are stored
with a content-based key and a stable LINE retry key. Double submissions of
an accepted version do not send again; edited schedules can be sent as an
update. If LINE fails after confirmation, the UI explains that the shifts
were confirmed but the message was not acknowledged, and allows retrying the
same persisted payload. Uncertain requests older than 24 hours are not retried.

## Employee name linking through LINE

`/admin/line/identity` configures a LINE Login channel and LIFF app under the
same provider as Messaging API (`2005791100`). Use the `openid` scope only;
no profile or email scope is required. LIFF endpoint:
`https://shift-schedule-1fk2.onrender.com/kibo/line`.
The public policy is `/line/privacy`. Configure and publish the LINE Login
channel before saving its public Channel ID and LIFF ID in the owner page.

The LIFF entry page sends a raw ID token to the server over HTTPS. The server
verifies it using LINE's `/oauth2/v2.1/verify` endpoint and checks issuer,
audience, expiry and subject. Client-supplied profile objects are ignored.
Tokens are not persisted or logged. The database retains LINE user ID,
employee ID and link timestamp; unique constraints prevent duplicate claims.
An employee explicitly selects their own name once. This is self-selection,
not independent proof of employment. Managers can remove mistaken links
without changing shifts or Kibo submissions. Employees can switch to another
unclaimed active name after fresh LINE verification.

When configured, registration reminder links open LIFF. New members of the
paired group receive a welcome button through LINE's signed join event.
Existing group members can open the LIFF registration link at any time.
Verified employee sessions preselect the associated name, while normal web
registration remains available without LINE. Provider IDs and channel IDs
must not be mixed across unrelated providers.

## Bot configuration

The owner page `/admin/line` configures bot `@323txucc`. Delivery is disabled
until the owner saves the channel credentials, pairs one group, and enables it.

1. Enable Messaging API and group invitations for the LINE Official Account.
2. Save its Channel secret and Channel access token directly in `/admin/line`.
   Secrets are encrypted at rest using a key derived from the application's
   stable session secret; neither secrets nor chat contents are logged.
3. Set the LINE webhook URL to
   `https://shift-schedule-1fk2.onrender.com/line/webhook` and enable webhooks.
4. Invite the bot to the staff group. Generate a pairing code on `/admin/line`
   and send `/lich CODE` in that group within 30 minutes. A signed webhook
   consumes the code once. No other group messages are saved or answered.
5. Reload the page, verify the group name, check the reminder preview, and enable reminders.

The web process checks every 30 seconds. At 10:00 Monday Asia/Tokyo it sends
one text reminder to register availability for the following Monday–Sunday.
The reminder uses the real Kibo registration window, including manager deadline
extensions, and links to `/kibo?week=YYYY-MM-DD` for that target week.
It does not read shift or payroll rows, render images, or require a completed
schedule. No reminder is sent if registration is not open.

After outages it recovers later on Monday, but never sends the previous week's
reminder on Tuesday. An always-running web service is required; no extra cron
service is used. Set `LINE_SCHEDULER_DISABLED=1` in development to disable the
worker. The owner's test button sends one text with a “GỬI THỬ” label. It has
its own idempotency key and does not consume the scheduled Monday delivery.

PostgreSQL session advisory locks serialize workers and instances. Each week
has a unique persisted delivery, immutable reminder payload and stable UUID retry
key. Failed requests retry every five minutes on Monday. HTTP 200 or an
HTTP 409 with `x-line-accepted-request-id` means LINE accepted the request;
this does not prove every group member viewed or received the image.

Previous image exports remain readable until their seven-day expiration;
new automatic and test deliveries contain only text. Re-pairing disables
reminders; a pending delivery cannot be redirected to or sent to a different
group. A bot leave event clears the paired group and disables reminders.

Tests: `python -m pytest -q test_line_schedule.py test_manager_auth.py test_kibo_deadlines.py test_staff_archive.py`
Tests mock LINE HTTP calls and use a local SQL adapter; they do not send messages.


## Private reminders for missing Kibo

Owner control: `/admin/line/private`. Requires both the master reminder switch and the private reminder switch. Each active employee is addressed using their verified `line_staff_links` association, never a display-name guess. They must add @323txucc as a friend to receive push notifications. Signed follow/unfollow events track known blocks. API acceptance is not proof of delivery.

The 30-second worker checks the final four hours before the effective deadline in Asia/Tokyo, including extended windows crossing into another calendar week. Submitted records (including all days off) and legacy wish rows suppress reminders; fixed prefills do not count as submission. Each person/week has one durable job and a stable retry key. Retry every five minutes while still eligible and before close; never retry after 23 hours from the first attempt. Fresh eligibility is checked before sending. An attempted job is stopped if its recipient or deadline changes, because a timed-out call may already have delivered; it is never retargeted. An already accepted job is never sent again after extension.

Tests use fake LINE HTTP; no live private messages are sent by the test suite.

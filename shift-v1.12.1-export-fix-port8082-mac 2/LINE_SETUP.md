# Weekly LINE Kibo registration reminder

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

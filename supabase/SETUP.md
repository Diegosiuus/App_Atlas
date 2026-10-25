# Supabase setup for account review and shared training

Run migrations `0001_initial_schema.sql`, `0002_player_game_results.sql`,
`0003_usernames_review_and_training.sql`, and then
`0004_tournament_format.sql` and `0005_archive_discontinued_saturdays.sql` in
order in the Supabase SQL Editor. Migration 0004 preserves existing results,
archives legacy events (while recognizing eligible 12-hour regular events),
and adds experimental fields without deleting the old play-time column.
Migration 0005 ensures discontinued last-Saturday events are labeled as legacy
and archived, including on databases that applied an earlier 0004 draft.

After you have signed up and confirmed your own account, grant that account
administrator access from the SQL Editor. Replace the email with the exact
email used for your account:

```sql
insert into public.app_admins (user_id)
select id from auth.users where lower(email) = lower('YOUR_EMAIL')
on conflict (user_id) do nothing;
```

The query should insert exactly one row. Do not add the Supabase `service_role`
key to the app or its `.env`; the app uses the publishable key and user access
tokens, while privileged work is restricted to database functions that check
the authenticated user's admin row.

Admin import accepts anonymous whitespace-separated rows in this order:
game, date, position, victories, total event coins, coins earned, duration in
minutes, optional losses (0-10), and optional activity phase (`inicio`,
`medio`, `final`). Only regular 12-hour tournaments on Monday, Tuesday,
Thursday, or Sunday are accepted. Legacy data, including last-Saturday events,
is retained but archived and excluded from predictions. The local-registry import filters out
all rows except eligible new-format events. Imported observations are
deduplicated by source key. A user's own result stays private and pending
until approved; only the approved rank, wins, losses, phase, game, date, and
coin pool are copied to model tables. Losses and phase are experimental and do
not currently alter predictions.

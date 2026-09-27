# Supabase setup for account review and shared training

Run `0003_usernames_review_and_training.sql` in the Supabase SQL Editor after
the existing migrations `0001_initial_schema.sql` and
`0002_player_game_results.sql`. This adds public handles, result moderation,
anonymized observations, schedule validation, and administrator-only RPCs.

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

Admin import accepts whitespace-separated rows in the same column order as
`registro_juegos.txt`: game, date, position, victories, total event coins,
coins earned, duration in minutes, and an optional player label. Player labels
are ignored. The app also offers an import of its local registry, likewise
without importing player names. Imported observations are deduplicated by
source key. A user's own result stays private and pending until approved; only
the approved rank, wins, game, date, event duration, and coin pool are copied
to model tables.

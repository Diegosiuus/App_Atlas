# Supabase setup for account review and shared training

Run migrations `0001_initial_schema.sql`, `0002_player_game_results.sql`,
`0003_usernames_review_and_training.sql`, `0004_tournament_format.sql`,
`0005_archive_discontinued_saturdays.sql`, `0006_admin_event_forms_and_tiers.sql`,
`0007_fix_admin_result_rejection.sql`, `0008_enforce_tournament_rotation.sql`,
`0009_top_three_individual_awards.sql`, `0010_store_top_three_without_training.sql`,
`0011_player_history_and_training_scope.sql`, and finally
`0012_support_feedback.sql`, in that order in the Supabase SQL Editor.
Migration 0004 preserves existing results,
archives legacy events (while recognizing eligible 12-hour regular events),
and adds experimental fields without deleting the old play-time column.
Migration 0005 ensures discontinued last-Saturday events are labeled as legacy
and archived, including on databases that applied an earlier 0004 draft.
Migration 0006 adds event-level aggregate coin awards for the five rank bands
and admin-only form RPCs. Its initial approval prerequisite is superseded by
migration 0011.
Migration 0007 fixes the admin rejection path so a result can be rejected without
event pool or tier data and the database trigger does not reset it to pending.
Migration 0008 enforces the game rotation anchored at 5 April 2026 (Warship),
archives invalid active event rows without deleting them, and limits current
regular tournaments to one active event per date. Admin event data is loaded and
updated by date; player and admin forms report rotation mismatches before saving.
Migration 0009 adds separate awards for places 1, 2, and 3 to each event. Existing
events retain their five aggregate bands and must have the three individual awards
filled in before they are considered complete for observation entry or approval.
Migration 0010 was superseded by 0011. Migration 0011 limits manual anonymous
observations to positions 25, 50, 100, 500, and 1500; lets players record any
positive final position; and stores zero coins for positions above 1500. Player
results are visible in "Mis partidas" only after admin approval. Approval is not
blocked by missing event prize data. Approved player results from positions 4–1500
are included anonymously in training when the event data is complete; results at
positions 1–3 or above 1500 remain only in the player's history. If the event is
completed after approval, those eligible results are synchronized automatically.
Migration 0012 adds private support reports, user submission limits, and
admin-only report review/status functions.

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

In the Admin screen, save the event date, total coin pool, the individual awards
for places 1, 2, and 3, and the aggregate coins for each rank band. The five band
amounts represent the coins awarded collectively to players in that band; they
are not individual payouts. Then add anonymous observations individually or
review submitted player results.
Regular tournaments on Monday, Tuesday, Thursday, or Sunday are accepted.
Legacy data, including discontinued Saturday events, is retained but excluded
from predictions. A user's result stays private and pending until approved;
only approved result data is copied to the anonymous model table.

To enable email alerts for support reports, verify a sender domain with Resend,
then link the Supabase CLI to this project and configure the function secrets:

```powershell
supabase link --project-ref YOUR_PROJECT_REF
supabase secrets set RESEND_API_KEY=re_your_key SUPPORT_INBOX_EMAIL=you@example.com SUPPORT_FROM_EMAIL="Atlas Earth Calc <reports@your-verified-domain.com>"
supabase functions deploy notify-support-ticket
```

The Resend key, inbox address, and sender address belong only in Supabase Edge
Function secrets. Never put the service-role key or Resend key in `.env`, the
public app configuration, or GitHub. Reports remain stored and visible in Admin
even while email delivery is not configured or temporarily unavailable.

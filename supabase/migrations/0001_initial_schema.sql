begin;

create table if not exists public.profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    common_parcels integer not null default 0 check (common_parcels >= 0),
    rare_parcels integer not null default 0 check (rare_parcels >= 0),
    epic_parcels integer not null default 0 check (epic_parcels >= 0),
    legendary_parcels integer not null default 0 check (legendary_parcels >= 0),
    badge_count integer not null default 0 check (badge_count >= 0),
    normal_boost_hours_per_day numeric(4, 2) not null default 0
        check (normal_boost_hours_per_day between 0 and 24),
    srb_boost_hours_per_month numeric(5, 2) not null default 0
        check (srb_boost_hours_per_month between 0 and 64),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
    before update on public.profiles
    for each row execute function public.set_updated_at();

create table if not exists public.minigame_events (
    id bigint generated always as identity primary key,
    game text not null check (
        game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling', 'Fishing(V)', 'Racer(V)')
    ),
    event_date date not null,
    duration_minutes integer not null check (duration_minutes > 0),
    total_coins bigint not null check (total_coins > 0),
    archived boolean not null default false,
    created_at timestamptz not null default now(),
    unique (game, event_date)
);

create table if not exists public.minigame_observations (
    id bigint generated always as identity primary key,
    event_id bigint not null references public.minigame_events (id) on delete cascade,
    position integer not null check (position between 4 and 1500),
    victories integer not null check (victories >= 0),
    coins_earned bigint check (coins_earned is null or coins_earned >= 0),
    created_at timestamptz not null default now()
);

create index if not exists minigame_events_date_game_idx
    on public.minigame_events (event_date, game);
create index if not exists minigame_observations_event_position_idx
    on public.minigame_observations (event_id, position);

-- Player identity and play time are private; global samples above contain no names.
create table if not exists public.player_game_sessions (
    id bigint generated always as identity primary key,
    user_id uuid not null references auth.users (id) on delete cascade,
    event_id bigint not null references public.minigame_events (id) on delete cascade,
    victories integer not null check (victories >= 0),
    played_minutes numeric(8, 2) not null check (played_minutes > 0),
    created_at timestamptz not null default now()
);

create index if not exists player_game_sessions_user_event_idx
    on public.player_game_sessions (user_id, event_id);

alter table public.profiles enable row level security;
alter table public.minigame_events enable row level security;
alter table public.minigame_observations enable row level security;
alter table public.player_game_sessions enable row level security;

drop policy if exists "Users can read their own profile" on public.profiles;
create policy "Users can read their own profile"
    on public.profiles for select to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists "Users can create their own profile" on public.profiles;
create policy "Users can create their own profile"
    on public.profiles for insert to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists "Users can update their own profile" on public.profiles;
create policy "Users can update their own profile"
    on public.profiles for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists "Authenticated users can read minigame events" on public.minigame_events;
create policy "Authenticated users can read minigame events"
    on public.minigame_events for select to authenticated
    using (true);

drop policy if exists "Authenticated users can read minigame observations" on public.minigame_observations;
create policy "Authenticated users can read minigame observations"
    on public.minigame_observations for select to authenticated
    using (true);

drop policy if exists "Users can read their own game sessions" on public.player_game_sessions;
create policy "Users can read their own game sessions"
    on public.player_game_sessions for select to authenticated
    using ((select auth.uid()) = user_id);

drop policy if exists "Users can create their own game sessions" on public.player_game_sessions;
create policy "Users can create their own game sessions"
    on public.player_game_sessions for insert to authenticated
    with check ((select auth.uid()) = user_id);

drop policy if exists "Users can update their own game sessions" on public.player_game_sessions;
create policy "Users can update their own game sessions"
    on public.player_game_sessions for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

drop policy if exists "Users can delete their own game sessions" on public.player_game_sessions;
create policy "Users can delete their own game sessions"
    on public.player_game_sessions for delete to authenticated
    using ((select auth.uid()) = user_id);

grant select, insert, update on public.profiles to authenticated;
grant select on public.minigame_events to authenticated;
grant select on public.minigame_observations to authenticated;
grant select, insert, update, delete on public.player_game_sessions to authenticated;
grant usage, select on sequence public.player_game_sessions_id_seq to authenticated;

commit;

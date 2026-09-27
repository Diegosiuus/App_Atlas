begin;

-- Personal results can be saved without a global minigame_events row.
alter table public.player_game_sessions
    alter column event_id drop not null;

alter table public.player_game_sessions
    add column if not exists game text,
    add column if not exists event_date date,
    add column if not exists final_position integer;

update public.player_game_sessions as sessions
set game = events.game,
    event_date = events.event_date
from public.minigame_events as events
where sessions.event_id = events.id
  and (sessions.game is null or sessions.event_date is null);

alter table public.player_game_sessions
    alter column game set not null,
    alter column event_date set not null;

alter table public.player_game_sessions
    drop constraint if exists player_game_sessions_game_check,
    add constraint player_game_sessions_game_check
        check (game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling', 'Fishing(V)', 'Racer(V)')),
    drop constraint if exists player_game_sessions_final_position_check,
    add constraint player_game_sessions_final_position_check
        check (final_position is null or final_position between 1 and 1500);

create index if not exists player_game_sessions_user_game_date_idx
    on public.player_game_sessions (user_id, game, event_date desc);
create unique index if not exists player_game_sessions_user_event_unique_idx
    on public.player_game_sessions (user_id, game, event_date);

commit;

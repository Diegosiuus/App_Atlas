begin;

-- Compatibility for databases that already applied the earlier 0004 draft.
alter table public.minigame_events
    drop constraint if exists minigame_events_event_format_check;
update public.minigame_events
set event_format = 'legacy', archived = true
where event_format = 'saturday_pending';
alter table public.minigame_events
    add constraint minigame_events_event_format_check
    check (event_format in ('legacy', 'regular_tournament'));

create or replace function public.check_event_schedule()
returns trigger language plpgsql set search_path = '' as $$
begin
    if new.event_format = 'regular_tournament'
       and not public.validate_game_schedule(new.game, new.event_date, new.duration_minutes) then
        raise exception 'El torneo regular debe ser de 12 h y celebrarse en una fecha válida.';
    end if;
    return new;
end;
$$;

commit;

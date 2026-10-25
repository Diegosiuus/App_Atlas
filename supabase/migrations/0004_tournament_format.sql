begin;

alter table public.minigame_events
    add column if not exists event_format text not null default 'legacy';
alter table public.minigame_events drop constraint if exists minigame_events_event_format_check;
alter table public.minigame_events add constraint minigame_events_event_format_check
    check (event_format in ('legacy', 'regular_tournament'));

-- Preserve every pre-existing row, but keep legacy formats out of new forecasts.
update public.minigame_events
set event_format = case
        when duration_minutes = 720
            and extract(isodow from event_date) in (1, 2, 4, 7)
            and game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling')
            then 'regular_tournament'
        else 'legacy'
    end,
    archived = not (
        duration_minutes = 720
        and extract(isodow from event_date) in (1, 2, 4, 7)
        and game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling')
    );

alter table public.player_game_sessions
    alter column played_minutes drop not null,
    add column if not exists losses integer,
    add column if not exists activity_phase text;
alter table public.player_game_sessions drop constraint if exists player_game_sessions_review_status_check;
alter table public.player_game_sessions add constraint player_game_sessions_review_status_check
    check (review_status in ('pending', 'approved', 'rejected', 'archived'));
update public.player_game_sessions
set review_status = 'archived', reviewed_at = now(),
    review_note = 'Resultado conservado del formato anterior; no se incorpora al modelo de torneos.'
where played_minutes is not null and review_status <> 'archived'
  and exists (
      select 1 from public.minigame_events e
      where e.id = player_game_sessions.event_id and e.event_format <> 'regular_tournament'
  );
alter table public.player_game_sessions drop constraint if exists player_game_sessions_losses_check;
alter table public.player_game_sessions add constraint player_game_sessions_losses_check
    check (losses is null or losses between 0 and 10);
alter table public.player_game_sessions drop constraint if exists player_game_sessions_activity_phase_check;
alter table public.player_game_sessions add constraint player_game_sessions_activity_phase_check
    check (activity_phase is null or activity_phase in ('inicio', 'medio', 'final'));

alter table public.minigame_observations
    add column if not exists losses integer,
    add column if not exists activity_phase text;
alter table public.minigame_observations drop constraint if exists minigame_observations_losses_check;
alter table public.minigame_observations add constraint minigame_observations_losses_check
    check (losses is null or losses between 0 and 10);
alter table public.minigame_observations drop constraint if exists minigame_observations_activity_phase_check;
alter table public.minigame_observations add constraint minigame_observations_activity_phase_check
    check (activity_phase is null or activity_phase in ('inicio', 'medio', 'final'));

create or replace function public.validate_game_schedule(p_game text, p_date date, p_duration integer)
returns boolean language plpgsql stable set search_path = 'pg_catalog' as $$
begin
    return p_date <= current_date
        and extract(isodow from p_date) in (1, 2, 4, 7)
        and p_duration = 720
        and p_game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling');
end;
$$;

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

drop trigger if exists minigame_events_schedule_check on public.minigame_events;
create trigger minigame_events_schedule_check before insert or update on public.minigame_events
for each row execute function public.check_event_schedule();

create or replace function public.check_player_result_schedule()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    if tg_op = 'DELETE' then
        delete from public.minigame_observations where source_key = 'player-session-' || old.id;
        return old;
    end if;
    if new.event_date > current_date then raise exception 'No se aceptan resultados futuros.'; end if;
    if not (
        extract(isodow from new.event_date) in (1, 2, 4, 7)
        and new.game in ('Racer', 'Fishing', 'Golf', 'Warship', 'Bowling')
    ) then raise exception 'Solo se admiten torneos regulares confirmados.'; end if;
    if tg_op = 'UPDATE' and current_setting('app.admin_review', true) = 'true'
       and public.is_app_admin() then return new; end if;
    if tg_op = 'UPDATE' and old.review_status = 'approved' then
        delete from public.minigame_observations where source_key = 'player-session-' || old.id;
    end if;
    new.review_status := 'pending';
    new.reviewed_at := null;
    new.review_note := null;
    return new;
end;
$$;

drop trigger if exists player_result_schedule_check on public.player_game_sessions;
create trigger player_result_schedule_check before insert or update or delete on public.player_game_sessions
for each row execute function public.check_player_result_schedule();

drop function if exists public.admin_pending_game_results();
create function public.admin_pending_game_results()
returns table(id bigint, game text, event_date date, victories integer, final_position integer,
    played_minutes numeric, losses integer, activity_phase text, username text)
language plpgsql security definer set search_path = '' as $$
begin
    if not public.is_app_admin() then raise exception 'Permiso de administrador requerido.'; end if;
    return query select s.id, s.game, s.event_date, s.victories, s.final_position,
        s.played_minutes, s.losses, s.activity_phase, p.username
    from public.player_game_sessions s join public.profiles p using(user_id)
    where s.review_status = 'pending' order by s.event_date, s.game, s.id;
end;
$$;

create or replace function public.admin_review_game_result(
    p_session_id bigint, p_approve boolean, p_total_coins bigint default null,
    p_duration_minutes integer default null, p_note text default null)
returns void language plpgsql security definer set search_path = '' as $$
declare sample public.player_game_sessions%rowtype; event_key bigint;
begin
    if not public.is_app_admin() then raise exception 'Permiso de administrador requerido.'; end if;
    perform set_config('app.admin_review', 'true', true);
    select * into sample from public.player_game_sessions where id = p_session_id for update;
    if not found then raise exception 'Resultado no encontrado.'; end if;
    if not p_approve then
        update public.player_game_sessions set review_status='rejected', reviewed_at=now(), review_note=left(p_note,500)
        where id=p_session_id;
        return;
    end if;
    if sample.final_position is null or sample.final_position not between 4 and 1500 then
        raise exception 'Solo se entrenan puestos entre 4 y 1500.';
    end if;
    if p_total_coins is null or p_total_coins <= 0 then raise exception 'Indica la bolsa total de monedas.'; end if;
    if not public.validate_game_schedule(sample.game, sample.event_date, p_duration_minutes) then
        raise exception 'El evento debe ser un torneo regular válido de 12 h.';
    end if;
    insert into public.minigame_events(game,event_date,duration_minutes,total_coins,archived,event_format)
    values(sample.game,sample.event_date,720,p_total_coins,false,'regular_tournament')
    on conflict(game,event_date) do update set duration_minutes=excluded.duration_minutes,
        total_coins=excluded.total_coins,archived=false,event_format='regular_tournament'
    where minigame_events.event_format='regular_tournament'
      and minigame_events.duration_minutes=720 and minigame_events.total_coins=excluded.total_coins
    returning id into event_key;
    if event_key is null then raise exception 'La bolsa no coincide con el evento registrado.'; end if;
    update public.player_game_sessions set review_status='approved',reviewed_at=now(),review_note=left(p_note,500)
    where id=p_session_id;
    insert into public.minigame_observations(event_id,position,victories,source_type,source_key,losses,activity_phase)
    values(event_key,sample.final_position,sample.victories,'player','player-session-'||sample.id,
        sample.losses,sample.activity_phase)
    on conflict(source_key) where source_key is not null do nothing;
end;
$$;

create or replace function public.admin_import_model_rows(p_rows jsonb)
returns integer language plpgsql security definer set search_path = '' as $$
declare row jsonb; event_key bigint; added integer := 0; g text; d date; dur integer; pool bigint;
begin
    if not public.is_app_admin() then raise exception 'Permiso de administrador requerido.'; end if;
    if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows)>5000 then
        raise exception 'La carga debe ser una lista de hasta 5000 filas.';
    end if;
    for row in select value from jsonb_array_elements(p_rows) loop
        g := row->>'game'; d := (row->>'event_date')::date;
        dur := (row->>'duration_minutes')::integer; pool := (row->>'total_coins')::bigint;
        if not public.validate_game_schedule(g,d,dur) then raise exception 'Solo se importan torneos regulares de 12 h: % %',g,d; end if;
        if (row->>'position')::integer not between 4 and 1500 or (row->>'victories')::integer < 0 or pool <= 0 then
            raise exception 'Puesto, victorias o bolsa inválidos.';
        end if;
        if nullif(row->>'losses','') is not null and (row->>'losses')::integer not between 0 and 10 then
            raise exception 'Las derrotas deben estar entre 0 y 10.';
        end if;
        if nullif(row->>'activity_phase','') is not null and row->>'activity_phase' not in ('inicio','medio','final') then
            raise exception 'Franja de actividad inválida.';
        end if;
        insert into public.minigame_events(game,event_date,duration_minutes,total_coins,archived,event_format)
        values(g,d,720,pool,false,'regular_tournament') on conflict(game,event_date) do update
        set total_coins=excluded.total_coins
        where minigame_events.event_format='regular_tournament' and minigame_events.duration_minutes=720
          and minigame_events.total_coins=excluded.total_coins returning id into event_key;
        if event_key is null then raise exception 'Evento duplicado o inconsistente: % %.',g,d; end if;
        insert into public.minigame_observations(event_id,position,victories,coins_earned,source_type,source_key,losses,activity_phase)
        values(event_key,(row->>'position')::integer,(row->>'victories')::integer,
            nullif(row->>'coins_earned','')::bigint,'admin',coalesce(nullif(row->>'source_key',''),
            md5(concat_ws('|',g,d,dur,pool,row->>'position',row->>'victories'))),
            nullif(row->>'losses','')::integer,nullif(row->>'activity_phase',''))
        on conflict(source_key) where source_key is not null do nothing;
        if found then added := added+1; end if;
    end loop;
    return added;
end;
$$;

revoke all on function public.admin_pending_game_results() from public,anon;
revoke all on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) from public,anon;
revoke all on function public.admin_import_model_rows(jsonb) from public,anon;
grant execute on function public.admin_pending_game_results() to authenticated;
grant execute on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) to authenticated;
grant execute on function public.admin_import_model_rows(jsonb) to authenticated;

commit;

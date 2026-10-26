begin;

create or replace function public.expected_regular_game(p_date date)
returns text
language plpgsql
stable
set search_path = ''
as $$
declare
    event_offset integer;
    game_index integer;
begin
    if extract(isodow from p_date) not in (1, 2, 4, 7) then
        return null;
    end if;

    if p_date >= date '2026-04-05' then
        select count(*)::integer into event_offset
        from generate_series(date '2026-04-06', p_date, interval '1 day') as days(day_value)
        where extract(isodow from day_value) in (1, 2, 4, 7);
        game_index := (3 + event_offset) % 5;
    else
        select count(*)::integer into event_offset
        from generate_series(p_date + 1, date '2026-04-05', interval '1 day') as days(day_value)
        where extract(isodow from day_value) in (1, 2, 4, 7);
        game_index := (3 - event_offset % 5 + 5) % 5;
    end if;

    return (array['Racer', 'Fishing', 'Golf', 'Warship', 'Bowling'])[game_index + 1];
end;
$$;

create or replace function public.validate_game_schedule(p_game text, p_date date, p_duration integer)
returns boolean
language sql
stable
set search_path = ''
as $$
    select p_date <= current_date
       and p_duration = 720
       and p_game = public.expected_regular_game(p_date);
$$;

-- Preserve erroneous/duplicate records as legacy history, but keep them out
-- of current predictions and free their dates for one canonical active event.
update public.minigame_events
set event_format = 'legacy', archived = true
where event_format = 'regular_tournament'
  and not archived
  and game is distinct from public.expected_regular_game(event_date);

alter table public.minigame_events
    drop constraint if exists minigame_events_game_event_date_key;

create unique index if not exists minigame_events_one_regular_event_per_date
    on public.minigame_events (event_date)
    where event_format = 'regular_tournament' and not archived;

create or replace function public.check_event_schedule()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.event_format = 'regular_tournament'
       and not public.validate_game_schedule(new.game, new.event_date, new.duration_minutes) then
        raise exception 'El minijuego no corresponde a la rotación de esa fecha; se esperaba %.',
            public.expected_regular_game(new.event_date);
    end if;
    return new;
end;
$$;

drop trigger if exists minigame_events_schedule_check on public.minigame_events;
create trigger minigame_events_schedule_check
    before insert or update on public.minigame_events
    for each row execute function public.check_event_schedule();

create or replace function public.check_player_result_schedule()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if tg_op = 'DELETE' then
        delete from public.minigame_observations
        where source_key = 'player-session-' || old.id;
        return old;
    end if;

    if not public.validate_game_schedule(new.game, new.event_date, 720) then
        raise exception 'El minijuego no corresponde a la rotación de esa fecha; se esperaba %.',
            public.expected_regular_game(new.event_date);
    end if;

    if tg_op = 'UPDATE'
       and current_setting('app.admin_review', true) = 'true'
       and public.is_app_admin() then
        return new;
    end if;

    if tg_op = 'UPDATE' and old.review_status = 'approved' then
        delete from public.minigame_observations
        where source_key = 'player-session-' || old.id;
    end if;
    new.review_status := 'pending';
    new.reviewed_at := null;
    new.review_note := null;
    return new;
end;
$$;

drop trigger if exists player_result_schedule_check on public.player_game_sessions;
create trigger player_result_schedule_check
    before insert or update or delete on public.player_game_sessions
    for each row execute function public.check_player_result_schedule();

create or replace function public.admin_save_event_data(
    p_game text,
    p_event_date date,
    p_duration_minutes integer,
    p_total_coins bigint,
    p_tiers jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    event_key bigint;
    tier jsonb;
    tier_sum bigint := 0;
    tier_count integer := 0;
    tier_min integer;
    tier_max integer;
    tier_coins bigint;
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    if not public.validate_game_schedule(p_game, p_event_date, p_duration_minutes) then
        raise exception 'El minijuego no corresponde a la rotación de esa fecha; se esperaba %.',
            public.expected_regular_game(p_event_date);
    end if;
    if p_total_coins is null or p_total_coins <= 0 then
        raise exception 'La bolsa total debe ser mayor que cero.';
    end if;
    if p_tiers is null or jsonb_typeof(p_tiers) <> 'array' or jsonb_array_length(p_tiers) <> 5 then
        raise exception 'Debes indicar los cinco tramos de puestos.';
    end if;

    for tier in select value from jsonb_array_elements(p_tiers) loop
        tier_min := (tier->>'position_min')::integer;
        tier_max := (tier->>'position_max')::integer;
        tier_coins := (tier->>'coins_awarded')::bigint;
        if (tier_min, tier_max) not in ((4,25),(26,50),(51,100),(101,500),(501,1500))
           or tier_coins is null or tier_coins < 0 then
            raise exception 'Hay un tramo o una cantidad de monedas no válida.';
        end if;
        tier_count := tier_count + 1;
        tier_sum := tier_sum + tier_coins;
    end loop;
    if tier_count <> 5 or tier_sum > p_total_coins or exists (
        select 1 from jsonb_array_elements(p_tiers) as entries(value)
        group by entries.value->>'position_min', entries.value->>'position_max'
        having count(*) > 1
    ) then
        raise exception 'La suma de los premios por tramo no puede superar la bolsa total.';
    end if;

    select id into event_key
    from public.minigame_events
    where event_date = p_event_date
      and event_format = 'regular_tournament'
      and not archived
    for update;

    if event_key is null then
        insert into public.minigame_events(
            game, event_date, duration_minutes, total_coins, archived, event_format
        ) values (
            p_game, p_event_date, p_duration_minutes, p_total_coins, false, 'regular_tournament'
        ) returning id into event_key;
    else
        update public.minigame_events
        set game = p_game,
            duration_minutes = p_duration_minutes,
            total_coins = p_total_coins,
            archived = false,
            event_format = 'regular_tournament'
        where id = event_key;
    end if;

    delete from public.minigame_event_tier_awards where event_id = event_key;
    for tier in select value from jsonb_array_elements(p_tiers) loop
        insert into public.minigame_event_tier_awards(event_id, position_min, position_max, coins_awarded)
        values (event_key, (tier->>'position_min')::integer, (tier->>'position_max')::integer,
                (tier->>'coins_awarded')::bigint);
    end loop;
    return event_key;
end;
$$;

create or replace function public.admin_import_model_rows(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    row jsonb;
    event_key bigint;
    added integer := 0;
    game_name text;
    saved_game text;
    event_day date;
    event_duration integer;
    event_pool bigint;
    saved_pool bigint;
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) > 5000 then
        raise exception 'La carga debe ser una lista de hasta 5000 filas.';
    end if;

    for row in select value from jsonb_array_elements(p_rows) loop
        game_name := row->>'game';
        event_day := (row->>'event_date')::date;
        event_duration := (row->>'duration_minutes')::integer;
        event_pool := (row->>'total_coins')::bigint;
        if not public.validate_game_schedule(game_name, event_day, event_duration) then
            raise exception 'Evento fuera de la rotación: % %; se esperaba %.',
                game_name, event_day, public.expected_regular_game(event_day);
        end if;
        if (row->>'position')::integer not between 4 and 1500
           or (row->>'victories')::integer < 0 or event_pool <= 0 then
            raise exception 'Puesto, victorias o bolsa inválidos.';
        end if;
        if nullif(row->>'losses','') is not null
           and (row->>'losses')::integer not between 0 and 10 then
            raise exception 'Las derrotas deben estar entre 0 y 10.';
        end if;
        if nullif(row->>'activity_phase','') is not null
           and row->>'activity_phase' not in ('inicio','medio','final') then
            raise exception 'Franja de actividad inválida.';
        end if;

        select id, game, total_coins into event_key, saved_game, saved_pool
        from public.minigame_events
        where event_date = event_day
          and event_format = 'regular_tournament'
          and not archived
        for update;
        if event_key is null then
            insert into public.minigame_events(
                game, event_date, duration_minutes, total_coins, archived, event_format
            ) values (
                game_name, event_day, event_duration, event_pool, false, 'regular_tournament'
            ) returning id into event_key;
        elsif saved_game is distinct from game_name or saved_pool is distinct from event_pool then
            raise exception 'El minijuego o la bolsa no coincide con el evento registrado en %.', event_day;
        end if;

        insert into public.minigame_observations(
            event_id, position, victories, coins_earned, source_type, source_key, losses, activity_phase
        ) values (
            event_key,
            (row->>'position')::integer,
            (row->>'victories')::integer,
            nullif(row->>'coins_earned','')::bigint,
            'admin',
            coalesce(nullif(row->>'source_key',''),
                md5(concat_ws('|', game_name, event_day, event_duration, event_pool,
                              row->>'position', row->>'victories'))),
            nullif(row->>'losses','')::integer,
            nullif(row->>'activity_phase','')
        ) on conflict (source_key) where source_key is not null do nothing;
        if found then
            added := added + 1;
        end if;
    end loop;
    return added;
end;
$$;

drop function if exists public.admin_pending_game_results();
create function public.admin_pending_game_results()
returns table(
    id bigint, game text, event_date date, victories integer, final_position integer,
    played_minutes numeric, losses integer, activity_phase text, username text,
    total_coins bigint, event_ready boolean
)
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    return query
    select s.id, s.game, s.event_date, s.victories, s.final_position, s.played_minutes,
        s.losses, s.activity_phase, p.username, e.total_coins,
        coalesce(e.event_format = 'regular_tournament' and not e.archived
            and e.duration_minutes = 720
            and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 5,
            false)
    from public.player_game_sessions s
    join public.profiles p using (user_id)
    left join public.minigame_events e
        on e.game = s.game and e.event_date = s.event_date
        and e.event_format = 'regular_tournament' and not e.archived
    where s.review_status = 'pending'
    order by s.event_date, s.game, s.id;
end;
$$;

revoke all on function public.expected_regular_game(date) from public, anon;
grant execute on function public.expected_regular_game(date) to authenticated;
revoke all on function public.admin_save_event_data(text,date,integer,bigint,jsonb) from public, anon;
revoke all on function public.admin_import_model_rows(jsonb) from public, anon;
revoke all on function public.admin_pending_game_results() from public, anon;
grant execute on function public.admin_save_event_data(text,date,integer,bigint,jsonb) to authenticated;
grant execute on function public.admin_import_model_rows(jsonb) to authenticated;
grant execute on function public.admin_pending_game_results() to authenticated;

commit;

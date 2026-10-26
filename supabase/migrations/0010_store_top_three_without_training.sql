begin;

alter table public.minigame_observations
    drop constraint if exists minigame_observations_position_check;
alter table public.minigame_observations
    add constraint minigame_observations_position_check
    check (position between 1 and 1500);

create or replace function public.admin_add_observation(
    p_game text,
    p_event_date date,
    p_position integer,
    p_victories integer,
    p_losses integer default null,
    p_activity_phase text default null,
    p_coins_earned bigint default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    event_key bigint;
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    if p_position is null or p_position not between 1 and 1500
       or p_victories is null or p_victories < 0 then
        raise exception 'Puesto o victorias no válidos.';
    end if;

    select e.id into event_key
    from public.minigame_events e
    where e.game = p_game and e.event_date = p_event_date
      and e.event_format = 'regular_tournament' and not e.archived
      and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 8;
    if event_key is null then
        raise exception 'Guarda primero la bolsa, los tres premios individuales y los cinco tramos.';
    end if;

    insert into public.minigame_observations(
        event_id, position, victories, coins_earned, source_type, source_key, losses, activity_phase
    ) values (
        event_key, p_position, p_victories, null, 'admin',
        'admin-manual-' || event_key || '-' || p_position, null, null
    ) on conflict (source_key) where source_key is not null do nothing;
    if not found then
        raise exception 'Ya existe una observación para ese puesto y evento.';
    end if;
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
        if (row->>'position')::integer not between 1 and 1500
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

revoke all on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) from public, anon;
revoke all on function public.admin_import_model_rows(jsonb) from public, anon;
grant execute on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) to authenticated;
grant execute on function public.admin_import_model_rows(jsonb) to authenticated;

commit;

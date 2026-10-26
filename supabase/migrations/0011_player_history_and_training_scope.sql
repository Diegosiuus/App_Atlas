begin;

alter table public.player_game_sessions
    drop constraint if exists player_game_sessions_final_position_check,
    add constraint player_game_sessions_final_position_check
        check (final_position is null or final_position >= 1),
    add column if not exists coins_earned bigint
        check (coins_earned is null or coins_earned >= 0);

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
    if p_position is null or p_position not in (25, 50, 100, 500, 1500)
       or p_victories is null or p_victories < 0 then
        raise exception 'Las observaciones anónimas solo admiten los puestos 25, 50, 100, 500 y 1500.';
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
        if (row->>'position')::integer not in (25, 50, 100, 500, 1500)
           or (row->>'victories')::integer < 0 or event_pool <= 0 then
            raise exception 'Las observaciones anónimas deben usar un puesto límite, victorias válidas y una bolsa positiva.';
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

    new.coins_earned := case
        when new.final_position > 1500 then 0
        else null
    end;
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

create or replace function public.sync_approved_player_observations(p_event_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not exists (
        select 1 from public.minigame_events e
        where e.id = p_event_id and e.event_format = 'regular_tournament'
          and not e.archived and e.duration_minutes = 720
          and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 8
    ) then
        return;
    end if;

    insert into public.minigame_observations(
        event_id, position, victories, coins_earned, source_type, source_key, losses, activity_phase
    )
    select e.id, s.final_position, s.victories, null, 'player',
        'player-session-' || s.id, s.losses, s.activity_phase
    from public.minigame_events e
    join public.player_game_sessions s
      on s.game = e.game and s.event_date = e.event_date
    where e.id = p_event_id and s.review_status = 'approved'
      and s.final_position between 4 and 1500
    on conflict (source_key) where source_key is not null do update
    set event_id = excluded.event_id,
        position = excluded.position,
        victories = excluded.victories,
        losses = excluded.losses,
        activity_phase = excluded.activity_phase;
end;
$$;

create or replace function public.sync_approved_player_observations_from_award()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    perform public.sync_approved_player_observations(new.event_id);
    return new;
end;
$$;

drop trigger if exists sync_approved_player_observations_on_award
    on public.minigame_event_tier_awards;
create trigger sync_approved_player_observations_on_award
    after insert or update on public.minigame_event_tier_awards
    for each row execute function public.sync_approved_player_observations_from_award();

create or replace function public.admin_review_game_result(
    p_session_id bigint,
    p_approve boolean,
    p_total_coins bigint default null,
    p_duration_minutes integer default null,
    p_note text default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    sample public.player_game_sessions%rowtype;
    event_key bigint;
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    select * into sample
    from public.player_game_sessions
    where id = p_session_id
    for update;
    if not found then
        raise exception 'Resultado no encontrado.';
    end if;

    perform set_config('app.admin_review', 'true', true);
    if not p_approve then
        update public.player_game_sessions
        set review_status = 'rejected', reviewed_at = now(), review_note = left(p_note, 500)
        where id = p_session_id;
        return;
    end if;

    if sample.final_position is null or sample.final_position < 1 then
        raise exception 'El resultado debe incluir un puesto final igual o superior a 1.';
    end if;
    if not public.validate_game_schedule(sample.game, sample.event_date, 720) then
        raise exception 'El evento no corresponde al calendario regular de 12 h.';
    end if;

    update public.player_game_sessions
    set review_status = 'approved', reviewed_at = now(), review_note = left(p_note, 500)
    where id = p_session_id;

    if sample.final_position between 4 and 1500 then
        select e.id into event_key
        from public.minigame_events e
        where e.game = sample.game and e.event_date = sample.event_date
          and e.event_format = 'regular_tournament' and not e.archived
          and e.duration_minutes = 720
          and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 8;
        if event_key is not null then
            perform public.sync_approved_player_observations(event_key);
        end if;
    end if;
end;
$$;

revoke all on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) from public, anon;
revoke all on function public.admin_import_model_rows(jsonb) from public, anon;
revoke all on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) from public, anon;
revoke all on function public.sync_approved_player_observations(bigint) from public, anon, authenticated;
revoke all on function public.sync_approved_player_observations_from_award() from public, anon, authenticated;
revoke all on function public.check_player_result_schedule() from public, anon, authenticated;
grant execute on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) to authenticated;
grant execute on function public.admin_import_model_rows(jsonb) to authenticated;
grant execute on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) to authenticated;

commit;

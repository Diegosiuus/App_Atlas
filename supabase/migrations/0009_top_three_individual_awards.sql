begin;

alter table public.minigame_event_tier_awards
    drop constraint if exists minigame_event_tier_awards_bracket_check;
alter table public.minigame_event_tier_awards
    add constraint minigame_event_tier_awards_bracket_check check (
        (position_min, position_max) in (
            (1,1), (2,2), (3,3),
            (4,25), (26,50), (51,100), (101,500), (501,1500)
        )
    );

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
    award jsonb;
    award_sum bigint := 0;
    award_count integer := 0;
    position_min integer;
    position_max integer;
    coins bigint;
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
    if p_tiers is null or jsonb_typeof(p_tiers) <> 'array' or jsonb_array_length(p_tiers) <> 8 then
        raise exception 'Debes indicar los premios del top 1–3 y los cinco tramos.';
    end if;

    for award in select value from jsonb_array_elements(p_tiers) loop
        position_min := (award->>'position_min')::integer;
        position_max := (award->>'position_max')::integer;
        coins := (award->>'coins_awarded')::bigint;
        if (position_min, position_max) not in (
            (1,1), (2,2), (3,3),
            (4,25), (26,50), (51,100), (101,500), (501,1500)
        ) or coins is null or coins < 0 then
            raise exception 'Hay un puesto, tramo o premio no válido.';
        end if;
        award_count := award_count + 1;
        award_sum := award_sum + coins;
    end loop;
    if award_count <> 8 or award_sum > p_total_coins or exists (
        select 1
        from jsonb_array_elements(p_tiers) as entries(value)
        group by entries.value->>'position_min', entries.value->>'position_max'
        having count(*) > 1
    ) then
        raise exception 'Los ocho premios deben ser únicos y su suma no puede superar la bolsa total.';
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
    for award in select value from jsonb_array_elements(p_tiers) loop
        insert into public.minigame_event_tier_awards(
            event_id, position_min, position_max, coins_awarded
        ) values (
            event_key,
            (award->>'position_min')::integer,
            (award->>'position_max')::integer,
            (award->>'coins_awarded')::bigint
        );
    end loop;
    return event_key;
end;
$$;

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
    if p_position is null or p_position not between 4 and 1500
       or p_victories is null or p_victories < 0 then
        raise exception 'Puesto o victorias no válidos.';
    end if;
    if p_losses is not null and p_losses not between 0 and 10 then
        raise exception 'Las derrotas deben estar entre 0 y 10.';
    end if;
    if p_activity_phase is not null and p_activity_phase not in ('inicio','medio','final') then
        raise exception 'La franja no es válida.';
    end if;
    if p_coins_earned is not null and p_coins_earned < 0 then
        raise exception 'Las monedas individuales no pueden ser negativas.';
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
        event_key, p_position, p_victories, p_coins_earned, 'admin',
        'admin-manual-' || event_key || '-' || p_position, p_losses, p_activity_phase
    ) on conflict (source_key) where source_key is not null do nothing;
    if not found then
        raise exception 'Ya existe una observación para ese puesto y evento.';
    end if;
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
            and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 8,
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
    saved_pool bigint;
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

    if not p_approve then
        perform set_config('app.admin_review', 'true', true);
        update public.player_game_sessions
        set review_status = 'rejected', reviewed_at = now(), review_note = left(p_note, 500)
        where id = p_session_id;
        return;
    end if;

    if sample.final_position is null or sample.final_position not between 4 and 1500 then
        raise exception 'Solo se entrenan puestos entre 4 y 1500.';
    end if;
    if not public.validate_game_schedule(sample.game, sample.event_date, 720)
       or p_duration_minutes is distinct from 720 then
        raise exception 'El evento debe ser un torneo regular de 12 h.';
    end if;

    select e.id, e.total_coins into event_key, saved_pool
    from public.minigame_events e
    where e.game = sample.game and e.event_date = sample.event_date
      and e.event_format = 'regular_tournament' and not e.archived
      and e.duration_minutes = 720
      and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 8;
    if event_key is null then
        raise exception 'Guarda primero la bolsa, los tres premios individuales y los cinco tramos.';
    end if;
    if p_total_coins is distinct from saved_pool then
        raise exception 'La bolsa guardada del evento no coincide.';
    end if;

    perform set_config('app.admin_review', 'true', true);
    update public.player_game_sessions
    set review_status = 'approved', reviewed_at = now(), review_note = left(p_note, 500)
    where id = p_session_id;

    insert into public.minigame_observations(
        event_id, position, victories, source_type, source_key, losses, activity_phase
    ) values (
        event_key, sample.final_position, sample.victories, 'player',
        'player-session-' || sample.id, sample.losses, sample.activity_phase
    ) on conflict (source_key) where source_key is not null do nothing;
end;
$$;

revoke all on function public.admin_save_event_data(text,date,integer,bigint,jsonb) from public, anon;
revoke all on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) from public, anon;
revoke all on function public.admin_pending_game_results() from public, anon;
revoke all on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) from public, anon;
grant execute on function public.admin_save_event_data(text,date,integer,bigint,jsonb) to authenticated;
grant execute on function public.admin_add_observation(text,date,integer,integer,integer,text,bigint) to authenticated;
grant execute on function public.admin_pending_game_results() to authenticated;
grant execute on function public.admin_review_game_result(bigint,boolean,bigint,integer,text) to authenticated;

commit;

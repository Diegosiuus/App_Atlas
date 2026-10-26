begin;

-- The player-result trigger resets ordinary updates to pending. Mark both
-- admin review outcomes so the trigger preserves rejected as well as approved.
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
        set review_status = 'rejected',
            reviewed_at = now(),
            review_note = left(p_note, 500)
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
    where e.game = sample.game
      and e.event_date = sample.event_date
      and e.event_format = 'regular_tournament'
      and not e.archived
      and e.duration_minutes = 720
      and (select count(*) from public.minigame_event_tier_awards t where t.event_id = e.id) = 5;

    if event_key is null then
        raise exception 'Guarda primero la bolsa y los cinco tramos del evento.';
    end if;
    if p_total_coins is distinct from saved_pool then
        raise exception 'La bolsa guardada del evento no coincide.';
    end if;

    perform set_config('app.admin_review', 'true', true);
    update public.player_game_sessions
    set review_status = 'approved',
        reviewed_at = now(),
        review_note = left(p_note, 500)
    where id = p_session_id;

    insert into public.minigame_observations(
        event_id, position, victories, source_type, source_key, losses, activity_phase
    ) values (
        event_key,
        sample.final_position,
        sample.victories,
        'player',
        'player-session-' || sample.id,
        sample.losses,
        sample.activity_phase
    )
    on conflict (source_key) where source_key is not null do nothing;
end;
$$;

revoke all on function public.admin_review_game_result(bigint, boolean, bigint, integer, text)
    from public, anon;
grant execute on function public.admin_review_game_result(bigint, boolean, bigint, integer, text)
    to authenticated;

commit;

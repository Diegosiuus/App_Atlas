begin;

alter table public.profiles add column if not exists username text;
update public.profiles set username = 'usuario_' || left(replace(user_id::text, '-', ''), 8)
where username is null;
alter table public.profiles alter column username set not null;
alter table public.profiles drop constraint if exists profiles_username_format_check;
alter table public.profiles add constraint profiles_username_format_check
    check (username ~ '^[a-zA-Z0-9._-]{3,24}$');
create unique index if not exists profiles_username_lower_unique
    on public.profiles (lower(username));

create or replace function public.create_profile_for_auth_user()
returns trigger language plpgsql security definer set search_path = '' as $$
declare handle text;
begin
    handle := new.raw_user_meta_data ->> 'username';
    if handle is null or handle !~ '^[a-zA-Z0-9._-]{3,24}$' then
        raise exception 'El nombre de usuario debe tener entre 3 y 24 caracteres (letras, números, punto, guion o guion bajo).';
    end if;
    insert into public.profiles(user_id, username) values (new.id, handle);
    return new;
end;
$$;
drop trigger if exists auth_user_create_profile on auth.users;
create trigger auth_user_create_profile after insert on auth.users
for each row execute function public.create_profile_for_auth_user();

create table if not exists public.app_admins (
    user_id uuid primary key references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);
alter table public.app_admins enable row level security;
revoke all on public.app_admins from anon, authenticated;

create or replace function public.is_app_admin()
returns boolean language sql stable security definer set search_path = '' as $$
    select exists(select 1 from public.app_admins where user_id = (select auth.uid()));
$$;
grant execute on function public.is_app_admin() to authenticated;
revoke all on function public.is_app_admin() from public, anon;
grant execute on function public.is_app_admin() to authenticated;

alter table public.player_game_sessions
    add column if not exists review_status text not null default 'pending',
    add column if not exists reviewed_at timestamptz,
    add column if not exists review_note text;
alter table public.player_game_sessions drop constraint if exists player_game_sessions_review_status_check;
alter table public.player_game_sessions add constraint player_game_sessions_review_status_check
    check (review_status in ('pending','approved','rejected'));

alter table public.minigame_observations
    add column if not exists source_type text not null default 'archive',
    add column if not exists source_key text;
alter table public.minigame_observations drop constraint if exists minigame_observations_source_type_check;
alter table public.minigame_observations add constraint minigame_observations_source_type_check
    check (source_type in ('archive','admin','player'));
create unique index if not exists minigame_observations_source_key_unique
    on public.minigame_observations(source_key) where source_key is not null;
create unique index if not exists minigame_events_racer_variant_day_unique
    on public.minigame_events(event_date) where game in ('Racer','Racer(V)');
create unique index if not exists minigame_events_fishing_variant_day_unique
    on public.minigame_events(event_date) where game in ('Fishing','Fishing(V)');

create or replace function public.validate_game_schedule(p_game text, p_date date, p_duration integer)
returns boolean language plpgsql stable set search_path = 'pg_catalog' as $$
declare last_sat boolean;
begin
    if p_date > current_date then return false; end if;
    -- Last Saturday is the Saturday whose following week enters a new month.
    last_sat := extract(isodow from p_date) = 6 and extract(month from p_date + 7) <> extract(month from p_date);
    if p_game = 'Racer(V)' then return last_sat and p_duration = 60; end if;
    if p_game = 'Fishing(V)' then return last_sat and p_duration = 60; end if;
    if last_sat then return p_duration = 60 and p_game in ('Racer','Golf','Warship','Bowling'); end if;
    return extract(isodow from p_date) in (1,2,4,7)
        and p_duration in (120,180)
        and p_game in ('Racer','Fishing','Golf','Warship','Bowling');
end;
$$;

create or replace function public.check_event_schedule()
returns trigger language plpgsql set search_path = '' as $$
begin
    if not public.validate_game_schedule(new.game, new.event_date, new.duration_minutes) then
        raise exception 'Fecha, minijuego o duración no válida para el calendario.';
    end if;
    return new;
end;
$$;
drop trigger if exists minigame_events_schedule_check on public.minigame_events;
create trigger minigame_events_schedule_check before insert or update on public.minigame_events
for each row execute function public.check_event_schedule();

create or replace function public.check_player_result_schedule()
returns trigger language plpgsql security definer set search_path = '' as $$
declare duration integer;
begin
    if tg_op = 'DELETE' then
        delete from public.minigame_observations
        where source_key = 'player-session-' || old.id;
        return old;
    end if;
    if new.event_date > current_date then raise exception 'No se aceptan resultados futuros.'; end if;
    if new.game = 'Racer(V)' then raise exception 'Racer(V) está archivado.'; end if;
    duration := case when extract(isodow from new.event_date) = 6
        and extract(month from new.event_date + 7) <> extract(month from new.event_date) then 60 else 180 end;
    if not public.validate_game_schedule(new.game, new.event_date, duration) then
        raise exception 'Fecha o minijuego no válido para el calendario.';
    end if;
    if tg_op = 'UPDATE' and current_setting('app.admin_review', true) = 'true'
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
create trigger player_result_schedule_check before insert or update or delete on public.player_game_sessions
for each row execute function public.check_player_result_schedule();

drop policy if exists "Users can create their own game sessions" on public.player_game_sessions;
create policy "Users can create their own game sessions" on public.player_game_sessions
for insert to authenticated with check ((select auth.uid()) = user_id);
drop policy if exists "Users can update their own game sessions" on public.player_game_sessions;
create policy "Users can update their own game sessions" on public.player_game_sessions
for update to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create or replace function public.admin_pending_game_results()
returns table(id bigint, game text, event_date date, victories integer, final_position integer,
    played_minutes numeric, username text)
language plpgsql security definer set search_path = '' as $$
begin
    if not public.is_app_admin() then raise exception 'Permiso de administrador requerido.'; end if;
    return query select s.id,s.game,s.event_date,s.victories,s.final_position,s.played_minutes,p.username
    from public.player_game_sessions s join public.profiles p using(user_id)
    where s.review_status='pending' order by s.event_date,s.game,s.id;
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
    select * into sample from public.player_game_sessions where id=p_session_id for update;
    if not found then raise exception 'Resultado no encontrado.'; end if;
    update public.player_game_sessions set review_status=case when p_approve then 'approved' else 'rejected' end,
        reviewed_at=now(), review_note=left(p_note,500) where id=p_session_id;
    if not p_approve then return; end if;
    if sample.final_position is null or sample.final_position < 4 then
        raise exception 'Solo se entrenan puestos entre 4 y 1500.';
    end if;
    if p_total_coins is null or p_total_coins <= 0 then raise exception 'Indica la bolsa total de monedas.'; end if;
    if not public.validate_game_schedule(sample.game,sample.event_date,p_duration_minutes) then
        raise exception 'Duración o calendario no válido.';
    end if;
    insert into public.minigame_events(game,event_date,duration_minutes,total_coins)
    values(sample.game,sample.event_date,p_duration_minutes,p_total_coins)
    on conflict(game,event_date) do update set duration_minutes=excluded.duration_minutes,total_coins=excluded.total_coins
    where minigame_events.duration_minutes=excluded.duration_minutes
      and minigame_events.total_coins=excluded.total_coins
    returning id into event_key;
    if event_key is null then raise exception 'La bolsa o duración no coincide con el evento ya registrado.'; end if;
    insert into public.minigame_observations(event_id,position,victories,source_type,source_key)
    values(event_key,sample.final_position,sample.victories,'player','player-session-'||sample.id)
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
        g := row->>'game'; d := (row->>'event_date')::date; dur := (row->>'duration_minutes')::integer;
        pool := (row->>'total_coins')::bigint;
        if not public.validate_game_schedule(g,d,dur) then raise exception 'Evento fuera del calendario: % %',g,d; end if;
        if (row->>'position')::integer not between 4 and 1500 or (row->>'victories')::integer < 0 or pool <= 0 then
            raise exception 'Puesto, victorias o bolsa inválidos.';
        end if;
        insert into public.minigame_events(game,event_date,duration_minutes,total_coins,archived)
        values(g,d,dur,pool,g='Racer(V)') on conflict(game,event_date) do update
        set duration_minutes=excluded.duration_minutes,total_coins=excluded.total_coins,archived=excluded.archived
        where minigame_events.duration_minutes=excluded.duration_minutes
          and minigame_events.total_coins=excluded.total_coins
        returning id into event_key;
        if event_key is null then raise exception 'Datos inconsistentes para el evento % %.',g,d; end if;
        insert into public.minigame_observations(event_id,position,victories,coins_earned,source_type,source_key)
        values(event_key,(row->>'position')::integer,(row->>'victories')::integer,
            nullif(row->>'coins_earned','')::bigint,'admin',coalesce(nullif(row->>'source_key',''),
            md5(concat_ws('|',g,d,dur,pool,row->>'position',row->>'victories'))))
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

begin;

create table if not exists public.support_tickets (
    id bigint generated always as identity primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    category text not null check (category in ('bug', 'idea', 'other')),
    subject text not null check (char_length(subject) between 3 and 120),
    message text not null check (char_length(message) between 10 and 4000),
    contact_email text check (contact_email is null or char_length(contact_email) <= 254),
    status text not null default 'new' check (status in ('new', 'reviewing', 'resolved')),
    created_at timestamptz not null default now()
);

create index if not exists support_tickets_created_at_idx
    on public.support_tickets (created_at desc);
create index if not exists support_tickets_user_created_idx
    on public.support_tickets (user_id, created_at desc);

alter table public.support_tickets enable row level security;
revoke all on public.support_tickets from anon, authenticated;
grant select on public.support_tickets to authenticated;

drop policy if exists "Users and admins can read support tickets" on public.support_tickets;
create policy "Users and admins can read support tickets"
    on public.support_tickets for select to authenticated
    using ((select auth.uid()) = user_id or public.is_app_admin());

create or replace function public.submit_support_ticket(
    p_category text,
    p_subject text,
    p_message text,
    p_contact_email text default null
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    ticket_id bigint;
    clean_subject text := btrim(p_subject);
    clean_message text := btrim(p_message);
    clean_email text := nullif(btrim(p_contact_email), '');
begin
    if auth.uid() is null then
        raise exception 'Inicia sesión para enviar un reporte.';
    end if;
    if p_category not in ('bug', 'idea', 'other') then
        raise exception 'Tipo de reporte no válido.';
    end if;
    if char_length(clean_subject) not between 3 and 120 then
        raise exception 'El asunto debe tener entre 3 y 120 caracteres.';
    end if;
    if char_length(clean_message) not between 10 and 4000 then
        raise exception 'El mensaje debe tener entre 10 y 4000 caracteres.';
    end if;
    if clean_email is not null and (
        char_length(clean_email) > 254
        or clean_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
    ) then
        raise exception 'El correo de contacto no es válido.';
    end if;
    if (
        select count(*) from public.support_tickets t
        where t.user_id = auth.uid() and t.created_at > now() - interval '24 hours'
    ) >= 5 then
        raise exception 'Has alcanzado el límite de 5 reportes cada 24 horas.';
    end if;

    insert into public.support_tickets(user_id, category, subject, message, contact_email)
    values (auth.uid(), p_category, clean_subject, clean_message, clean_email)
    returning id into ticket_id;
    return ticket_id;
end;
$$;

create or replace function public.admin_list_support_tickets()
returns table(
    id bigint,
    category text,
    subject text,
    message text,
    contact_email text,
    status text,
    created_at timestamptz,
    username text
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
    select t.id, t.category, t.subject, t.message, t.contact_email, t.status,
        t.created_at, p.username
    from public.support_tickets t
    left join public.profiles p on p.user_id = t.user_id
    order by t.created_at desc
    limit 100;
end;
$$;

create or replace function public.admin_update_support_ticket(p_ticket_id bigint, p_status text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.is_app_admin() then
        raise exception 'Permiso de administrador requerido.';
    end if;
    if p_status not in ('new', 'reviewing', 'resolved') then
        raise exception 'Estado del reporte no válido.';
    end if;
    update public.support_tickets set status = p_status where id = p_ticket_id;
    if not found then
        raise exception 'No se encontró el reporte.';
    end if;
end;
$$;

revoke all on function public.submit_support_ticket(text,text,text,text) from public, anon;
revoke all on function public.admin_list_support_tickets() from public, anon;
revoke all on function public.admin_update_support_ticket(bigint,text) from public, anon;
grant execute on function public.submit_support_ticket(text,text,text,text) to authenticated;
grant execute on function public.admin_list_support_tickets() to authenticated;
grant execute on function public.admin_update_support_ticket(bigint,text) to authenticated;

commit;

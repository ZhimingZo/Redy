-- Per-person tracker. Run this once in the Supabase SQL editor.
-- Google sign-in itself is configured in Authentication → Providers, not here.
-- Ask Google only for openid, email and profile. Do not add more scopes.
-- Redirect URL Google must allow: https://<project-ref>.supabase.co/auth/v1/callback
-- Site URL: https://redy.fyi
-- Extra redirect URLs: https://www.redy.fyi  http://127.0.0.1:8000  http://localhost:8000

create table public.saved_jobs (
  user_id uuid not null references auth.users (id) on delete cascade,
  job_id text not null check (char_length(job_id) between 1 and 200),
  stage text not null default 'Saved' check (stage in (
    'Saved', 'Applied', 'Recruiter screen', 'Interviewing', 'Offer', 'Rejected', 'Withdrawn'
  )),
  note text not null default '' check (char_length(note) <= 2000),
  applied date,
  follow date,
  updated_ms bigint not null,
  job jsonb not null check (octet_length(job::text) <= 8000),
  primary key (user_id, job_id)
);

alter table public.saved_jobs enable row level security;

create policy saved_jobs_own
  on public.saved_jobs
  for all
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

grant select, insert, update, delete on public.saved_jobs to authenticated;

do $$
begin
  alter publication supabase_realtime add table public.saved_jobs;
exception
  when duplicate_object then null;
  when undefined_object then null;
end $$;

-- Deletes this person's tracker rows (via cascade) and their Supabase login.
-- Does not touch the Google account.
create or replace function public.delete_own_account()
returns void
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null then
    raise exception 'not authenticated';
  end if;
  delete from auth.users where id = uid;
end;
$$;

revoke all on function public.delete_own_account() from public;
grant execute on function public.delete_own_account() to authenticated;

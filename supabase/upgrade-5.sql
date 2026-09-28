-- Privacy: each player can keep their games to themselves, and leave the
-- scoreboard.
--
-- Run this once in the Supabase SQL Editor, after upgrade-4.sql (it is also
-- part of games.sql). Safe to run more than once.
--
-- A private player's games and puzzle attempts can't be read with the public
-- key at all; they get their own through my_games and my_puzzle_attempts,
-- which need their sign-in. A player who leaves the scoreboard is left out
-- of other people's rankings and totals on the Stats page (hidden_players
-- names them), but their games stay readable.

alter table public.players add column if not exists private boolean not null default false;
alter table public.players add column if not exists on_scoreboard boolean not null default true;

-- Whether a name belongs to a private player (used by the policies below).
create or replace function public.player_is_private(p_player text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.players where name_key = lower(trim(p_player)) and private);
$$;

drop policy if exists "Anyone can read games" on public.games;
drop policy if exists "Anyone can read public players' games" on public.games;
create policy "Anyone can read public players' games" on public.games
  for select to anon, authenticated using (not public.player_is_private(player));

drop policy if exists "Anyone can read puzzle attempts" on public.puzzle_attempts;
drop policy if exists "Anyone can read public players' puzzle attempts" on public.puzzle_attempts;
create policy "Anyone can read public players' puzzle attempts" on public.puzzle_attempts
  for select to anon, authenticated using (not public.player_is_private(player));

-- The signed-in player's own games and puzzle attempts, private or not.
create or replace function public.my_games(p_token text)
returns setof public.games
language sql stable security definer set search_path = ''
as $$
  select g.* from public.games g
  where lower(trim(g.player)) = lower(public.session_name(p_token))
  order by g.played_at desc
  limit 5000;
$$;

create or replace function public.my_puzzle_attempts(p_token text)
returns setof public.puzzle_attempts
language sql stable security definer set search_path = ''
as $$
  select a.* from public.puzzle_attempts a
  where lower(trim(a.player)) = lower(public.session_name(p_token))
  order by a.played_at desc
  limit 5000;
$$;

-- The signed-in player's settings: {"name", "private", "on_scoreboard"}, or null.
create or replace function public.player_options(p_token text)
returns json
language sql stable security definer set search_path = ''
as $$
  select json_build_object('name', p.name, 'private', p.private, 'on_scoreboard', p.on_scoreboard)
  from public.player_sessions s join public.players p on p.name_key = s.name_key
  where s.token_hash = encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex');
$$;

-- Change the signed-in player's settings (null leaves one as it is); returns them.
create or replace function public.set_player_options(
  p_token text, p_private boolean default null, p_on_scoreboard boolean default null
)
returns json
language plpgsql security definer set search_path = ''
as $$
declare
  key text;
begin
  select name_key into key from public.player_sessions
  where token_hash = encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex');
  if key is null then
    raise exception 'Sign in again to change your settings.';
  end if;
  update public.players
  set private = coalesce(p_private, private),
      on_scoreboard = coalesce(p_on_scoreboard, on_scoreboard)
  where name_key = key;
  return public.player_options(p_token);
end;
$$;

-- Players who left the scoreboard (private players' games can't be read anyway).
create or replace function public.hidden_players()
returns setof text
language sql stable security definer set search_path = ''
as $$
  select name from public.players where not on_scoreboard and not private;
$$;

revoke execute on function public.player_is_private(text) from public;
revoke execute on function public.my_games(text) from public;
revoke execute on function public.my_puzzle_attempts(text) from public;
revoke execute on function public.player_options(text) from public;
revoke execute on function public.set_player_options(text, boolean, boolean) from public;
revoke execute on function public.hidden_players() from public;
grant execute on function public.player_is_private(text) to anon, authenticated;
grant execute on function public.my_games(text) to anon, authenticated;
grant execute on function public.my_puzzle_attempts(text) to anon, authenticated;
grant execute on function public.player_options(text) to anon, authenticated;
grant execute on function public.set_player_options(text, boolean, boolean) to anon, authenticated;
grant execute on function public.hidden_players() to anon, authenticated;

notify pgrst, 'reload schema';

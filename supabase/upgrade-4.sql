-- Name lock: every name on the site belongs to one player, who signs in with
-- a password.
--
-- Run this once in the Supabase SQL Editor (it is also part of games.sql).
-- Safe to run more than once.
--
-- The first person to use a name claims it by choosing a password. After
-- that, games, puzzle attempts and online games under the name need a browser
-- that has signed in with that password. Passwords are kept only as bcrypt
-- hashes. A browser that signs in gets a random token, kept here only as a
-- hash. Five wrong passwords in a row lock the name for 15 minutes.
--
-- Games and puzzle attempts are saved through record_game and
-- record_puzzle_attempt, which put the signed-in name on them; the public key
-- can no longer add rows to those tables directly.
--
-- A forgotten password: free the name in the SQL Editor with
--   delete from public.players where name_key = lower('Their name');
-- Its games stay, and the next person to use the name claims it again.

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.players (
  name_key       text primary key,  -- lower(trim(name)): "Paul" and " paul" are one name
  name           text not null check (char_length(name) between 1 and 24),
  password_hash  text not null,
  created_at     timestamptz not null default now(),
  failed_logins  smallint not null default 0,
  locked_until   timestamptz
);

create table if not exists public.player_sessions (
  token_hash  text primary key,
  name_key    text not null references public.players (name_key) on delete cascade on update cascade,
  created_at  timestamptz not null default now()
);

alter table public.players enable row level security;
alter table public.player_sessions enable row level security;
revoke all on public.players from anon, authenticated;
revoke all on public.player_sessions from anon, authenticated;

-- The name a sign-in token belongs to, or null.
create or replace function public.session_name(p_token text)
returns text
language sql stable security definer set search_path = ''
as $$
  select p.name
  from public.player_sessions s join public.players p on p.name_key = s.name_key
  where s.token_hash = encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex');
$$;

create or replace function public.name_taken(p_name text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (select 1 from public.players where name_key = lower(trim(p_name)));
$$;

-- A new sign-in token for a name. Each name keeps its 20 newest.
create or replace function public.new_player_session(p_name_key text)
returns text
language plpgsql security definer set search_path = ''
as $$
declare
  token text := encode(extensions.gen_random_bytes(32), 'hex');
begin
  insert into public.player_sessions (token_hash, name_key)
  values (encode(extensions.digest(token, 'sha256'), 'hex'), p_name_key);
  delete from public.player_sessions
  where name_key = p_name_key
    and token_hash not in (
      select token_hash from public.player_sessions where name_key = p_name_key order by created_at desc limit 20
    );
  return token;
end;
$$;

-- Claim a free name. Returns {"name", "token"}, or {"error"}.
create or replace function public.claim_name(p_name text, p_password text)
returns json
language plpgsql security definer set search_path = ''
as $$
declare
  clean text := trim(p_name);
begin
  if char_length(coalesce(clean, '')) not between 1 and 24 then
    return json_build_object('error', 'A name has 1 to 24 characters.');
  end if;
  if char_length(coalesce(p_password, '')) < 4 then
    return json_build_object('error', 'Choose a password of at least 4 characters.');
  end if;
  if octet_length(p_password) > 72 then
    return json_build_object('error', 'That password is too long.');
  end if;
  insert into public.players (name_key, name, password_hash)
  values (lower(clean), clean, extensions.crypt(p_password, extensions.gen_salt('bf', 8)))
  on conflict (name_key) do nothing;
  if not found then
    return json_build_object('error', 'Someone already has this name. If it''s yours, sign in with its password.');
  end if;
  return json_build_object('name', clean, 'token', public.new_player_session(lower(clean)));
end;
$$;

-- Sign in to a claimed name. Returns {"name", "token"}, or {"error"}. (Not
-- raising an error on a wrong password is what lets the count of wrong
-- passwords be saved.)
create or replace function public.sign_in(p_name text, p_password text)
returns json
language plpgsql security definer set search_path = ''
as $$
declare
  player public.players;
begin
  select * into player from public.players where name_key = lower(trim(p_name)) for update;
  if not found then
    return json_build_object('error', 'Nobody has this name yet.');
  end if;
  if player.locked_until > now() then
    return json_build_object(
      'error',
      format('Too many wrong passwords. Try again in %s minutes.', ceil(extract(epoch from player.locked_until - now()) / 60))
    );
  end if;
  if player.password_hash is distinct from extensions.crypt(coalesce(p_password, ''), player.password_hash) then
    update public.players
    set failed_logins = case when failed_logins >= 4 then 0 else failed_logins + 1 end,
        locked_until = case when failed_logins >= 4 then now() + interval '15 minutes' end
    where name_key = player.name_key;
    return json_build_object('error', 'Wrong password.');
  end if;
  update public.players set failed_logins = 0, locked_until = null where name_key = player.name_key;
  return json_build_object('name', player.name, 'token', public.new_player_session(player.name_key));
end;
$$;

create or replace function public.sign_out(p_token text)
returns void
language sql security definer set search_path = ''
as $$
  delete from public.player_sessions
  where token_hash = encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex');
$$;

-- Save a finished game (the games table's columns, as JSON) under the signed-in name.
create or replace function public.record_game(p_token text, p_game jsonb)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  who text := public.session_name(p_token);
  game public.games := jsonb_populate_record(null::public.games, p_game);
begin
  if who is null then
    raise exception 'Sign in again to save games under your name.';
  end if;
  game.player := who;
  game.played_at := least(coalesce(game.played_at, now()), now());
  game.takebacks := coalesce(game.takebacks, 0);
  game.hints := coalesce(game.hints, 0);
  insert into public.games values (game.*) on conflict (id) do nothing;
end;
$$;

create or replace function public.record_puzzle_attempt(p_token text, p_attempt jsonb)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  who text := public.session_name(p_token);
  attempt public.puzzle_attempts := jsonb_populate_record(null::public.puzzle_attempts, p_attempt);
begin
  if who is null then
    raise exception 'Sign in again to save puzzles under your name.';
  end if;
  attempt.player := who;
  attempt.played_at := least(coalesce(attempt.played_at, now()), now());
  insert into public.puzzle_attempts values (attempt.*) on conflict (id) do nothing;
end;
$$;

-- Only the functions above can add games and puzzle attempts now.
drop policy if exists "Anyone can add a game" on public.games;
drop policy if exists "Anyone can add a puzzle attempt" on public.puzzle_attempts;
revoke insert, update, delete, truncate on public.games from anon, authenticated;
revoke insert, update, delete, truncate on public.puzzle_attempts from anon, authenticated;

-- Online games: a claimed name needs its owner's sign-in token; guests pick a free name.
create or replace function public.check_player_name(p_name text, p_player_token text)
returns void
language plpgsql stable security definer set search_path = ''
as $$
begin
  if exists (select 1 from public.players where name_key = lower(trim(p_name)))
     and lower(trim(p_name)) is distinct from lower(public.session_name(p_player_token)) then
    raise exception 'That name belongs to a player. Sign in on the Play tab to use it, or pick another name.';
  end if;
end;
$$;

drop function if exists public.create_live_game(uuid, text, text, text);
create or replace function public.create_live_game(
  p_id uuid, p_token text, p_name text, p_color text, p_player_token text default null
)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  perform public.check_player_name(p_name, p_player_token);
  if p_color not in ('white', 'black') then
    raise exception 'Choose white or black.';
  end if;
  insert into public.live_games (id, white_name, black_name)
  values (p_id, case when p_color = 'white' then p_name end, case when p_color = 'black' then p_name end);
  insert into public.live_game_seats (game_id, color, token) values (p_id, p_color, p_token);
end;
$$;

-- Take the free seat of a game that is waiting for its second player; returns its color.
drop function if exists public.join_live_game(uuid, text, text);
create or replace function public.join_live_game(p_id uuid, p_token text, p_name text, p_player_token text default null)
returns text
language plpgsql security definer set search_path = ''
as $$
declare
  game public.live_games;
  seat text;
begin
  perform public.check_player_name(p_name, p_player_token);
  select * into game from public.live_games where id = p_id for update;
  if not found then
    raise exception 'There is no such game.';
  end if;
  if game.status <> 'waiting' then
    raise exception 'Someone has already joined this game.';
  end if;
  seat := case when game.white_name is null then 'white' else 'black' end;
  insert into public.live_game_seats (game_id, color, token) values (p_id, seat, p_token);
  update public.live_games
  set white_name = coalesce(white_name, p_name),
      black_name = coalesce(black_name, p_name),
      status = 'playing',
      updated_at = now()
  where id = p_id;
  return seat;
end;
$$;

revoke execute on function public.new_player_session(text) from public, anon, authenticated;
revoke execute on function public.check_player_name(text, text) from public, anon, authenticated;
revoke execute on function public.session_name(text) from public;
revoke execute on function public.name_taken(text) from public;
revoke execute on function public.claim_name(text, text) from public;
revoke execute on function public.sign_in(text, text) from public;
revoke execute on function public.sign_out(text) from public;
revoke execute on function public.record_game(text, jsonb) from public;
revoke execute on function public.record_puzzle_attempt(text, jsonb) from public;
revoke execute on function public.create_live_game(uuid, text, text, text, text) from public;
revoke execute on function public.join_live_game(uuid, text, text, text) from public;
grant execute on function public.session_name(text) to anon, authenticated;
grant execute on function public.name_taken(text) to anon, authenticated;
grant execute on function public.claim_name(text, text) to anon, authenticated;
grant execute on function public.sign_in(text, text) to anon, authenticated;
grant execute on function public.sign_out(text) to anon, authenticated;
grant execute on function public.record_game(text, jsonb) to anon, authenticated;
grant execute on function public.record_puzzle_attempt(text, jsonb) to anon, authenticated;
grant execute on function public.create_live_game(uuid, text, text, text, text) to anon, authenticated;
grant execute on function public.join_live_game(uuid, text, text, text) to anon, authenticated;

notify pgrst, 'reload schema';

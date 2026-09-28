-- Shared player stats for the ChessBot website.
--
-- Run this once in your Supabase project (SQL Editor -> New query -> Run).
-- It creates a table of finished games, one of puzzle attempts, the name lock
-- (players and their sign-ins) and live games between two people. Anyone
-- using the site can read the games and puzzle attempts. New ones come in
-- through functions that check the player has signed in to their name, and
-- nobody can change or delete them through the public key. The checks keep
-- junk out; they can't stop a signed-in player from submitting made-up results.

create table if not exists public.games (
  id           uuid primary key,
  played_at    timestamptz not null default now(),
  player       text not null check (char_length(trim(player)) between 1 and 24),
  color        text not null check (color in ('white', 'black')),
  result       text not null check (result in ('win', 'loss', 'draw')),
  reason       text not null check (char_length(reason) <= 40),
  level        smallint not null check (level between 1 and 20),
  bot_elo      smallint not null check (bot_elo between 0 and 4000),
  moves        smallint not null check (moves between 0 and 2000),
  accuracy     real check (accuracy between 0 and 100),
  blunders     smallint check (blunders between 0 and 2000),
  mistakes     smallint check (mistakes between 0 and 2000),
  inaccuracies smallint check (inaccuracies between 0 and 2000),
  takebacks    smallint not null default 0 check (takebacks between 0 and 2000),
  opening      text check (char_length(opening) <= 80),
  hints        smallint not null default 0 check (hints between 0 and 2000),
  moves_uci    text check (char_length(moves_uci) <= 10000)
);

create index if not exists games_played_at on public.games (played_at desc);

alter table public.games enable row level security;

drop policy if exists "Anyone can read games" on public.games;
create policy "Anyone can read games" on public.games
  for select to anon, authenticated using (true);

-- Games are added by record_game (below), under the signed-in name.
drop policy if exists "Anyone can add a game" on public.games;
grant select on public.games to anon, authenticated;
revoke insert, update, delete, truncate on public.games from anon, authenticated;

-- Puzzle attempts, for puzzle ratings on the Stats page (also in upgrade-2.sql).
create table if not exists public.puzzle_attempts (
  id            uuid primary key,
  played_at     timestamptz not null default now(),
  player        text not null check (char_length(trim(player)) between 1 and 24),
  puzzle_id     text not null check (char_length(puzzle_id) <= 20),
  puzzle_rating smallint not null check (puzzle_rating between 0 and 4000),
  solved        boolean not null,
  rating_after  smallint not null check (rating_after between 0 and 4000)
);

create index if not exists puzzle_attempts_played_at on public.puzzle_attempts (played_at desc);

alter table public.puzzle_attempts enable row level security;

drop policy if exists "Anyone can read puzzle attempts" on public.puzzle_attempts;
create policy "Anyone can read puzzle attempts" on public.puzzle_attempts
  for select to anon, authenticated using (true);

drop policy if exists "Anyone can add a puzzle attempt" on public.puzzle_attempts;
grant select on public.puzzle_attempts to anon, authenticated;
revoke insert, update, delete, truncate on public.puzzle_attempts from anon, authenticated;

-- The name lock (also in upgrade-4.sql): the first person to use a name claims it
-- with a password, and signs in with it on other devices. Five wrong passwords
-- in a row lock the name for 15 minutes. A forgotten password: free the name with
--   delete from public.players where name_key = lower('Their name');
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

revoke execute on function public.new_player_session(text) from public, anon, authenticated;
revoke execute on function public.check_player_name(text, text) from public, anon, authenticated;
revoke execute on function public.session_name(text) from public;
revoke execute on function public.name_taken(text) from public;
revoke execute on function public.claim_name(text, text) from public;
revoke execute on function public.sign_in(text, text) from public;
revoke execute on function public.sign_out(text) from public;
revoke execute on function public.record_game(text, jsonb) from public;
revoke execute on function public.record_puzzle_attempt(text, jsonb) from public;
grant execute on function public.session_name(text) to anon, authenticated;
grant execute on function public.name_taken(text) to anon, authenticated;
grant execute on function public.claim_name(text, text) to anon, authenticated;
grant execute on function public.sign_in(text, text) to anon, authenticated;
grant execute on function public.sign_out(text) to anon, authenticated;
grant execute on function public.record_game(text, jsonb) to anon, authenticated;
grant execute on function public.record_puzzle_attempt(text, jsonb) to anon, authenticated;

-- Live games between two people, for the Friend tab (also in upgrade-3.sql).
create table if not exists public.live_games (
  id          uuid primary key,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  white_name  text check (char_length(trim(white_name)) between 1 and 24),
  black_name  text check (char_length(trim(black_name)) between 1 and 24),
  moves       text not null default '' check (char_length(moves) <= 10000),
  status      text not null default 'waiting' check (status in ('waiting', 'playing', 'over')),
  result      text check (result in ('1-0', '0-1', '1/2-1/2')),
  reason      text check (char_length(reason) <= 40),
  draw_offer  text check (draw_offer in ('white', 'black'))
);

create table if not exists public.live_game_seats (
  game_id  uuid not null references public.live_games (id) on delete cascade,
  color    text not null check (color in ('white', 'black')),
  token    text not null check (char_length(token) between 20 and 100),
  primary key (game_id, color)
);

alter table public.live_games enable row level security;
alter table public.live_game_seats enable row level security;

drop policy if exists "Anyone can watch live games" on public.live_games;
create policy "Anyone can watch live games" on public.live_games
  for select to anon, authenticated using (true);

grant select on public.live_games to anon, authenticated;
revoke all on public.live_game_seats from anon, authenticated;

-- The seat a token belongs to, or an error.
create or replace function public.live_seat(p_id uuid, p_token text)
returns text
language plpgsql security definer set search_path = ''
as $$
declare
  seat text;
begin
  select color into seat from public.live_game_seats where game_id = p_id and token = p_token;
  if seat is null then
    raise exception 'You are not playing in this game.';
  end if;
  return seat;
end;
$$;

drop function if exists public.create_live_game(uuid, text, text, text);  -- before names were locked
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

-- Add one move (p_moves is the whole game so far, space-separated UCI). A move
-- that ends the game passes its result and reason; a move also declines a draw offer.
create or replace function public.play_live_move(
  p_id uuid, p_token text, p_moves text, p_result text default null, p_reason text default null
)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  seat text := public.live_seat(p_id, p_token);
  game public.live_games;
  to_move text;
  move text := split_part(p_moves, ' ', -1);
  expected text;
begin
  select * into game from public.live_games where id = p_id for update;
  if game.status <> 'playing' then
    raise exception 'This game is not in progress.';
  end if;
  to_move := case when cardinality(string_to_array(nullif(game.moves, ''), ' ')) % 2 = 1 then 'black' else 'white' end;
  if seat <> to_move then
    raise exception 'It is not your turn.';
  end if;
  expected := case when game.moves = '' then move else game.moves || ' ' || move end;
  if move !~ '^[a-h][1-8][a-h][1-8][qrbn]?$' or p_moves <> expected then
    raise exception 'The game has moved on; reload it.';
  end if;
  update public.live_games
  set moves = p_moves,
      draw_offer = null,
      status = case when p_result is null then 'playing' else 'over' end,
      result = p_result,
      reason = p_reason,
      updated_at = now()
  where id = p_id;
end;
$$;

create or replace function public.resign_live_game(p_id uuid, p_token text)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  seat text := public.live_seat(p_id, p_token);
begin
  update public.live_games
  set status = 'over',
      result = case when seat = 'white' then '0-1' else '1-0' end,
      reason = 'resignation',
      draw_offer = null,
      updated_at = now()
  where id = p_id and status = 'playing';
end;
$$;

-- p_action: 'offer', 'accept' (the opponent's offer) or 'decline' (likewise).
create or replace function public.live_game_draw(p_id uuid, p_token text, p_action text)
returns void
language plpgsql security definer set search_path = ''
as $$
declare
  seat text := public.live_seat(p_id, p_token);
  opponent text := case when seat = 'white' then 'black' else 'white' end;
begin
  if p_action = 'offer' then
    update public.live_games set draw_offer = seat, updated_at = now()
    where id = p_id and status = 'playing' and draw_offer is null;
  elsif p_action = 'accept' then
    update public.live_games
    set status = 'over', result = '1/2-1/2', reason = 'agreement', draw_offer = null, updated_at = now()
    where id = p_id and status = 'playing' and draw_offer = opponent;
  elsif p_action = 'decline' then
    update public.live_games set draw_offer = null, updated_at = now()
    where id = p_id and status = 'playing' and draw_offer = opponent;
  else
    raise exception 'Unknown draw action.';
  end if;
end;
$$;

revoke execute on function public.live_seat(uuid, text) from public, anon, authenticated;
revoke execute on function public.create_live_game(uuid, text, text, text, text) from public;
revoke execute on function public.join_live_game(uuid, text, text, text) from public;
revoke execute on function public.play_live_move(uuid, text, text, text, text) from public;
revoke execute on function public.resign_live_game(uuid, text) from public;
revoke execute on function public.live_game_draw(uuid, text, text) from public;
grant execute on function public.create_live_game(uuid, text, text, text, text) to anon, authenticated;
grant execute on function public.join_live_game(uuid, text, text, text) to anon, authenticated;
grant execute on function public.play_live_move(uuid, text, text, text, text) to anon, authenticated;
grant execute on function public.resign_live_game(uuid, text) to anon, authenticated;
grant execute on function public.live_game_draw(uuid, text, text) to anon, authenticated;

-- Privacy (also in upgrade-5.sql): a private player's games and puzzle attempts
-- can only be read by them, through my_games and my_puzzle_attempts; a player who
-- leaves the scoreboard is left out of other people's rankings (hidden_players).
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

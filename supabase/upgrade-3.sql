-- Live games between two people, for the Friend tab.
--
-- Run this once in the Supabase SQL Editor (it is also part of games.sql).
-- Anyone can watch a live game, but only its two players can move: each seat
-- has a secret token that lives in the player's browser, and every change
-- goes through the functions below, which check the token. The tokens are in
-- a table the public key can't read.
--
-- The functions check whose turn it is and that each call adds exactly one
-- move. Whether the move is legal is checked by both players' browsers.

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

create or replace function public.create_live_game(p_id uuid, p_token text, p_name text, p_color text)
returns void
language plpgsql security definer set search_path = ''
as $$
begin
  if p_color not in ('white', 'black') then
    raise exception 'Choose white or black.';
  end if;
  insert into public.live_games (id, white_name, black_name)
  values (p_id, case when p_color = 'white' then p_name end, case when p_color = 'black' then p_name end);
  insert into public.live_game_seats (game_id, color, token) values (p_id, p_color, p_token);
end;
$$;

-- Take the free seat of a game that is waiting for its second player; returns its color.
create or replace function public.join_live_game(p_id uuid, p_token text, p_name text)
returns text
language plpgsql security definer set search_path = ''
as $$
declare
  game public.live_games;
  seat text;
begin
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
revoke execute on function public.create_live_game(uuid, text, text, text) from public;
revoke execute on function public.join_live_game(uuid, text, text) from public;
revoke execute on function public.play_live_move(uuid, text, text, text, text) from public;
revoke execute on function public.resign_live_game(uuid, text) from public;
revoke execute on function public.live_game_draw(uuid, text, text) from public;
grant execute on function public.create_live_game(uuid, text, text, text) to anon, authenticated;
grant execute on function public.join_live_game(uuid, text, text) to anon, authenticated;
grant execute on function public.play_live_move(uuid, text, text, text, text) to anon, authenticated;
grant execute on function public.resign_live_game(uuid, text) to anon, authenticated;
grant execute on function public.live_game_draw(uuid, text, text) to anon, authenticated;

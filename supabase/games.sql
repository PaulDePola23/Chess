-- Shared player stats for the ChessBot website.
--
-- Run this once in your Supabase project (SQL Editor -> New query -> Run).
-- It creates one table of finished games. Anyone using the site can read all
-- games and add new ones, but nobody can change or delete them through the
-- public key. The checks keep junk out; they can't stop someone from
-- submitting made-up results, because the site has no logins.

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

drop policy if exists "Anyone can add a game" on public.games;
create policy "Anyone can add a game" on public.games
  for insert to anon, authenticated with check (true);

grant select, insert on public.games to anon, authenticated;

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
create policy "Anyone can add a puzzle attempt" on public.puzzle_attempts
  for insert to anon, authenticated with check (true);

grant select, insert on public.puzzle_attempts to anon, authenticated;

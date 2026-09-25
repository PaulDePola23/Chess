-- Upgrade for tables created from the first version of games.sql.
-- Adds the number of hints used and the game's moves (for replays).
-- Safe to run more than once.

alter table public.games
  add column if not exists hints smallint not null default 0 check (hints between 0 and 2000);

alter table public.games
  add column if not exists moves_uci text check (char_length(moves_uci) <= 10000);

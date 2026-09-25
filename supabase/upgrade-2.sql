-- Adds the puzzle_attempts table for puzzle ratings on the Stats page.
-- Safe to run more than once.

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

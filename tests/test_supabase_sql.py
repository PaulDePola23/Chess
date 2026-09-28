"""The Supabase SQL, run against a real PostgreSQL set up to look like Supabase.

Skipped where PostgreSQL isn't installed. The site's public key acts as the
`anon` role, so every check runs as anon unless it says otherwise.
"""

import glob
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUPABASE = ROOT / "supabase"

# What a new Supabase project has before any of our SQL: the API roles, the
# extensions schema, and default grants of everything new in public to the API roles.
SUPABASE_LIKE = """
do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
create schema extensions;
grant usage on schema extensions to anon, authenticated;
grant usage on schema public to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
alter default privileges in schema public grant all on functions to anon, authenticated;
"""


def _bin_dir():
    found = shutil.which("initdb") or next(
        iter(sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True)), None
    )
    return pathlib.Path(found).parent if found and shutil.which("psql") else None


@pytest.fixture(scope="module")
def postgres():
    bin_dir = _bin_dir()
    if bin_dir is None:
        pytest.skip("PostgreSQL isn't installed")
    as_user = []
    if os.geteuid() == 0:  # PostgreSQL won't run as root
        if not shutil.which("runuser"):
            pytest.skip("running as root without runuser")
        as_user = ["runuser", "-u", "postgres", "--"]
    base = pathlib.Path(tempfile.mkdtemp(prefix="chess-pg-", dir="/tmp"))
    base.chmod(0o777)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = base / "data"
    quiet = {"check": True, "capture_output": True}
    subprocess.run([*as_user, bin_dir / "initdb", "-D", data, "-A", "trust", "-U", "postgres"], **quiet)
    options = f"-p {port} -k {base} -c listen_addresses=''"
    subprocess.run(
        [*as_user, bin_dir / "pg_ctl", "-D", data, "-o", options, "-l", base / "log", "-w", "start"], **quiet
    )
    try:
        yield {"host": str(base), "port": str(port)}
    finally:
        subprocess.run([*as_user, bin_dir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


def psql(server, db, sql, role=None):
    """Runs SQL (as `role` if given); returns the output rows, or raises with the error."""
    if role:
        sql = f"set role {role};\n{sql}"
    result = subprocess.run(
        [
            "psql",
            "-h",
            server["host"],
            "-p",
            server["port"],
            "-U",
            "postgres",
            "-d",
            db,
            "-X",
            "-q",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def new_database(server, *files, before=""):
    db = "t" + uuid.uuid4().hex[:12]
    psql(server, "postgres", f"create database {db}")
    psql(server, db, SUPABASE_LIKE + before)
    for name in files:
        psql(server, db, (SUPABASE / name).read_text())
    return db


def call(server, db, sql):
    return psql(server, db, f"select {sql};", role="anon")


def fails(server, db, sql):
    with pytest.raises(RuntimeError) as error:
        call(server, db, sql)
    return str(error.value)


GAME = {"color": "white", "result": "win", "reason": "checkmate", "level": 3, "bot_elo": 800, "moves": 20}


def game(**fields):
    record = {"id": str(uuid.uuid4()), **GAME, **fields}
    return "'" + json.dumps(record).replace("'", "''") + "'::jsonb"


@pytest.fixture(scope="module")
def db(postgres):
    return new_database(postgres, "games.sql")


def claim(server, db, name, password):
    return json.loads(call(server, db, f"public.claim_name('{name}', '{password}')"))


def test_a_name_is_claimed_once(postgres, db):
    assert call(postgres, db, "public.name_taken('Summer')") == "f"
    first = claim(postgres, db, "Summer", "biscuit")
    assert first["name"] == "Summer" and len(first["token"]) == 64
    assert call(postgres, db, "public.name_taken('  SUMMER ')") == "t"
    assert "already has this name" in claim(postgres, db, " summer", "other1")["error"]
    assert "at least 4" in claim(postgres, db, "Someone", "abc")["error"]
    assert "1 to 24" in claim(postgres, db, "   ", "abcd")["error"]
    # Only the hash of the password is kept.
    stored = psql(postgres, db, "select password_hash from public.players where name_key = 'summer'")
    assert stored.startswith("$2a$") and "biscuit" not in stored


def test_signing_in_and_wrong_passwords(postgres, db):
    claim(postgres, db, "Titan", "goodboy")
    signed_in = json.loads(call(postgres, db, "public.sign_in('titan', 'goodboy')"))
    assert signed_in["name"] == "Titan"
    assert call(postgres, db, f"public.session_name('{signed_in['token']}')") == "Titan"
    assert json.loads(call(postgres, db, "public.sign_in('Nobody', 'x')"))["error"] == "Nobody has this name yet."
    for _ in range(5):
        assert json.loads(call(postgres, db, "public.sign_in('Titan', 'wrong')"))["error"] == "Wrong password."
    # Locked now, even with the right password.
    assert "Too many wrong passwords" in json.loads(call(postgres, db, "public.sign_in('Titan', 'goodboy')"))["error"]
    call(postgres, db, f"public.sign_out('{signed_in['token']}')")
    assert call(postgres, db, f"public.session_name('{signed_in['token']}')") == ""


def test_the_public_key_cannot_reach_around_the_lock(postgres, db):
    for table in ("players", "player_sessions"):
        assert "permission denied" in fails(postgres, db, f"* from public.{table}")
    assert "permission denied" in psql_error(
        postgres,
        db,
        "insert into public.games (id, player, color, result, reason, "
        "level, bot_elo, moves) values (gen_random_uuid(), 'Summer', 'white', "
        "'win', 'x', 3, 800, 20)",
    )
    for function in ("new_player_session('summer')", "check_player_name('Summer', null)"):
        assert "permission denied" in fails(postgres, db, f"public.{function}")
    assert call(postgres, db, "count(*) >= 0 from public.games") == "t"  # reading is still open


def psql_error(server, db, sql):
    with pytest.raises(RuntimeError) as error:
        psql(server, db, sql + ";", role="anon")
    return str(error.value)


def test_games_and_puzzles_are_saved_under_the_signed_in_name(postgres, db):
    token = claim(postgres, db, "Pinky", "meow")["token"]
    call(postgres, db, f"public.record_game('{token}', {game(player='Summer', accuracy=90.5)})")
    rows = psql(postgres, db, "select player, accuracy, hints, takebacks from public.games where player = 'Pinky'")
    assert rows == "Pinky|90.5|0|0"
    same = game()
    call(postgres, db, f"public.record_game('{token}', {same})")
    call(postgres, db, f"public.record_game('{token}', {same})")  # saving twice is harmless
    assert "Sign in again" in fails(postgres, db, f"public.record_game('not-a-token', {game()})")
    assert "games_color_check" in fails(postgres, db, f"public.record_game('{token}', {game(color='purple')})")
    attempt = json.dumps(
        {
            "id": str(uuid.uuid4()),
            "player": "Titan",
            "puzzle_id": "p1",
            "puzzle_rating": 1200,
            "solved": True,
            "rating_after": 1110,
        }
    )
    call(postgres, db, f"public.record_puzzle_attempt('{token}', '{attempt}'::jsonb)")
    assert psql(postgres, db, "select player from public.puzzle_attempts") == "Pinky"


def test_online_games_keep_claimed_names_for_their_owners(postgres, db):
    token = claim(postgres, db, "Paul", "rook")["token"]
    game_id, seat = str(uuid.uuid4()), "a" * 24
    assert "belongs to a player" in fails(
        postgres, db, f"public.create_live_game('{game_id}', '{seat}', 'paul', 'white')"
    )
    assert "belongs to a player" in fails(
        postgres, db, f"public.create_live_game('{game_id}', '{seat}', 'Paul', 'white', 'wrong')"
    )
    call(postgres, db, f"public.create_live_game('{game_id}', '{seat}', 'Paul', 'white', '{token}')")
    assert "belongs to a player" in fails(postgres, db, f"public.join_live_game('{game_id}', '{'b' * 24}', ' PAUL ')")
    assert call(postgres, db, f"public.join_live_game('{game_id}', '{'b' * 24}', 'Guest')") == "black"


def test_a_private_player_is_the_only_one_who_sees_their_games(postgres, db):
    token = claim(postgres, db, "Bishop", "diagonal")["token"]
    call(postgres, db, f"public.record_game('{token}', {game()})")
    attempt = json.dumps({"id": str(uuid.uuid4()), "puzzle_id": "p2", "puzzle_rating": 900, "solved": True,
                          "rating_after": 1010})  # fmt: skip
    call(postgres, db, f"public.record_puzzle_attempt('{token}', '{attempt}'::jsonb)")

    def visible(table):
        return call(postgres, db, f"count(*) from public.{table} where player = 'Bishop'")

    assert json.loads(call(postgres, db, f"public.player_options('{token}')")) == {
        "name": "Bishop", "private": False, "on_scoreboard": True,
    }  # fmt: skip
    assert visible("games") == "1" and visible("puzzle_attempts") == "1"

    options = json.loads(call(postgres, db, f"public.set_player_options('{token}', true)"))
    assert options["private"] is True and options["on_scoreboard"] is True
    assert visible("games") == "0" and visible("puzzle_attempts") == "0"  # to everyone else
    assert call(postgres, db, f"count(*) from public.my_games('{token}')") == "1"  # but not to Bishop
    assert call(postgres, db, f"count(*) from public.my_puzzle_attempts('{token}')") == "1"
    assert call(postgres, db, "count(*) from public.my_games('not-a-token')") == "0"
    assert "Sign in again" in fails(postgres, db, "public.set_player_options('not-a-token', false)")

    # Public again, but off the scoreboard: the games can be read, the name is listed as hidden.
    options = json.loads(call(postgres, db, f"public.set_player_options('{token}', false, false)"))
    assert options == {"name": "Bishop", "private": False, "on_scoreboard": False}
    assert visible("games") == "1"
    assert "Bishop" in call(postgres, db, "* from public.hidden_players()").splitlines()
    call(postgres, db, f"public.set_player_options('{token}', p_on_scoreboard => true)")
    assert "Bishop" not in call(postgres, db, "* from public.hidden_players()").splitlines()


OLD_POLICY = """
create table public.games (
  id uuid primary key, played_at timestamptz not null default now(), player text not null,
  color text not null, result text not null, reason text not null, level smallint not null,
  bot_elo smallint not null, moves smallint not null, accuracy real, blunders smallint, mistakes smallint,
  inaccuracies smallint, takebacks smallint not null default 0, opening text
);
alter table public.games enable row level security;
create policy "Anyone can add a game" on public.games for insert to anon, authenticated with check (true);
grant select, insert on public.games to anon, authenticated;
"""


def test_upgrading_an_older_database(postgres):
    # A database set up before names were locked, upgraded in order.
    upgrades = [f"upgrade-{n}.sql" for n in range(1, 6)]
    db = new_database(postgres, *upgrades, before=OLD_POLICY)
    functions = psql(
        postgres,
        db,
        "select oid::regprocedure from pg_proc where proname in ('create_live_game', 'join_live_game') order by 1",
    )
    assert functions.splitlines() == [
        "create_live_game(uuid,text,text,text,text)",
        "join_live_game(uuid,text,text,text)",
    ]
    assert psql(postgres, db, "select count(*) from pg_policies where policyname like 'Anyone can add%'") == "0"
    assert psql(postgres, db, "select has_table_privilege('anon', 'public.games', 'insert')") == "f"
    token = claim(postgres, db, "Mikayla", "queen")["token"]
    call(postgres, db, f"public.record_game('{token}', {game(hints=2, moves_uci='e2e4')})")
    assert psql(postgres, db, "select player, hints, moves_uci from public.games") == "Mikayla|2|e2e4"
    assert call(postgres, db, "count(*) from public.games") == "1"  # readable, Mikayla isn't private
    for name in ("upgrade-4.sql", "upgrade-5.sql"):
        psql(postgres, db, (SUPABASE / name).read_text())  # safe to run again

// Paul's Chess web UI: play, review, lessons and player stats.
//
// The page never implements chess rules itself. It keeps a game as a list
// of UCI moves and asks a backend for everything else:
//   backend.state(moves, fen?)                -> legal moves, notation, result
//   backend.move(moves, level, onProgress)    -> the engine's reply
//   backend.review(moves, ply)                -> how good moves[ply] was
// `chessbot serve` provides the HTTP backend below. The static site sets
// window.chessbotBackend to one that runs the engine in the browser.
//
// Finished games are saved to a stats store: a Supabase table when
// config.js names one, otherwise this browser's localStorage.
(() => {
  "use strict";

  const CONFIG = window.CHESSBOT_CONFIG || {};
  const LEVELS = CONFIG.levels || [{ level: 3, name: "Casual", elo: 1100 }];
  const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const FILES = "abcdefgh";
  const GAME_KEY = "chessbot.game.v2";
  const NAME_KEY = "chessbot.name";
  const COACH_KEY = "chessbot.coach";
  const SOUND_KEY = "chessbot.sound";
  const HINTS_PER_GAME = 3;
  const LESSONS_KEY = "chessbot.lessons.done";
  const PIECE_NAMES = { p: "pawn", n: "knight", b: "bishop", r: "rook", q: "queen", k: "king" };
  const VERDICT_GLYPHS = { blunder: "??", mistake: "?", inaccuracy: "?!" };
  const VERDICT_NAMES = { blunder: "Blunder", mistake: "Mistake", inaccuracy: "Inaccuracy" };

  const $ = (id) => document.getElementById(id);

  function storageGet(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Private windows and blocked storage: nothing is remembered.
    }
  }

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
  }

  // ------------------------------------------------------------ backend

  function httpBackend() {
    async function post(path, body) {
      let response;
      try {
        response = await fetch(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch {
        throw new Error("Can't reach the chess bot server. Is `chessbot serve` still running?");
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `The server answered ${response.status}.`);
      return data;
    }
    return {
      ready: Promise.resolve(),
      state: (moves, fen) => post("/api/state", { moves, fen: fen || null }),
      move: (moves, level) => post("/api/move", { moves, level }),
      review: (moves, ply) => post("/api/review", { moves, ply }),
      hint: (moves) => post("/api/hint", { moves }),
      replay: (moves) => post("/api/replay", { moves }),
    };
  }

  const backend = window.chessbotBackend || httpBackend();
  let backendReady = false;

  // ------------------------------------------------------------ stats store

  function localStore() {
    const KEY = "chessbot.games.v1";
    const PUZZLE_KEY = "chessbot.puzzle_attempts.v1";
    return {
      shared: false,
      async listPuzzleAttempts() {
        return storageGet(PUZZLE_KEY, []);
      },
      async savePuzzleAttempt(record) {
        storageSet(PUZZLE_KEY, [record, ...storageGet(PUZZLE_KEY, [])].slice(0, 5000));
      },
      async list() {
        return storageGet(KEY, []);
      },
      async save(record) {
        const games = storageGet(KEY, []);
        games.unshift(record);
        storageSet(KEY, games.slice(0, 2000));
      },
    };
  }

  // Supabase's REST API over a `games` table (see supabase/games.sql).
  function supabaseStore({ url, key }) {
    const PENDING = "chessbot.pending.v1";
    const headers = { apikey: key, "Content-Type": "application/json" };
    // Legacy anon keys are JWTs and also go in Authorization; newer
    // publishable keys (sb_publishable_...) must not.
    if (key.startsWith("eyJ")) headers.Authorization = `Bearer ${key}`;

    // Columns added after the first version of supabase/games.sql. If the
    // table doesn't have them yet, save the game without them.
    const NEWER_COLUMNS = ["hints", "moves_uci"];

    async function insert(record, keepalive = false) {
      const post = (body) =>
        fetch(`${url}/rest/v1/games`, {
          method: "POST",
          headers: { ...headers, Prefer: "return=minimal" },
          body: JSON.stringify(body),
          keepalive,
        });
      let response = await post(record);
      if (response.status === 400 && /column/i.test(await response.text())) {
        const older = { ...record };
        for (const column of NEWER_COLUMNS) delete older[column];
        response = await post(older);
      }
      // 409: already saved (the same game id), which is fine.
      if (!response.ok && response.status !== 409) throw new Error(`Saving failed (${response.status}).`);
    }

    async function flushPending() {
      const pending = storageGet(PENDING, []);
      const left = [];
      for (const record of pending) {
        try {
          await insert(record);
        } catch {
          left.push(record);
        }
      }
      storageSet(PENDING, left);
    }

    // Live games against a friend need supabase/upgrade-3.sql; PostgREST
    // answers 404 for its tables and functions until that has been run.
    const NOT_SET_UP = "Online games aren't switched on for this site yet (supabase/upgrade-3.sql).";

    async function liveGames(ids) {
      if (!ids.length) return [];
      const response = await fetch(`${url}/rest/v1/live_games?id=in.(${ids.join(",")})&select=*`, {
        headers,
        cache: "no-store",
      });
      if (response.status === 404) throw new Error(NOT_SET_UP);
      if (!response.ok) throw new Error(`Couldn't load the game (${response.status}).`);
      return response.json();
    }

    return {
      shared: true,
      flush: flushPending,
      liveGames,
      async liveGame(id) {
        return (await liveGames([id]))[0] || null;
      },
      // Every change to a live game goes through a database function that checks the seat's token.
      async rpc(name, args) {
        const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
          method: "POST",
          headers,
          body: JSON.stringify(args),
        });
        const text = await response.text();
        if (response.status === 404) throw new Error(NOT_SET_UP);
        if (!response.ok) {
          let message = `The server turned that down (${response.status}).`;
          try {
            message = JSON.parse(text).message || message;
          } catch {
            // Not JSON; keep the generic message.
          }
          throw new Error(message);
        }
        return text ? JSON.parse(text) : null;
      },
      // Puzzle attempts need the puzzle_attempts table (supabase/upgrade-2.sql);
      // without it they stay in this browser only.
      async listPuzzleAttempts() {
        const response = await fetch(`${url}/rest/v1/puzzle_attempts?select=*&order=played_at.desc&limit=5000`, { headers });
        return response.ok ? response.json() : [];
      },
      // The latest saved puzzle attempt under this name (any capitalisation), or null.
      async latestPuzzleAttempt(player) {
        const name = player.trim();
        const query = `player=ilike.${encodeURIComponent(name)}&select=player,rating_after,played_at&order=played_at.desc&limit=20`;
        const response = await fetch(`${url}/rest/v1/puzzle_attempts?${query}`, { headers, cache: "no-store" });
        if (!response.ok) return null;
        const rows = await response.json();
        return rows.find((row) => row.player.trim().toLowerCase() === name.toLowerCase()) || null;
      },
      async savePuzzleAttempt(record) {
        await fetch(`${url}/rest/v1/puzzle_attempts`, {
          method: "POST",
          headers: { ...headers, Prefer: "return=minimal" },
          body: JSON.stringify(record),
        });
      },
      async list() {
        await flushPending();
        const response = await fetch(`${url}/rest/v1/games?select=*&order=played_at.desc&limit=5000`, { headers });
        if (!response.ok) throw new Error(`Couldn't load stats (${response.status}).`);
        return response.json();
      },
      async save(record, { keepalive = false } = {}) {
        try {
          await insert(record, keepalive);
        } catch (error) {
          // Keep it and try again next time the stats load.
          storageSet(PENDING, [...storageGet(PENDING, []), record]);
          throw error;
        }
      },
    };
  }

  const stats = CONFIG.stats && CONFIG.stats.url && CONFIG.stats.key ? supabaseStore(CONFIG.stats) : localStore();

  // ------------------------------------------------------------ helpers

  function parseFen(fen) {
    const squares = new Array(64).fill(null);
    fen
      .split(" ")[0]
      .split("/")
      .forEach((row, i) => {
        let file = 0;
        for (const ch of row) {
          if (/\d/.test(ch)) file += Number(ch);
          else squares[(7 - i) * 8 + file++] = ch;
        }
      });
    return squares;
  }

  const squareName = (index) => FILES[index % 8] + (Math.floor(index / 8) + 1);
  const squareIndex = (name) => FILES.indexOf(name[0]) + (Number(name[1]) - 1) * 8;
  const pieceColor = (piece) => (piece === piece.toUpperCase() ? "white" : "black");
  const pieceClass = (piece) => "p-" + (piece === piece.toUpperCase() ? "w" : "b") + piece.toLowerCase();
  const other = (color) => (color === "white" ? "black" : "white");
  const levelInfo = (number) => LEVELS.find((l) => l.level === number) || LEVELS[0];

  function formatScore(info) {
    if (!info) return "0.00";
    if (info.mate !== null && info.mate !== undefined) return info.mate > 0 ? `#${info.mate}` : `#-${-info.mate}`;
    const pawns = info.score / 100;
    return (pawns > 0 ? "+" : pawns < 0 ? "−" : "") + Math.abs(pawns).toFixed(2);
  }

  function formatNodes(n) {
    if (n === undefined || n === null) return "–";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e4) return Math.round(n / 1e3) + "k";
    return String(n);
  }

  // White's share of the eval bar, 0..100.
  function whiteShare(info) {
    if (!info) return 50;
    if (info.mate !== null && info.mate !== undefined) return info.mate > 0 ? 100 : 0;
    return 100 / (1 + Math.pow(10, -info.score / 400));
  }

  function installPieceStyles() {
    const rules = Object.entries(window.CHESSBOT_PIECES || {}).map(([symbol, svg]) => {
      const url = `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}")`;
      return `.${pieceClass(symbol)}{background-image:${url}}`;
    });
    document.head.appendChild(el("style", { text: rules.join("\n") }));
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  // ------------------------------------------------------------ board

  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // A clickable, draggable board. render() takes what to show:
  //   fen, orientation, last ("e2e4"), check ("e1"), marks ({e4: "good"}),
  //   rings ({e4: "danger"}), player (color that may move), legal (UCI moves),
  //   onMove(uci). A new last move slides into place.
  function createBoard(wrap) {
    const board = wrap.querySelector(".board");
    const promotion = wrap.querySelector(".promotion");
    const choices = promotion.querySelector(".promotion-choices");
    let view = { fen: START_FEN, orientation: "white", legal: [], marks: {}, rings: {} };
    let selected = null;
    let drag = null;
    let shown = { fen: null, last: null }; // what the last draw put on the board
    let dropped = false; // the last move was dragged into place, so don't slide it

    const interactive = () => Boolean(view.onMove && view.player && view.legal && view.legal.length);

    function render(next) {
      view = { ...view, marks: {}, rings: {}, last: null, check: null, onMove: null, player: null, legal: [], ...next };
      if (!interactive()) selected = null;
      promotion.hidden = true;
      draw();
    }

    // Slide the piece that just moved from its old square (FLIP: draw it in
    // place, offset it back to where it came from, then let it transition).
    function slideLastMove() {
      const from = board.querySelector(`[data-square="${view.last.slice(0, 2)}"]`);
      const to = board.querySelector(`[data-square="${view.last.slice(2, 4)}"]`);
      const piece = to && to.querySelector(".piece");
      if (!from || !piece) return;
      const a = from.getBoundingClientRect();
      const b = to.getBoundingClientRect();
      piece.style.transition = "none";
      piece.style.transform = `translate(${a.left - b.left}px, ${a.top - b.top}px)`;
      piece.style.zIndex = "5";
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          piece.style.transition = "transform 180ms ease-out";
          piece.style.transform = "";
          piece.addEventListener("transitionend", () => (piece.style.zIndex = ""), { once: true });
        }),
      );
    }

    function draw() {
      const squares = parseFen(view.fen);
      const whiteBottom = view.orientation === "white";
      const targets = new Set();
      if (selected && interactive()) {
        for (const uci of view.legal) if (uci.startsWith(selected)) targets.add(uci.slice(2, 4));
      }
      board.classList.toggle("interactive", interactive());
      board.textContent = "";
      for (let row = 0; row < 8; row++) {
        for (let col = 0; col < 8; col++) {
          const rank = whiteBottom ? 7 - row : row;
          const file = whiteBottom ? col : 7 - col;
          const index = rank * 8 + file;
          const name = squareName(index);
          const piece = squares[index];
          const sq = el("div", { class: "sq " + ((rank + file) % 2 === 0 ? "dark" : "light") });
          sq.dataset.square = name;
          if (view.last && (view.last.slice(0, 2) === name || view.last.slice(2, 4) === name)) sq.classList.add("last");
          if (view.marks[name]) sq.classList.add("mark-" + view.marks[name]);
          if (view.rings[name]) sq.classList.add("ring-" + view.rings[name]);
          if (selected === name) sq.classList.add("selected");
          if (view.check === name) sq.classList.add("check");
          if (targets.has(name)) sq.classList.add("target", ...(piece ? ["capture"] : []));
          if (piece && pieceColor(piece) === view.player) sq.classList.add("own");
          if (piece) {
            sq.append(el("div", { class: "piece " + pieceClass(piece) }));
            sq.setAttribute("aria-label", `${name} ${pieceColor(piece)} ${PIECE_NAMES[piece.toLowerCase()]}`);
          }
          if (col === 0) sq.append(el("span", { class: "coord rank", text: String(rank + 1) }));
          if (row === 7) sq.append(el("span", { class: "coord file", text: FILES[file] }));
          board.append(sq);
        }
      }
      const moved = view.last && view.last !== shown.last && view.fen !== shown.fen && shown.fen !== null;
      if (moved && !dropped && !reducedMotion) slideLastMove();
      if (view.fen !== shown.fen) dropped = false;
      shown = { fen: view.fen, last: view.last };
    }

    function squareAt(x, y) {
      const rect = board.getBoundingClientRect();
      if (x < rect.left || y < rect.top || x >= rect.right || y >= rect.bottom) return null;
      const col = Math.floor(((x - rect.left) / rect.width) * 8);
      const row = Math.floor(((y - rect.top) / rect.height) * 8);
      const whiteBottom = view.orientation === "white";
      return squareName((whiteBottom ? 7 - row : row) * 8 + (whiteBottom ? col : 7 - col));
    }

    function movableFrom(name) {
      const piece = parseFen(view.fen)[squareIndex(name)];
      return Boolean(piece) && pieceColor(piece) === view.player;
    }

    function attempt(from, to) {
      const candidates = view.legal.filter((uci) => uci.slice(0, 4) === from + to);
      if (candidates.length === 0) return false;
      selected = null;
      if (candidates.length > 1) askPromotion(candidates);
      else view.onMove(candidates[0]);
      return true;
    }

    function askPromotion(candidates) {
      choices.textContent = "";
      for (const letter of ["q", "r", "b", "n"]) {
        const uci = candidates.find((c) => c.endsWith(letter));
        if (!uci) continue;
        const symbol = view.player === "white" ? letter.toUpperCase() : letter;
        choices.append(
          el("button", {
            type: "button",
            class: pieceClass(symbol),
            "aria-label": PIECE_NAMES[letter],
            onclick: () => {
              promotion.hidden = true;
              view.onMove(uci);
            },
          }),
        );
      }
      promotion.hidden = false;
      choices.querySelector("button").focus();
    }

    board.addEventListener("pointerdown", (event) => {
      if (!interactive() || event.button > 0) return;
      const name = squareAt(event.clientX, event.clientY);
      if (!name) return;
      if (selected && selected !== name && attempt(selected, name)) return;
      if (!movableFrom(name)) {
        selected = null;
        draw();
        return;
      }
      selected = name;
      draw();
      const piece = board.querySelector(`[data-square="${name}"] .piece`);
      const size = board.getBoundingClientRect().width / 8;
      const ghost = el("div", { class: "ghost " + [...piece.classList].find((c) => c.startsWith("p-")) });
      ghost.style.width = ghost.style.height = `${size}px`;
      ghost.hidden = true;
      document.body.append(ghost);
      drag = { from: name, pointerId: event.pointerId, ghost, x: event.clientX, y: event.clientY, moved: false };
      board.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    board.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
      if (!drag.moved) {
        drag.moved = true;
        drag.ghost.hidden = false;
        const sq = board.querySelector(`[data-square="${drag.from}"]`);
        if (sq) sq.classList.add("dragging");
      }
      drag.ghost.style.left = `${event.clientX}px`;
      drag.ghost.style.top = `${event.clientY}px`;
      for (const node of board.querySelectorAll(".drop-hover")) node.classList.remove("drop-hover");
      const over = squareAt(event.clientX, event.clientY);
      const target = over && board.querySelector(`[data-square="${over}"].target`);
      if (target) target.classList.add("drop-hover");
    });

    function endDrag(event, cancelled) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const { from, ghost, moved } = drag;
      ghost.remove();
      drag = null;
      if (moved && !cancelled) {
        const to = squareAt(event.clientX, event.clientY);
        dropped = true;
        if (to && to !== from && attempt(from, to)) return;
        dropped = false;
      }
      // A tap keeps the piece selected so the next tap can choose its square.
      draw();
    }

    board.addEventListener("pointerup", (event) => endDrag(event, false));
    board.addEventListener("pointercancel", (event) => endDrag(event, true));

    return {
      render,
      deselect() {
        selected = null;
        promotion.hidden = true;
        draw();
      },
    };
  }

  // ------------------------------------------------------------ play: state

  const saved = storageGet(GAME_KEY, {}) || {};
  const game = {
    id: typeof saved.id === "string" ? saved.id : uuid(),
    moves: Array.isArray(saved.moves) ? saved.moves.filter((m) => typeof m === "string") : [],
    human: saved.human === "black" ? "black" : "white",
    level: LEVELS.some((l) => l.level === saved.level) ? saved.level : CONFIG.defaultLevel || LEVELS[0].level,
    flipped: Boolean(saved.flipped),
    takebacks: Number(saved.takebacks) || 0,
    hints: Number(saved.hints) || 0,
    hint: null, // {move, san} shown on the board until the next move
    resigned: Boolean(saved.resigned),
    recorded: Boolean(saved.recorded),
    state: null,
    token: 0, // bumped on every new game so late replies are ignored
    thinking: false,
    pending: false, // the player's move is being processed
    engineInfo: null,
    review: null, // {status, items, done, total, error}
    viewing: null, // index into review.items shown on the board
    error: null,
    loading: "Setting up the board…",
  };
  let playerName = storageGet(NAME_KEY, "") || "";
  let coachMode = Boolean(storageGet(COACH_KEY, false));
  let soundOn = Boolean(storageGet(SOUND_KEY, false));

  function saveGame() {
    storageSet(GAME_KEY, {
      id: game.id,
      moves: game.moves,
      human: game.human,
      level: game.level,
      flipped: game.flipped,
      takebacks: game.takebacks,
      hints: game.hints,
      resigned: game.resigned,
      recorded: game.recorded,
    });
  }

  // {over, result, reason, winner} including resignation, which the rules backend doesn't know about.
  function outcome() {
    if (game.resigned) {
      const winner = other(game.human);
      return { over: true, result: winner === "white" ? "1-0" : "0-1", reason: "resignation", winner };
    }
    const s = game.state;
    if (!s || !s.over) return { over: false };
    const winner = s.result === "1-0" ? "white" : s.result === "0-1" ? "black" : null;
    return { over: true, result: s.result, reason: s.reason, winner };
  }

  const whiteAtBottom = () => (game.human === "white") !== game.flipped;
  const humanToMove = () =>
    Boolean(game.state) &&
    !outcome().over &&
    game.state.turn === game.human &&
    !game.thinking &&
    !game.pending &&
    game.viewing === null;
  const humanPlies = () => game.moves.map((_, i) => i).filter((i) => (i % 2 === 0 ? "white" : "black") === game.human);
  const rated = () => game.takebacks === 0 && game.hints === 0;

  // ------------------------------------------------------------ play: rendering

  const mainBoard = createBoard($("board-wrap"));

  function renderBoard() {
    if (game.viewing !== null && game.review) {
      const item = game.review.items[game.viewing];
      const marks = {};
      marks[item.uci.slice(0, 2)] = marks[item.uci.slice(2, 4)] = "bad";
      marks[item.best_uci.slice(0, 2)] = marks[item.best_uci.slice(2, 4)] = "good";
      mainBoard.render({ fen: item.fen, orientation: whiteAtBottom() ? "white" : "black", marks });
      return;
    }
    const s = game.state;
    // Coach mode rings the player's pieces that can be taken for free, and on
    // the player's turn the bot's too.
    const rings = {};
    if (coachMode && s && !outcome().over) {
      for (const square of s.hanging[game.human]) rings[square] = "danger";
      if (humanToMove()) for (const square of s.hanging[other(game.human)]) rings[square] = "chance";
    }
    const marks = {};
    if (game.hint && humanToMove()) marks[game.hint.move.slice(0, 2)] = marks[game.hint.move.slice(2, 4)] = "good";
    mainBoard.render({
      fen: s ? s.fen : START_FEN,
      orientation: whiteAtBottom() ? "white" : "black",
      last: s && s.last,
      check: s && s.check,
      marks,
      rings,
      player: humanToMove() ? game.human : null,
      legal: humanToMove() ? s.legal : [],
      onMove: playHumanMove,
    });
  }

  function renderPlayers() {
    const bottom = whiteAtBottom() ? "white" : "black";
    const turn = game.state && !outcome().over ? game.state.turn : null;
    const bot = levelInfo(game.level);
    for (const [id, color] of [
      ["player-top", other(bottom)],
      ["player-bottom", bottom],
    ]) {
      const node = $(id);
      // Titan and Pinky are Stockfish, so they play under their own names.
      node.querySelector(".player-name").textContent =
        color === game.human ? playerName || "You" : bot.stockfish ? bot.name : "Paul's Chess Bot";
      node.querySelector(".player-side").textContent =
        color === game.human ? color : `${color} · ${bot.stockfish ? "Stockfish" : bot.name} ${bot.elo}`;
      node.classList.toggle("to-move", turn === color);
    }
  }

  function statusText() {
    if (game.error) return game.error;
    if (!game.state) return game.loading;
    const o = outcome();
    if (o.over) {
      if (o.reason === "resignation") return "You resigned.";
      if (o.reason === "checkmate") return o.winner === game.human ? "Checkmate. You win." : "Checkmate. The bot wins.";
      return `Draw by ${o.reason}.`;
    }
    if (game.thinking) {
      if (levelInfo(game.level).stockfish && !stockfishLoaded) return "Loading Stockfish (about 7 MB, only the first time)…";
      const depth = game.engineInfo && game.engineInfo.depth;
      return depth ? `The bot is thinking… depth ${depth}` : "The bot is thinking…";
    }
    if (game.state.turn === game.human) {
      const base = game.state.check ? "Your move. You're in check." : "Your move.";
      return game.hint ? `${base} Hint: try ${game.hint.san}.` : base;
    }
    return "Waiting for the bot…";
  }

  function renderStatus() {
    $("status").textContent = statusText();
    const info = game.engineInfo;
    $("eval").textContent = formatScore(info);
    $("depth").textContent = info ? (info.book ? "book" : String(info.depth)) : "–";
    $("nodes").textContent = info ? formatNodes(info.nodes) : "–";
    $("time").textContent = info ? `${info.time.toFixed(1)}s` : "–";
    $("pv").textContent = info && info.pv ? (info.book ? `${info.pv} (opening book)` : info.pv) : "";
    $("evalbar-fill").style.height = `${whiteShare(info)}%`;
    const opening = game.state && game.state.opening;
    $("opening-name").hidden = !opening;
    if (opening) {
      $("opening-name").textContent = "";
      $("opening-name").append(el("b", { text: opening.name }), ` · ${opening.eco}`);
    }
    $("evalbar").classList.toggle("white-top", !whiteAtBottom());
  }

  // Fill a move table body with numbered rows of SAN moves, marking review verdicts by ply.
  function fillMoveSheet(body, san, verdicts = {}) {
    body.textContent = "";
    for (let i = 0; i < san.length; i += 2) {
      const tr = el("tr", {}, el("td", { text: `${i / 2 + 1}.` }));
      for (const ply of [i, i + 1]) {
        const td = el("td", { text: san[ply] || "" });
        if (verdicts[ply]) td.append(el("span", { class: `glyph ${verdicts[ply]}`, text: VERDICT_GLYPHS[verdicts[ply]] }));
        if (ply === san.length - 1) td.classList.add("current");
        tr.append(td);
      }
      body.append(tr);
    }
  }

  function renderSheet() {
    const san = game.state ? game.state.san : [];
    const verdicts = {};
    if (game.review) for (const item of game.review.items) if (item.verdict) verdicts[item.ply] = item.verdict;
    fillMoveSheet($("moves"), san, verdicts);
    $("sheet-empty").hidden = san.length > 0;
    $("sheet").scrollTop = $("sheet").scrollHeight;
  }

  function renderControls() {
    const o = outcome();
    const inProgress = Boolean(game.state) && !o.over && game.moves.length > 0;
    $("undo").disabled = !canUndo();
    const hintsLeft = HINTS_PER_GAME - game.hints;
    $("hint").disabled = !humanToMove() || hintsLeft <= 0 || Boolean(game.hint) || !backend.hint;
    $("hint").textContent = hintsLeft > 0 ? `Hint (${hintsLeft} left)` : "No hints left";
    $("coach-toggle").checked = coachMode;
    $("sound-toggle").checked = soundOn;
    $("coach-legend").hidden = !coachMode;
    $("resign").disabled = !game.state || o.over || game.moves.length === 0;
    $("copy-pgn").disabled = !game.state;
    const canType = humanToMove();
    $("move-input").disabled = !canType;
    $("move-submit").disabled = !canType;

    const bot = levelInfo(game.level);
    const chosenLevel = document.querySelector('input[name="level"]:checked');
    const chosenColor = document.querySelector('input[name="color"]:checked');
    const changed =
      (chosenLevel && Number(chosenLevel.value) !== game.level) || (chosenColor && chosenColor.value !== game.human);
    let note;
    if (inProgress && changed) note = "Your new choices take effect when you start a new game.";
    else if (!playerName.trim()) note = "Add your name to save your games to the Stats page.";
    else if (!rated()) {
      const why = game.takebacks && game.hints ? "took back a move and used a hint" : game.takebacks ? "took back a move" : "used a hint";
      note = `Unrated: you ${why}. It still counts on the Stats page, but not for your rating.`;
    }
    else if (inProgress || !o.over) note = `Rated game against ${bot.name} (${bot.elo}).`;
    else note = game.recorded ? "Saved to the Stats page." : "Saving to the Stats page after the review…";
    $("game-note").textContent = note;

    // The game-over note sits over the board, except while a reviewed move is
    // shown there (the review panel explains that one).
    $("board-note").hidden = !o.over || game.viewing !== null;
    if (o.over) $("board-note-text").textContent = `${statusText()} ${o.result}. Start a new game to play again.`;
  }

  function renderReview() {
    const r = game.review;
    const panel = $("review");
    $("view-play").classList.toggle("has-review", Boolean(r));
    panel.hidden = !r;
    if (!r) return;
    $("review-progress").hidden = r.status !== "running";
    $("review-summary").hidden = r.status !== "done";
    $("review-error").hidden = r.status !== "failed";
    if (r.status === "running") {
      $("review-progress-text").textContent = `Reviewing your moves… ${r.done} of ${r.total}`;
      $("review-meter").style.width = `${r.total ? (100 * r.done) / r.total : 0}%`;
    }
    if (r.status === "failed") $("review-error").textContent = `The review couldn't finish: ${r.error}`;
    if (r.status !== "done") return;

    const summary = reviewSummary(r.items);
    $("review-accuracy").textContent = summary.accuracy === null ? "–" : `${summary.accuracy.toFixed(0)}%`;
    const counts = $("review-counts");
    counts.textContent = "";
    const plurals = { blunder: "blunders", mistake: "mistakes", inaccuracy: "inaccuracies" };
    for (const verdict of ["blunder", "mistake", "inaccuracy"]) {
      const n = summary.counts[verdict];
      const label = n === 1 ? verdict : plurals[verdict];
      counts.append(el("li", { class: verdict }, el("b", { text: String(n) }), ` ${label}`));
    }
    const list = $("review-list");
    list.textContent = "";
    const worst = r.items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => item.verdict)
      .sort((a, b) => b.item.loss - a.item.loss)
      .slice(0, 5);
    $("review-empty").hidden = worst.length > 0;
    $("review-list-title").hidden = worst.length === 0;
    const viewing = game.viewing !== null ? r.items[game.viewing] : null;
    $("review-viewing").hidden = !viewing;
    if (viewing) {
      $("review-viewing-text").textContent =
        `On the board: move ${viewing.number}, where you played ${viewing.san} (red squares). ` +
        `${viewing.best_san} (green squares) was better.`;
    }
    for (const { item, index } of worst) {
      const dots = item.color === "white" ? "." : "...";
      list.append(
        el(
          "li",
          {},
          el(
            "button",
            {
              type: "button",
              "aria-pressed": String(game.viewing === index),
              onclick: () => viewReviewItem(game.viewing === index ? null : index),
            },
            el("span", { class: "mistake-move", text: `${item.number}${dots} ${item.san}${VERDICT_GLYPHS[item.verdict]}` }),
            el("span", { class: `chip ${item.verdict}`, text: VERDICT_NAMES[item.verdict] }),
            el("span", { class: "mistake-better" }, "Better was ", el("b", { text: item.best_san })),
            el("span", {
              class: "mistake-eval",
              text: `${formatScore(item.before)} → ${formatScore(item.after)} · ${item.line}`,
            }),
          ),
        ),
      );
    }
  }

  function render() {
    renderBoard();
    renderPlayers();
    renderStatus();
    renderSheet();
    renderControls();
    renderReview();
  }

  // ------------------------------------------------------------ play: flow

  // Short synthesized sounds, so there are no audio files to load.
  let audio = null;
  function playSound(kind) {
    if (!soundOn) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const notes = {
        move: [[520, 0, 0.05, "triangle"]],
        capture: [[200, 0, 0.09, "square"], [140, 0.02, 0.1, "triangle"]],
        check: [[660, 0, 0.08, "sine"], [880, 0.08, 0.12, "sine"]],
        end: [[523, 0, 0.18, "sine"], [659, 0.1, 0.18, "sine"], [784, 0.2, 0.3, "sine"]],
      }[kind];
      const now = audio.currentTime;
      for (const [freq, start, length, type] of notes) {
        const osc = audio.createOscillator();
        const gain = audio.createGain();
        osc.type = type;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, now + start);
        gain.gain.exponentialRampToValueAtTime(0.18, now + start + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + start + length);
        osc.connect(gain).connect(audio.destination);
        osc.start(now + start);
        osc.stop(now + start + length + 0.02);
      }
    } catch {
      // No audio available; play silently.
    }
  }

  function soundForLastMove(state) {
    const san = state.san[state.san.length - 1] || "";
    playSound(san.includes("#") ? "end" : san.includes("+") ? "check" : san.includes("x") ? "capture" : "move");
  }

  async function setMoves(moves) {
    const token = game.token;
    const state = await backend.state(moves);
    if (token !== game.token) return false;
    const advanced = moves.length > game.moves.length;
    game.moves = moves;
    game.state = state;
    game.hint = null;
    if (advanced) soundForLastMove(state);
    game.error = null;
    saveGame();
    render();
    return true;
  }

  async function playHumanMove(uci) {
    if (game.pending) return;
    game.pending = true; // no second move until this one is processed
    renderBoard();
    try {
      const applied = await setMoves([...game.moves, uci]);
      game.pending = false;
      if (!applied) return;
      if (outcome().over) finishGame();
      else engineTurn();
    } catch (error) {
      game.pending = false;
      showError(error);
      render();
    }
  }

  // ------------------------------------------------------------ play: stockfish

  // Titan and Pinky are Stockfish, held to their rating with UCI_Elo. It runs
  // in a Web Worker with Stockfish.js (GPL-3.0), served from this site (about
  // 7 MB, see web/stockfish/) the first time one of them plays; the service
  // worker keeps it for offline play.
  const STOCKFISH_JS = "stockfish/stockfish-18-lite-single.js";
  const STOCKFISH_MOVE_MS = 1000;
  let stockfishEngine = null;
  let stockfishLoaded = false;

  function startStockfish() {
    const listeners = new Set();
    let failure = null;
    // Stockfish.js finds its .wasm next to itself.
    const worker = new Worker(STOCKFISH_JS);
    worker.onmessage = (event) => {
      for (const listener of [...listeners]) listener(String(event.data));
    };
    worker.onerror = (event) => {
      failure = new Error(`Stockfish couldn't start (${event.message || "no connection?"}).`);
      for (const listener of [...listeners]) listener(null);
    };
    const send = (command) => worker.postMessage(command);
    // Resolves with the first line that passes `test`.
    const next = (test, ms) =>
      new Promise((resolve, reject) => {
        const finish = (settle, value) => {
          clearTimeout(timer);
          listeners.delete(listener);
          settle(value);
        };
        const listener = (line) => {
          if (line === null) finish(reject, failure);
          else if (test(line)) finish(resolve, line);
        };
        const timer = setTimeout(() => finish(reject, new Error("Stockfish stopped answering.")), ms);
        listeners.add(listener);
      });
    const engine = { send, next, listeners, busy: null };
    engine.ready = (async () => {
      send("uci");
      await next((line) => line === "uciok", 120000);
      send("setoption name UCI_LimitStrength value true");
      send("isready");
      await next((line) => line === "readyok", 60000);
    })();
    engine.ready.catch(() => {
      if (stockfishEngine === engine) stockfishEngine = null;
      worker.terminate();
    });
    return engine;
  }

  // Stockfish's move after `moves`, at UCI_Elo `elo`, with its search info from White's side.
  async function stockfishMove(moves, elo, onProgress) {
    if (typeof WebAssembly !== "object") throw new Error("This browser can't run Stockfish (it needs WebAssembly).");
    stockfishEngine = stockfishEngine || startStockfish();
    const engine = stockfishEngine;
    await engine.ready;
    stockfishLoaded = true;
    // A search for an abandoned game may still be running.
    if (engine.busy) {
      engine.send("stop");
      await engine.busy.catch(() => {});
    }
    const sign = moves.length % 2 === 0 ? 1 : -1; // Stockfish scores from the side to move
    const started = performance.now();
    let info = { depth: 0, score: 0, mate: null, nodes: 0 };
    const progress = (line) => {
      if (!line || !line.startsWith("info ") || / multipv [2-9]/.test(line)) return;
      const depth = / depth (\d+)/.exec(line);
      const cp = / score cp (-?\d+)/.exec(line);
      const mate = / score mate (-?\d+)/.exec(line);
      if (!depth || !(cp || mate)) return;
      const nodes = / nodes (\d+)/.exec(line);
      info = {
        depth: Number(depth[1]),
        score: cp ? sign * Number(cp[1]) : null,
        mate: mate ? sign * Number(mate[1]) : null,
        nodes: nodes ? Number(nodes[1]) : 0,
      };
      if (onProgress) onProgress({ ...info, time: (performance.now() - started) / 1000, pv: "" });
    };
    engine.listeners.add(progress);
    try {
      engine.send(`setoption name UCI_Elo value ${elo}`);
      engine.send(`position startpos${moves.length ? ` moves ${moves.join(" ")}` : ""}`);
      engine.busy = engine.next((line) => line.startsWith("bestmove"), 60000);
      engine.send(`go movetime ${STOCKFISH_MOVE_MS}`);
      const line = await engine.busy;
      return { ...info, move: line.split(" ")[1], time: (performance.now() - started) / 1000, pv: "" };
    } finally {
      engine.busy = null;
      engine.listeners.delete(progress);
    }
  }

  // The same {reply, state} as backend.move, for a Stockfish level.
  async function stockfishReply(moves, elo, onProgress) {
    const found = await stockfishMove(moves, elo, onProgress);
    const state = await backend.state([...moves, found.move]);
    return { reply: { ...found, san: state.san[state.san.length - 1] }, state };
  }

  async function engineTurn() {
    const s = game.state;
    if (!s || outcome().over || s.turn === game.human || game.thinking) return;
    const token = game.token;
    game.thinking = true;
    game.engineInfo = null;
    render();
    try {
      const onProgress = (info) => {
        if (token !== game.token) return;
        game.engineInfo = info;
        renderStatus();
      };
      const level = levelInfo(game.level);
      // Paul asked for easier games: for him the bot quietly plays well below
      // its label, and the game still records the chosen level.
      const handicapped = playerName.trim().toLowerCase() === "paul";
      const { reply, state } = level.stockfish
        ? await stockfishReply(game.moves, handicapped ? Math.max(1320, level.stockfish - 500) : level.stockfish, onProgress)
        : await backend.move(game.moves, handicapped ? Math.max(1, game.level - 2) : game.level, onProgress);
      if (token !== game.token) return;
      game.moves = [...game.moves, reply.move];
      game.state = state;
      game.engineInfo = reply;
      game.hint = null;
      soundForLastMove(state);
      saveGame();
    } catch (error) {
      if (token === game.token) game.error = error.message || String(error);
    } finally {
      if (token === game.token) {
        game.thinking = false;
        render();
        if (outcome().over) finishGame();
      }
    }
  }

  function canUndo() {
    if (!game.state || game.thinking || outcome().over) return false;
    return game.moves.length >= (game.state.turn === game.human ? 2 : 1);
  }

  async function newGame() {
    // A finished game that is still being reviewed gets saved without the review.
    if (outcome().over && !game.recorded) recordGame(null);
    game.token++;
    game.id = uuid();
    game.thinking = false;
    game.engineInfo = null;
    game.review = null;
    game.viewing = null;
    game.takebacks = 0;
    game.hints = 0;
    game.hint = null;
    game.resigned = false;
    game.recorded = false;
    game.human = document.querySelector('input[name="color"]:checked').value;
    const level = document.querySelector('input[name="level"]:checked');
    if (level) game.level = Number(level.value);
    try {
      await setMoves([]);
      engineTurn();
    } catch (error) {
      showError(error);
    }
  }

  function showError(error) {
    game.error = error.message || String(error);
    renderStatus();
  }

  // ------------------------------------------------------------ play: review and recording

  function finishGame() {
    if (outcome().reason !== "checkmate") playSound("end");
    if (game.review || game.recorded) {
      render();
      return;
    }
    runReview();
  }

  async function runReview() {
    const token = game.token;
    const plies = humanPlies();
    if (plies.length === 0) {
      recordGame(null);
      render();
      return;
    }
    game.review = { status: "running", items: [], done: 0, total: plies.length };
    render();
    const moves = game.moves.slice();
    try {
      for (const ply of plies) {
        const item = await backend.review(moves, ply);
        if (token !== game.token) return;
        game.review.items.push(item);
        game.review.done++;
        renderReview();
      }
      game.review.status = "done";
    } catch (error) {
      if (token !== game.token) return;
      game.review.status = "failed";
      game.review.error = error.message || String(error);
    }
    if (!game.recorded) recordGame(game.review.status === "done" ? game.review.items : null);
    render();
  }

  function reviewSummary(items) {
    const counts = { blunder: 0, mistake: 0, inaccuracy: 0 };
    for (const item of items) if (item.verdict) counts[item.verdict]++;
    const accuracy = items.length ? items.reduce((sum, item) => sum + item.accuracy, 0) / items.length : null;
    return { counts, accuracy };
  }

  function viewReviewItem(index) {
    game.viewing = index;
    render();
  }

  function openingLabel() {
    if (!game.state) return "";
    if (game.state.opening) return game.state.opening.name;
    return game.state.san.slice(0, 4).join(" ");
  }

  function recordGame(reviewItems, { keepalive = false } = {}) {
    const o = outcome();
    game.recorded = true;
    saveGame();
    const name = playerName.trim();
    if (!o.over || !name || game.moves.length < 2) return;
    const bot = levelInfo(game.level);
    const summary = reviewItems ? reviewSummary(reviewItems) : null;
    const record = {
      id: game.id,
      played_at: new Date().toISOString(),
      player: name.slice(0, 24),
      color: game.human,
      result: o.winner === null ? "draw" : o.winner === game.human ? "win" : "loss",
      reason: o.reason,
      level: game.level,
      bot_elo: bot.elo,
      moves: Math.ceil(game.moves.length / 2),
      accuracy: summary && summary.accuracy !== null ? Math.round(summary.accuracy * 10) / 10 : null,
      blunders: summary ? summary.counts.blunder : null,
      mistakes: summary ? summary.counts.mistake : null,
      inaccuracies: summary ? summary.counts.inaccuracy : null,
      takebacks: game.takebacks,
      hints: game.hints,
      opening: openingLabel().slice(0, 80),
      moves_uci: game.moves.join(" ").slice(0, 10000),
    };
    stats.save(record, { keepalive }).catch(() => {
      // Supabase saves that fail are queued and retried when stats next load.
    });
    statsCache = null;
  }

  // If the page closes before a finished game's review is done, save the result anyway.
  window.addEventListener("pagehide", () => {
    if (outcome().over && !game.recorded) recordGame(null, { keepalive: true });
  });

  // ------------------------------------------------------------ play: input

  // Match typed SAN (any case, check marks optional, 0-0 for O-O) or UCI.
  function findTypedMove(text) {
    const s = game.state;
    const clean = (t) => t.replace(/[+#!?]/g, "").replace(/0/g, "O").trim();
    const wanted = clean(text);
    let index = s.legal_san.findIndex((san) => clean(san) === wanted);
    if (index < 0) index = s.legal_san.findIndex((san) => clean(san).toLowerCase() === wanted.toLowerCase());
    if (index >= 0) return s.legal[index];
    const uci = text.trim().toLowerCase();
    if (s.legal.includes(uci)) return uci;
    if (s.legal.includes(uci + "q")) return uci + "q"; // e7e8 promotes to a queen
    return null;
  }

  $("move-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("move-input");
    const text = input.value;
    if (!text.trim() || !humanToMove()) return;
    const uci = findTypedMove(text);
    const error = $("move-error");
    if (!uci) {
      error.textContent = `"${text.trim()}" isn't a legal move here. Try something like e4, Nf3 or O-O.`;
      error.hidden = false;
      return;
    }
    error.hidden = true;
    input.value = "";
    playHumanMove(uci);
  });

  $("new-game").addEventListener("click", newGame);

  $("coach-toggle").addEventListener("change", (event) => {
    coachMode = event.target.checked;
    storageSet(COACH_KEY, coachMode);
    render();
  });

  $("sound-toggle").addEventListener("change", (event) => {
    soundOn = event.target.checked;
    storageSet(SOUND_KEY, soundOn);
    if (soundOn) playSound("move"); // also unlocks audio, which needs a click
  });

  $("hint").addEventListener("click", async () => {
    if (!humanToMove() || game.hints >= HINTS_PER_GAME || game.hint) return;
    const token = game.token;
    $("hint").disabled = true;
    try {
      const hint = await backend.hint(game.moves);
      if (token !== game.token) return;
      game.hints++;
      game.hint = hint;
      saveGame();
    } catch (error) {
      showError(error);
    }
    render();
  });

  $("undo").addEventListener("click", async () => {
    if (!canUndo()) return;
    try {
      const count = game.state.turn === game.human ? 2 : 1;
      game.takebacks++;
      await setMoves(game.moves.slice(0, game.moves.length - count));
      game.engineInfo = null;
      render();
      engineTurn();
    } catch (error) {
      showError(error);
    }
  });

  let resignTimer = null;
  $("resign").addEventListener("click", () => {
    const button = $("resign");
    if (!button.classList.contains("confirming")) {
      button.classList.add("confirming");
      button.textContent = "Confirm resign";
      resignTimer = setTimeout(() => {
        button.classList.remove("confirming");
        button.textContent = "Resign";
      }, 3000);
      return;
    }
    clearTimeout(resignTimer);
    button.classList.remove("confirming");
    button.textContent = "Resign";
    game.token++; // ignore a reply that is still being computed
    game.thinking = false;
    game.resigned = true;
    saveGame();
    render();
    finishGame();
  });

  $("flip").addEventListener("click", () => {
    game.flipped = !game.flipped;
    saveGame();
    render();
  });

  $("review-back").addEventListener("click", () => viewReviewItem(null));

  $("player-name").value = playerName;
  $("player-name").addEventListener("input", (event) => {
    playerName = event.target.value.slice(0, 24);
    storageSet(NAME_KEY, playerName);
    renderPlayers();
    renderControls();
  });

  // Show the current game's settings in the pickers (only at start and for a new game,
  // so that choosing settings for the next game isn't undone by every re-render).
  function syncChoices() {
    $(`color-${game.human}`).checked = true;
    const level = $(`level-${game.level}`);
    if (level) level.checked = true;
  }

  for (const input of document.querySelectorAll('input[name="color"]')) input.addEventListener("change", () => renderControls());

  function buildLevelPicker() {
    const container = $("levels");
    for (const level of LEVELS) {
      container.append(
        el(
          "label",
          {},
          el("input", {
            type: "radio",
            name: "level",
            id: `level-${level.level}`,
            value: String(level.level),
            onchange: () => renderControls(),
          }),
          el("span", {}, el("b", { text: level.name }), el("small", { text: String(level.elo) })),
        ),
      );
    }
  }

  $("copy-pgn").addEventListener("click", () => {
    if (!game.state) return;
    const pgn = game.state.pgn;
    const box = $("pgn");
    const fallback = () => {
      box.value = pgn;
      box.hidden = false;
      box.focus();
      box.select();
    };
    if (!navigator.clipboard) return fallback();
    navigator.clipboard.writeText(pgn).then(() => {
      const button = $("copy-pgn");
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Copy PGN"), 1500);
    }, fallback);
  });

  // ------------------------------------------------------------ learn

  const learn = { lessons: [], index: 0, moves: [], state: null, solved: false, token: 0 };
  const puzzleBoard = createBoard($("puzzle-wrap"));
  const lessonsDone = new Set(storageGet(LESSONS_KEY, []));

  async function loadLessons() {
    try {
      const response = await fetch("lessons.json");
      learn.lessons = await response.json();
    } catch {
      learn.lessons = [];
    }
    renderLessonList();
    openLesson(0);
  }

  function renderLessonList() {
    const list = $("lesson-list");
    list.textContent = "";
    learn.lessons.forEach((lesson, index) => {
      list.append(
        el(
          "li",
          {},
          el(
            "button",
            {
              type: "button",
              class: lessonsDone.has(lesson.id) ? "done" : "",
              "aria-current": String(index === learn.index),
              onclick: () => openLesson(index),
            },
            el("span", { class: "lesson-name", text: lesson.title }),
            el("span", { class: "lesson-summary", text: lesson.summary }),
          ),
        ),
      );
    });
  }

  function openLesson(index) {
    const lesson = learn.lessons[index];
    if (!lesson) return;
    learn.index = index;
    learn.token++;
    $("lesson-title").textContent = lesson.title;
    const body = $("lesson-body");
    body.textContent = "";
    for (const paragraph of lesson.body) body.append(el("p", { text: paragraph }));
    $("lesson-prev").disabled = index === 0;
    $("lesson-next").disabled = index === learn.lessons.length - 1;
    $("puzzle").hidden = !lesson.puzzle;
    renderLessonList();
    if (lesson.puzzle) resetPuzzle();
    else markLessonDone(lesson);
  }

  const puzzleTurn = (fen) => (fen.split(" ")[1] === "w" ? "white" : "black");

  async function resetPuzzle() {
    const lesson = learn.lessons[learn.index];
    learn.moves = [];
    learn.solved = false;
    learn.state = null;
    $("puzzle-goal").textContent = lesson.puzzle.goal;
    setFeedback(backendReady ? "" : "The board wakes up once the engine has loaded.", "");
    renderPuzzle();
    if (backendReady) await refreshPuzzleState();
  }

  async function refreshPuzzleState() {
    const token = learn.token;
    const lesson = learn.lessons[learn.index];
    const state = await backend.state(learn.moves, lesson.puzzle.fen);
    if (token !== learn.token) return;
    learn.state = state;
    renderPuzzle();
  }

  function renderPuzzle() {
    const puzzle = learn.lessons[learn.index].puzzle;
    const solver = puzzleTurn(puzzle.fen);
    const s = learn.state;
    const canMove = s && !learn.solved && s.turn === solver && !s.over;
    puzzleBoard.render({
      fen: s ? s.fen : puzzle.fen,
      orientation: solver,
      last: s && s.last,
      check: s && s.check,
      player: canMove ? solver : null,
      legal: canMove ? s.legal : [],
      onMove: puzzleMove,
    });
    $("puzzle-hint").disabled = learn.solved;
    $("puzzle-answer").disabled = learn.solved || !backendReady;
  }

  function setFeedback(text, kind) {
    const node = $("puzzle-feedback");
    node.textContent = text;
    node.className = "puzzle-feedback" + (kind ? ` ${kind}` : "");
  }

  async function puzzleMove(uci) {
    const token = learn.token;
    const lesson = learn.lessons[learn.index];
    const puzzle = lesson.puzzle;
    const step = learn.moves.length;
    let correct = puzzle.accept ? puzzle.accept.includes(uci) : uci === puzzle.solution[step];
    let after = null;
    if (!correct && puzzle.mate) {
      // Any checkmate counts, not just the one in the answer key.
      after = await backend.state([...learn.moves, uci], puzzle.fen);
      correct = after.over && after.reason === "checkmate";
    }
    if (token !== learn.token) return;
    if (!correct) {
      setFeedback("Not quite. Try again, or take a hint.", "wrong");
      renderPuzzle();
      return;
    }
    learn.moves.push(uci);
    const remaining = puzzle.solution ? puzzle.solution.length - learn.moves.length : 0;
    if (!puzzle.accept && remaining > 0 && !puzzle.mate) {
      setFeedback("Good. Now the opponent replies…", "right");
      await refreshPuzzleState();
      await new Promise((resolve) => setTimeout(resolve, 600));
      if (token !== learn.token) return;
      learn.moves.push(puzzle.solution[learn.moves.length]);
      setFeedback("Your move again.", "right");
      await refreshPuzzleState();
      return;
    }
    learn.solved = true;
    setFeedback(`Correct! ${puzzle.explain}`, "right");
    markLessonDone(lesson);
    await refreshPuzzleState();
  }

  async function showAnswer() {
    const token = learn.token;
    const puzzle = learn.lessons[learn.index].puzzle;
    const answer = puzzle.solution || [puzzle.accept[0]];
    learn.solved = true;
    learn.moves = [];
    await refreshPuzzleState();
    for (const uci of answer) {
      await new Promise((resolve) => setTimeout(resolve, 600));
      if (token !== learn.token) return;
      learn.moves.push(uci);
      await refreshPuzzleState();
    }
    setFeedback(puzzle.explain, "");
  }

  function markLessonDone(lesson) {
    lessonsDone.add(lesson.id);
    storageSet(LESSONS_KEY, [...lessonsDone]);
    renderLessonList();
  }

  $("puzzle-hint").addEventListener("click", () => setFeedback(learn.lessons[learn.index].puzzle.hint, ""));
  $("puzzle-answer").addEventListener("click", showAnswer);
  $("puzzle-reset").addEventListener("click", () => {
    learn.token++;
    resetPuzzle();
  });
  $("lesson-prev").addEventListener("click", () => openLesson(learn.index - 1));
  $("lesson-next").addEventListener("click", () => openLesson(learn.index + 1));

  // ------------------------------------------------------------ stats

  let statsCache = null;
  let statsLoadedAt = 0;
  let puzzleCache = [];

  // Each player's latest puzzle rating and number of puzzles tried, keyed by lower-case name.
  function puzzleRatings() {
    const byPlayer = new Map();
    for (const a of [...puzzleCache].sort((x, y) => (x.played_at < y.played_at ? -1 : 1))) {
      const key = a.player.trim().toLowerCase();
      const entry = byPlayer.get(key) || { rating: null, tried: 0, solved: 0 };
      entry.rating = a.rating_after;
      entry.tried++;
      if (a.solved) entry.solved++;
      byPlayer.set(key, entry);
    }
    return byPlayer;
  }
  let statsPlayer = null;

  async function loadStats(force = false) {
    $("stats-source").textContent = stats.shared
      ? "Everyone's games, shared online."
      : "Games played in this browser. Connect a database to share stats between everyone (see the README).";
    const message = $("stats-message");
    // Reload data more than a minute old, so games and changes from elsewhere show up.
    if (!statsCache || force || Date.now() - statsLoadedAt > 60000) {
      message.hidden = false;
      message.textContent = "Loading…";
      try {
        const [games, attempts] = await Promise.all([stats.list(), stats.listPuzzleAttempts().catch(() => [])]);
        puzzleCache = attempts;
        statsCache = currentGames(games);
        statsLoadedAt = Date.now();
        message.hidden = true;
      } catch (error) {
        message.textContent = `${error.message} Check your connection and press Refresh.`;
        return;
      }
    }
    renderStats();
  }

  function summarizePlayers(games) {
    const players = new Map();
    for (const g of games) {
      const key = g.player.trim().toLowerCase();
      if (!players.has(key)) players.set(key, { name: g.player.trim(), games: [] });
      players.get(key).games.push(g);
    }
    // People who have only done puzzles belong on the board too.
    for (const a of puzzleCache) {
      const key = a.player.trim().toLowerCase();
      if (!players.has(key)) players.set(key, { name: a.player.trim(), games: [] });
    }
    const puzzles = puzzleRatings();
    return [...players.values()].map((p) => ({
      ...p,
      ...summarizeGames(p.games),
      puzzles: puzzles.get(p.name.toLowerCase()) || null,
    }));
  }

  // Ratings are Elo, like the puzzle rating: everyone starts at RATING_START
  // (or their entry in RATING_STARTS) and each rated game moves it, more for a
  // surprise result.
  const RATING_START = 1000;
  const RATING_STARTS = { paul: 1150 };
  const RATING_EPOCH = "2026-09-25T21:00:00Z";
  const startingRating = (name) => RATING_STARTS[(name || "").trim().toLowerCase()] || RATING_START;

  // Games from before ratings began aren't shown anywhere.
  const currentGames = (games) => games.filter((g) => g.played_at >= RATING_EPOCH);
  // Games that move the rating: no take-backs or hints.
  const isRated = (g) => !g.takebacks && !g.hints && g.played_at >= RATING_EPOCH;

  function eloWalk(games) {
    const rated = games
      .filter(isRated)
      .sort((a, b) => (a.played_at < b.played_at ? -1 : 1));
    let rating = startingRating(games.length ? games[0].player : "");
    return rated.map((g, i) => {
      const k = i < 20 ? 40 : 20;
      const expected = 1 / (1 + Math.pow(10, (g.bot_elo - rating) / 400));
      const score = g.result === "win" ? 1 : g.result === "draw" ? 0.5 : 0;
      rating = Math.max(100, rating + k * (score - expected));
      return { n: i + 1, game: g, rating: Math.round(rating) };
    });
  }

  function summarizeGames(games) {
    const wins = games.filter((g) => g.result === "win").length;
    const draws = games.filter((g) => g.result === "draw").length;
    const losses = games.length - wins - draws;
    const reviewed = games.filter((g) => typeof g.accuracy === "number");
    const walk = eloWalk(games);
    const rating = games.length ? (walk.length ? walk[walk.length - 1].rating : startingRating(games[0].player)) : null;
    const bestWin = games
      .filter((g) => g.result === "win")
      .reduce((best, g) => (!best || g.bot_elo > best.bot_elo ? g : best), null);
    return {
      count: games.length,
      wins,
      draws,
      losses,
      score: games.length ? (wins + draws / 2) / games.length : null,
      accuracy: reviewed.length ? reviewed.reduce((sum, g) => sum + g.accuracy, 0) / reviewed.length : null,
      blunders: reviewed.length ? reviewed.reduce((sum, g) => sum + (g.blunders || 0), 0) / reviewed.length : null,
      rating,
      provisional: walk.length < 5,
      ratedCount: walk.length,
      bestWin,
      last: games.reduce((last, g) => (g.played_at > last ? g.played_at : last), ""),
    };
  }

  const pct = (x) => (x === null ? "–" : `${Math.round(x * 100)}%`);
  const acc = (x) => (x === null ? "–" : `${x.toFixed(0)}%`);
  const wdl = (s) => `${s.wins}–${s.draws}–${s.losses}`;
  const levelName = (g) => {
    const level = LEVELS.find((l) => l.level === g.level);
    return level ? `${level.name} (${g.bot_elo})` : String(g.bot_elo);
  };

  function formatDate(iso) {
    if (!iso) return "–";
    const date = new Date(iso);
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function tile(label, value, detail) {
    return el("div", {}, el("dt", { text: label }), el("dd", {}, value, detail ? el("small", { text: detail }) : null));
  }

  function ratingCell(s) {
    if (s.rating === null) return el("span", { class: "provisional", text: "–" });
    return el("span", {}, String(s.rating), s.provisional ? el("span", { class: "provisional", text: "?" }) : null);
  }

  function renderStats() {
    const games = statsCache || [];
    const players = summarizePlayers(games).sort(
      (a, b) =>
        (a.rating === null) - (b.rating === null) ||
        a.provisional - b.provisional ||
        (b.rating || 0) - (a.rating || 0) ||
        b.count - a.count,
    );
    const detail = statsPlayer && players.find((p) => p.name.toLowerCase() === statsPlayer.toLowerCase());
    $("stats-overview").hidden = Boolean(detail) || Boolean(viewer.record);
    $("stats-player").hidden = !detail || Boolean(viewer.record);
    $("stats-game").hidden = !viewer.record;
    if (viewer.record) return renderViewer();
    if (detail) return renderPlayer(detail);

    const all = summarizeGames(games);
    const tiles = $("stats-tiles");
    tiles.textContent = "";
    tiles.append(
      tile("Games played", String(games.length)),
      tile("Players", String(players.length)),
      tile("The bot's record", games.length ? `${all.losses}–${all.draws}–${all.wins}` : "–", "wins–draws–losses"),
      tile("Average accuracy", acc(all.accuracy)),
    );

    const body = $("leaderboard-body");
    body.textContent = "";
    if (players.length === 0) {
      body.append(
        el(
          "tr",
          {},
          el("td", {
            class: "empty",
            colspan: "10",
            text: "No games yet. Put your name in on the Play tab and finish a game to appear here.",
          }),
        ),
      );
      return;
    }
    players.forEach((p, i) => {
      const open = () => {
        statsPlayer = p.name;
        renderStats();
      };
      body.append(
        el(
          "tr",
          { class: "clickable", onclick: open },
          el("td", { class: "num", text: String(i + 1) }),
          el("td", {}, el("button", { type: "button", class: "player-link", text: p.name, onclick: open })),
          el("td", { class: "num" }, ratingCell(p)),
          el("td", { class: "num", text: p.puzzles ? String(p.puzzles.rating) : "–" }),
          el("td", { class: "num", text: String(p.count) }),
          el("td", { class: "num", text: wdl(p) }),
          el("td", { class: "num", text: pct(p.score) }),
          el("td", { class: "num", text: acc(p.accuracy) }),
          el("td", { text: p.bestWin ? levelName(p.bestWin) : "–" }),
          el("td", { text: formatDate(p.last) }),
        ),
      );
    });
  }

  function renderPlayer(p) {
    $("player-title").textContent = p.name;
    const tiles = $("player-tiles");
    tiles.textContent = "";
    tiles.append(
      tile(
        "Rating",
        p.rating === null ? "–" : `${p.rating}${p.provisional ? "?" : ""}`,
        `from ${p.ratedCount} rated game${p.ratedCount === 1 ? "" : "s"}`,
      ),
      tile("Games", String(p.count), wdl(p) + " (W–D–L)"),
      tile("Score", pct(p.score)),
      tile("Accuracy", acc(p.accuracy), "average over reviewed games"),
      tile("Blunders per game", p.blunders === null ? "–" : p.blunders.toFixed(1)),
      tile(
        "Puzzle rating",
        p.puzzles ? String(p.puzzles.rating) : "–",
        p.puzzles ? `${p.puzzles.solved} of ${p.puzzles.tried} solved` : "no puzzles yet",
      ),
    );
    renderRatingChart(ratingHistory(p.games));
    renderBadges(p.games);

    const byLevel = $("player-levels");
    byLevel.textContent = "";
    for (const level of LEVELS) {
      const games = p.games.filter((g) => g.level === level.level);
      if (!games.length) continue;
      const s = summarizeGames(games);
      byLevel.append(
        el(
          "tr",
          {},
          el("td", { text: `${level.name} (${level.elo})` }),
          el("td", { class: "num", text: String(s.count) }),
          el("td", { class: "num", text: wdl(s) }),
          el("td", { class: "num", text: pct(s.score) }),
          el("td", { class: "num", text: acc(s.accuracy) }),
        ),
      );
    }

    const recent = $("player-games");
    recent.textContent = "";
    const recentGames = [...p.games].sort((a, b) => (a.played_at < b.played_at ? 1 : -1)).slice(0, 20);
    $("replay-tip").hidden = !recentGames.some((g) => g.moves_uci);
    for (const g of recentGames) {
      const result = { win: "Win", loss: "Loss", draw: "Draw" }[g.result];
      const replayable = Boolean(g.moves_uci);
      recent.append(
        el(
          "tr",
          replayable
            ? { class: "clickable", tabindex: "0", title: "Replay this game", onclick: () => openGame(g),
                onkeydown: (event) => event.key === "Enter" && openGame(g) }
            : {},
          el("td", { text: formatDate(g.played_at) }),
          el("td", { text: levelName(g) }),
          el("td", { text: g.color }),
          el(
            "td",
            {},
            el("span", { class: `result-${g.result}`, text: `${result} (${g.reason})` }),
            isRated(g) ? null : el("span", { class: "provisional", text: " · unrated" }),
          ),
          el("td", { class: "num", text: String(g.moves) }),
          el("td", { class: "num", text: typeof g.accuracy === "number" ? acc(g.accuracy) : "–" }),
          el("td", { class: "num", text: typeof g.blunders === "number" ? String(g.blunders) : "–" }),
        ),
      );
    }
  }

  // ------------------------------------------------------------ stats: rating chart

  // The estimated rating after each rated game, oldest first.
  // The chart's points: the starting rating, then the rating after each rated game.
  function ratingHistory(games) {
    const walk = eloWalk(games);
    return walk.length ? [{ n: 0, game: null, rating: startingRating(games[0].player) }, ...walk] : [];
  }

  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  let chartPoints = [];

  function resultWords(g) {
    return `${{ win: "Win", loss: "Loss", draw: "Draw" }[g.result]} vs ${levelName(g)}`;
  }

  function renderRatingChart(points) {
    chartPoints = points;
    $("rating-chart-block").hidden = points.length < 2;
    const table = $("rating-table");
    table.textContent = "";
    for (const pt of [...points].reverse()) {
      table.append(
        el(
          "tr",
          {},
          el("td", { class: "num", text: pt.game ? String(pt.n) : "–" }),
          el("td", { text: pt.game ? formatDate(pt.game.played_at) : "–" }),
          el("td", { text: pt.game ? resultWords(pt.game) : "Starting rating" }),
          el("td", { class: "num", text: String(pt.rating) }),
        ),
      );
    }
    if (points.length >= 2) drawRatingChart();
  }

  function drawRatingChart() {
    const points = chartPoints;
    const box = $("rating-chart");
    box.textContent = "";
    const width = Math.max(260, box.clientWidth - 24);
    const height = 220;
    const m = { top: 14, right: 52, bottom: 26, left: 44 };
    const ratings = points.map((pt) => pt.rating);
    const step = Math.max(...ratings) - Math.min(...ratings) > 600 ? 200 : 100;
    const lo = Math.floor((Math.min(...ratings) - step / 2) / step) * step;
    const hi = Math.ceil((Math.max(...ratings) + step / 2) / step) * step;
    const first = points[0].n;
    const last = points[points.length - 1].n;
    const x = (n) => m.left + ((n - first) / (last - first)) * (width - m.left - m.right);
    const y = (r) => m.top + (1 - (r - lo) / (hi - lo)) * (height - m.top - m.bottom);

    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": `Rating over ${last} rated games, from ${ratings[0]} to ${ratings[ratings.length - 1]}.` });
    const grid = svgEl("g", { class: "grid" });
    for (let r = lo; r <= hi; r += step) {
      grid.append(svgEl("line", { x1: m.left, x2: width - m.right, y1: y(r), y2: y(r) }));
      const label = svgEl("text", { class: "tick", x: m.left - 8, y: y(r) + 4, "text-anchor": "end" });
      label.textContent = String(r);
      svg.append(label);
    }
    svg.prepend(grid);
    const xTicks = [...new Set([first, Math.ceil((first + last) / 2), last])];
    for (const n of xTicks) {
      const label = svgEl("text", { class: "tick", x: x(n), y: height - 6, "text-anchor": "middle" });
      label.textContent = n === 0 ? "Start" : n === 1 ? "Game 1" : String(n);
      svg.append(label);
    }
    const line = points.map((pt, i) => `${i ? "L" : "M"}${x(pt.n).toFixed(1)},${y(pt.rating).toFixed(1)}`).join(" ");
    svg.append(svgEl("path", { class: "area", d: `${line} L${x(last)},${y(lo)} L${x(first)},${y(lo)} Z` }));
    svg.append(svgEl("path", { class: "series", d: line }));
    const end = points[points.length - 1];
    svg.append(svgEl("circle", { class: "end-dot", cx: x(end.n), cy: y(end.rating), r: 4 }));
    const endLabel = svgEl("text", { class: "end-label", x: x(end.n) + 9, y: y(end.rating) + 4 });
    endLabel.textContent = String(end.rating);
    svg.append(endLabel);

    // Hover: a crosshair snaps to the nearest game; the arrow keys do the same.
    const crosshair = svgEl("line", { class: "crosshair", y1: m.top, y2: height - m.bottom, visibility: "hidden" });
    const dot = svgEl("circle", { class: "hover-dot", r: 4, visibility: "hidden" });
    svg.append(crosshair, dot);
    const tooltip = el("div", { class: "chart-tooltip", hidden: true });
    box.append(svg, tooltip);
    let active = null;
    const show = (index) => {
      active = Math.max(0, Math.min(points.length - 1, index));
      const pt = points[active];
      crosshair.setAttribute("x1", x(pt.n));
      crosshair.setAttribute("x2", x(pt.n));
      dot.setAttribute("cx", x(pt.n));
      dot.setAttribute("cy", y(pt.rating));
      crosshair.setAttribute("visibility", "visible");
      dot.setAttribute("visibility", "visible");
      tooltip.textContent = "";
      tooltip.append(el("b", { text: String(pt.rating) }), pt.game ? `Game ${pt.n} · ${resultWords(pt.game)}` : "Starting rating");
      tooltip.hidden = false;
      const scale = box.clientWidth / width;
      const left = 12 + x(pt.n) * scale;
      tooltip.style.left = `${Math.min(left + 10, box.clientWidth - tooltip.offsetWidth - 4)}px`;
      tooltip.style.top = `${Math.max(4, 12 + y(pt.rating) * scale - tooltip.offsetHeight - 10)}px`;
    };
    const hide = () => {
      crosshair.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
      active = null;
    };
    svg.addEventListener("pointermove", (event) => {
      const rect = svg.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / rect.width) * width;
      show(Math.round(((px - m.left) / (width - m.left - m.right)) * (points.length - 1)));
    });
    svg.addEventListener("pointerleave", hide);
    box.tabIndex = 0;
    box.onkeydown = (event) => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        show(active === null ? points.length - 1 : active + (event.key === "ArrowRight" ? 1 : -1));
      }
    };
    box.onblur = hide;
  }

  let chartResize = null;
  window.addEventListener("resize", () => {
    clearTimeout(chartResize);
    chartResize = setTimeout(() => {
      if (!$("rating-chart-block").hidden && !$("stats-player").hidden && chartPoints.length >= 2) drawRatingChart();
    }, 150);
  });

  // ------------------------------------------------------------ stats: badges

  const BADGES = [
    { icon: "♙", name: "First win", desc: "Win a game.", earned: (gs) => gs.some((g) => g.result === "win") },
    { icon: "♘", name: "On a roll", desc: "Win three games in a row.", earned: (gs) => longestStreak(gs) >= 3 },
    { icon: "♗", name: "Sharpshooter", desc: "Play a game with 90% accuracy or better.",
      earned: (gs) => gs.some((g) => typeof g.accuracy === "number" && g.accuracy >= 90) },
    { icon: "♖", name: "Clean sheet", desc: "Win without a single blunder.",
      earned: (gs) => gs.some((g) => g.result === "win" && g.blunders === 0) },
    { icon: "♚", name: "On your own", desc: "Win without hints or take-backs.",
      earned: (gs) => gs.some((g) => g.result === "win" && !g.hints && !g.takebacks) },
    { icon: "♞", name: "Explorer", desc: "Win at three different levels.",
      earned: (gs) => new Set(gs.filter((g) => g.result === "win").map((g) => g.level)).size >= 3 },
    { icon: "♜", name: "Marathon", desc: "Play a game of 60 moves or more.", earned: (gs) => gs.some((g) => g.moves >= 60) },
    { icon: "♕", name: "Club champion", desc: `Beat ${levelInfo(4).name} or a stronger level.`, earned: beatLevel(4) },
    { icon: "♔", name: "Giant slayer", desc: `Beat ${levelInfo(6).name} or a stronger level.`, earned: beatLevel(6) },
    // The top three levels are named after two dogs and a cat.
    { icon: "🐕", name: "Summer's friend", desc: "Beat Summer.", earned: beatLevel(8) },
    { icon: "🦴", name: "Titan tamer", desc: "Beat Titan.", earned: beatLevel(9) },
    { icon: "🐈", name: "Top cat", desc: "Beat Pinky.", earned: beatLevel(10) },
  ];

  function beatLevel(number) {
    return (games) => games.some((g) => g.result === "win" && g.level >= number);
  }

  function longestStreak(games) {
    let best = 0;
    let run = 0;
    for (const g of [...games].sort((a, b) => (a.played_at < b.played_at ? -1 : 1))) {
      run = g.result === "win" ? run + 1 : 0;
      best = Math.max(best, run);
    }
    return best;
  }

  function renderBadges(games) {
    const list = $("player-badges");
    list.textContent = "";
    const sorted = BADGES.map((badge) => ({ ...badge, got: badge.earned(games) })).sort((a, b) => b.got - a.got);
    for (const badge of sorted) {
      list.append(
        el(
          "li",
          { class: `badge ${badge.got ? "earned" : "locked"}` },
          el("span", { class: "badge-icon", "aria-hidden": "true", text: badge.icon }),
          el("span", { class: "badge-name", text: badge.name }, badge.got ? null : el("span", { class: "sr-only", text: " (not earned yet)" })),
          el("span", { class: "badge-desc", text: badge.desc }),
        ),
      );
    }
  }

  // ------------------------------------------------------------ stats: game viewer

  const viewer = { record: null, moves: [], replay: null, ply: 0, review: null, focus: null, token: 0 };
  const viewerBoard = createBoard($("viewer-wrap"));

  async function openGame(record) {
    const token = ++viewer.token;
    viewer.record = record;
    viewer.moves = record.moves_uci.split(" ").filter(Boolean);
    viewer.replay = null;
    viewer.review = null;
    viewer.focus = null;
    viewer.ply = viewer.moves.length;
    renderStats();
    window.scrollTo(0, 0);
    try {
      await backend.ready;
      const replay = await backend.replay(viewer.moves);
      if (token !== viewer.token) return;
      viewer.replay = replay;
    } catch (error) {
      $("viewer-meta").textContent = `This game can't be replayed: ${error.message || error}`;
      return;
    }
    renderViewer();
  }

  function closeGame() {
    viewer.token++;
    viewer.record = null;
    renderStats();
  }

  const playerPlies = () =>
    viewer.moves.map((_, i) => i).filter((i) => (i % 2 === 0 ? "white" : "black") === viewer.record.color);

  function renderViewer() {
    const g = viewer.record;
    const r = viewer.replay;
    $("viewer-title").textContent = `${g.player} vs Paul's Chess Bot`;
    const result = { win: "Win", loss: "Loss", draw: "Draw" }[g.result];
    const opening = (r && r.opening && r.opening.name) || g.opening;
    $("viewer-meta").textContent = [levelName(g), `${result} (${g.reason})`, formatDate(g.played_at), opening]
      .filter(Boolean)
      .join(" · ");
    if (!r) {
      viewerBoard.render({ fen: START_FEN, orientation: g.color });
      $("viewer-position").textContent = "Loading…";
      return;
    }
    const ply = viewer.ply;
    const item = viewer.focus !== null && viewer.review ? viewer.review.items[viewer.focus] : null;
    const marks = {};
    if (item && ply === item.ply) {
      marks[item.uci.slice(0, 2)] = marks[item.uci.slice(2, 4)] = "bad";
      marks[item.best_uci.slice(0, 2)] = marks[item.best_uci.slice(2, 4)] = "good";
    }
    viewerBoard.render({
      fen: r.fens[ply],
      orientation: g.color,
      last: ply > 0 && !item ? viewer.moves[ply - 1] : null,
      check: r.checks[ply],
      marks,
    });
    $("viewer-position").textContent =
      ply === 0 ? `Start · 0/${r.san.length}` : `${Math.ceil(ply / 2)}${ply % 2 ? "." : "..."} ${r.san[ply - 1]} · ${ply}/${r.san.length}`;
    $("viewer-first").disabled = $("viewer-prev").disabled = ply === 0;
    $("viewer-next").disabled = $("viewer-last").disabled = ply === r.san.length;

    const verdicts = {};
    if (viewer.review) for (const it of viewer.review.items) if (it.verdict) verdicts[it.ply] = it.verdict;
    const body = $("viewer-moves");
    body.textContent = "";
    for (let i = 0; i < r.san.length; i += 2) {
      const tr = el("tr", {}, el("td", { text: `${i / 2 + 1}.` }));
      for (const index of [i, i + 1]) {
        const td = el("td");
        if (index < r.san.length) {
          const button = el("button", {
            type: "button",
            class: "move-link" + (index === ply - 1 ? " active" : ""),
            text: r.san[index],
            onclick: () => {
              viewer.ply = index + 1;
              viewer.focus = null;
              renderViewer();
            },
          });
          if (verdicts[index]) button.append(el("span", { class: `glyph ${verdicts[index]}`, text: VERDICT_GLYPHS[verdicts[index]] }));
          td.append(button);
        }
        tr.append(td);
      }
      body.append(tr);
    }
    const active = body.querySelector(".move-link.active");
    if (active) active.scrollIntoView({ block: "nearest" });

    const rv = viewer.review;
    $("viewer-review").hidden = Boolean(rv);
    $("viewer-review-status").hidden = !rv || rv.status === "done";
    $("viewer-review-summary").hidden = !rv || rv.status !== "done";
    if (rv && rv.status === "running") $("viewer-review-status").textContent = `Reviewing… ${rv.items.length} of ${rv.total}`;
    if (rv && rv.status === "failed") $("viewer-review-status").textContent = `The review couldn't finish: ${rv.error}`;
    if (rv && rv.status === "done") renderViewerReview();
  }

  function renderViewerReview() {
    const summary = reviewSummary(viewer.review.items);
    $("viewer-accuracy").textContent = summary.accuracy === null ? "–" : `${summary.accuracy.toFixed(0)}%`;
    const counts = $("viewer-counts");
    counts.textContent = "";
    const plurals = { blunder: "blunders", mistake: "mistakes", inaccuracy: "inaccuracies" };
    for (const verdict of ["blunder", "mistake", "inaccuracy"]) {
      const n = summary.counts[verdict];
      counts.append(el("li", { class: verdict }, el("b", { text: String(n) }), ` ${n === 1 ? verdict : plurals[verdict]}`));
    }
    const list = $("viewer-mistakes");
    list.textContent = "";
    const worst = viewer.review.items
      .map((it, index) => ({ it, index }))
      .filter(({ it }) => it.verdict)
      .sort((a, b) => b.it.loss - a.it.loss)
      .slice(0, 5);
    for (const { it, index } of worst) {
      list.append(
        el(
          "li",
          {},
          el(
            "button",
            {
              type: "button",
              "aria-pressed": String(viewer.focus === index),
              onclick: () => {
                viewer.focus = viewer.focus === index ? null : index;
                viewer.ply = viewer.focus === null ? it.ply + 1 : it.ply;
                renderViewer();
              },
            },
            el("span", { class: "mistake-move", text: `${it.number}${it.color === "white" ? "." : "..."} ${it.san}${VERDICT_GLYPHS[it.verdict]}` }),
            el("span", { class: `chip ${it.verdict}`, text: VERDICT_NAMES[it.verdict] }),
            el("span", { class: "mistake-better" }, "Better was ", el("b", { text: it.best_san })),
            el("span", { class: "mistake-eval", text: `${formatScore(it.before)} → ${formatScore(it.after)} · ${it.line}` }),
          ),
        ),
      );
    }
  }

  async function reviewViewedGame() {
    const token = viewer.token;
    const plies = playerPlies();
    viewer.review = { status: "running", items: [], total: plies.length };
    renderViewer();
    try {
      for (const ply of plies) {
        const item = await backend.review(viewer.moves, ply);
        if (token !== viewer.token) return;
        viewer.review.items.push(item);
        renderViewer();
      }
      viewer.review.status = "done";
    } catch (error) {
      if (token !== viewer.token) return;
      viewer.review.status = "failed";
      viewer.review.error = error.message || String(error);
    }
    renderViewer();
  }

  function stepViewer(ply) {
    if (!viewer.replay) return;
    viewer.ply = Math.max(0, Math.min(viewer.replay.san.length, ply));
    viewer.focus = null;
    renderViewer();
  }

  $("viewer-back").addEventListener("click", closeGame);
  $("viewer-first").addEventListener("click", () => stepViewer(0));
  $("viewer-prev").addEventListener("click", () => stepViewer(viewer.ply - 1));
  $("viewer-next").addEventListener("click", () => stepViewer(viewer.ply + 1));
  $("viewer-last").addEventListener("click", () => stepViewer(Infinity));
  $("viewer-review").addEventListener("click", reviewViewedGame);
  document.addEventListener("keydown", (event) => {
    if ($("stats-game").hidden || $("view-stats").hidden || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
    if (event.target.closest && event.target.closest(".chart")) return;
    if (event.key === "ArrowLeft") stepViewer(viewer.ply - 1);
    if (event.key === "ArrowRight") stepViewer(viewer.ply + 1);
  });

  $("stats-refresh").addEventListener("click", () => loadStats(true));
  $("stats-back").addEventListener("click", () => {
    statsPlayer = null;
    viewer.record = null;
    renderStats();
  });

  // ------------------------------------------------------------ puzzles

  const TRAINER_KEY = "chessbot.trainer.v1";
  const trainerBoard = createBoard($("trainer-wrap"));
  const trainer = {
    puzzles: null,
    puzzle: null,
    played: [], // moves played so far in this puzzle (the opponent's mistake first)
    state: null,
    result: null, // null while unresolved, then "solved" or "failed"
    done: false, // the whole solution is on the board
    token: 0,
    hintSquare: null,
    busy: false, // a move is being checked or the opponent is replying
    progress: Object.assign(
      { rating: 1000, attempts: 0, solved: 0, streak: 0, best: 0, seen: [] },
      storageGet(TRAINER_KEY, {}) || {},
    ),
  };

  const saveTrainer = () => storageSet(TRAINER_KEY, trainer.progress);
  const solverColor = () => (trainer.puzzle.fen.split(" ")[1] === "w" ? "black" : "white");
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function loadPuzzles() {
    if (trainer.puzzles) return;
    try {
      trainer.puzzles = await (await fetch("puzzles.json")).json();
    } catch {
      trainer.puzzles = [];
    }
  }

  // A puzzle rating saved under the player's name that is newer than this
  // browser's (from another device, or set by hand in the database) replaces it.
  async function syncPuzzleRating() {
    const name = playerName.trim();
    if (!name || !stats.latestPuzzleAttempt) return;
    let latest = null;
    try {
      latest = await stats.latestPuzzleAttempt(name);
    } catch {
      return; // offline: keep this browser's rating
    }
    const p = trainer.progress;
    if (!latest || (p.at && Date.parse(latest.played_at) <= Date.parse(p.at))) return;
    p.rating = latest.rating_after;
    p.at = latest.played_at;
    saveTrainer();
    renderTrainer();
  }

  function pickPuzzle() {
    const { rating, seen } = trainer.progress;
    const unseen = trainer.puzzles.filter((p) => !seen.includes(p.id));
    const pool = unseen.length ? unseen : trainer.puzzles;
    if (!unseen.length) trainer.progress.seen = [];
    for (const range of [150, 300, 600, Infinity]) {
      const near = pool.filter((p) => Math.abs(p.rating - rating) <= range);
      if (near.length) return near[Math.floor(Math.random() * near.length)];
    }
    return null;
  }

  async function nextPuzzle() {
    const token = ++trainer.token;
    await loadPuzzles();
    await backend.ready;
    trainer.puzzle = pickPuzzle();
    if (!trainer.puzzle) {
      $("trainer-prompt").textContent = "No puzzles are available right now.";
      return;
    }
    trainer.played = [];
    trainer.result = null;
    trainer.done = false;
    trainer.hintSquare = null;
    trainer.state = await backend.state([], trainer.puzzle.fen);
    if (token !== trainer.token) return;
    const side = solverColor() === "white" ? "White" : "Black";
    $("trainer-prompt").textContent = `Find the best move for ${side}.`;
    setTrainerFeedback(`${side === "White" ? "Black" : "White"} just moved. Can you punish it?`, "");
    $("trainer-change").textContent = "";
    renderTrainer();
    await wait(700);
    if (token !== trainer.token) return;
    await trainerPlay(trainer.puzzle.moves[0]);
  }

  async function trainerPlay(uci) {
    const token = trainer.token;
    trainer.played.push(uci);
    const state = await backend.state(trainer.played, trainer.puzzle.fen);
    if (token !== trainer.token) return;
    trainer.state = state;
    soundForLastMove(state);
    renderTrainer();
  }

  function renderTrainer() {
    const puzzle = trainer.puzzle;
    const p = trainer.progress;
    $("trainer-rating").textContent = String(Math.round(p.rating));
    $("trainer-solved").textContent = String(p.solved);
    $("trainer-streak").textContent = String(p.streak);
    $("trainer-best").textContent = String(p.best);
    $("trainer-note").textContent = playerName.trim()
      ? `Saving your puzzles as ${playerName.trim()}.`
      : "Enter your name on the Play tab to put your puzzle rating on the Stats page.";
    if (!puzzle || !trainer.state) {
      trainerBoard.render({ fen: puzzle ? puzzle.fen : START_FEN });
      return;
    }
    const s = trainer.state;
    const solver = solverColor();
    const canMove = !trainer.done && !trainer.busy && s.turn === solver && !s.over && trainer.played.length > 0;
    const marks = {};
    if (trainer.hintSquare && canMove) marks[trainer.hintSquare] = "good";
    trainerBoard.render({
      fen: s.fen,
      orientation: solver,
      last: s.last,
      check: s.check,
      marks,
      player: canMove ? solver : null,
      legal: canMove ? s.legal : [],
      onMove: trainerMove,
    });
    $("trainer-facts").hidden = !trainer.result;
    if (trainer.result) $("trainer-facts").textContent = `Puzzle rating ${puzzle.rating} · ${puzzle.theme}`;
    $("trainer-hint").disabled = !canMove || Boolean(trainer.hintSquare);
    $("trainer-solution").disabled = trainer.done || !trainer.played.length;
    $("trainer-next").textContent = trainer.done ? "Next puzzle" : "Skip to the next puzzle";
  }

  function setTrainerFeedback(text, kind) {
    const node = $("trainer-feedback");
    node.textContent = text;
    node.className = "trainer-feedback" + (kind ? ` ${kind}` : "");
  }

  // The first mistake, hint or look at the solution settles the puzzle as failed.
  function resolvePuzzle(solved) {
    if (trainer.result) return;
    trainer.result = solved ? "solved" : "failed";
    const p = trainer.progress;
    const k = p.attempts < 20 ? 40 : 20;
    const expected = 1 / (1 + Math.pow(10, (trainer.puzzle.rating - p.rating) / 400));
    const change = Math.round(k * ((solved ? 1 : 0) - expected));
    p.rating = Math.max(100, p.rating + change);
    p.attempts++;
    if (solved) {
      p.solved++;
      p.streak++;
      p.best = Math.max(p.best, p.streak);
    } else {
      p.streak = 0;
    }
    p.seen = [...p.seen, trainer.puzzle.id].slice(-2000);
    p.at = new Date().toISOString(); // when the rating last changed, for syncing between devices
    saveTrainer();
    $("trainer-change").textContent = change >= 0 ? `+${change}` : `−${-change}`;
    $("trainer-change").className = "trainer-change " + (change >= 0 ? "up" : "down");
    const name = playerName.trim();
    if (name) {
      stats
        .savePuzzleAttempt({
          id: uuid(),
          player: name.slice(0, 24),
          puzzle_id: trainer.puzzle.id,
          puzzle_rating: trainer.puzzle.rating,
          solved,
          rating_after: Math.round(p.rating),
          played_at: p.at,
        })
        .catch(() => {});
      statsCache = null;
    }
  }

  async function trainerMove(uci) {
    if (trainer.busy) return;
    trainer.busy = true;
    try {
      await checkTrainerMove(uci);
    } finally {
      trainer.busy = false;
      renderTrainer();
    }
  }

  async function checkTrainerMove(uci) {
    const token = trainer.token;
    renderTrainer();
    const moves = trainer.puzzle.moves;
    const expected = moves[trainer.played.length];
    let correct = uci === expected;
    if (!correct) {
      // Any checkmate is as good as the one in the answer key.
      const after = await backend.state([...trainer.played, uci], trainer.puzzle.fen);
      correct = after.over && after.reason === "checkmate";
    }
    if (token !== trainer.token) return;
    if (!correct) {
      resolvePuzzle(false);
      setTrainerFeedback("That's not it. Try again, or look at the solution.", "wrong");
      renderTrainer();
      return;
    }
    trainer.hintSquare = null;
    await trainerPlay(uci);
    if (token !== trainer.token) return;
    if (trainer.played.length >= moves.length || trainer.state.over) {
      finishPuzzle(true);
      return;
    }
    setTrainerFeedback("Good. Keep going…", "right");
    await wait(500);
    if (token !== trainer.token) return;
    await trainerPlay(moves[trainer.played.length]);
    setTrainerFeedback("Your move again.", "right");
  }

  // solvedNow: the player played the last move themselves (not "Show solution").
  function finishPuzzle(solvedNow) {
    trainer.done = true;
    if (solvedNow && !trainer.result) resolvePuzzle(true);
    if (solvedNow) {
      // Finishing it after a miss or a hint is still a success; it just doesn't raise the rating.
      const clean = trainer.result === "solved";
      setTrainerFeedback(clean ? "Solved!" : "Solved! Only a clean first try raises your rating.", "right");
      playSound("end");
    } else {
      setTrainerFeedback("That's the solution. On to the next one.", "");
    }
    renderTrainer();
  }

  async function showTrainerSolution() {
    const token = trainer.token;
    resolvePuzzle(false);
    trainer.done = true;
    renderTrainer();
    while (trainer.played.length < trainer.puzzle.moves.length) {
      await wait(600);
      if (token !== trainer.token) return;
      await trainerPlay(trainer.puzzle.moves[trainer.played.length]);
    }
    finishPuzzle(false);
  }

  $("trainer-hint").addEventListener("click", () => {
    resolvePuzzle(false);
    trainer.hintSquare = trainer.puzzle.moves[trainer.played.length].slice(0, 2);
    setTrainerFeedback("Hint: move the highlighted piece.", "");
    renderTrainer();
  });
  $("trainer-solution").addEventListener("click", showTrainerSolution);
  $("trainer-next").addEventListener("click", () => {
    // Skipping an unsolved puzzle counts as a miss.
    if (trainer.puzzle && !trainer.result && trainer.played.length) resolvePuzzle(false);
    nextPuzzle();
  });

  // ------------------------------------------------------------ friend

  // Games between two people: online through the live_games table (see
  // supabase/upgrade-3.sql), which both players poll every couple of seconds,
  // or taking turns on this device. Routes: #friend (the lobby),
  // #friend/local and #friend/<game id>. These games don't count on the Stats page.
  const FRIEND_KEY = "chessbot.friend.v1";
  const GAME_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const POLL_MS = 1500;
  const POLL_HIDDEN_MS = 5000;
  const onlineGames = Boolean(stats.rpc);
  const BASE_TITLE = document.title;

  const friendSaved = storageGet(FRIEND_KEY, {}) || {};
  const friend = {
    // Seats this browser holds in online games: id -> {token, color, at}.
    seats: Object.fromEntries(
      Object.entries(friendSaved.seats || {}).filter(([id, seat]) => GAME_ID.test(id) && seat && seat.token),
    ),
    // The game on this device: {moves, result, reason}.
    local: friendSaved.local && Array.isArray(friendSaved.local.moves) ? friendSaved.local : null,
    route: null,
    mode: null, // "online", "local", or null for the lobby
    id: null,
    row: null, // the online game's live_games row
    moves: [],
    state: null,
    flipped: false,
    busy: false,
    confirmResign: false,
    error: null,
    token: 0, // bumped when another game opens, so late replies are ignored
    version: 0, // bumped by this player's own changes, so older polls don't undo them
    timer: null,
  };

  function saveFriend() {
    const recent = Object.entries(friend.seats)
      .sort((a, b) => b[1].at - a[1].at)
      .slice(0, 20);
    friend.seats = Object.fromEntries(recent);
    storageSet(FRIEND_KEY, { seats: friend.seats, local: friend.local });
  }

  const friendBoard = createBoard($("friend-wrap"));
  const friendSeat = () => (friend.mode === "online" && friend.seats[friend.id]) || null;
  const friendColor = () => (friendSeat() ? friendSeat().color : null);
  const friendLink = (id) => `${location.origin}${location.pathname}#friend/${id}`;
  const capitalized = (word) => word[0].toUpperCase() + word.slice(1);
  const plies = (moves) => (moves ? moves.split(" ").length : 0);

  // {over, result, reason, winner}: the rules' verdict, or a resignation or agreed draw.
  function friendOutcome() {
    const ended =
      friend.mode === "online"
        ? friend.row && friend.row.status === "over" && friend.row
        : friend.mode === "local" && friend.local && friend.local.result && friend.local;
    const s = friend.state;
    const result = ended ? ended.result : s && s.over ? s.result : null;
    if (!result) return { over: false };
    const reason = ended ? ended.reason : s.reason;
    return { over: true, result, reason, winner: result === "1-0" ? "white" : result === "0-1" ? "black" : null };
  }

  // The color that may move on this screen now, if any.
  function friendMover() {
    const s = friend.state;
    if (!s || friend.busy || friendOutcome().over) return null;
    if (friend.mode === "local") return s.turn;
    const playing = friend.mode === "online" && friend.row && friend.row.status === "playing";
    return playing && friendColor() === s.turn ? s.turn : null;
  }

  function friendName(color) {
    if (friend.mode === "local") return capitalized(color);
    const name = friend.row && friend.row[`${color}_name`];
    return name || "Waiting for a player…";
  }

  function friendBottom() {
    const base = friendColor() || "white";
    return friend.flipped ? other(base) : base;
  }

  function friendStatusText() {
    const s = friend.state;
    const row = friend.row;
    const you = friendColor();
    const o = friendOutcome();
    if (friend.mode === "online" && !row) return friend.error ? "" : "Loading the game…";
    if (!s) return "Setting up the board…";
    if (o.over) {
      const winner = o.winner;
      if (!winner) return o.reason === "agreement" ? "Draw agreed." : `Draw by ${o.reason}.`;
      const loser = other(winner);
      if (o.reason === "resignation") {
        if (you === loser) return "You resigned.";
        return you === winner ? `${friendName(loser)} resigned. You win.` : `${friendName(loser)} resigned.`;
      }
      const how = o.reason === "checkmate" ? "Checkmate." : `${capitalized(o.reason)}.`;
      if (you) return you === winner ? `${how} You win.` : `${how} ${friendName(winner)} wins.`;
      return `${how} ${friendName(winner)} wins.`;
    }
    const check = s.check ? " Check!" : "";
    if (friend.mode === "local") return `${capitalized(s.turn)} to move.${check}`;
    if (row.status === "waiting") return you ? "Waiting for your friend to join." : "Waiting for a second player.";
    if (!you) return `Watching. ${friendName(s.turn)} (${s.turn}) to move.${check}`;
    const offer = row.draw_offer;
    if (offer && offer !== you) return `${friendName(offer)} offers a draw.`;
    if (s.turn === you) return s.check ? "Your move. You're in check." : "Your move.";
    return offer === you ? `You offered a draw. ${friendName(s.turn)} to move.` : `${friendName(s.turn)} to move…`;
  }

  function renderFriend() {
    const lobby = friend.mode === null;
    const invite = friend.mode === "online" && !friendSeat() && friend.row && friend.row.status === "waiting";
    $("friend-lobby").hidden = !lobby;
    $("friend-invite").hidden = !invite;
    $("friend-game").hidden = lobby || invite;
    $("friend-error").hidden = !friend.error;
    $("friend-error").textContent = friend.error || "";
    renderFriendBoard();
    renderFriendPlayers();
    if (lobby) renderFriendLobby();
    else if (invite) renderFriendInvite();
    else renderFriendGame();
    const yourTurn = friend.mode === "online" && friendMover() !== null;
    document.title = yourTurn ? `Your move · ${BASE_TITLE}` : BASE_TITLE;
    // The tab returns to the game that is open.
    document.querySelector('[data-tab="friend"]').setAttribute("href", friend.route ? `#friend/${friend.route}` : "#friend");
  }

  function renderFriendBoard() {
    const s = friend.state;
    const mover = friendMover();
    friendBoard.render({
      fen: s ? s.fen : START_FEN,
      orientation: friendBottom(),
      last: s && s.last,
      check: s && s.check,
      player: mover,
      legal: mover ? s.legal : [],
      onMove: friendMove,
    });
  }

  function renderFriendPlayers() {
    const bottom = friendBottom();
    const waiting = friend.row && friend.row.status === "waiting";
    const turn = friend.state && !friendOutcome().over && !waiting ? friend.state.turn : null;
    for (const [id, color] of [
      ["friend-top", other(bottom)],
      ["friend-bottom", bottom],
    ]) {
      const node = $(id);
      const shown = friend.mode !== null;
      node.querySelector(".player-name").textContent = shown ? friendName(color) : "";
      node.querySelector(".player-side").textContent = shown ? color + (friendColor() === color ? " · you" : "") : "";
      node.classList.toggle("to-move", turn === color);
    }
  }

  function renderFriendLobby() {
    const name = $("friend-name");
    if (document.activeElement !== name) name.value = playerName;
    $("friend-create").disabled = friend.busy || !onlineGames;
    $("friend-local").disabled = friend.busy;
    $("friend-lobby-note").textContent = onlineGames
      ? "Your friend opens the link, adds their name and you're playing. Moves show up for both of you within a couple of seconds."
      : "Online games need the site's shared database, which this copy doesn't have. You can still play on this device.";
  }

  function renderFriendInvite() {
    const row = friend.row;
    const host = row.white_name ? "white" : "black";
    $("friend-invite-title").textContent = `${row[`${host}_name`]} invited you to a game`;
    $("friend-invite-text").textContent = `You'll play ${other(host)}. Add your name so they know who's joined.`;
    const name = $("friend-join-name");
    if (document.activeElement !== name) name.value = playerName;
    $("friend-join").disabled = friend.busy;
  }

  function renderFriendGame() {
    const row = friend.row;
    const you = friendColor();
    const o = friendOutcome();
    $("friend-status").textContent = friendStatusText();
    const waiting = friend.mode === "online" && row && row.status === "waiting";
    $("friend-share").hidden = !(waiting && you);
    if (waiting && you) $("friend-link").value = friendLink(friend.id);
    $("friend-send").hidden = !navigator.share;
    const offer = row && row.status === "playing" ? row.draw_offer : null;
    $("friend-draw-offer").hidden = !(you && offer && offer !== you);
    $("friend-accept").disabled = $("friend-decline").disabled = friend.busy;

    const san = friend.state ? friend.state.san : [];
    fillMoveSheet($("friend-moves"), san);
    $("friend-sheet-empty").hidden = san.length > 0;
    $("friend-sheet").scrollTop = $("friend-sheet").scrollHeight;

    const playing = Boolean(friend.state) && !o.over && (friend.mode === "local" || (you && row && row.status === "playing"));
    const offerButton = $("friend-offer");
    if (friend.mode === "local") offerButton.textContent = "Agree a draw";
    else offerButton.textContent = offer && offer === you ? "Draw offered" : "Offer draw";
    offerButton.disabled = !playing || friend.busy || Boolean(offer) || friend.moves.length < 2;
    const resign = $("friend-resign");
    resign.disabled = !playing || friend.busy;
    if (!playing) friend.confirmResign = false;
    const resigner = friend.mode === "local" && friend.state ? `${capitalized(friend.state.turn)} resigns` : "Resign";
    resign.textContent = friend.confirmResign ? "Confirm resign" : resigner;
    resign.classList.toggle("confirming", friend.confirmResign);
    $("friend-leave").textContent = o.over || !playing ? "New game" : "Leave";
  }

  function friendError(error) {
    const text = (error && error.message) || String(error);
    if (/failed to fetch|networkerror|load failed/i.test(text)) return "Couldn't reach the server. Check your connection.";
    return text;
  }

  // Loads a live_games row into the page, checking its moves with the rules backend.
  async function applyRow(row) {
    if (!row) {
      friend.row = null;
      friend.error = "There's no game at this link. Check that it was copied in full.";
      return;
    }
    const moves = row.moves ? row.moves.split(" ") : [];
    const wasOver = friend.row && friend.row.status === "over";
    friend.row = row;
    if (!friend.state || moves.join(" ") !== friend.moves.join(" ")) {
      let state;
      try {
        state = await backend.state(moves);
      } catch {
        friend.error = "This game's moves aren't legal chess, so it can't be shown.";
        return;
      }
      const advanced = friend.state && moves.length > friend.moves.length;
      friend.moves = moves;
      friend.state = state;
      if (advanced) soundForLastMove(state);
    } else if (!wasOver && row.status === "over" && friend.state) {
      playSound("end");
    }
    friend.error = null;
  }

  function schedulePoll() {
    clearTimeout(friend.timer);
    if (friend.mode !== "online" || (friend.row && friend.row.status === "over")) return;
    friend.timer = setTimeout(pollFriend, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
  }

  async function pollFriend() {
    clearTimeout(friend.timer);
    const { token, version } = friend;
    try {
      const row = await stats.liveGame(friend.id);
      // Skip a poll that started before this player's own move or action landed.
      if (token !== friend.token || version !== friend.version || friend.busy) return;
      await applyRow(row);
    } catch (error) {
      if (token === friend.token) friend.error = friendError(error);
    } finally {
      if (token === friend.token) {
        renderFriend();
        schedulePoll();
      }
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && friend.mode === "online") pollFriend();
  });

  async function openFriend(route) {
    if (friend.route === route && (friend.mode !== null || route === null)) {
      renderFriend();
      if (friend.mode === "online") pollFriend();
      return;
    }
    friend.token++;
    clearTimeout(friend.timer);
    Object.assign(friend, { route, row: null, moves: [], state: null, flipped: false, busy: false, error: null });
    friend.confirmResign = false;
    friend.id = route && GAME_ID.test(route) ? route : null;
    friend.mode = route === "local" && friend.local ? "local" : friend.id ? "online" : null;
    if (friend.mode === null) friend.route = null;
    renderFriend();
    if (friend.mode === null) {
      loadFriendRecent();
      return;
    }
    const token = friend.token;
    try {
      await backend.ready;
      if (friend.mode === "local") {
        const state = await backend.state(friend.local.moves);
        if (token !== friend.token) return;
        friend.moves = [...friend.local.moves];
        friend.state = state;
      } else if (!onlineGames) {
        throw new Error("Online games need the site's shared database, which this copy doesn't have.");
      } else {
        const row = await stats.liveGame(friend.id);
        if (token !== friend.token) return;
        await applyRow(row);
      }
    } catch (error) {
      if (token === friend.token) friend.error = friendError(error);
    }
    if (token !== friend.token) return;
    renderFriend();
    schedulePoll();
  }

  async function friendMove(uci) {
    if (friend.busy || !friendMover()) return;
    const token = friend.token;
    const moves = [...friend.moves, uci];
    friend.busy = true;
    friend.version++;
    try {
      const state = await backend.state(moves);
      if (token !== friend.token) return;
      friend.moves = moves;
      friend.state = state;
      friend.error = null;
      soundForLastMove(state);
      if (friend.mode === "local") {
        friend.local = { moves, result: null, reason: null };
        saveFriend();
        return;
      }
      renderFriend();
      const ending = state.over ? { p_result: state.result, p_reason: state.reason } : {};
      await stats.rpc("play_live_move", {
        p_id: friend.id,
        p_token: friendSeat().token,
        p_moves: moves.join(" "),
        ...ending,
      });
      if (token !== friend.token) return;
      friend.row = { ...friend.row, moves: moves.join(" "), draw_offer: null };
      if (state.over) Object.assign(friend.row, { status: "over", result: state.result, reason: state.reason });
    } catch (error) {
      if (token !== friend.token) return;
      friend.error = friendError(error);
      friend.state = null; // the next poll puts back the server's version of the game
    } finally {
      if (token === friend.token) {
        friend.busy = false;
        friend.version++;
        renderFriend();
        if (friend.mode === "online" && !friend.state) pollFriend();
      }
    }
  }

  // A resignation, draw offer or reply: one database call, then a fresh look at the game.
  async function friendAction(name, args = {}) {
    if (friend.busy) return;
    const token = friend.token;
    friend.busy = true;
    friend.version++;
    renderFriend();
    try {
      await stats.rpc(name, { p_id: friend.id, p_token: friendSeat().token, ...args });
      if (token === friend.token) friend.error = null;
    } catch (error) {
      if (token === friend.token) friend.error = friendError(error);
    } finally {
      if (token === friend.token) {
        friend.busy = false;
        friend.version++;
        pollFriend();
      }
    }
  }

  function friendNameFrom(input) {
    const name = input.value.trim().slice(0, 24);
    if (!name) {
      friend.error = "Add your name first, so your friend knows who they're playing.";
      renderFriend();
      input.focus();
      return null;
    }
    playerName = name;
    storageSet(NAME_KEY, name);
    $("player-name").value = name;
    friend.error = null;
    return name;
  }

  const randomToken = () =>
    Array.from(crypto.getRandomValues(new Uint8Array(24)), (byte) => byte.toString(16).padStart(2, "0")).join("");

  $("friend-create").addEventListener("click", async () => {
    const name = friendNameFrom($("friend-name"));
    if (!name || friend.busy) return;
    const chosen = document.querySelector('input[name="friend-color"]:checked').value;
    const color = chosen === "random" ? (Math.random() < 0.5 ? "white" : "black") : chosen;
    const id = uuid();
    const token = randomToken();
    friend.busy = true;
    renderFriend();
    try {
      await stats.rpc("create_live_game", { p_id: id, p_token: token, p_name: name, p_color: color });
      friend.seats[id] = { token, color, at: Date.now() };
      saveFriend();
      location.hash = `#friend/${id}`;
    } catch (error) {
      friend.error = friendError(error);
    } finally {
      friend.busy = false;
      renderFriend();
    }
  });

  $("friend-local").addEventListener("click", () => {
    friend.local = { moves: [], result: null, reason: null };
    saveFriend();
    friend.route = null; // open it afresh even if the old one was showing
    if (location.hash === "#friend/local") openFriend("local");
    else location.hash = "#friend/local";
  });

  $("friend-join").addEventListener("click", async () => {
    const name = friendNameFrom($("friend-join-name"));
    if (!name || friend.busy) return;
    const token = randomToken();
    const id = friend.id;
    friend.busy = true;
    renderFriend();
    try {
      const color = await stats.rpc("join_live_game", { p_id: id, p_token: token, p_name: name });
      friend.seats[id] = { token, color, at: Date.now() };
      saveFriend();
    } catch (error) {
      friend.error = friendError(error);
    } finally {
      friend.busy = false;
      friend.version++;
      pollFriend();
    }
  });

  $("friend-name").addEventListener("input", (event) => {
    playerName = event.target.value.slice(0, 24);
    storageSet(NAME_KEY, playerName);
    $("player-name").value = playerName;
  });

  $("friend-copy").addEventListener("click", async () => {
    const button = $("friend-copy");
    try {
      await navigator.clipboard.writeText($("friend-link").value);
      button.textContent = "Copied";
    } catch {
      $("friend-link").select();
      button.textContent = "Press Ctrl+C";
    }
    setTimeout(() => (button.textContent = "Copy"), 2000);
  });

  $("friend-send").addEventListener("click", () => {
    const host = friendName(friendColor());
    navigator
      .share({ title: "Paul's Chess", text: `${host} invites you to a game of chess.`, url: $("friend-link").value })
      .catch(() => {});
  });

  $("friend-link").addEventListener("focus", (event) => event.target.select());

  let friendResignTimer = null;
  $("friend-resign").addEventListener("click", () => {
    if (!friend.confirmResign) {
      friend.confirmResign = true;
      friendResignTimer = setTimeout(() => {
        friend.confirmResign = false;
        renderFriend();
      }, 3000);
      renderFriend();
      return;
    }
    clearTimeout(friendResignTimer);
    friend.confirmResign = false;
    if (friend.mode === "local") {
      const loser = friend.state.turn;
      friend.local = { ...friend.local, result: loser === "white" ? "0-1" : "1-0", reason: "resignation" };
      saveFriend();
      playSound("end");
      renderFriend();
    } else {
      friendAction("resign_live_game");
    }
  });

  $("friend-offer").addEventListener("click", () => {
    if (friend.mode === "local") {
      friend.local = { ...friend.local, result: "1/2-1/2", reason: "agreement" };
      saveFriend();
      playSound("end");
      renderFriend();
    } else {
      friendAction("live_game_draw", { p_action: "offer" });
    }
  });
  $("friend-accept").addEventListener("click", () => friendAction("live_game_draw", { p_action: "accept" }));
  $("friend-decline").addEventListener("click", () => friendAction("live_game_draw", { p_action: "decline" }));

  $("friend-flip").addEventListener("click", () => {
    friend.flipped = !friend.flipped;
    renderFriend();
  });

  $("friend-leave").addEventListener("click", () => {
    location.hash = "#friend";
  });

  // The lobby lists the unfinished game on this device and this browser's recent online games.
  async function loadFriendRecent() {
    const token = friend.token;
    const items = [];
    const local = friend.local;
    if (local && local.moves.length && !local.result) {
      const turn = local.moves.length % 2 === 0 ? "White" : "Black";
      items.push({ href: "#friend/local", label: "On this device", detail: `${turn} to move` });
    }
    const ids = Object.entries(friend.seats)
      .sort((a, b) => b[1].at - a[1].at)
      .slice(0, 5)
      .map(([id]) => id);
    let rows = [];
    if (onlineGames && ids.length) {
      try {
        rows = await stats.liveGames(ids);
      } catch {
        rows = [];
      }
    }
    if (token !== friend.token) return;
    rows.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    for (const row of rows) {
      const you = friend.seats[row.id].color;
      const opponent = row[`${other(you)}_name`];
      let detail;
      if (row.status === "waiting") detail = "Waiting for a player";
      else if (row.status === "playing") {
        const turn = plies(row.moves) % 2 === 0 ? "white" : "black";
        detail = turn === you ? "Your move" : "Their move";
      } else if (row.result === "1/2-1/2") detail = "Draw";
      else detail = (row.result === "1-0") === (you === "white") ? "You won" : "You lost";
      items.push({ href: `#friend/${row.id}`, label: opponent ? `vs ${opponent}` : "Waiting for a player", detail });
    }
    const list = $("friend-recent-list");
    list.textContent = "";
    for (const item of items) {
      list.append(el("li", {}, el("a", { href: item.href }, el("span", { text: item.label }), el("span", { text: item.detail }))));
    }
    $("friend-recent").hidden = items.length === 0;
  }

  // ------------------------------------------------------------ home

  const showcaseBoard = createBoard($("showcase-wrap"));
  const showcase = { game: null, index: 0, timer: null };

  function buildLadder() {
    const ladder = $("home-levels");
    for (const level of LEVELS) {
      ladder.append(
        el(
          "li",
          {},
          el("span", { class: "rating", text: String(level.elo) }),
          el("span", { class: "name", text: level.name }),
          el("span", { class: "how", text: level.description || "" }),
        ),
      );
    }
  }

  function drawShowcase() {
    const { game: g, index } = showcase;
    const move = index > 0 ? g.moves[index - 1] : null;
    showcaseBoard.render({ fen: move ? move.fen : START_FEN, orientation: "white", last: move && move.uci });
    const label = move ? `${Math.floor((index - 1) / 2) + 1}${index % 2 === 1 ? "." : "..."} ${move.san}` : "Start";
    $("showcase-move").textContent = label;
  }

  // Replays the showcase game move by move while the Home tab is visible.
  function tickShowcase() {
    const g = showcase.game;
    const visible = !$("view-home").hidden && !document.hidden;
    if (visible) {
      showcase.index = showcase.index >= g.moves.length ? 0 : showcase.index + 1;
      drawShowcase();
    }
    const atEnd = showcase.index >= g.moves.length;
    showcase.timer = setTimeout(tickShowcase, atEnd ? 4500 : showcase.index === 0 ? 1500 : 1100);
  }

  async function loadShowcase() {
    try {
      showcase.game = await (await fetch("showcase.json")).json();
    } catch {
      return;
    }
    const g = showcase.game;
    $("showcase-title").textContent = `${g.title}: ${g.white} vs ${g.black}`;
    $("showcase-meta").textContent = `${g.event}. ${g.note}`;
    if (reducedMotion) {
      showcase.index = g.moves.length;
      drawShowcase();
      return;
    }
    drawShowcase();
    showcase.timer = setTimeout(tickShowcase, 1500);
  }

  // One line of live numbers under the hero, when stats are shared.
  async function loadHomeStats() {
    if (!stats.shared) return;
    try {
      if (!statsCache) statsCache = currentGames(await stats.list());
    } catch {
      return;
    }
    const players = new Set(statsCache.map((g) => g.player.trim().toLowerCase())).size;
    if (!statsCache.length) return;
    const games = statsCache.length;
    $("home-live").textContent = `${games} game${games === 1 ? "" : "s"} played by ${players} player${players === 1 ? "" : "s"} so far.`;
    $("home-live").hidden = false;
  }

  // ------------------------------------------------------------ theme

  function currentTheme() {
    const explicit = document.documentElement.dataset.theme;
    if (explicit === "light" || explicit === "dark") return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function renderThemeToggle() {
    const dark = currentTheme() === "dark";
    const button = $("theme-toggle");
    button.classList.toggle("is-dark", dark);
    $("theme-label").textContent = dark ? "Light" : "Dark";
    button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  }

  $("theme-toggle").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("chessbot.theme", next);
    } catch {
      // The choice just won't be remembered.
    }
    renderThemeToggle();
  });

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderThemeToggle);
  }

  // ------------------------------------------------------------ tabs

  const VIEWS = ["home", "play", "puzzles", "friend", "learn", "stats"];

  function showView() {
    // #friend/<route> opens a game on the Friend tab.
    const [hash, route] = location.hash.slice(1).split(/\/(.*)/s);
    const name = VIEWS.includes(hash) ? hash : "home";
    for (const view of document.querySelectorAll("[data-view]")) view.hidden = view.dataset.view !== name;
    for (const tab of document.querySelectorAll("[data-tab]")) {
      if (tab.dataset.tab === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    if (name === "stats") loadStats();
    if (name === "puzzles") {
      const first = !trainer.puzzle;
      if (first) renderTrainer();
      // Pick the first puzzle with the synced rating, unless the database is slow to answer.
      const synced = Promise.race([syncPuzzleRating(), wait(2500)]);
      if (first) synced.then(nextPuzzle);
    }
    if (name === "home") loadHomeStats();
    if (name === "friend") openFriend(route ? route.toLowerCase() : null);
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", showView);

  // Coming back to the page refreshes the shared numbers on the tab that is showing.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (!$("view-puzzles").hidden) syncPuzzleRating();
    if (!$("view-stats").hidden) loadStats();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    mainBoard.deselect();
    puzzleBoard.deselect();
    trainerBoard.deselect();
    friendBoard.deselect();
  });

  // ------------------------------------------------------------ install and offline

  // The static site registers a service worker so it can be installed and
  // opened offline (see sw.js). The local server doesn't, to avoid stale files.
  if (CONFIG.offline && "serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }

  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    $("install-note").hidden = false;
    $("install-app").hidden = false;
    $("install-text").textContent = "It opens like an app and plays offline.";
  });
  window.addEventListener("appinstalled", () => ($("install-note").hidden = true));
  $("install-app").addEventListener("click", async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice.catch(() => null);
    installPrompt = null;
    $("install-note").hidden = true;
  });
  // iPhones and iPads have no install prompt; tell people how to do it by hand.
  const standalone = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
  if (CONFIG.offline && /iPhone|iPad|iPod/.test(navigator.userAgent) && !standalone && !navigator.standalone) {
    $("install-note").hidden = false;
    $("install-app").hidden = true;
    $("install-text").textContent = "To install it, tap the Share button, then Add to Home Screen.";
  }

  function renderOnline() {
    $("offline-banner").hidden = navigator.onLine !== false;
  }
  window.addEventListener("offline", renderOnline);
  window.addEventListener("online", () => {
    renderOnline();
    if (stats.flush) stats.flush().catch(() => {});
    statsCache = null;
  });
  renderOnline();

  // ------------------------------------------------------------ start

  async function start() {
    installPieceStyles();
    renderThemeToggle();
    buildLevelPicker();
    buildLadder();
    syncChoices();
    showView();
    render();
    loadLessons();
    loadShowcase();
    backend.onStatus = (text) => {
      game.loading = text;
      renderStatus();
    };
    try {
      await backend.ready;
    } catch (error) {
      game.error = `The engine couldn't start: ${error.message || error}`;
      renderStatus();
      return;
    }
    backendReady = true;
    game.loading = null;
    try {
      await setMoves(game.moves);
    } catch {
      game.moves = [];
      await setMoves([]);
    }
    if (learn.lessons.length && learn.lessons[learn.index].puzzle) resetPuzzle();
    // A game that ended before a reload shows its review again (it isn't re-saved).
    if (outcome().over) runReview();
    else engineTurn();
  }

  start();
})();

// ChessBot web UI: play, review, lessons and player stats.
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
        throw new Error("Can't reach the ChessBot server. Is `chessbot serve` still running?");
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
    };
  }

  const backend = window.chessbotBackend || httpBackend();
  let backendReady = false;

  // ------------------------------------------------------------ stats store

  function localStore() {
    const KEY = "chessbot.games.v1";
    return {
      shared: false,
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

    async function insert(record, keepalive = false) {
      const response = await fetch(`${url}/rest/v1/games`, {
        method: "POST",
        headers: { ...headers, Prefer: "return=minimal" },
        body: JSON.stringify(record),
        keepalive,
      });
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

    return {
      shared: true,
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

  // A clickable, draggable board. render() takes what to show:
  //   fen, orientation, last ("e2e4"), check ("e1"), marks ({e4: "good"}),
  //   player (color that may move), legal (UCI moves), onMove(uci).
  function createBoard(wrap) {
    const board = wrap.querySelector(".board");
    const promotion = wrap.querySelector(".promotion");
    const choices = promotion.querySelector(".promotion-choices");
    let view = { fen: START_FEN, orientation: "white", legal: [], marks: {} };
    let selected = null;
    let drag = null;

    const interactive = () => Boolean(view.onMove && view.player && view.legal && view.legal.length);

    function render(next) {
      view = { ...view, marks: {}, last: null, check: null, onMove: null, player: null, legal: [], ...next };
      if (!interactive()) selected = null;
      promotion.hidden = true;
      draw();
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
        if (to && to !== from && attempt(from, to)) return;
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
    resigned: Boolean(saved.resigned),
    recorded: Boolean(saved.recorded),
    state: null,
    token: 0, // bumped on every new game so late replies are ignored
    thinking: false,
    engineInfo: null,
    review: null, // {status, items, done, total, error}
    viewing: null, // index into review.items shown on the board
    error: null,
    loading: "Setting up the board…",
  };
  let playerName = storageGet(NAME_KEY, "") || "";

  function saveGame() {
    storageSet(GAME_KEY, {
      id: game.id,
      moves: game.moves,
      human: game.human,
      level: game.level,
      flipped: game.flipped,
      takebacks: game.takebacks,
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
    Boolean(game.state) && !outcome().over && game.state.turn === game.human && !game.thinking && game.viewing === null;
  const humanPlies = () => game.moves.map((_, i) => i).filter((i) => (i % 2 === 0 ? "white" : "black") === game.human);
  const rated = () => game.takebacks === 0;

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
    mainBoard.render({
      fen: s ? s.fen : START_FEN,
      orientation: whiteAtBottom() ? "white" : "black",
      last: s && s.last,
      check: s && s.check,
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
      node.querySelector(".player-name").textContent =
        color === game.human ? playerName || "You" : `ChessBot · ${bot.name}`;
      node.querySelector(".player-side").textContent = color === game.human ? color : `${color} · ${bot.elo}`;
      node.classList.toggle("to-move", turn === color);
    }
  }

  function statusText() {
    if (game.error) return game.error;
    if (!game.state) return game.loading;
    const o = outcome();
    if (o.over) {
      if (o.reason === "resignation") return "You resigned.";
      if (o.reason === "checkmate") return o.winner === game.human ? "Checkmate. You win." : "Checkmate. ChessBot wins.";
      return `Draw by ${o.reason}.`;
    }
    if (game.thinking) {
      const depth = game.engineInfo && game.engineInfo.depth;
      return depth ? `ChessBot is thinking… depth ${depth}` : "ChessBot is thinking…";
    }
    if (game.state.turn === game.human) return game.state.check ? "Your move. You're in check." : "Your move.";
    return "Waiting for ChessBot…";
  }

  function renderStatus() {
    $("status").textContent = statusText();
    const info = game.engineInfo;
    $("eval").textContent = formatScore(info);
    $("depth").textContent = info ? String(info.depth) : "–";
    $("nodes").textContent = info ? formatNodes(info.nodes) : "–";
    $("time").textContent = info ? `${info.time.toFixed(1)}s` : "–";
    $("pv").textContent = info && info.pv ? info.pv : "";
    $("evalbar-fill").style.height = `${whiteShare(info)}%`;
    $("evalbar").classList.toggle("white-top", !whiteAtBottom());
  }

  function renderSheet() {
    const san = game.state ? game.state.san : [];
    const verdicts = {};
    if (game.review) for (const item of game.review.items) if (item.verdict) verdicts[item.ply] = item.verdict;
    const body = $("moves");
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
    $("sheet-empty").hidden = san.length > 0;
    $("sheet").scrollTop = $("sheet").scrollHeight;
  }

  function renderControls() {
    const o = outcome();
    const inProgress = Boolean(game.state) && !o.over && game.moves.length > 0;
    $("undo").disabled = !canUndo();
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
    else if (!rated()) note = "Unrated: you took back a move. It still counts on the Stats page, but not for your rating.";
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

  async function setMoves(moves) {
    const token = game.token;
    const state = await backend.state(moves);
    if (token !== game.token) return false;
    game.moves = moves;
    game.state = state;
    game.error = null;
    saveGame();
    render();
    return true;
  }

  async function playHumanMove(uci) {
    try {
      if (!(await setMoves([...game.moves, uci]))) return;
      if (outcome().over) finishGame();
      else engineTurn();
    } catch (error) {
      showError(error);
    }
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
      const { reply, state } = await backend.move(game.moves, game.level, onProgress);
      if (token !== game.token) return;
      game.moves = [...game.moves, reply.move];
      game.state = state;
      game.engineInfo = reply;
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
      opening: game.state ? game.state.san.slice(0, 4).join(" ") : "",
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
  let statsPlayer = null;

  async function loadStats(force = false) {
    $("stats-source").textContent = stats.shared
      ? "Everyone's games, shared online."
      : "Games played in this browser. Connect a database to share stats between everyone (see the README).";
    const message = $("stats-message");
    if (!statsCache || force) {
      message.hidden = false;
      message.textContent = "Loading…";
      try {
        statsCache = await stats.list();
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
    return [...players.values()].map((p) => ({ ...p, ...summarizeGames(p.games) }));
  }

  function summarizeGames(games) {
    const wins = games.filter((g) => g.result === "win").length;
    const draws = games.filter((g) => g.result === "draw").length;
    const losses = games.length - wins - draws;
    const reviewed = games.filter((g) => typeof g.accuracy === "number");
    const ratedGames = games.filter((g) => !g.takebacks);
    const ratedWins = ratedGames.filter((g) => g.result === "win").length;
    const ratedLosses = ratedGames.filter((g) => g.result === "loss").length;
    const rating = ratedGames.length
      ? Math.round(
          ratedGames.reduce((sum, g) => sum + g.bot_elo, 0) / ratedGames.length +
            (400 * (ratedWins - ratedLosses)) / ratedGames.length,
        )
      : null;
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
      provisional: ratedGames.length < 5,
      ratedCount: ratedGames.length,
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
    $("stats-overview").hidden = Boolean(detail);
    $("stats-player").hidden = !detail;
    if (detail) return renderPlayer(detail);

    const all = summarizeGames(games);
    const tiles = $("stats-tiles");
    tiles.textContent = "";
    tiles.append(
      tile("Games played", String(games.length)),
      tile("Players", String(players.length)),
      tile("ChessBot's record", games.length ? `${all.losses}–${all.draws}–${all.wins}` : "–", "wins–draws–losses"),
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
            colspan: "9",
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
        "Estimated rating",
        p.rating === null ? "–" : `${p.rating}${p.provisional ? "?" : ""}`,
        `from ${p.ratedCount} rated game${p.ratedCount === 1 ? "" : "s"}`,
      ),
      tile("Games", String(p.count), wdl(p) + " (W–D–L)"),
      tile("Score", pct(p.score)),
      tile("Accuracy", acc(p.accuracy), "average over reviewed games"),
      tile("Blunders per game", p.blunders === null ? "–" : p.blunders.toFixed(1)),
    );

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
    for (const g of [...p.games].sort((a, b) => (a.played_at < b.played_at ? 1 : -1)).slice(0, 20)) {
      const result = { win: "Win", loss: "Loss", draw: "Draw" }[g.result];
      recent.append(
        el(
          "tr",
          {},
          el("td", { text: formatDate(g.played_at) }),
          el("td", { text: levelName(g) }),
          el("td", { text: g.color }),
          el("td", {}, el("span", { class: `result-${g.result}`, text: `${result} (${g.reason})` })),
          el("td", { class: "num", text: String(g.moves) }),
          el("td", { class: "num", text: typeof g.accuracy === "number" ? acc(g.accuracy) : "–" }),
          el("td", { class: "num", text: typeof g.blunders === "number" ? String(g.blunders) : "–" }),
        ),
      );
    }
  }

  $("stats-refresh").addEventListener("click", () => loadStats(true));
  $("stats-back").addEventListener("click", () => {
    statsPlayer = null;
    renderStats();
  });

  // ------------------------------------------------------------ tabs

  function showView() {
    const name = ["learn", "stats"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "play";
    for (const view of document.querySelectorAll("[data-view]")) view.hidden = view.dataset.view !== name;
    for (const tab of document.querySelectorAll("[data-tab]")) {
      if (tab.dataset.tab === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    if (name === "stats") loadStats();
  }

  window.addEventListener("hashchange", showView);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    mainBoard.deselect();
    puzzleBoard.deselect();
  });

  // ------------------------------------------------------------ start

  async function start() {
    installPieceStyles();
    buildLevelPicker();
    syncChoices();
    showView();
    render();
    loadLessons();
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

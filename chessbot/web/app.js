// ChessBot web UI.
//
// The page never implements chess rules itself. It keeps the game as a list
// of UCI moves and asks a backend for everything else:
//   backend.state(moves)                      -> legal moves, notation, result
//   backend.move(moves, seconds, onProgress)  -> the engine's reply
// `chessbot serve` provides the HTTP backend below. Another page can supply
// its own by setting window.chessbotBackend before this script runs.
(() => {
  "use strict";

  const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const FILES = "abcdefgh";
  const THINK_TIMES = { quick: 0.5, club: 1.5, strong: 4 };
  const STORAGE_KEY = "chessbot.game.v1";
  const PIECE_NAMES = { p: "pawn", n: "knight", b: "bishop", r: "rook", q: "queen", k: "king" };

  const $ = (id) => document.getElementById(id);

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
      state: (moves) => post("/api/state", { moves }),
      move: (moves, seconds) => post("/api/move", { moves, think_time: seconds }),
    };
  }

  const backend = window.chessbotBackend || httpBackend();

  const game = {
    moves: [],
    human: "white",
    strength: "club",
    flipped: false,
    state: null, // last answer from backend.state
    token: 0, // bumped on every new game so late engine replies are ignored
    thinking: false,
    engineInfo: null, // latest search summary (live while thinking)
    selected: null,
    error: null,
    loading: "Setting up the board…",
  };

  // ------------------------------------------------------------ storage

  function save() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ moves: game.moves, human: game.human, strength: game.strength, flipped: game.flipped }),
      );
    } catch {
      // Private windows and blocked storage: the game just won't survive a reload.
    }
  }

  function restore() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (!saved) return;
      if (Array.isArray(saved.moves)) game.moves = saved.moves.filter((m) => typeof m === "string");
      if (saved.human === "white" || saved.human === "black") game.human = saved.human;
      if (saved.strength in THINK_TIMES) game.strength = saved.strength;
      game.flipped = Boolean(saved.flipped);
    } catch {
      // Ignore unreadable storage.
    }
  }

  // ------------------------------------------------------------ helpers

  function parseFen(fen) {
    const squares = new Array(64).fill(null);
    const rows = fen.split(" ")[0].split("/");
    rows.forEach((row, i) => {
      const rank = 7 - i;
      let file = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) file += Number(ch);
        else squares[rank * 8 + file++] = ch;
      }
    });
    return squares;
  }

  const squareName = (index) => FILES[index % 8] + (Math.floor(index / 8) + 1);
  const pieceColor = (piece) => (piece === piece.toUpperCase() ? "white" : "black");
  const whiteAtBottom = () => (game.human === "white") !== game.flipped;
  const humanToMove = () => game.state && !game.state.over && game.state.turn === game.human && !game.thinking;

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
    const pieces = window.CHESSBOT_PIECES || {};
    const rules = Object.entries(pieces).map(([symbol, svg]) => {
      const url = `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}")`;
      const cls = (symbol === symbol.toUpperCase() ? "w" : "b") + symbol.toLowerCase();
      return `.p-${cls}{background-image:${url}}`;
    });
    const style = document.createElement("style");
    style.textContent = rules.join("\n");
    document.head.appendChild(style);
  }

  const pieceClass = (piece) => "p-" + (piece === piece.toUpperCase() ? "w" : "b") + piece.toLowerCase();

  // ------------------------------------------------------------ rendering

  const boardEl = $("board");

  function renderBoard() {
    const squares = parseFen(game.state ? game.state.fen : START_FEN);
    const whiteBottom = whiteAtBottom();
    const last = game.state && game.state.last;
    const targets = new Set();
    if (game.selected && humanToMove()) {
      for (const uci of game.state.legal) if (uci.startsWith(game.selected)) targets.add(uci.slice(2, 4));
    }
    boardEl.classList.toggle("interactive", Boolean(humanToMove()));
    boardEl.textContent = "";
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        const rank = whiteBottom ? 7 - row : row;
        const file = whiteBottom ? col : 7 - col;
        const index = rank * 8 + file;
        const name = squareName(index);
        const piece = squares[index];
        const sq = document.createElement("div");
        sq.className = "sq " + ((rank + file) % 2 === 0 ? "dark" : "light");
        sq.dataset.square = name;
        if (last && (last.slice(0, 2) === name || last.slice(2, 4) === name)) sq.classList.add("last");
        if (game.selected === name) sq.classList.add("selected");
        if (game.state && game.state.check === name) sq.classList.add("check");
        if (targets.has(name)) sq.classList.add("target", ...(piece ? ["capture"] : []));
        if (piece && pieceColor(piece) === game.human) sq.classList.add("own");
        if (piece) {
          const p = document.createElement("div");
          p.className = "piece " + pieceClass(piece);
          sq.appendChild(p);
          sq.setAttribute("aria-label", `${name} ${pieceColor(piece)} ${PIECE_NAMES[piece.toLowerCase()]}`);
        }
        if (col === 0) sq.insertAdjacentHTML("beforeend", `<span class="coord rank">${rank + 1}</span>`);
        if (row === 7) sq.insertAdjacentHTML("beforeend", `<span class="coord file">${FILES[file]}</span>`);
        boardEl.appendChild(sq);
      }
    }
  }

  function renderPlayers() {
    const bottomColor = whiteAtBottom() ? "white" : "black";
    const topColor = bottomColor === "white" ? "black" : "white";
    const turn = game.state && !game.state.over ? game.state.turn : null;
    for (const [id, color] of [["player-top", topColor], ["player-bottom", bottomColor]]) {
      const el = $(id);
      el.querySelector(".player-name").textContent = color === game.human ? "You" : "ChessBot";
      el.querySelector(".player-side").textContent = color;
      el.classList.toggle("to-move", turn === color);
    }
  }

  function statusText() {
    if (game.error) return game.error;
    if (!game.state) return game.loading;
    const s = game.state;
    if (s.over) {
      if (s.reason === "checkmate") {
        const winner = s.result === "1-0" ? "white" : "black";
        return winner === game.human ? "Checkmate. You win." : "Checkmate. ChessBot wins.";
      }
      return `Draw by ${s.reason}.`;
    }
    if (game.thinking) {
      const depth = game.engineInfo && game.engineInfo.depth;
      return depth ? `ChessBot is thinking… depth ${depth}` : "ChessBot is thinking…";
    }
    if (s.turn === game.human) return s.check ? "Your move. You're in check." : "Your move.";
    return game.loading || "Waiting for ChessBot…";
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
    const body = $("moves");
    body.textContent = "";
    for (let i = 0; i < san.length; i += 2) {
      const tr = document.createElement("tr");
      const cells = [String(i / 2 + 1) + ".", san[i], san[i + 1] || ""];
      cells.forEach((text, j) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (j > 0 && i + j - 1 === san.length - 1) td.classList.add("current");
        tr.appendChild(td);
      });
      body.appendChild(tr);
    }
    $("sheet-empty").hidden = san.length > 0;
    const sheet = $("sheet");
    sheet.scrollTop = sheet.scrollHeight;
  }

  function renderControls() {
    $(`color-${game.human}`).checked = true;
    $(`strength-${game.strength}`).checked = true;
    $("undo").disabled = !canUndo();
    $("copy-pgn").disabled = !game.state;
    const canType = Boolean(humanToMove());
    $("move-input").disabled = !canType;
    $("move-submit").disabled = !canType;
    const note = $("board-note");
    if (game.state && game.state.over) {
      note.hidden = false;
      note.textContent = `${statusText()} ${game.state.result}. Start a new game or take back a move.`;
    } else {
      note.hidden = true;
    }
  }

  function render() {
    renderBoard();
    renderPlayers();
    renderStatus();
    renderSheet();
    renderControls();
  }

  // ------------------------------------------------------------ game flow

  async function setMoves(moves) {
    const token = game.token;
    const state = await backend.state(moves);
    if (token !== game.token) return false;
    game.moves = moves;
    game.state = state;
    game.selected = null;
    game.error = null;
    save();
    render();
    return true;
  }

  async function playHumanMove(uci) {
    try {
      if (await setMoves([...game.moves, uci])) engineTurn();
    } catch (error) {
      showError(error);
    }
  }

  async function engineTurn() {
    const s = game.state;
    if (!s || s.over || s.turn === game.human || game.thinking) return;
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
      const { reply, state } = await backend.move(game.moves, THINK_TIMES[game.strength], onProgress);
      if (token !== game.token) return;
      game.moves = [...game.moves, reply.move];
      game.state = state;
      game.engineInfo = reply;
      save();
    } catch (error) {
      if (token === game.token) game.error = error.message || String(error);
    } finally {
      if (token === game.token) {
        game.thinking = false;
        render();
      }
    }
  }

  function canUndo() {
    if (!game.state || game.thinking) return false;
    return game.moves.length >= undoCount();
  }

  // Take back to the human's previous turn: usually their move plus the reply.
  function undoCount() {
    const s = game.state;
    if (s.turn === game.human) return 2;
    return 1;
  }

  async function newGame() {
    game.token++;
    game.thinking = false;
    game.engineInfo = null;
    game.human = document.querySelector('input[name="color"]:checked').value;
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

  // ------------------------------------------------------------ input: board

  let drag = null; // {from, pointerId, ghost, startX, startY, moved}

  function squareAt(x, y) {
    const rect = boardEl.getBoundingClientRect();
    if (x < rect.left || y < rect.top || x >= rect.right || y >= rect.bottom) return null;
    const col = Math.floor(((x - rect.left) / rect.width) * 8);
    const row = Math.floor(((y - rect.top) / rect.height) * 8);
    const whiteBottom = whiteAtBottom();
    const rank = whiteBottom ? 7 - row : row;
    const file = whiteBottom ? col : 7 - col;
    return squareName(rank * 8 + file);
  }

  function ownPieceOn(name) {
    if (!game.state) return false;
    const index = FILES.indexOf(name[0]) + (Number(name[1]) - 1) * 8;
    const piece = parseFen(game.state.fen)[index];
    return Boolean(piece) && pieceColor(piece) === game.human;
  }

  function attemptMove(from, to) {
    const candidates = game.state.legal.filter((uci) => uci.slice(0, 4) === from + to);
    if (candidates.length === 0) return false;
    if (candidates.length > 1) askPromotion(candidates);
    else playHumanMove(candidates[0]);
    return true;
  }

  function askPromotion(candidates) {
    const white = game.human === "white";
    const choices = $("promotion-choices");
    choices.textContent = "";
    for (const letter of ["q", "r", "b", "n"]) {
      const uci = candidates.find((c) => c.endsWith(letter));
      if (!uci) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = pieceClass(white ? letter.toUpperCase() : letter);
      button.setAttribute("aria-label", PIECE_NAMES[letter]);
      button.addEventListener("click", () => {
        $("promotion").hidden = true;
        playHumanMove(uci);
      });
      choices.appendChild(button);
    }
    $("promotion").hidden = false;
    choices.querySelector("button").focus();
  }

  boardEl.addEventListener("pointerdown", (event) => {
    if (!humanToMove() || event.button > 0) return;
    const name = squareAt(event.clientX, event.clientY);
    if (!name) return;
    if (game.selected && game.selected !== name && attemptMove(game.selected, name)) {
      game.selected = null;
      return;
    }
    if (!ownPieceOn(name)) {
      game.selected = null;
      renderBoard();
      return;
    }
    game.selected = name;
    renderBoard();
    const sq = boardEl.querySelector(`[data-square="${name}"]`);
    const piece = sq && sq.querySelector(".piece");
    if (!piece) return;
    const size = boardEl.getBoundingClientRect().width / 8;
    const ghost = document.createElement("div");
    ghost.className = "ghost " + [...piece.classList].find((c) => c.startsWith("p-"));
    ghost.style.width = ghost.style.height = `${size}px`;
    ghost.hidden = true;
    document.body.appendChild(ghost);
    drag = { from: name, pointerId: event.pointerId, ghost, startX: event.clientX, startY: event.clientY, moved: false };
    boardEl.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  boardEl.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4) return;
    if (!drag.moved) {
      drag.moved = true;
      drag.ghost.hidden = false;
      const sq = boardEl.querySelector(`[data-square="${drag.from}"]`);
      if (sq) sq.classList.add("dragging");
    }
    drag.ghost.style.left = `${event.clientX}px`;
    drag.ghost.style.top = `${event.clientY}px`;
    const over = squareAt(event.clientX, event.clientY);
    for (const el of boardEl.querySelectorAll(".drop-hover")) el.classList.remove("drop-hover");
    if (over) {
      const el = boardEl.querySelector(`[data-square="${over}"].target`);
      if (el) el.classList.add("drop-hover");
    }
  });

  function endDrag(event, cancelled) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const { from, ghost, moved } = drag;
    ghost.remove();
    drag = null;
    if (moved && !cancelled) {
      const to = squareAt(event.clientX, event.clientY);
      if (to && to !== from && attemptMove(from, to)) {
        game.selected = null;
        return;
      }
    }
    // A tap keeps the piece selected so the next tap can choose its square.
    renderBoard();
  }

  boardEl.addEventListener("pointerup", (event) => endDrag(event, false));
  boardEl.addEventListener("pointercancel", (event) => endDrag(event, true));

  // ------------------------------------------------------------ input: typing

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

  // ------------------------------------------------------------ controls

  $("new-game").addEventListener("click", newGame);

  $("undo").addEventListener("click", async () => {
    if (!canUndo()) return;
    try {
      await setMoves(game.moves.slice(0, game.moves.length - undoCount()));
      game.engineInfo = null;
      renderStatus();
      engineTurn();
    } catch (error) {
      showError(error);
    }
  });

  $("flip").addEventListener("click", () => {
    game.flipped = !game.flipped;
    save();
    render();
  });

  for (const input of document.querySelectorAll('input[name="strength"]')) {
    input.addEventListener("change", () => {
      game.strength = input.value;
      save();
    });
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
    navigator.clipboard.writeText(pgn).then(
      () => {
        const button = $("copy-pgn");
        button.textContent = "Copied";
        setTimeout(() => (button.textContent = "Copy PGN"), 1500);
      },
      fallback,
    );
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $("promotion").hidden = true;
      game.selected = null;
      renderBoard();
    }
  });

  // ------------------------------------------------------------ start

  async function start() {
    installPieceStyles();
    restore();
    render();
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
    game.loading = null;
    try {
      await setMoves(game.moves);
    } catch {
      game.moves = [];
      await setMoves([]);
    }
    engineTurn();
  }

  start();
})();

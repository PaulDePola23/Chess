// Runs ChessBot's Python engine in the browser with Pyodide, for the static
// site built by `chessbot build-site`. Setting window.chessbotBackend makes
// app.js use it instead of the `chessbot serve` HTTP API.
//
// Pyodide loads from jsDelivr. The chess code itself (python-chess and
// chessbot) is fetched from python.json next to the page. Everything runs in
// a Web Worker so the page stays responsive while the engine thinks.
(() => {
  "use strict";
  const PYODIDE_URL = "https://cdn.jsdelivr.net/npm/pyodide@314.0.7/";

  const workerSource = String.raw`
    const BRIDGE = [
      "import json",
      "from chessbot.search import Searcher",
      "from chessbot.webapi import engine_reply, game_state, review_move",
      "_searcher = Searcher()",
      "def _call(method, params_json, progress):",
      "    params = json.loads(params_json)",
      "    if method == 'state':",
      "        return json.dumps(game_state(params['moves'], params.get('fen')))",
      "    if method == 'move':",
      "        report = lambda info: progress(json.dumps(info))",
      "        reply = engine_reply(params['moves'], None, _searcher, on_progress=report, level=params['level'])",
      "        return json.dumps(reply)",
      "    if method == 'review':",
      "        return json.dumps(review_move(params['moves'], params['ply'], _searcher))",
      "    raise ValueError('unknown method ' + method)",
      "_call",
    ].join("\n");

    let call = null;
    const status = (text) => postMessage({ type: "status", text });

    function lastLine(error) {
      const text = String((error && error.message) || error).trim();
      const lines = text.split("\n").filter(Boolean);
      return lines[lines.length - 1] || text;
    }

    async function init({ pyodideURL, sourcesURL }) {
      // Pyodide never finishes loading if WebAssembly is blocked, so check first.
      try {
        await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
      } catch {
        throw new Error("this browser isn't allowed to run WebAssembly, which the Python engine needs");
      }
      status("Downloading the Python engine (about 13 MB)…");
      const sources = fetch(sourcesURL).then((response) => {
        if (!response.ok) throw new Error("couldn't download the engine's code (" + response.status + ")");
        return response.json();
      });
      const { loadPyodide } = await import(pyodideURL + "pyodide.mjs");
      const pyodide = await loadPyodide({ indexURL: pyodideURL });
      status("Starting the engine…");
      const root = "/home/pyodide/src/";
      for (const [path, code] of Object.entries(await sources)) {
        pyodide.FS.mkdirTree((root + path).split("/").slice(0, -1).join("/"));
        pyodide.FS.writeFile(root + path, code);
      }
      pyodide.runPython("import sys; sys.path.insert(0, '" + root + "')");
      call = pyodide.runPython(BRIDGE);
    }

    self.onmessage = async (event) => {
      const message = event.data;
      if (message.type === "init") {
        try {
          await init(message);
          postMessage({ type: "ready" });
        } catch (error) {
          postMessage({ type: "failed", error: lastLine(error) });
        }
        return;
      }
      const { id, method, params } = message;
      try {
        const progress = (json) => postMessage({ type: "progress", id, info: JSON.parse(json) });
        const value = JSON.parse(call(method, JSON.stringify(params), progress));
        postMessage({ type: "result", id, value });
      } catch (error) {
        postMessage({ type: "result", id, error: lastLine(error) });
      }
    };
  `;

  const backend = { onStatus: null };
  let resolveReady;
  let rejectReady;
  backend.ready = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  // A stalled download shouldn't leave the page waiting forever.
  const watchdog = setTimeout(
    () => rejectReady(new Error("it didn't finish loading within two minutes. Check your connection and reload the page")),
    120000,
  );
  backend.ready.then(
    () => clearTimeout(watchdog),
    () => clearTimeout(watchdog),
  );

  const pending = new Map();
  let nextId = 1;
  let worker = null;
  try {
    const url = URL.createObjectURL(new Blob([workerSource], { type: "text/javascript" }));
    worker = new Worker(url, { type: "module" });
  } catch (error) {
    rejectReady(new Error("this browser can't run the engine in the background (" + error.message + ")"));
  }

  if (worker) {
    worker.onmessage = (event) => {
      const message = event.data;
      const call = pending.get(message.id);
      if (message.type === "status") {
        if (backend.onStatus) backend.onStatus(message.text);
      } else if (message.type === "ready") {
        resolveReady();
      } else if (message.type === "failed") {
        rejectReady(new Error(message.error));
      } else if (message.type === "progress") {
        if (call && call.onProgress) call.onProgress(message.info);
      } else if (message.type === "result" && call) {
        pending.delete(message.id);
        if (message.error) call.reject(new Error(message.error));
        else call.resolve(message.value);
      }
    };
    worker.onerror = (event) => rejectReady(new Error(event.message || "the engine's worker stopped"));
    worker.postMessage({
      type: "init",
      pyodideURL: PYODIDE_URL,
      sourcesURL: new URL("python.json", location.href).href,
    });
  }

  function call(method, params, onProgress) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject, onProgress });
      worker.postMessage({ type: "call", id, method, params });
    });
  }

  backend.state = (moves, fen) => call("state", { moves, fen: fen || null });
  backend.move = (moves, level, onProgress) => call("move", { moves, level }, onProgress);
  backend.review = (moves, ply) => call("review", { moves, ply });
  window.chessbotBackend = backend;
})();

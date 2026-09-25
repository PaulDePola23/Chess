# Stockfish for Titan and Pinky

`stockfish-18-lite-single.js` and `.wasm` are [Stockfish.js](https://github.com/nmrugg/stockfish.js)
18.0.8 (the `stockfish` npm package), Nathan Rugg's WebAssembly build of
[Stockfish](https://stockfishchess.org): the "lite", single-threaded
flavour, which runs in any modern browser without special headers. Both are
GPL-3.0, like this project; see `COPYING.txt`.

They are served from the site itself because jsDelivr refuses the npm
package: it bundles every flavour of the engine and is over its 150 MB limit.

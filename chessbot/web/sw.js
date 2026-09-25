// Service worker for the static site (chessbot build-site fills in VERSION
// and APP_FILES). It makes the site installable and playable offline:
//   - the site's own files: network first, falling back to the cache, so the
//     site stays up to date online and still opens offline;
//   - Pyodide from jsDelivr and the Google Fonts files: cache first, since
//     their URLs are versioned and never change.
// Game results are never cached; the page queues them while offline.
const VERSION = "__VERSION__";
const APP_FILES = __APP_FILES__;
const APP_CACHE = `app-${VERSION}`;
const RUNTIME_CACHE = "runtime-v1";
const NETWORK_TIMEOUT = 4000;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(APP_CACHE)
      .then((cache) => cache.addAll(APP_FILES))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("app-") && k !== APP_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function networkFirst(request) {
  return new Promise((resolve) => {
    let settled = false;
    const fromCache = () =>
      caches.match(request, { ignoreSearch: true }).then((hit) => {
        if (!settled && hit) {
          settled = true;
          resolve(hit);
        }
        return hit;
      });
    const timer = setTimeout(fromCache, NETWORK_TIMEOUT);
    fetch(request)
      .then((response) => {
        clearTimeout(timer);
        if (response.ok) {
          const copy = response.clone();
          caches.open(APP_CACHE).then((cache) => cache.put(request, copy));
        }
        if (!settled) {
          settled = true;
          resolve(response);
        }
      })
      .catch(async () => {
        clearTimeout(timer);
        const hit = await fromCache();
        if (!settled) {
          settled = true;
          resolve(hit || Response.error());
        }
      });
  });
}

async function cacheFirst(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok || response.type === "opaque") cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    event.respondWith(networkFirst(request));
  } else if (
    url.hostname === "cdn.jsdelivr.net" ||
    url.hostname === "fonts.googleapis.com" ||
    url.hostname === "fonts.gstatic.com"
  ) {
    event.respondWith(cacheFirst(request));
  }
});

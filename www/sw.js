/* Service worker mínimo: cache-first para la shell, red para /api y WS. */
const CACHE = "deckmic-v1";
const SHELL = ["/", "/index.html", "/app.js", "/style.css", "/pcm-worklet.js", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/icons/icon-192.png")) {
    // network-only
    return;
  }
  e.respondWith(
    caches.match(e.request).then(
      (hit) =>
        hit ||
        fetch(e.request).then((r) => {
          if (r.ok && e.request.method === "GET") {
            const cp = r.clone();
            caches.open(CACHE).then((c) => c.put(e.request, cp));
          }
          return r;
        }).catch(() => caches.match("/index.html"))
    )
  );
});

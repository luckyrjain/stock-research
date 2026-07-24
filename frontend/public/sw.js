// Minimal service worker: makes the app installable and lets previously-visited
// pages/static assets load offline. Deliberately does NOT touch /api/* — this app
// is a live stock-data tool, and serving a cached quote/verdict while offline would
// be actively misleading rather than a helpful fallback.
const CACHE_NAME = 'alphapulse-shell-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    // Never cache a URL that carries a query string: this app has at least one
    // route (/auth/verify?token=...) where the query string IS a sensitive,
    // single-use credential, and the Cache API keys entries by full URL —
    // caching it would persist that secret in Cache Storage indefinitely.
    // Skipping all query strings (not just that one route) is the safe
    // default for a general-purpose SW that shouldn't need route-specific
    // knowledge of which params are sensitive.
    const cacheable = url.search === '';
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (cacheable && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(
          () =>
            caches.match(request).then((cached) => cached || caches.match('/'))
              // Nothing cached at all yet (e.g. first-ever visit went straight
              // to a deep link, offline, before "/" was cached) — a graceful
              // message beats letting the browser's own network-error page show.
              .then((res) => res || new Response('You are offline.', {
                status: 503,
                headers: { 'Content-Type': 'text/plain' },
              })),
        ),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        }),
    ),
  );
});

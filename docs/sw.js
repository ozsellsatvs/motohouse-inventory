// Minimal service worker: caches the app shell for offline/instant load.
// Inventory data is always fetched fresh over the network (see index.html),
// falling back to the cached copy only if the network request fails.
//
// Bump the CACHE version string every time index.html/manifest.json change
// -- the "activate" handler below deletes any cache that isn't the current
// version, which is what makes a PWA that's already installed on someone's
// phone actually pick up the new app shell instead of serving the old
// cached copy forever.
const CACHE = 'motohouse-shell-v4';
const SHELL = ['./', './index.html', './manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Inventory data: network-first, cache fallback (so the app still shows
  // last-known inventory if you open it with no signal).
  if (url.pathname.endsWith('/data/inventory.json')) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, clone));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // App shell: cache-first.
  if (SHELL.some((s) => event.request.url.endsWith(s.replace('./', '')))) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});

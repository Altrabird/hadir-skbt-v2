// Hadir@SKBT — Service Worker v1
const CACHE_NAME = "hadir-skbt-v1";
const STATIC_ASSETS = [
  "/",
  "/static/css/custom.css",
  "/static/js/app.js",
  "/static/img/logo.png",
  "/static/img/icon-192x192.png",
  "/static/img/icon-512x512.png",
  "/static/manifest.json",
];

// Install — cache static assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      );
    })
  );
  self.clients.claim();
});

// Fetch — network first, fallback to cache for static assets
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API calls — always network, never cache
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Static assets — network first, fallback to cache
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Update cache with fresh version
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, clone);
          });
        }
        return response;
      })
      .catch(() => {
        // Offline — serve from cache
        return caches.match(event.request);
      })
  );
});

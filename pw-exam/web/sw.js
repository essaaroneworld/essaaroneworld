// Service worker: caches the app shell so the portal (and an in-progress exam) can reload offline.
// API calls always go to the network; exam answers are queued in localStorage by the app itself.
const CACHE = 'pwexam-shell-v1';
const SHELL = ['/', '/index.html', '/css/app.css', '/js/app.js', '/js/main.js', '/js/api.js', '/js/util.js',
  '/js/admin.js', '/js/candidate.js', '/js/exam.js', '/icon.svg', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Network first (so updates arrive immediately), falling back to the cache when offline.
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request).then((hit) => hit || caches.match('/index.html'))));
});

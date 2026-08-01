// static/sw.js
self.addEventListener('install', function(event) {
  console.log('Service Worker installing.');
});

self.addEventListener('fetch', function(event) {
  // بسيط جداً عشان بس يسمح بالتثبيت
});
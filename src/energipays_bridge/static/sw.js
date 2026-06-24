const CACHE = 'ep-bridge-v1'
const STATIC = ['/static/js/tailwind.min.js', '/static/js/alpine.min.js',
                '/static/js/chart.min.js', '/static/css/app.css']

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC).catch(() => {})))
  self.skipWaiting()
})

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ))
  self.clients.claim()
})

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url)
  // API calls: network-first, no cache
  if (url.pathname.startsWith('/api/')) return
  // Static assets: cache-first
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(resp => {
      if (resp.ok && e.request.method === 'GET') {
        caches.open(CACHE).then(c => c.put(e.request, resp.clone()))
      }
      return resp
    }))
  )
})

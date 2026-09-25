/* Service worker for the installed (home-screen) app.

   It exists for notifications: it receives Web Push messages from the server
   (ARMED / OPENED, sent even while the app is closed and the phone locked),
   Android only shows notifications through a service worker's registration,
   and iOS only allows them at all for an installed web app. It deliberately
   has no fetch handler - the UI is live data, so there is nothing useful to
   serve offline, and every request goes straight to the network. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

/* Every push must show a notification: browsers revoke the subscription of a
   site that receives pushes silently. */
self.addEventListener('push', function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(self.registration.showNotification(data.title || '0DTE', {
    body: data.body || '',
    tag: data.tag,
    renotify: Boolean(data.tag),   // a repeat ARMED for the same ticker still buzzes
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url || '/' }
  }));
});

/* Tapping an alert brings the app forward on the page it's about: an open
   window is focused and told where to go; otherwise a new one opens there. */
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/';
  var page = url.indexOf('#') >= 0 ? url.split('#')[1] : null;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function (windows) {
      for (var i = 0; i < windows.length; i += 1) {
        if ('focus' in windows[i]) {
          if (page) { windows[i].postMessage({ page: page }); }
          return windows[i].focus();
        }
      }
      return self.clients.openWindow(url);
    }));
});

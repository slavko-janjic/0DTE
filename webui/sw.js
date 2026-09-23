/* Service worker for the installed (home-screen) app.

   It exists for notifications: Android only shows them through a service
   worker's registration, and iOS only allows them at all for an installed web
   app. It deliberately has no fetch handler - the UI is live data, so there
   is nothing useful to serve offline, and every request goes straight to the
   network as if this file weren't here. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

/* Tapping an alert brings the app forward (opening it if it was closed). */
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    .then(function (windows) {
      for (var i = 0; i < windows.length; i += 1) {
        if ('focus' in windows[i]) { return windows[i].focus(); }
      }
      return self.clients.openWindow('/');
    }));
});

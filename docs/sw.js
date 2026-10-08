// Restock Radar service worker: shows push alerts and opens the product on tap.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch { msg = { body: event.data && event.data.text() }; }
  const title = msg.title || "Restock Radar";
  event.waitUntil(self.registration.showNotification(title, {
    body: msg.body || "",
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: msg.url || title,
    renotify: true,
    requireInteraction: !!msg.urgent,
    data: { url: msg.url || "./" },
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "./";
  event.waitUntil(self.clients.openWindow(url));
});

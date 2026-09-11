/* Service Worker — der Teil, der laeuft, wenn die App zu ist.
 *
 * Zwei Aufgaben, mehr nicht:
 *   1. Push-Nachrichten entgegennehmen und als Systemmeldung anzeigen.
 *   2. Beim Antippen die App oeffnen (oder den schon offenen Tab nach vorn holen).
 *
 * Bewusst KEIN Caching der Seite. Ein Service Worker, der Dateien zwischenspeichert,
 * zeigt frueher oder spaeter einen alten Scan an — und ein alter Kurs ist schlimmer
 * als gar keiner. Die Daten sollen immer frisch vom Netz kommen.
 */

const VERSION = 'v3';

self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch (_) {
    d = { titel: 'Trading-Signal', text: event.data ? event.data.text() : '' };
  }
  const titel = d.titel || 'Trading-Signal';
  const text = (d.text || '').slice(0, 400);
  event.waitUntil(
    self.registration.showNotification(titel, {
      body: text,
      icon: './icon.png',
      badge: './icon.png',
      tag: d.dedup || titel,
      renotify: true,
      requireInteraction: !!d.dringend,
      // Vibration nur bei dringenden Meldungen — sonst gewoehnt man sich daran
      // und ueberliest genau die, auf die es ankommt.
      vibrate: d.dringend ? [80, 50, 80, 50, 160] : undefined,
      data: { ts: d.ts || '', dringend: !!d.dringend },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((liste) => {
      for (const c of liste) {
        if ('focus' in c) return c.focus();
      }
      return self.clients.openWindow('./');
    })
  );
});

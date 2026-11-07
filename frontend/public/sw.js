// Lets phones and tablets install the assistant (Add to Home Screen) and show its notifications.
// It caches nothing: the assistant needs the computer it runs on anyway.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const sessionId = event.notification.data && event.notification.data.sessionId;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      if (windows.length > 0) {
        await windows[0].focus();
        windows[0].postMessage({ type: "open-chat", sessionId });
      } else {
        await self.clients.openWindow(sessionId ? `/?chat=${encodeURIComponent(sessionId)}` : "/");
      }
    })(),
  );
});

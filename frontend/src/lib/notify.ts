/** Local time as YYYY-MM-DDTHH:MM, the format the server and datetime-local inputs use. */
export function localNow(date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** Parses YYYY-MM-DDTHH:MM as local time. */
export function parseLocal(value: string): Date {
  return new Date(value.length === 16 ? `${value}:00` : value);
}

export function formatWhen(value: string): string {
  return parseLocal(value).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Ask once, from a user gesture, so later reviews and reminders can reach the desktop. */
export function requestNotifyPermission(): void {
  if ("Notification" in window && Notification.permission === "default") {
    void Notification.requestPermission();
  }
}

const openChatListeners = new Set<(sessionId: string) => void>();

/** Runs when the user taps a notification that belongs to a chat. */
export function onNotificationOpen(listener: (sessionId: string) => void): () => void {
  openChatListeners.add(listener);
  return () => openChatListeners.delete(listener);
}

function openChat(sessionId?: string) {
  window.focus();
  if (sessionId) openChatListeners.forEach((l) => l(sessionId));
}

// Taps on notifications shown by the service worker (see public/sw.js).
navigator.serviceWorker?.addEventListener("message", (e: MessageEvent) => {
  if (e.data?.type === "open-chat") openChat(e.data.sessionId);
});

/** A system notification when the page isn't in front; the in-app toast covers the rest. */
export function notifyDesktop(title: string, body: string, sessionId?: string): void {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible" && document.hasFocus()) return;
  const options = { body, tag: `${title}:${body}`, icon: "/icon-192.png", data: { sessionId } };
  try {
    const n = new Notification(title, options);
    n.onclick = () => {
      openChat(sessionId);
      n.close();
    };
  } catch {
    // Android, and the app installed on an iPhone or iPad, only allow it through the service worker.
    void navigator.serviceWorker?.ready.then((reg) => reg.showNotification(title, options)).catch(() => {});
  }
}

/** "just now", "5 min ago", "3 h ago", then the date. */
export function timeAgo(iso: string, now = Date.now()): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

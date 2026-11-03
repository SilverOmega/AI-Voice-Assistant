import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { AppNotification, LiveNotification } from "../lib/types";

const DEVICE_KEY = "assistant.deviceId";

/** A random id for this browser, kept in local storage, so the server can tell new devices apart. */
export function deviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_KEY);
    if (saved) return saved;
  } catch {
    // storage blocked: a fresh id per visit
  }
  // crypto.randomUUID needs a secure context; a phone on http://<LAN IP> isn't one.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  try {
    localStorage.setItem(DEVICE_KEY, id);
  } catch {
    // see above
  }
  return id;
}

/**
 * The bell's list plus live updates. `onLive` runs for each new notification as it arrives,
 * so the app can pop it up.
 */
export function useNotifications(onLive: (note: LiveNotification) => void) {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const onLiveRef = useRef(onLive);
  onLiveRef.current = onLive;

  const refresh = useCallback(async () => {
    try {
      const list = await api.notifications();
      setItems(list.items);
      setUnread(list.unread);
    } catch {
      // shown by the health indicators
    }
  }, []);

  useEffect(() => {
    void api.deviceHello(deviceId()).catch(() => {});
    const source = new EventSource(api.notificationStreamUrl());
    // On every (re)connect: catch up on anything sent while the stream was down.
    source.onopen = () => void refresh();
    source.addEventListener("notification", (e) => {
      const note = JSON.parse((e as MessageEvent).data) as LiveNotification;
      setItems((list) => [note, ...list.filter((n) => n.id !== note.id)].slice(0, 50));
      setUnread((n) => n + 1);
      onLiveRef.current(note);
    });
    return () => source.close();
  }, [refresh]);

  const markRead = useCallback(
    async (ids?: string[]) => {
      setItems((list) => list.map((n) => (!ids || ids.includes(n.id) ? { ...n, read: true } : n)));
      if (!ids) setUnread(0);
      await api.markNotificationsRead(ids).catch(() => {});
      await refresh();
    },
    [refresh],
  );

  const clear = useCallback(async () => {
    setItems([]);
    setUnread(0);
    await api.clearNotifications().catch(() => {});
  }, []);

  return { items, unread, markRead, clear, refresh };
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { localNow, parseLocal } from "../lib/notify";
import type { CalendarEvent } from "../lib/types";

const REMIND_BEFORE_MS = 10 * 60_000;
const LOOKAHEAD_MS = 24 * 3600_000; // timers further out are set on a later refresh

/** Upcoming events, with a reminder shortly before each one starts. */
export function useSchedule(onRemind: (event: CalendarEvent) => void) {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const reminded = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      setEvents(await api.listEvents(localNow()));
    } catch {
      // the chat service being down is already shown in the sidebar
    }
  }, []);

  const remove = useCallback(async (id: string) => {
    await api.deleteEvent(id);
    setEvents((list) => list.filter((e) => e.id !== id));
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3600_000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    const now = Date.now();
    const timers = events.flatMap((event) => {
      const remindAt = parseLocal(event.start).getTime() - REMIND_BEFORE_MS;
      const delay = Math.max(0, remindAt - now);
      if (reminded.current.has(event.id) || parseLocal(event.start).getTime() < now || delay > LOOKAHEAD_MS) return [];
      return [
        setTimeout(() => {
          reminded.current.add(event.id);
          onRemind(event);
        }, delay),
      ];
    });
    return () => timers.forEach(clearTimeout);
  }, [events, onRemind]);

  return { events, refresh, remove };
}

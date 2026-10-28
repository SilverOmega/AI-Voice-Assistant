import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamMessage } from "../lib/api";
import type { PreparedImage } from "../lib/images";
import type { Message, Session, Source } from "../lib/types";

function localMessage(sessionId: string, role: "user" | "assistant", content: string, source: Source): Message {
  return {
    id: `local-${crypto.randomUUID()}`,
    session_id: sessionId,
    role,
    content,
    source,
    created_at: new Date().toISOString(),
    pending: true,
  };
}

/** Sessions, the active conversation, and text + voice message streaming. */
export function useChat() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch (err) {
      setError(`Chat service unavailable: ${(err as Error).message}`);
    }
  }, []);

  const loadSession = useCallback(async (id: string) => {
    try {
      const detail = await api.getSession(id);
      if (activeIdRef.current === id) setMessages(detail.messages);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  const selectSession = useCallback(
    (id: string | null) => {
      abortRef.current?.abort();
      activeIdRef.current = id;
      setActiveId(id);
      setMessages([]);
      setError(null);
      if (id) void loadSession(id);
    },
    [loadSession],
  );

  const newSession = useCallback(() => selectSession(null), [selectSession]);

  const ensureSession = useCallback(async (): Promise<string> => {
    if (activeIdRef.current) return activeIdRef.current;
    const session = await api.createSession();
    activeIdRef.current = session.id;
    setActiveId(session.id);
    setSessions((s) => [session, ...s]);
    return session.id;
  }, []);

  const renameSession = useCallback(async (id: string, title: string) => {
    const updated = await api.renameSession(id, title);
    setSessions((s) => s.map((x) => (x.id === id ? updated : x)));
  }, []);

  const deleteSession = useCallback(
    async (id: string) => {
      await api.deleteSession(id);
      setSessions((s) => s.filter((x) => x.id !== id));
      if (activeIdRef.current === id) selectSession(null);
    },
    [selectSession],
  );

  const updateLastAssistant = (fn: (m: Message) => Message) =>
    setMessages((msgs) => {
      const idx = msgs.findLastIndex((m) => m.role === "assistant" && m.pending);
      if (idx === -1) return msgs;
      const copy = msgs.slice();
      copy[idx] = fn(copy[idx]);
      return copy;
    });

  const send = useCallback(
    async (text: string, images: PreparedImage[] = []) => {
      const content = text.trim();
      if ((!content && images.length === 0) || streaming) return;
      setError(null);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const sessionId = await ensureSession();
        setMessages((m) => [
          ...m,
          { ...localMessage(sessionId, "user", content, "text"), previews: images.map((i) => i.previewUrl) },
          localMessage(sessionId, "assistant", "", "text"),
        ]);
        await streamMessage(
          sessionId,
          content,
          {
            onToken: (t) => updateLastAssistant((m) => ({ ...m, content: m.content + t })),
            onError: (message) => updateLastAssistant((m) => ({ ...m, error: message })),
          },
          controller.signal,
          "text",
          images.map((i) => ({ media_type: i.mediaType, data: i.data })),
        );
        updateLastAssistant((m) => ({ ...m, pending: false }));
        await Promise.all([loadSession(sessionId), refreshSessions()]);
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          updateLastAssistant((m) => ({ ...m, pending: false }));
        } else {
          updateLastAssistant((m) => ({ ...m, pending: false, error: (err as Error).message }));
          setError((err as Error).message);
        }
      } finally {
        abortRef.current = null;
        setStreaming(false);
      }
    },
    [streaming, ensureSession, loadSession, refreshSessions],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  // ---- voice turn events (from the voice service) ---------------------------

  const voice = {
    session: (id: string) => {
      if (activeIdRef.current === id) return;
      activeIdRef.current = id;
      setActiveId(id); // keep the optimistic messages already on screen
      void refreshSessions();
    },
    transcript: (text: string) =>
      setMessages((m) => [...m, localMessage(activeIdRef.current ?? "", "user", text, "voice")]),
    assistantStart: () =>
      setMessages((m) => [...m, localMessage(activeIdRef.current ?? "", "assistant", "", "voice")]),
    token: (text: string) => updateLastAssistant((m) => ({ ...m, content: m.content + text })),
    error: (message: string) => {
      setMessages((msgs) => {
        const last = msgs[msgs.length - 1];
        if (last?.role === "assistant" && last.pending) {
          return [...msgs.slice(0, -1), { ...last, pending: false, error: message }];
        }
        return msgs;
      });
    },
    turnDone: () => {
      updateLastAssistant((m) => ({ ...m, pending: false }));
      const id = activeIdRef.current;
      if (id) void loadSession(id);
      void refreshSessions();
    },
  };

  return {
    sessions,
    activeId,
    messages,
    streaming,
    error,
    setError,
    selectSession,
    newSession,
    renameSession,
    deleteSession,
    send,
    stop,
    voice,
  };
}

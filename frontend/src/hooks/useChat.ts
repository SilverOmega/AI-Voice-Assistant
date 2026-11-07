import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamMessage } from "../lib/api";
import type { PreparedImage } from "../lib/images";
import { localNow } from "../lib/notify";
import type { PlanTemplate } from "../lib/plans";
import type { Action, ActionKind, Message, Session, Source, UploadedFile } from "../lib/types";

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

interface Options {
  /** A task was drafted, approved or rejected (for notifications and the schedule). */
  onActionChange?: (action: Action) => void;
}

/** Sessions, the active conversation, text + voice message streaming, and task drafts. */
export function useChat({ onActionChange }: Options = {}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [drafting, setDrafting] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const onActionChangeRef = useRef(onActionChange);
  onActionChangeRef.current = onActionChange;

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
      if (activeIdRef.current === id) {
        setMessages(detail.messages);
        setActions(detail.actions ?? []);
      }
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
      setActions([]);
      setError(null);
      if (id) void loadSession(id);
    },
    [loadSession],
  );

  const newSession = useCallback(() => selectSession(null), [selectSession]);

  const ensureSession = useCallback(async (title?: string): Promise<string> => {
    if (activeIdRef.current) return activeIdRef.current;
    const session = await api.createSession(title);
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
    async (text: string, images: PreparedImage[] = [], files: UploadedFile[] = [], newSessionTitle?: string) => {
      const content = text.trim();
      if ((!content && images.length === 0 && files.length === 0) || streaming) return;
      setError(null);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const sessionId = await ensureSession(newSessionTitle);
        setMessages((m) => [
          ...m,
          {
            ...localMessage(sessionId, "user", content, "text"),
            previews: images.map((i) => i.previewUrl),
            files: files.map(({ id, name, media_type, size }) => ({ id, name, media_type, size })),
          },
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
          files.map((f) => f.id),
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

  // ---- tasks: the model drafts, the user reviews, the server runs it on approval --

  const replaceAction = (id: string, next: Action | null) =>
    setActions((list) => (next ? list.map((a) => (a.id === id ? next : a)) : list.filter((a) => a.id !== id)));

  const draftTask = useCallback(
    async (kind: ActionKind, text: string) => {
      const instruction = text.trim();
      if (!instruction || streaming || drafting) return;
      setError(null);
      setDrafting(true);
      const placeholderId = `local-${crypto.randomUUID()}`;
      try {
        const sessionId = await ensureSession();
        const now = new Date().toISOString();
        setActions((list) => [
          ...list,
          {
            id: placeholderId,
            session_id: sessionId,
            kind,
            status: "drafting",
            instruction,
            payload: {},
            result: null,
            error: null,
            created_at: now,
            updated_at: now,
          },
        ]);
        const action = await api.draftAction(sessionId, kind, instruction, localNow());
        if (activeIdRef.current === sessionId) replaceAction(placeholderId, action);
        onActionChangeRef.current?.(action);
        void refreshSessions();
      } catch (err) {
        replaceAction(placeholderId, null);
        setError(`Couldn't draft that: ${(err as Error).message}`);
      } finally {
        setDrafting(false);
      }
    },
    [streaming, drafting, ensureSession, refreshSessions],
  );

  /** Runs the action with the user's edits. Throws (for the card to show) if it is invalid. */
  const approveAction = useCallback(async (id: string, payload: Record<string, unknown>) => {
    const action = await api.approveAction(id, payload);
    replaceAction(id, action);
    onActionChangeRef.current?.(action);
    const url = action.status === "done" ? action.result?.open_url : undefined;
    if (url?.startsWith("mailto:")) window.location.href = url;
    else if (url) {
      // Gmail / Outlook compose in a new tab. If the browser blocks it, the card keeps the link.
      const tab = window.open(url, "_blank");
      if (tab) tab.opener = null;
    }
    return action;
  }, []);

  const rejectAction = useCallback(async (id: string) => {
    const action = await api.rejectAction(id);
    replaceAction(id, action);
    onActionChangeRef.current?.(action);
  }, []);

  /** Opens a new chat named after the plan and asks the assistant to help build it. */
  const startPlan = useCallback(
    (plan: PlanTemplate) => {
      if (streaming) return;
      selectSession(null);
      void send(plan.prompt, [], [], `Plan: ${plan.title}`);
    },
    [streaming, selectSession, send],
  );

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
    actions,
    drafting,
    streaming,
    error,
    setError,
    selectSession,
    newSession,
    renameSession,
    deleteSession,
    send,
    stop,
    startPlan,
    draftTask,
    approveAction,
    rejectAction,
    voice,
  };
}

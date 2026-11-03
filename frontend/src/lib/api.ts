import { SseParser } from "./sse";
import type {
  Action,
  ActionKind,
  AppNotification,
  AutomationInfo,
  CalendarEvent,
  ChannelPrefs,
  ChatConfig,
  ImageUpload,
  Message,
  NotificationCategory,
  NotificationSettings,
  ProviderName,
  Session,
  SessionDetail,
  Source,
} from "./types";

export const CHAT_BASE = "/chat";
export const VOICE_BASE = "/voice";
export const WAKEWORD_BASE = "/wakeword";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!resp.ok) throw new ApiError(resp.status, await errorDetail(resp));
  return (resp.status === 204 ? undefined : await resp.json()) as T;
}

async function errorDetail(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return `${resp.status} ${resp.statusText}`;
  }
}

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

// ---- chat service ---------------------------------------------------------

export const api = {
  listSessions: () => request<Session[]>(`${CHAT_BASE}/api/sessions`),
  createSession: (title?: string) =>
    request<Session>(`${CHAT_BASE}/api/sessions`, { method: "POST", body: JSON.stringify({ title }) }),
  getSession: (id: string) => request<SessionDetail>(`${CHAT_BASE}/api/sessions/${id}`),
  renameSession: (id: string, title: string) =>
    request<Session>(`${CHAT_BASE}/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteSession: (id: string) =>
    request<void>(`${CHAT_BASE}/api/sessions/${id}`, { method: "DELETE" }),

  getConfig: () => request<ChatConfig>(`${CHAT_BASE}/api/config`),
  updateConfig: (changes: Partial<{ provider: ProviderName; model: string; system_prompt: string; temperature: number }>) =>
    request<ChatConfig>(`${CHAT_BASE}/api/config`, { method: "PATCH", body: JSON.stringify(changes) }),
  listModels: () =>
    request<{ provider: string; default: string; models: string[] }>(`${CHAT_BASE}/api/models`),
  imageUrl: (id: string) => `${CHAT_BASE}/api/images/${id}`,
  /** Links the chat service returns (downloads) are relative to it. */
  chatUrl: (path: string) => `${CHAT_BASE}${path}`,

  // ---- task automation: drafts run only through approveAction ---------
  automationInfo: () => request<AutomationInfo>(`${CHAT_BASE}/api/automation`),
  draftAction: (sessionId: string, kind: ActionKind, instruction: string, now: string) =>
    request<Action>(`${CHAT_BASE}/api/sessions/${sessionId}/actions`, {
      method: "POST",
      body: JSON.stringify({ kind, instruction, now }),
    }),
  testNotification: () =>
    request<{ channels: string[]; errors: string[] }>(`${CHAT_BASE}/api/notify/test`, { method: "POST" }),
  pendingActions: () => request<Action[]>(`${CHAT_BASE}/api/actions?status=pending`),
  notifications: () =>
    request<{ items: AppNotification[]; unread: number }>(`${CHAT_BASE}/api/notifications`),
  notificationStreamUrl: () => `${CHAT_BASE}/api/notifications/stream`,
  markNotificationsRead: (ids?: string[]) =>
    request<void>(`${CHAT_BASE}/api/notifications/read`, { method: "POST", body: JSON.stringify({ ids }) }),
  clearNotifications: () => request<void>(`${CHAT_BASE}/api/notifications`, { method: "DELETE" }),
  notificationSettings: () => request<NotificationSettings>(`${CHAT_BASE}/api/notifications/settings`),
  updateNotificationSettings: (changes: Partial<Record<NotificationCategory, ChannelPrefs>>) =>
    request<NotificationSettings>(`${CHAT_BASE}/api/notifications/settings`, {
      method: "PUT",
      body: JSON.stringify(changes),
    }),
  reportPhotoLimit: (count: number) =>
    request<{ id: string | null }>(`${CHAT_BASE}/api/notifications/report`, {
      method: "POST",
      body: JSON.stringify({ category: "photo_limit", count }),
    }),
  deviceHello: (deviceId: string) =>
    request<{ new: boolean }>(`${CHAT_BASE}/api/devices/hello`, {
      method: "POST",
      body: JSON.stringify({ device_id: deviceId }),
    }),
  approveAction: (id: string, payload: Record<string, unknown>) =>
    request<Action>(`${CHAT_BASE}/api/actions/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ payload }),
    }),
  rejectAction: (id: string) =>
    request<Action>(`${CHAT_BASE}/api/actions/${id}/reject`, { method: "POST" }),
  listEvents: (since: string) =>
    request<CalendarEvent[]>(`${CHAT_BASE}/api/events?since=${encodeURIComponent(since)}`),
  deleteEvent: (id: string) => request<void>(`${CHAT_BASE}/api/events/${id}`, { method: "DELETE" }),

  // ---- voice / wake-word services --------------------------------------
  transcribe: async (audio: Blob): Promise<{ text: string; elapsed: number }> => {
    const form = new FormData();
    const ext = audio.type.includes("ogg") ? "ogg" : audio.type.includes("mp4") ? "m4a" : "webm";
    form.append("file", audio, `dictation.${ext}`);
    const resp = await fetch(`${VOICE_BASE}/api/stt`, { method: "POST", body: form });
    if (!resp.ok) throw new ApiError(resp.status, await errorDetail(resp));
    return resp.json();
  },
  wakeModels: () =>
    request<{ models: string[]; default: string; threshold: number }>(`${WAKEWORD_BASE}/api/models`),

  health: async (base: string): Promise<{ ok: boolean; body: Record<string, unknown> | null }> => {
    try {
      const resp = await fetch(`${base}/health`);
      const body = await resp.json();
      return { ok: resp.ok && body.status === "ok", body };
    } catch {
      return { ok: false, body: null };
    }
  },
};

// ---- streaming chat -------------------------------------------------------

export interface StreamHandlers {
  onStart?: (data: { user_message: Message; provider: string; model: string }) => void;
  onToken: (text: string) => void;
  onDone?: (message: Message | null) => void;
  onError?: (message: string) => void;
}

/** Send a message and stream the reply. Resolves when the stream ends; abort via `signal`. */
export async function streamMessage(
  sessionId: string,
  content: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  source: Source = "text",
  images: ImageUpload[] = [],
): Promise<void> {
  const resp = await fetch(`${CHAT_BASE}/api/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, source, images }),
    signal,
  });
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, await errorDetail(resp));

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const { event, data } of parser.feed(value)) {
      const payload = JSON.parse(data);
      if (event === "start") handlers.onStart?.(payload);
      else if (event === "token") handlers.onToken(payload.text);
      else if (event === "done") handlers.onDone?.(payload.message);
      else if (event === "error") handlers.onError?.(payload.message);
    }
  }
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KIND_LABELS } from "./components/ActionCard";
import { Composer } from "./components/Composer";
import { EraserIcon, PopOutIcon, SparkleIcon, UserIcon, WaveIcon, XIcon } from "./components/icons";
import { MessageList } from "./components/MessageList";
import { NotificationBell } from "./components/NotificationBell";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { Pet } from "./components/Pet";
import { Toasts, type Toast } from "./components/Toasts";
import { VoiceBar } from "./components/VoiceBar";
import { useChat } from "./hooks/useChat";
import { useHealth } from "./hooks/useHealth";
import { useNotifications } from "./hooks/useNotifications";
import { usePet } from "./hooks/usePet";
import { useSchedule } from "./hooks/useSchedule";
import { useVoice, useVoicePrefs } from "./hooks/useVoice";
import { api } from "./lib/api";
import { MAX_IMAGES } from "./lib/images";
import { formatWhen, notifyDesktop, onNotificationOpen } from "./lib/notify";
import type { Action, AuthStatus, AutomationInfo, CalendarEvent, LiveNotification, VoiceServerMessage } from "./lib/types";

const TOAST_SECONDS = { info: 8, warning: 12, error: 20 };

/** The compact window opened by the pop-out button. The name survives reloads of that window. */
const POPOUT_NAME = "assistant-popout";
const VOICE_PANEL_KEY = "assistant.voicePanel";

function popOut(sessionId: string | null) {
  const url = sessionId ? `/?chat=${encodeURIComponent(sessionId)}` : "/";
  window.open(url, POPOUT_NAME, "popup,width=440,height=860");
}

interface Props {
  /** How this device reached the app; null if the server couldn't say. */
  auth: AuthStatus | null;
  onSignedOut: () => void;
}

export default function App({ auth, onSignedOut }: Props) {
  const [automation, setAutomation] = useState<AutomationInfo | null>(null);
  const [pending, setPending] = useState<Action[]>([]);
  const [reminders, setReminders] = useState<CalendarEvent[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const [live, setLive] = useState<LiveNotification[]>([]);
  // The action callbacks below run inside useChat/useSchedule, which are created after them.
  const selectRef = useRef<(id: string) => void>(() => {});
  const activeRef = useRef<string | null>(null);
  const refreshScheduleRef = useRef<() => Promise<void>>(async () => {});

  const refreshPending = useCallback(async () => {
    try {
      setPending(await api.pendingActions());
    } catch {
      // shown by the health indicators
    }
  }, []);

  useEffect(() => {
    api.automationInfo().then(setAutomation, () => {});
    void refreshPending();
  }, [refreshPending]);

  const onActionChange = useCallback(
    (action: Action) => {
      void refreshPending();
      if (action.kind === "event" && action.status === "done") void refreshScheduleRef.current();
    },
    [refreshPending],
  );

  /** A notification from the server, as it happens, from any device or service. */
  const onLive = useCallback(
    (note: LiveNotification) => {
      if (note.category === "review") void refreshPending(); // so every open device shows the draft
      if (!note.popup) return;
      const sessionId = note.session_id;
      notifyDesktop(note.title, note.body, sessionId ?? undefined);
      if (note.category === "review") return; // the review toast below covers it
      // Already in front of the user: the chat shows it (review card, cut-off reply, error).
      if (sessionId && sessionId === activeRef.current && document.visibilityState === "visible") return;
      setLive((list) => [...list, note]);
      setTimeout(() => setLive((list) => list.filter((n) => n.id !== note.id)), TOAST_SECONDS[note.level] * 1000);
    },
    [refreshPending],
  );
  const notifications = useNotifications(onLive);

  const onRemind = useCallback((event: CalendarEvent) => {
    setReminders((list) => [...list, event]);
    notifyDesktop(`Coming up: ${event.title}`, formatWhen(event.start));
  }, []);

  const chat = useChat({ onActionChange });
  const schedule = useSchedule(onRemind);
  selectRef.current = chat.selectSession;
  useEffect(() => onNotificationOpen((id) => selectRef.current(id)), []);
  // Opened from a notification while the app was closed: /?chat=<id>.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("chat");
    if (!id) return;
    window.history.replaceState(null, "", "/");
    selectRef.current(id);
  }, []);
  activeRef.current = chat.activeId;
  refreshScheduleRef.current = schedule.refresh;
  const health = useHealth();
  const [prefs, updatePrefs] = useVoicePrefs();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pet, setPet] = usePet();
  const [voicePanel, setVoicePanel] = useState(() => {
    try {
      return localStorage.getItem(VOICE_PANEL_KEY) !== "off";
    } catch {
      return true;
    }
  });
  const toggleVoicePanel = () => {
    const next = !voicePanel;
    setVoicePanel(next);
    try {
      localStorage.setItem(VOICE_PANEL_KEY, next ? "on" : "off");
    } catch {
      /* storage unavailable: the choice just won't persist */
    }
  };
  const compact = window.name === POPOUT_NAME;

  const { voice: voiceEvents } = chat;
  const onVoiceMessage = useCallback(
    (msg: VoiceServerMessage) => {
      switch (msg.type) {
        case "session":
          voiceEvents.session(msg.session_id);
          break;
        case "transcript":
          if (msg.text) voiceEvents.transcript(msg.text);
          break;
        case "assistant_start":
          voiceEvents.assistantStart();
          break;
        case "token":
          voiceEvents.token(msg.text);
          break;
        case "error":
          voiceEvents.error(msg.message);
          break;
        case "turn_done":
        case "interrupted":
          voiceEvents.turnDone();
          break;
      }
    },
    [voiceEvents],
  );
  const voice = useVoice(chat.activeId, prefs, onVoiceMessage);

  const title = chat.sessions.find((s) => s.id === chat.activeId)?.title ?? "New chat";
  // The assistant can't answer: the chat service is unreachable, or its language model is down.
  const asleep = health.chat !== null && !health.chat.ok;
  const showVoice = voicePanel || voice.enabled;
  const iconButton =
    "rounded-lg p-1.5 pointer-coarse:p-2.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white";
  const needsReview = useMemo(() => new Set(pending.map((a) => a.session_id)), [pending]);

  // Drafts waiting in other chats (the open chat shows its own review cards), then reminders.
  const toasts: Toast[] = [
    ...live.map((n): Toast => {
      const sessionId = n.session_id;
      return {
        id: n.id,
        title: n.title,
        body: n.body,
        level: n.level,
        ...(sessionId ? { actionLabel: "Open chat", onAction: () => chat.selectSession(sessionId) } : {}),
      };
    }),
    ...pending
      .filter((a) => a.session_id !== chat.activeId && !dismissed.has(a.id))
      .map((a) => ({
        id: a.id,
        title: `Review needed: ${KIND_LABELS[a.kind].title.toLowerCase()}`,
        body: a.instruction,
        actionLabel: "Review",
        onAction: () => chat.selectSession(a.session_id),
      })),
    ...reminders.map((e) => ({ id: `reminder-${e.id}`, title: `Coming up: ${e.title}`, body: formatWhen(e.start) })),
  ];
  const dismissToast = (id: string) => {
    setDismissed((d) => new Set(d).add(id));
    setReminders((list) => list.filter((e) => `reminder-${e.id}` !== id));
    setLive((list) => list.filter((n) => n.id !== id));
  };

  return (
    <div
      className={`safe-x flex h-full items-center justify-center ${
        compact
          ? ""
          : "md:bg-gradient-to-br md:from-slate-50 md:via-white md:to-sky-50 md:p-6 dark:md:from-slate-950 dark:md:via-slate-950 dark:md:to-slate-900"
      }`}
    >
      <Sidebar
        sessions={chat.sessions}
        activeId={chat.activeId}
        health={health}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => {
          chat.selectSession(id);
          setSidebarOpen(false);
        }}
        onNew={() => {
          chat.newSession();
          setSidebarOpen(false);
        }}
        onPlan={(plan) => {
          chat.startPlan(plan);
          setSidebarOpen(false);
        }}
        busy={chat.streaming || chat.drafting}
        needsReview={needsReview}
        events={schedule.events}
        onDeleteEvent={schedule.remove}
        onRename={chat.renameSession}
        onDelete={chat.deleteSession}
        onSettings={() => {
          setSidebarOpen(false);
          setSettingsOpen(true);
        }}
      />

      <div className={`relative flex h-full w-full ${compact ? "" : "md:h-[min(56rem,100%)] md:max-w-xl"}`}>
        <main
          className={`flex min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-slate-900 ${
            compact
              ? ""
              : "md:rounded-[1.75rem] md:shadow-[0_24px_70px_-20px_rgba(15,23,42,0.35)] md:ring-1 md:ring-slate-900/5 dark:md:ring-white/10"
          }`}
        >
          <header className="safe-top flex items-center gap-2 px-4 pb-2">
            <SparkleIcon width={18} height={18} className="shrink-0 text-sky-400" />
            <h2 className="min-w-0 truncate font-semibold" title={health.chat?.detail.split(" — ")[0]}>
              {title}
            </h2>
            <div className="ml-auto flex shrink-0 items-center gap-1">
              <NotificationBell
                items={notifications.items}
                unread={notifications.unread}
                onMarkRead={notifications.markRead}
                onClear={notifications.clear}
                onOpenChat={chat.selectSession}
              />
              <button
                className={iconButton}
                onClick={() => setSidebarOpen(true)}
                aria-label="Conversations and settings"
                title="Conversations and settings"
              >
                <UserIcon />
              </button>
              {!compact && (
                <button
                  className={`${iconButton} hidden md:block`}
                  onClick={() => popOut(chat.activeId)}
                  aria-label="Open in a small window"
                  title="Open in a small window"
                >
                  <PopOutIcon />
                </button>
              )}
              <button
                onClick={toggleVoicePanel}
                disabled={voice.enabled}
                aria-pressed={showVoice}
                aria-label="Voice controls"
                title={voice.enabled ? "Voice is on" : showVoice ? "Hide voice controls" : "Show voice controls"}
                className={`mx-0.5 flex h-7 w-7 items-center justify-center rounded-full pointer-coarse:h-9 pointer-coarse:w-9 ${
                  showVoice
                    ? "bg-sky-400 text-white hover:bg-sky-500"
                    : "bg-sky-100 text-sky-500 hover:bg-sky-200 dark:bg-sky-500/20 dark:text-sky-300"
                }`}
              >
                <WaveIcon width={15} height={15} />
              </button>
              {compact && (
                <button className={iconButton} onClick={() => window.close()} aria-label="Close window" title="Close">
                  <XIcon width={16} height={16} />
                </button>
              )}
            </div>
          </header>

          <div className="relative flex min-h-0 flex-1 flex-col">
            <button
              onClick={chat.newSession}
              disabled={chat.messages.length === 0 && chat.actions.length === 0}
              aria-label="New chat"
              title="New chat"
              className="absolute right-3 top-1 z-10 rounded-lg p-1.5 pointer-coarse:p-2.5 text-sky-300 hover:bg-sky-50 hover:text-sky-500 disabled:invisible dark:hover:bg-slate-800"
            >
              <EraserIcon width={18} height={18} />
            </button>

            {chat.error && (
              <div className="mx-auto mt-3 flex w-full max-w-3xl items-start gap-2 px-4 pr-12">
                <p className="flex-1 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">
                  {chat.error}
                </p>
                <button onClick={() => chat.setError(null)} aria-label="Dismiss error" className="p-2 text-slate-500">
                  <XIcon width={16} height={16} />
                </button>
              </div>
            )}

            <MessageList
              messages={chat.messages}
              actions={chat.actions}
              automation={automation}
              onApprove={chat.approveAction}
              onReject={chat.rejectAction}
              emptyHint="Type a message, dictate with the mic, or talk using the voice controls."
            />
          </div>

          {pet !== "none" && (
            <div className={`flex justify-center pb-1 ${compact ? "" : "lg:hidden"}`}>
              <Pet key={pet} kind={pet} asleep={asleep} className="w-24" />
            </div>
          )}

          {showVoice && (
            <VoiceBar
              prefs={prefs}
              onPrefs={updatePrefs}
              state={voice.state}
              level={voice.level}
              wakeScore={voice.wakeScore}
              error={voice.error}
              enabled={voice.enabled}
              onToggle={voice.toggle}
              onPttDown={voice.pttDown}
              onPttUp={voice.pttUp}
              onInterrupt={voice.interrupt}
              onWakeNow={voice.wakeNow}
            />
          )}
          <Composer
            streaming={chat.streaming}
            drafting={chat.drafting}
            automation={automation}
            onSend={chat.send}
            onTask={chat.draftTask}
            onStop={chat.stop}
            onError={chat.setError}
            onPhotoLimit={(attempted) =>
              api
                .reportPhotoLimit(attempted)
                .catch(() => chat.setError(`You can attach up to ${MAX_IMAGES} pictures per message.`))
            }
          />
        </main>

        {pet !== "none" && !compact && (
          <Pet key={pet} kind={pet} asleep={asleep} className="absolute left-full top-[45%] ml-14 hidden w-44 lg:block" />
        )}
      </div>

      <Toasts toasts={toasts} onDismiss={dismissToast} />

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        prefs={prefs}
        onPrefs={updatePrefs}
        auth={auth}
        onSignedOut={onSignedOut}
        pet={pet}
        onPet={setPet}
      />
    </div>
  );
}

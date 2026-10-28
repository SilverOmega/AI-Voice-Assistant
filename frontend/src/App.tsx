import { useCallback, useState } from "react";
import { Composer } from "./components/Composer";
import { MenuIcon, XIcon } from "./components/icons";
import { MessageList } from "./components/MessageList";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import { VoiceBar } from "./components/VoiceBar";
import { useChat } from "./hooks/useChat";
import { useHealth } from "./hooks/useHealth";
import { useVoice, useVoicePrefs } from "./hooks/useVoice";
import type { VoiceServerMessage } from "./lib/types";

export default function App() {
  const chat = useChat();
  const health = useHealth();
  const [prefs, updatePrefs] = useVoicePrefs();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

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

  return (
    <div className="flex h-full">
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
        onRename={chat.renameSession}
        onDelete={chat.deleteSession}
        onSettings={() => setSettingsOpen(true)}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <button className="rounded p-1 md:hidden" onClick={() => setSidebarOpen(true)} aria-label="Open sidebar">
            <MenuIcon />
          </button>
          <h2 className="truncate font-medium">{title}</h2>
          {health.chat && (
            <span className="ml-auto hidden truncate rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-400 sm:block">
              {health.chat.detail.split(" — ")[0]}
            </span>
          )}
        </header>

        {chat.error && (
          <div className="mx-auto mt-3 flex w-full max-w-3xl items-start gap-2 px-4">
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
          emptyHint="Type a message, dictate with the mic, or turn on voice mode below."
        />

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
        <Composer streaming={chat.streaming} onSend={chat.send} onStop={chat.stop} onError={chat.setError} />
      </main>

      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        prefs={prefs}
        onPrefs={updatePrefs}
      />
    </div>
  );
}

import { wsUrl, VOICE_BASE, WAKEWORD_BASE } from "../lib/api";
import type { VoicePrefs, VoiceServerMessage, VoiceState, WakeServerMessage } from "../lib/types";
import { MicCapture } from "./mic";
import { SpeechPlayer } from "./player";

export interface VoiceCallbacks {
  onState: (state: VoiceState) => void;
  onLevel: (level: number) => void;
  onWakeScore: (score: number) => void;
  onMessage: (msg: VoiceServerMessage) => void;
  onError: (message: string) => void;
}

type Phase = "off" | "wake" | "conversation";

const RECONNECT_MS = 1500;
const LEVEL_INTERVAL_MS = 80;
const POST_CHIME_IGNORE_MS = 350;

/**
 * Orchestrates one voice experience in the browser:
 *  - ptt:       push-to-talk; audio is sent only while the button is held
 *  - handsfree: continuous conversation; the server detects end of speech
 *  - wake:      audio goes to the wake-word service until the wake phrase is heard,
 *               then to the voice service; after a reply, a follow-up window lets the
 *               user keep talking before returning to wake-word listening
 * Audio is half-duplex unless barge-in is enabled: the mic is not forwarded while the
 * assistant is speaking, so it cannot hear (and answer) itself.
 */
export class VoiceController {
  readonly mic = new MicCapture();
  readonly player = new SpeechPlayer();
  private voiceWs: WebSocket | null = null;
  private wakeWs: WebSocket | null = null;
  private phase: Phase = "off";
  private serverState = "listening";
  private serverSessionId: string | null = null;
  private connected = false;
  private pttActive = false;
  private ignoreAudioUntil = 0;
  private followUpTimer: number | null = null;
  private reconnectTimers = new Set<number>();
  private cleanups: (() => void)[] = [];
  private lastLevelAt = 0;
  private lastState: VoiceState = "off";

  constructor(
    private prefs: VoicePrefs,
    private sessionId: string | null,
    private cb: VoiceCallbacks,
  ) {}

  get enabled(): boolean {
    return this.phase !== "off";
  }

  // ---- lifecycle ------------------------------------------------------------

  async enable(): Promise<void> {
    if (this.enabled) return;
    this.player.unlock(); // called from a click, so audio playback is allowed
    try {
      await this.mic.start();
    } catch (err) {
      this.cb.onError(micErrorMessage(err));
      return;
    }
    this.phase = this.prefs.mode === "wake" ? "wake" : "conversation";
    this.cleanups = [
      this.mic.onFrame((frame) => this.routeFrame(frame)),
      this.mic.onLevel((level) => this.emitLevel(level)),
      this.player.onIdle(() => this.onPlaybackIdle()),
    ];
    this.connectVoice();
    if (this.prefs.mode === "wake") this.connectWake();
    this.updateState();
  }

  async disable(): Promise<void> {
    this.phase = "off";
    this.clearFollowUp();
    this.reconnectTimers.forEach((t) => window.clearTimeout(t));
    this.reconnectTimers.clear();
    this.cleanups.forEach((fn) => fn());
    this.cleanups = [];
    this.closeSocket(this.voiceWs);
    this.closeSocket(this.wakeWs);
    this.voiceWs = this.wakeWs = null;
    this.connected = false;
    this.pttActive = false;
    this.player.stop();
    await this.mic.stop();
    this.updateState();
  }

  async setPrefs(prefs: VoicePrefs): Promise<void> {
    const old = this.prefs;
    this.prefs = prefs;
    if (!this.enabled) return;
    const wakeChanged =
      prefs.mode === "wake" &&
      (old.wakeModel !== prefs.wakeModel || old.wakeThreshold !== prefs.wakeThreshold);
    if (old.mode !== prefs.mode || wakeChanged) {
      await this.disable();
      await this.enable();
    } else {
      this.sendStart();
    }
  }

  setSession(sessionId: string | null): void {
    if (sessionId === this.sessionId) return;
    this.sessionId = sessionId;
    // The server already uses a session it created itself (it told us via a "session" event).
    if (sessionId !== null && sessionId === this.serverSessionId) return;
    this.sendStart();
  }

  // ---- user actions -----------------------------------------------------------

  pttDown(): void {
    if (this.prefs.mode !== "ptt" || !this.connected) return;
    this.player.stop();
    this.pttActive = true;
    this.send({ type: "ptt_start" });
  }

  pttUp(): void {
    if (!this.pttActive) return;
    this.pttActive = false;
    this.send({ type: "ptt_end" });
  }

  interrupt(): void {
    this.player.stop();
    this.send({ type: "cancel" });
  }

  /** Wake mode: skip the wake word and start listening now. */
  wakeNow(): void {
    if (this.phase === "wake") this.onWake();
  }

  // ---- audio routing ----------------------------------------------------------

  private routeFrame(frame: Int16Array): void {
    if (this.phase === "wake") {
      if (this.wakeWs?.readyState === WebSocket.OPEN) this.wakeWs.send(frame.buffer);
      return;
    }
    if (!this.connected || this.voiceWs?.readyState !== WebSocket.OPEN) return;
    if (performance.now() < this.ignoreAudioUntil) return;
    if (this.prefs.mode === "ptt") {
      if (this.pttActive) this.voiceWs.send(frame.buffer);
      return;
    }
    if (this.player.playing && !this.prefs.bargeIn) return;
    this.voiceWs.send(frame.buffer);
  }

  private emitLevel(level: number): void {
    const now = performance.now();
    if (now - this.lastLevelAt < LEVEL_INTERVAL_MS) return;
    this.lastLevelAt = now;
    this.cb.onLevel(level);
  }

  // ---- voice socket -----------------------------------------------------------

  private connectVoice(): void {
    const ws = new WebSocket(wsUrl(`${VOICE_BASE}/ws/voice`));
    ws.binaryType = "arraybuffer";
    this.voiceWs = ws;
    this.updateState();
    ws.onopen = () => this.sendStart();
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        this.player.enqueueWav(e.data);
        this.updateState();
        return;
      }
      this.handleVoiceMessage(JSON.parse(e.data) as VoiceServerMessage);
    };
    ws.onclose = () => {
      if (this.voiceWs !== ws) return;
      this.connected = false;
      this.updateState();
      if (this.enabled) {
        this.cb.onError("Voice service disconnected — reconnecting…");
        this.scheduleReconnect(() => this.connectVoice());
      }
    };
  }

  private sendStart(): void {
    this.send({
      type: "start",
      session_id: this.sessionId,
      mode: this.prefs.mode === "ptt" ? "ptt" : "vad",
      tts: this.prefs.speakReplies,
      barge_in: this.prefs.bargeIn,
    });
  }

  private send(msg: Record<string, unknown>): void {
    if (this.voiceWs?.readyState === WebSocket.OPEN) this.voiceWs.send(JSON.stringify(msg));
  }

  private handleVoiceMessage(msg: VoiceServerMessage): void {
    switch (msg.type) {
      case "ready":
        this.connected = true;
        this.serverSessionId = msg.session_id;
        break;
      case "session":
        this.serverSessionId = msg.session_id;
        break;
      case "state":
        this.serverState = msg.state;
        if (msg.state !== "listening") this.clearFollowUp();
        break;
      case "vad":
        if (msg.speaking) {
          this.clearFollowUp();
          if (this.prefs.bargeIn && this.player.playing) this.player.stop();
        }
        break;
      case "speak":
        this.player.speakText(msg.text);
        break;
      case "interrupted":
        this.player.stop();
        break;
      case "transcript":
        if (!msg.text) this.armFollowUp();
        break;
      case "turn_done":
        this.armFollowUp();
        break;
    }
    this.cb.onMessage(msg);
    this.updateState();
  }

  // ---- wake word ----------------------------------------------------------------

  private connectWake(): void {
    const params = new URLSearchParams({
      model: this.prefs.wakeModel,
      threshold: String(this.prefs.wakeThreshold),
      scores: "1",
    });
    const ws = new WebSocket(wsUrl(`${WAKEWORD_BASE}/ws/wakeword?${params}`));
    this.wakeWs = ws;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data) as WakeServerMessage;
      if (msg.type === "wake") this.onWake();
      else if (msg.type === "score") this.cb.onWakeScore(msg.score);
      else if (msg.type === "error") this.cb.onError(`Wake word: ${msg.message}`);
    };
    ws.onclose = () => {
      if (this.wakeWs !== ws) return;
      if (this.enabled && this.prefs.mode === "wake") {
        this.scheduleReconnect(() => this.connectWake());
      }
    };
  }

  private onWake(): void {
    if (this.phase !== "wake") return;
    this.phase = "conversation";
    this.player.chime();
    this.ignoreAudioUntil = performance.now() + POST_CHIME_IGNORE_MS;
    this.sendStart(); // resets the server's endpointer
    this.armFollowUp();
    this.updateState();
  }

  /** In wake mode, return to wake-word listening if the user stays silent. */
  private armFollowUp(): void {
    if (this.prefs.mode !== "wake" || this.phase !== "conversation") return;
    this.clearFollowUp();
    if (this.player.playing) return; // re-armed when playback ends
    this.followUpTimer = window.setTimeout(() => {
      this.followUpTimer = null;
      if (this.phase !== "conversation" || this.serverState !== "listening") return;
      this.phase = "wake";
      if (this.wakeWs?.readyState === WebSocket.OPEN) this.wakeWs.send(JSON.stringify({ type: "reset" }));
      this.updateState();
    }, this.prefs.followUpSeconds * 1000);
  }

  private clearFollowUp(): void {
    if (this.followUpTimer !== null) window.clearTimeout(this.followUpTimer);
    this.followUpTimer = null;
  }

  private onPlaybackIdle(): void {
    if (this.serverState === "listening") this.armFollowUp();
    this.updateState();
  }

  // ---- helpers --------------------------------------------------------------

  private scheduleReconnect(fn: () => void): void {
    const timer = window.setTimeout(() => {
      this.reconnectTimers.delete(timer);
      if (this.enabled) fn();
    }, RECONNECT_MS);
    this.reconnectTimers.add(timer);
  }

  private closeSocket(ws: WebSocket | null): void {
    if (!ws) return;
    ws.onclose = null;
    ws.onmessage = null;
    ws.close();
  }

  private computeState(): VoiceState {
    if (this.phase === "off") return "off";
    if (!this.connected) return "connecting";
    if (this.phase === "wake") return "wake-listening";
    if (this.player.playing) return "speaking";
    if (this.prefs.mode === "ptt" && this.pttActive) return "recording";
    switch (this.serverState) {
      case "recording":
      case "transcribing":
      case "thinking":
      case "speaking":
        return this.serverState;
      default:
        return "listening";
    }
  }

  private updateState(): void {
    const state = this.computeState();
    if (state !== this.lastState) {
      this.lastState = state;
      this.cb.onState(state);
    }
  }
}

function micErrorMessage(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError") return "Microphone permission was denied.";
    if (err.name === "NotFoundError") return "No microphone was found.";
  }
  return `Could not start the microphone: ${err instanceof Error ? err.message : String(err)}`;
}

import { VOICE_BASE, wsUrl } from "../lib/api";
import { MicCapture } from "./mic";

export interface DictationHandlers {
  /** What the phrase being spoken sounds like so far; replaced by the next partial or final. */
  onPartial: (text: string) => void;
  /** A finished phrase (may be empty: then the partial goes away). */
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  /** The connection dropped while listening. */
  onLost: () => void;
}

const OPEN_TIMEOUT_MS = 5_000;
/** How long stop() waits for the last phrase to be written out. */
const FINISH_TIMEOUT_MS = 10_000;

/**
 * Live dictation: streams the microphone to the voice service's /ws/dictate and reports the
 * text as it is recognized, so the message box fills while the user talks. Nothing is sent
 * to the chat; the text stays in the box.
 */
export class LiveDictation {
  private mic = new MicCapture();
  private ws: WebSocket | null = null;
  private stopFrames: (() => void) | null = null;
  private finished: (() => void) | null = null;
  private stopping = false;

  constructor(private handlers: DictationHandlers) {}

  async start(language: string | null): Promise<void> {
    const ws = await this.open();
    ws.send(JSON.stringify({ type: "start", language: language ?? "" }));
    try {
      await this.mic.start();
    } catch (err) {
      this.cancel();
      throw err;
    }
    if (this.ws !== ws) {
      void this.mic.stop(); // cancelled while the mic was starting
      return;
    }
    this.stopFrames = this.mic.onFrame((frame) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(frame.buffer);
    });
  }

  /** Stop listening and wait until the last phrase has been written out. */
  async stop(): Promise<void> {
    this.stopping = true;
    this.stopMic();
    const ws = this.ws;
    if (ws?.readyState === WebSocket.OPEN) {
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, FINISH_TIMEOUT_MS);
        this.finished = () => {
          window.clearTimeout(timer);
          resolve();
        };
        ws.send(JSON.stringify({ type: "stop" }));
      });
    }
    this.close();
  }

  /** Stop at once, without waiting for the rest of the text. */
  cancel(): void {
    this.stopping = true;
    this.stopMic();
    this.close();
  }

  private open(): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl(`${VOICE_BASE}/ws/dictate`));
      ws.binaryType = "arraybuffer";
      const timer = window.setTimeout(() => {
        ws.close();
        reject(new Error("the voice service didn't answer"));
      }, OPEN_TIMEOUT_MS);
      ws.onopen = () => {
        window.clearTimeout(timer);
        if (this.stopping) {
          ws.close();
          reject(new Error("stopped"));
          return;
        }
        this.ws = ws;
        resolve(ws);
      };
      ws.onerror = () => {
        window.clearTimeout(timer);
        if (this.ws !== ws) reject(new Error("couldn't reach the voice service"));
      };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null;
        this.stopMic();
        this.finished?.();
        if (!this.stopping) this.handlers.onLost();
      };
      ws.onmessage = (e: MessageEvent) => {
        if (typeof e.data !== "string") return;
        let msg: { type?: string; text?: unknown; message?: unknown };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (msg.type === "partial") this.handlers.onPartial(String(msg.text ?? ""));
        else if (msg.type === "final") this.handlers.onFinal(String(msg.text ?? ""));
        else if (msg.type === "error") this.handlers.onError(String(msg.message ?? "Something went wrong."));
        else if (msg.type === "done") this.finished?.();
      };
    });
  }

  private stopMic(): void {
    this.stopFrames?.();
    this.stopFrames = null;
    void this.mic.stop();
  }

  private close(): void {
    const ws = this.ws;
    this.ws = null;
    this.finished = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
  }
}

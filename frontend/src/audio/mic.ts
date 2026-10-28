import { Downsampler, FrameBuffer, floatToPcm16, rms } from "./pcm";

/** Served from public/ as a real file: some browsers reject data: URLs for worklets. */
const WORKLET_URL = "/capture-worklet.js";
/** 80 ms at 16 kHz: openWakeWord's native frame; also fine-grained enough for VAD. */
const FRAME_SAMPLES = 1280;

export type FrameListener = (frame: Int16Array) => void;
export type LevelListener = (level: number) => void;

/**
 * Shared microphone capture. Produces 16 kHz PCM16 frames for any number of
 * listeners (wake-word socket, voice socket) plus an input level for meters.
 */
export class MicCapture {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private frameListeners = new Set<FrameListener>();
  private levelListeners = new Set<LevelListener>();
  private starting: Promise<void> | null = null;

  get active(): boolean {
    return this.ctx !== null;
  }

  async start(): Promise<void> {
    if (this.ctx) return;
    if (this.starting) return this.starting;
    this.starting = this.open().finally(() => (this.starting = null));
    return this.starting;
  }

  private async open(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone access needs a secure context (https or localhost).");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ctx = new AudioContext();
    try {
      await ctx.audioWorklet.addModule(WORKLET_URL);
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      await ctx.close();
      throw err;
    }
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "capture-processor");
    const downsampler = new Downsampler(ctx.sampleRate);
    const frames = new FrameBuffer(FRAME_SAMPLES);

    node.port.onmessage = (e: MessageEvent<Float32Array>) => {
      const samples = downsampler.process(e.data);
      const level = rms(samples);
      this.levelListeners.forEach((l) => l(level));
      for (const frame of frames.push(floatToPcm16(samples))) {
        this.frameListeners.forEach((l) => l(frame));
      }
    };
    source.connect(node);
    // Keep the graph pulling audio without playing the mic back to the speakers.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);

    this.ctx = ctx;
    this.stream = stream;
    this.node = node;
  }

  onFrame(listener: FrameListener): () => void {
    this.frameListeners.add(listener);
    return () => this.frameListeners.delete(listener);
  }

  onLevel(listener: LevelListener): () => void {
    this.levelListeners.add(listener);
    return () => this.levelListeners.delete(listener);
  }

  async stop(): Promise<void> {
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = null;
    this.stream = null;
    this.node = null;
  }
}

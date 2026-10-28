/**
 * Plays assistant speech gap-free: WAV chunks from the server (Piper) are decoded and
 * scheduled back to back on one AudioContext; text chunks (browser TTS fallback) go
 * through speechSynthesis. `onIdle` fires when everything queued has finished.
 */
export class SpeechPlayer {
  private ctx: AudioContext | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private decodeChain: Promise<void> = Promise.resolve();
  private generation = 0;
  private pendingDecodes = 0;
  private utterances = 0;
  private idleListeners = new Set<() => void>();

  get playing(): boolean {
    return this.sources.size > 0 || this.pendingDecodes > 0 || this.utterances > 0;
  }

  /** Must be called from a user gesture at least once so browsers allow playback. */
  unlock(): void {
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  enqueueWav(data: ArrayBuffer): void {
    this.unlock();
    const ctx = this.ctx!;
    const generation = this.generation;
    this.pendingDecodes++;
    // Decode in arrival order so sentences never play out of sequence.
    this.decodeChain = this.decodeChain
      .then(() => ctx.decodeAudioData(data))
      .then((buffer) => {
        if (generation !== this.generation) return;
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        const startAt = Math.max(ctx.currentTime + 0.02, this.nextStart);
        source.start(startAt);
        this.nextStart = startAt + buffer.duration;
        this.sources.add(source);
        source.onended = () => {
          this.sources.delete(source);
          this.checkIdle();
        };
      })
      .catch((err) => console.warn("Could not decode speech audio", err))
      .finally(() => {
        this.pendingDecodes--;
        this.checkIdle();
      });
  }

  speakText(text: string): void {
    if (!("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    this.utterances++;
    const done = () => {
      this.utterances = Math.max(0, this.utterances - 1);
      this.checkIdle();
    };
    utterance.onend = done;
    utterance.onerror = done;
    window.speechSynthesis.speak(utterance);
  }

  /** Stop all speech immediately (barge-in / interrupt). */
  stop(): void {
    this.generation++;
    for (const source of this.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.nextStart = 0;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    this.utterances = 0;
    this.checkIdle();
  }

  onIdle(listener: () => void): () => void {
    this.idleListeners.add(listener);
    return () => this.idleListeners.delete(listener);
  }

  private checkIdle(): void {
    if (!this.playing) this.idleListeners.forEach((l) => l());
  }

  /** Short two-tone chime played when the wake word is heard. */
  chime(): void {
    this.unlock();
    const ctx = this.ctx!;
    const now = ctx.currentTime;
    [660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.09);
      gain.gain.exponentialRampToValueAtTime(0.15, now + i * 0.09 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.09 + 0.15);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.09);
      osc.stop(now + i * 0.09 + 0.16);
    });
  }
}

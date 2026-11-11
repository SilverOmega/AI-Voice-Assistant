/**
 * A snore, synthesized with Web Audio (no sound files): a fluttering rumble breathing in, then
 * a soft whoosh breathing out, repeated every CYCLE seconds until stopped.
 */

/** Seconds per breath. The pet's breathing animation uses the same length. */
export const SNORE_CYCLE = 3.6;

export interface SnoreVoice {
  /** Pitch of the rumble, Hz. */
  pitch: number;
  /** How fast the throat flutters, Hz. */
  flutter: number;
  /** How raspy the rumble is, 0..1. */
  rasp: number;
  /** Overall loudness, 0..1. */
  volume: number;
}

let ctx: AudioContext | null = null;
let noise: AudioBuffer | null = null;

/** Browsers only start audio after the user has clicked or typed on the page at least once. */
function context(): AudioContext {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function noiseSource(ctx: AudioContext): AudioBufferSourceNode {
  if (!noise) {
    noise = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = noise.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  }
  return new AudioBufferSourceNode(ctx, { buffer: noise, loop: true });
}

/** A gain that starts silent at `t` and ramps through [seconds after t, level] points. */
function envelope(ctx: AudioContext, t: number, points: [number, number][]): GainNode {
  const gain = new GainNode(ctx, { gain: 0 });
  gain.gain.setValueAtTime(0, t);
  for (const [at, level] of points) gain.gain.linearRampToValueAtTime(level, t + at);
  return gain;
}

/** Schedules one breath starting at `t`. Every node stops itself when the breath ends. */
function breath(ctx: AudioContext, out: AudioNode, t: number, voice: SnoreVoice): void {
  const end = t + SNORE_CYCLE;

  // Breathing in: a buzzy tone plus rasp, chopped by the flutter, through a low-pass.
  const tone = new OscillatorNode(ctx, { type: "sawtooth" });
  tone.frequency.setValueAtTime(voice.pitch * 0.9, t);
  tone.frequency.linearRampToValueAtTime(voice.pitch * 1.1, t + 1.0);
  tone.frequency.linearRampToValueAtTime(voice.pitch * 0.95, t + 1.5);
  const rasp = noiseSource(ctx);
  const raspFilter = new BiquadFilterNode(ctx, { type: "bandpass", frequency: 450, Q: 1.2 });
  const raspGain = new GainNode(ctx, { gain: voice.rasp });
  const lowpass = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 380, Q: 4 });
  const flutter = new GainNode(ctx, { gain: 0.5 });
  const lfo = new OscillatorNode(ctx, { frequency: voice.flutter });
  const lfoDepth = new GainNode(ctx, { gain: 0.5 }); // the flutter gain swings 0..1
  const snoreIn = envelope(ctx, t, [
    [0.45, 1],
    [1.05, 0.9],
    [1.5, 0],
  ]);
  tone.connect(lowpass);
  rasp.connect(raspFilter).connect(raspGain).connect(lowpass);
  lfo.connect(lfoDepth).connect(flutter.gain);
  lowpass.connect(flutter).connect(snoreIn).connect(out);

  // Breathing out: soft, airy noise.
  const air = noiseSource(ctx);
  const airFilter = new BiquadFilterNode(ctx, { type: "bandpass", frequency: 1000, Q: 0.6 });
  const snoreOut = envelope(ctx, t, [
    [1.8, 0],
    [2.25, 0.22],
    [3.1, 0],
  ]);
  air.connect(airFilter).connect(snoreOut).connect(out);

  for (const node of [tone, rasp, lfo, air]) {
    node.start(t);
    node.stop(end);
  }
}

/** Snores while started; stop() fades out. */
export class Snorer {
  private out: GainNode | null = null;
  private timer = 0;

  get snoring(): boolean {
    return this.out !== null;
  }

  start(voice: SnoreVoice): void {
    const ctx = context(); // also resumes audio that was blocked until now
    if (this.out) return;
    const out = new GainNode(ctx, { gain: voice.volume });
    out.connect(ctx.destination);
    this.out = out;
    let next = ctx.currentTime + 0.05;
    const schedule = () => {
      while (next < ctx.currentTime + SNORE_CYCLE) {
        breath(ctx, out, next, voice);
        next += SNORE_CYCLE;
      }
    };
    schedule();
    this.timer = window.setInterval(schedule, 1000);
  }

  stop(): void {
    const out = this.out;
    if (!out) return;
    this.out = null;
    window.clearInterval(this.timer);
    out.gain.setTargetAtTime(0, out.context.currentTime, 0.08);
    window.setTimeout(() => out.disconnect(), 600);
  }
}

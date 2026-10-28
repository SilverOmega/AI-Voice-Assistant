/** Target format for everything streamed to the backend: 16 kHz mono PCM16. */
export const TARGET_RATE = 16_000;

/**
 * Streaming downsampler from the device rate (typically 44.1/48 kHz) to 16 kHz.
 * Each output sample averages the input samples it covers (a cheap low-pass that
 * avoids most aliasing) and the fractional position carries across calls.
 */
export class Downsampler {
  private readonly ratio: number;
  private carry = new Float32Array(0);
  private position = 0;

  constructor(inputRate: number, outputRate = TARGET_RATE) {
    if (inputRate < outputRate) throw new Error(`Input rate ${inputRate} is below ${outputRate}`);
    this.ratio = inputRate / outputRate;
  }

  process(input: Float32Array): Float32Array {
    const data = new Float32Array(this.carry.length + input.length);
    data.set(this.carry);
    data.set(input, this.carry.length);

    const count = Math.floor((data.length - this.position) / this.ratio);
    const out = new Float32Array(Math.max(0, count));
    let pos = this.position;
    for (let i = 0; i < out.length; i++) {
      const start = Math.floor(pos);
      const end = Math.min(data.length, Math.floor(pos + this.ratio));
      let sum = 0;
      for (let j = start; j < end; j++) sum += data[j];
      out[i] = end > start ? sum / (end - start) : data[start];
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    this.carry = data.slice(consumed);
    this.position = pos - consumed;
    return out;
  }
}

export function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function rms(input: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
  return input.length ? Math.sqrt(sum / input.length) : 0;
}

/** Re-chunks a stream of Int16 samples into fixed-size frames. */
export class FrameBuffer {
  private buffer = new Int16Array(0);

  constructor(private readonly frameSize: number) {}

  push(samples: Int16Array): Int16Array[] {
    const merged = new Int16Array(this.buffer.length + samples.length);
    merged.set(this.buffer);
    merged.set(samples, this.buffer.length);
    const frames: Int16Array[] = [];
    let offset = 0;
    while (merged.length - offset >= this.frameSize) {
      frames.push(merged.slice(offset, offset + this.frameSize));
      offset += this.frameSize;
    }
    this.buffer = merged.slice(offset);
    return frames;
  }
}

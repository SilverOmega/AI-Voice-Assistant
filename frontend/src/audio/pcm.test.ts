import { describe, expect, it } from "vitest";
import { Downsampler, FrameBuffer, floatToPcm16, rms } from "./pcm";

describe("Downsampler", () => {
  it("produces 16 kHz output regardless of how input is chunked", () => {
    const input = new Float32Array(48_000).map((_, i) => Math.sin((2 * Math.PI * 440 * i) / 48_000));
    const whole = new Downsampler(48_000).process(input);

    const chunked = new Downsampler(48_000);
    const parts: number[] = [];
    for (let i = 0; i < input.length; i += 128) parts.push(...chunked.process(input.subarray(i, i + 128)));

    expect(whole.length).toBe(16_000);
    expect(parts.length).toBe(16_000);
    expect(parts[1234]).toBeCloseTo(whole[1234], 5);
  });

  it("handles non-integer ratios like 44.1 kHz", () => {
    const ds = new Downsampler(44_100);
    let total = 0;
    for (let i = 0; i < 100; i++) total += ds.process(new Float32Array(441)).length;
    expect(total).toBeGreaterThanOrEqual(15_999);
    expect(total).toBeLessThanOrEqual(16_000);
  });

  it("preserves a 440 Hz tone's energy and attenuates content above Nyquist", () => {
    const tone = (freq: number) =>
      new Float32Array(48_000).map((_, i) => Math.sin((2 * Math.PI * freq * i) / 48_000));
    const low = rms(new Downsampler(48_000).process(tone(440)));
    const high = rms(new Downsampler(48_000).process(tone(15_000)));
    expect(low).toBeGreaterThan(0.65);
    expect(high).toBeLessThan(low / 2);
  });

  it("rejects upsampling", () => {
    expect(() => new Downsampler(8_000)).toThrow();
  });
});

describe("floatToPcm16", () => {
  it("scales and clips", () => {
    expect(Array.from(floatToPcm16(new Float32Array([0, 1, -1, 2, -2, 0.5])))).toEqual([
      0, 32767, -32768, 32767, -32768, 16383,
    ]);
  });
});

describe("FrameBuffer", () => {
  it("re-chunks into fixed frames and keeps the remainder", () => {
    const fb = new FrameBuffer(4);
    expect(fb.push(new Int16Array([1, 2, 3]))).toEqual([]);
    const frames = fb.push(new Int16Array([4, 5, 6, 7, 8, 9]));
    expect(frames.map((f) => Array.from(f))).toEqual([
      [1, 2, 3, 4],
      [5, 6, 7, 8],
    ]);
    expect(fb.push(new Int16Array([10, 11, 12])).map((f) => Array.from(f))).toEqual([[9, 10, 11, 12]]);
  });
});

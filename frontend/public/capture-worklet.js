// AudioWorklet that forwards raw microphone samples to the main thread in ~40 ms batches.
// Resampling and PCM conversion happen on the main thread (see src/audio/pcm.ts) so they can be tested.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batch = new Float32Array(Math.round(sampleRate * 0.04));
    this.length = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      let offset = 0;
      while (offset < channel.length) {
        const n = Math.min(channel.length - offset, this.batch.length - this.length);
        this.batch.set(channel.subarray(offset, offset + n), this.length);
        this.length += n;
        offset += n;
        if (this.length === this.batch.length) {
          this.port.postMessage(this.batch.slice(0));
          this.length = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);

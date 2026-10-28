/** Records a dictation clip with MediaRecorder (compressed webm/opus) for /api/stt. */
export class ClipRecorder {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];

  get recording(): boolean {
    return this.recorder?.state === "recording";
  }

  async start(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    const mimeType = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((t) =>
      MediaRecorder.isTypeSupported(t),
    );
    this.chunks = [];
    this.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.recorder.start(250);
  }

  stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (!recorder) return Promise.reject(new Error("Not recording"));
    return new Promise((resolve) => {
      recorder.onstop = () => {
        recorder.stream.getTracks().forEach((t) => t.stop());
        this.recorder = null;
        resolve(new Blob(this.chunks, { type: recorder.mimeType }));
      };
      recorder.stop();
    });
  }

  cancel(): void {
    if (!this.recorder) return;
    this.recorder.onstop = () => this.recorder?.stream.getTracks().forEach((t) => t.stop());
    this.recorder.stop();
    this.recorder = null;
  }
}

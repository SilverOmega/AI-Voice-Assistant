/**
 * Translations of chat text, kept for this page so switching back and forth between languages,
 * or re-opening a message's translation, doesn't ask the model again.
 */
export const MAX_TRANSLATE_CHARS = 8000;

type Fetcher = (text: string, target: string) => Promise<string>;

export class TranslationStore {
  private done = new Map<string, string>();
  private running = new Map<string, Promise<string>>();

  constructor(
    private fetcher: Fetcher,
    private size = 300,
  ) {}

  private key(text: string, target: string): string {
    return `${target.trim().toLowerCase()}\u0000${text.trim()}`;
  }

  /** Already translated, so it can show without a loading state. */
  cached(text: string, target: string): string | undefined {
    return this.done.get(this.key(text, target));
  }

  /** One request per text and language, however many places ask at once. */
  translate(text: string, target: string): Promise<string> {
    const key = this.key(text, target);
    const hit = this.done.get(key);
    if (hit !== undefined) return Promise.resolve(hit);
    const running = this.running.get(key);
    if (running) return running;
    const request = this.fetcher(text.trim(), target.trim())
      .then((result) => {
        this.done.set(key, result);
        if (this.done.size > this.size) this.done.delete(this.done.keys().next().value!);
        return result;
      })
      .finally(() => this.running.delete(key));
    this.running.set(key, request);
    return request;
  }
}

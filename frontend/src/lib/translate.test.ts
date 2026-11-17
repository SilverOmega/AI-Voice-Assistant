import { describe, expect, it, vi } from "vitest";
import { browserLanguage, languageCode, languageLabel, validLanguageName } from "./languages";
import { TranslationStore } from "./translate";

describe("TranslationStore", () => {
  it("asks once per text and language, and caches the result", async () => {
    const fetcher = vi.fn(async (text: string, target: string) => `${target}: ${text}`);
    const store = new TranslationStore(fetcher);
    const [a, b] = await Promise.all([store.translate("Hello", "French"), store.translate(" Hello ", "french")]);
    expect(a).toBe("French: Hello");
    expect(b).toBe(a);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(store.cached("Hello", "FRENCH")).toBe("French: Hello");
    await store.translate("Hello", "German");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("doesn't keep failures, so trying again asks again", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("model down")).mockResolvedValueOnce("Hola");
    const store = new TranslationStore(fetcher);
    await expect(store.translate("Hello", "Spanish")).rejects.toThrow("model down");
    expect(await store.translate("Hello", "Spanish")).toBe("Hola");
  });

  it("forgets the oldest translations past its size", async () => {
    const store = new TranslationStore(async (t) => t.toUpperCase(), 2);
    for (const word of ["a", "b", "c"]) await store.translate(word, "X");
    expect(store.cached("a", "X")).toBeUndefined();
    expect(store.cached("c", "X")).toBe("C");
  });
});

describe("languages", () => {
  it("finds codes and labels by English or native name", () => {
    expect(languageCode("Vietnamese")).toBe("vi");
    expect(languageCode("tiếng việt")).toBe("vi");
    expect(languageCode("Klingon")).toBeNull();
    expect(languageLabel("Japanese")).toBe("Japanese · 日本語");
    expect(languageLabel("English")).toBe("English");
    expect(languageLabel("Klingon")).toBe("Klingon");
  });

  it("accepts language names in any script, nothing else", () => {
    for (const ok of ["Klingon", "Swiss German", "हिन्दी", "Português (Brasil)", "N'Ko"]) expect(validLanguageName(ok)).toBe(true);
    for (const bad of ["", "C3PO", 'English"', "Say hi. Now", "x".repeat(61)]) expect(validLanguageName(bad)).toBe(false);
  });

  it("picks the browser's language", () => {
    expect(browserLanguage(["vi-VN", "en-US"])).toBe("Vietnamese");
    expect(browserLanguage(["zh-Hant-TW"])).toBe("Chinese (Traditional)");
    expect(browserLanguage(["zh-CN"])).toBe("Chinese (Simplified)");
    expect(browserLanguage(["nb-NO"])).toBe("Norwegian");
    expect(browserLanguage(["xx"])).toBe("English");
  });
});

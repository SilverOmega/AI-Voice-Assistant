/**
 * Languages offered in the pickers. Any other language can be typed in ("Other language…"):
 * the model understands it by name; only speech recognition and voices need a code.
 */
export interface Language {
  /** English name: what the model is told ("Reply in Vietnamese"). */
  name: string;
  /** The name in the language itself, shown next to it. */
  native: string;
  /** ISO 639-1 code, as Whisper, Piper and the browser's speech use it. */
  code: string;
}

export const LANGUAGES: Language[] = [
  { name: "Afrikaans", native: "Afrikaans", code: "af" },
  { name: "Arabic", native: "العربية", code: "ar" },
  { name: "Bengali", native: "বাংলা", code: "bn" },
  { name: "Bulgarian", native: "Български", code: "bg" },
  { name: "Catalan", native: "Català", code: "ca" },
  { name: "Chinese (Simplified)", native: "简体中文", code: "zh" },
  { name: "Chinese (Traditional)", native: "繁體中文", code: "zh" },
  { name: "Croatian", native: "Hrvatski", code: "hr" },
  { name: "Czech", native: "Čeština", code: "cs" },
  { name: "Danish", native: "Dansk", code: "da" },
  { name: "Dutch", native: "Nederlands", code: "nl" },
  { name: "English", native: "English", code: "en" },
  { name: "Estonian", native: "Eesti", code: "et" },
  { name: "Filipino", native: "Filipino", code: "tl" },
  { name: "Finnish", native: "Suomi", code: "fi" },
  { name: "French", native: "Français", code: "fr" },
  { name: "German", native: "Deutsch", code: "de" },
  { name: "Greek", native: "Ελληνικά", code: "el" },
  { name: "Gujarati", native: "ગુજરાતી", code: "gu" },
  { name: "Hebrew", native: "עברית", code: "he" },
  { name: "Hindi", native: "हिन्दी", code: "hi" },
  { name: "Hungarian", native: "Magyar", code: "hu" },
  { name: "Icelandic", native: "Íslenska", code: "is" },
  { name: "Indonesian", native: "Bahasa Indonesia", code: "id" },
  { name: "Italian", native: "Italiano", code: "it" },
  { name: "Japanese", native: "日本語", code: "ja" },
  { name: "Kannada", native: "ಕನ್ನಡ", code: "kn" },
  { name: "Kazakh", native: "Қазақ тілі", code: "kk" },
  { name: "Khmer", native: "ខ្មែរ", code: "km" },
  { name: "Korean", native: "한국어", code: "ko" },
  { name: "Lao", native: "ລາວ", code: "lo" },
  { name: "Latvian", native: "Latviešu", code: "lv" },
  { name: "Lithuanian", native: "Lietuvių", code: "lt" },
  { name: "Malay", native: "Bahasa Melayu", code: "ms" },
  { name: "Malayalam", native: "മലയാളം", code: "ml" },
  { name: "Marathi", native: "मराठी", code: "mr" },
  { name: "Burmese", native: "မြန်မာ", code: "my" },
  { name: "Nepali", native: "नेपाली", code: "ne" },
  { name: "Norwegian", native: "Norsk", code: "no" },
  { name: "Persian", native: "فارسی", code: "fa" },
  { name: "Polish", native: "Polski", code: "pl" },
  { name: "Portuguese (Brazil)", native: "Português (Brasil)", code: "pt" },
  { name: "Portuguese (Portugal)", native: "Português (Portugal)", code: "pt" },
  { name: "Punjabi", native: "ਪੰਜਾਬੀ", code: "pa" },
  { name: "Romanian", native: "Română", code: "ro" },
  { name: "Russian", native: "Русский", code: "ru" },
  { name: "Serbian", native: "Српски", code: "sr" },
  { name: "Slovak", native: "Slovenčina", code: "sk" },
  { name: "Slovenian", native: "Slovenščina", code: "sl" },
  { name: "Spanish", native: "Español", code: "es" },
  { name: "Swahili", native: "Kiswahili", code: "sw" },
  { name: "Swedish", native: "Svenska", code: "sv" },
  { name: "Tamil", native: "தமிழ்", code: "ta" },
  { name: "Telugu", native: "తెలుగు", code: "te" },
  { name: "Thai", native: "ไทย", code: "th" },
  { name: "Turkish", native: "Türkçe", code: "tr" },
  { name: "Ukrainian", native: "Українська", code: "uk" },
  { name: "Urdu", native: "اردو", code: "ur" },
  { name: "Vietnamese", native: "Tiếng Việt", code: "vi" },
  { name: "Welsh", native: "Cymraeg", code: "cy" },
].sort((a, b) => a.name.localeCompare(b.name));

const byName = new Map(LANGUAGES.map((l) => [l.name.toLowerCase(), l]));
for (const l of LANGUAGES) if (!byName.has(l.native.toLowerCase())) byName.set(l.native.toLowerCase(), l);

/** The listed language with this English or native name, if any. */
export function findLanguage(name: string): Language | undefined {
  return byName.get(name.trim().toLowerCase());
}

/** The code for speech recognition and voices, or null for a language typed in by hand. */
export function languageCode(name: string): string | null {
  return findLanguage(name)?.code ?? null;
}

/** "Vietnamese · Tiếng Việt", or the name as typed. */
export function languageLabel(name: string): string {
  const l = findLanguage(name);
  if (!l) return name;
  return l.native === l.name ? l.name : `${l.name} · ${l.native}`;
}

/** Same rule as the server: letters of any script, spaces, hyphens, apostrophes, brackets. */
const NAME = /^\p{L}[\p{L}\p{M} ,'()-]*$/u;
export const MAX_LANGUAGE_CHARS = 60;

export function validLanguageName(name: string): boolean {
  const value = name.trim().replace(/\s+/g, " ");
  return value.length > 0 && value.length <= MAX_LANGUAGE_CHARS && NAME.test(value);
}

/** The browser's own language (e.g. vi-VN -> Vietnamese), else English. */
export function browserLanguage(locales: readonly string[] = navigator.languages ?? [navigator.language]): string {
  for (const locale of locales) {
    const lower = locale.toLowerCase();
    const [base, region] = lower.split("-");
    if (base === "zh") {
      const traditional = lower.includes("hant") || region === "tw" || region === "hk" || region === "mo";
      return traditional ? "Chinese (Traditional)" : "Chinese (Simplified)";
    }
    if (base === "pt") return region === "pt" ? "Portuguese (Portugal)" : "Portuguese (Brazil)";
    const match = LANGUAGES.find((l) => l.code === (base === "nb" || base === "nn" ? "no" : base === "fil" ? "tl" : base));
    if (match) return match.name;
  }
  return "English";
}

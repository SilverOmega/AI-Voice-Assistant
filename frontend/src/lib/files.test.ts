import { describe, expect, it } from "vitest";
import { ATTACH_ACCEPT, describeFile, fileKind, formatSize } from "./files";

describe("files", () => {
  it("names the kind of document", () => {
    expect(fileKind("Report.PDF")).toBe("PDF");
    expect(fileKind("letter.docx")).toBe("Word");
    expect(fileKind("budget.xlsx")).toBe("Spreadsheet");
    expect(fileKind("deck.pptx")).toBe("Slides");
    expect(fileKind("notes.txt")).toBe("Text");
  });

  it("describes size and pages", () => {
    expect(formatSize(900)).toBe("900 B");
    expect(formatSize(340 * 1024)).toBe("340 KB");
    expect(formatSize(3.5 * 1024 * 1024)).toBe("3.5 MB");
    expect(describeFile({ name: "a.pdf", size: 2048, pages: 12 })).toBe("PDF · 12 pages · 2 KB");
    expect(describeFile({ name: "deck.pptx", size: 2048, pages: 1 })).toBe("Slides · 1 slide · 2 KB");
    expect(describeFile({ name: "a.txt", size: 10 })).toBe("Text · 10 B");
  });

  it("offers pictures and documents in one picker", () => {
    expect(ATTACH_ACCEPT.startsWith("image/*,.pdf,.docx")).toBe(true);
  });
});

import { describe, expect, it } from "vitest";
import { MAX_EDGE, fitWithin, imageFiles } from "./images";

describe("fitWithin", () => {
  it("leaves small images alone", () => {
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
  });

  it("scales the longest edge down to the limit", () => {
    expect(fitWithin(4032, 3024)).toEqual({ width: MAX_EDGE, height: 1176 });
    expect(fitWithin(1000, 4000)).toEqual({ width: 392, height: MAX_EDGE });
  });

  it("never returns a zero dimension", () => {
    expect(fitWithin(10000, 1)).toEqual({ width: MAX_EDGE, height: 1 });
  });
});

describe("imageFiles", () => {
  it("keeps only images", () => {
    const files = [
      new File(["x"], "a.png", { type: "image/png" }),
      new File(["x"], "notes.txt", { type: "text/plain" }),
      new File(["x"], "b.webp", { type: "image/webp" }),
    ];
    expect(imageFiles(files).map((f) => f.name)).toEqual(["a.png", "b.webp"]);
  });
});

import { describe, expect, it, vi } from "vitest";
import { downloadBlob, sanitizeFilename } from "./download";

describe("sanitizeFilename", () => {
  it("replaces path separators so the browser cannot nest the download into a subfolder", () => {
    expect(sanitizeFilename("番外/正片", "script")).toBe("番外-正片");
    expect(sanitizeFilename("a\\b", "script")).toBe("a-b");
  });

  it("strips control characters and Windows-reserved characters", () => {
    expect(sanitizeFilename('a:b*c?d"e<f>g|h', "script")).toBe("abcdefgh");
  });

  it("falls back when the cleaned result is empty", () => {
    expect(sanitizeFilename("", "script")).toBe("script");
    expect(sanitizeFilename("   ", "script")).toBe("script");
    expect(sanitizeFilename("...", "script")).toBe("script");
  });

  it("leaves ordinary titles untouched", () => {
    expect(sanitizeFilename("第1集 开场", "script")).toBe("第1集 开场");
  });
});

describe("downloadBlob", () => {
  it("defers URL revocation until the browser has started consuming the download", async () => {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:download"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    vi.useFakeTimers();
    try {
      downloadBlob(new Blob(["zip"]), "presentation.zip");

      expect(URL.revokeObjectURL).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(0);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download");
    } finally {
      vi.useRealTimers();
    }
  });
});

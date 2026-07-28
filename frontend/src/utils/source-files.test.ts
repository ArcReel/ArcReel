import { describe, it, expect } from "vitest";
import {
  SOURCE_FILE_ACCEPT,
  SOURCE_FILE_EXTENSIONS,
  SOURCE_FILE_FORMATS_LABEL,
  isSupportedSourceFile,
} from "./source-files";

describe("SOURCE_FILE_ACCEPT", () => {
  it("joins every extension for the input accept attribute", () => {
    expect(SOURCE_FILE_ACCEPT).toBe(".txt,.md,.docx,.epub,.pdf");
  });
});

describe("isSupportedSourceFile", () => {
  it("accepts every supported extension", () => {
    for (const ext of SOURCE_FILE_EXTENSIONS) {
      expect(isSupportedSourceFile(`novel${ext}`)).toBe(true);
    }
  });
  it("is case-insensitive", () => {
    expect(isSupportedSourceFile("Novel.PDF")).toBe(true);
    expect(isSupportedSourceFile("Story.DocX")).toBe(true);
  });
  it("rejects unsupported extensions", () => {
    expect(isSupportedSourceFile("image.png")).toBe(false);
    expect(isSupportedSourceFile("archive.zip")).toBe(false);
    expect(isSupportedSourceFile("noextension")).toBe(false);
    expect(isSupportedSourceFile("")).toBe(false);
  });
  it("matches the suffix, not a substring", () => {
    // A supported extension in the middle must not pass.
    expect(isSupportedSourceFile("foo.txt.zip")).toBe(false);
    // ...but the trailing extension is what counts.
    expect(isSupportedSourceFile("report.pdf.txt")).toBe(true);
  });
});

describe("SOURCE_FILE_FORMATS_LABEL", () => {
  it("renders the uppercase dotless list", () => {
    expect(SOURCE_FILE_FORMATS_LABEL).toBe("TXT · MD · DOCX · EPUB · PDF");
  });
});

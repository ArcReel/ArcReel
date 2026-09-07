import { describe, expect, it } from "vitest";

import {
  anthropicMessagesUrl,
  hasUnsupportedUrlComponents,
  normalizeAnthropicBaseUrl,
} from "./anthropic-url";

describe("normalizeAnthropicBaseUrl", () => {
  it.each([
    ["https://x/anthropic/", "https://x/anthropic"],
    ["https://x/anthropic/v1/messages", "https://x/anthropic"],
    ["https://x/anthropic/v1/messages/", "https://x/anthropic"],
    ["https://x//v1/messages", "https://x"],
    ["  https://x  ", "https://x"],
  ])("归一 %s", (raw, expected) => {
    expect(normalizeAnthropicBaseUrl(raw)).toBe(expected);
  });

  it.each(["https://x/anthropic/v1", "https://x/messages", "https://x/v1beta"])(
    "不剥版本段与单独的 /messages：%s",
    (raw) => {
      expect(normalizeAnthropicBaseUrl(raw)).toBe(raw);
    },
  );
});

describe("anthropicMessagesUrl", () => {
  it("预览即调用地址，重复的版本段原样保留", () => {
    expect(anthropicMessagesUrl("https://api.deepseek.com/anthropic/v1")).toBe(
      "https://api.deepseek.com/anthropic/v1/v1/messages",
    );
  });
});

describe("hasUnsupportedUrlComponents", () => {
  it.each([
    "https://relay.example.com/anthropic?api_key=sk-x",
    "https://x/a#frag",
    "https://x/a?",
    "https://x/a#",
    "https://u:p@x/a",
  ])("拦下 %s", (raw) => {
    expect(hasUnsupportedUrlComponents(raw)).toBe(true);
  });

  it.each(["", "   ", "https://x/anthropic", "x.example.com"])(
    "放行 %s（缺 scheme 等形态由后端判定）",
    (raw) => {
      expect(hasUnsupportedUrlComponents(raw)).toBe(false);
    },
  );
});

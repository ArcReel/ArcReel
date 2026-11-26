import { expect, it } from "vitest";

it("waits for the language detector and resources before returning the test module", async () => {
  const { default: i18n } = await import("@/i18n");
  expect(i18n.isInitialized).toBe(true);
  expect(i18n.language).toBe("zh");
});

import { describe, expect, it } from "vitest";
import { mergeServerDraftPreservingDirty } from "./system-config-draft-utils";

describe("mergeServerDraftPreservingDirty", () => {
  it("keeps local dirty edits while adopting untouched server updates", () => {
    const currentDraft = {
      apiKey: "typed-locally",
      baseUrl: "https://saved.example.com",
      model: "claude-3.7",
    };
    const previousSavedDraft = {
      apiKey: "",
      baseUrl: "https://saved.example.com",
      model: "claude-3.5",
    };
    const nextSavedDraft = {
      apiKey: "",
      baseUrl: "",
      model: "claude-3.5",
    };

    expect(
      mergeServerDraftPreservingDirty(currentDraft, previousSavedDraft, nextSavedDraft),
    ).toEqual({
      apiKey: "typed-locally",
      baseUrl: "",
      model: "claude-3.7",
    });
  });
});

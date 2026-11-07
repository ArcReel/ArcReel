/**
 * Minimal `ScriptReviewGate` test double: render a placeholder without fetching script-review drafts or sending requests.
 * For canvas tests where the script-review gate itself is not under test.
 *
 * Usage (the `vi.mock` factory dynamically imports this module, avoiding ordinary import bindings being unavailable
 * when vi.mock is hoisted to the file top):
 *
 * ```ts
 * vi.mock("@/components/canvas/timeline/ScriptReviewGate", async () => {
 *   const { scriptReviewGateMock } = await import("@/__mocks__/ScriptReviewGate");
 *   return scriptReviewGateMock();
 * });
 * ```
 */
export function scriptReviewGateMock() {
  return {
    ScriptReviewGate: () => <div data-testid="script-review-gate" />,
  };
}

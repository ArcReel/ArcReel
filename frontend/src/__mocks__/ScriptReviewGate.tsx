/**
 * `ScriptReviewGate` 的最简测试替身：渲染占位节点，不拉剧本审阅草稿、不发请求。
 * 用于剧本审阅闸门本身不是被测对象的画布测试。
 *
 * 用法（`vi.mock` 的 factory 通过动态 import 引用本模块，绕开 vi.mock 提升到文件顶部
 * 时访问不到普通 import 绑定的限制）：
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

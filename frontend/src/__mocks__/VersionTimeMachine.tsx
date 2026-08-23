/**
 * `VersionTimeMachine` 的最简测试替身：渲染一个可断言存在的占位节点，不拉版本列表、
 * 不发请求。用于版本历史入口不是被测组件本身重点的场景（如卡片渲染/交互测试）。
 *
 * 用法（`vi.mock` 的 factory 通过动态 import 引用本模块，绕开 vi.mock 提升到文件顶部
 * 时访问不到普通 import 绑定的限制）：
 *
 * ```ts
 * vi.mock("@/components/canvas/timeline/VersionTimeMachine", async () => {
 *   const { versionTimeMachineMock } = await import("@/__mocks__/VersionTimeMachine");
 *   return versionTimeMachineMock();
 * });
 * ```
 */
export function versionTimeMachineMock() {
  return {
    VersionTimeMachine: () => <div data-testid="version-time-machine">versions</div>,
  };
}

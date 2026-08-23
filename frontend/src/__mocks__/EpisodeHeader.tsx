/**
 * `EpisodeHeader` 的最简测试替身：渲染占位节点，并把 `canEditTitle` 摊到
 * `data-can-edit-title` 上供断言标题可编辑性的用例读取；不拉剧集数据、不发请求。
 *
 * 用法（`vi.mock` 的 factory 通过动态 import 引用本模块，绕开 vi.mock 提升到文件顶部
 * 时访问不到普通 import 绑定的限制）：
 *
 * ```ts
 * vi.mock("@/components/canvas/timeline/EpisodeHeader", async () => {
 *   const { episodeHeaderMock } = await import("@/__mocks__/EpisodeHeader");
 *   return episodeHeaderMock();
 * });
 * ```
 */
export function episodeHeaderMock() {
  return {
    EpisodeHeader: ({ canEditTitle }: { canEditTitle?: boolean }) => (
      <div data-testid="episode-header" data-can-edit-title={canEditTitle ? "yes" : "no"} />
    ),
  };
}

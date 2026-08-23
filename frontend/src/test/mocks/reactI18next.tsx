/**
 * 共享的 `react-i18next` 测试替身：`t()` 原样返回 key（带 `{{param}}` 占位符插值），
 * 不加载真实翻译资源。用于断言只关心 key/交互、不关心具体译文的组件测试。
 *
 * 用法（`vi.mock` 的 factory 通过动态 import 引用本模块，绕开 vi.mock 提升到文件顶部
 * 时访问不到普通 import 绑定的限制）：
 *
 * ```ts
 * vi.mock("react-i18next", async () => {
 *   const { reactI18nextMock } = await import("@/test/mocks/reactI18next");
 *   return reactI18nextMock();
 * });
 * ```
 */
export function reactI18nextMock() {
  return {
    useTranslation: () => ({
      t: (key: string, opts?: Record<string, unknown>) => {
        if (!opts) return key;
        let result = key;
        for (const [k, v] of Object.entries(opts)) {
          result = result.replace(`{{${k}}}`, String(v));
        }
        return result;
      },
      i18n: { language: "en" },
    }),
  };
}

/**
 * unit / 分镜 id 的等宽标签。面板、准入结论与画布单元列表共用同一书写形态，
 * 用户才能在几处对上号；`translate="no"` 防止浏览器翻译改写 id。
 */
export function UnitTag({ unitId }: { unitId: string }) {
  return (
    <span
      translate="no"
      className="rounded-md px-1.5 py-0.5 font-mono text-[11.5px]"
      style={{ background: "var(--color-surface-2)", color: "var(--color-text-2)" }}
    >
      {unitId}
    </span>
  );
}

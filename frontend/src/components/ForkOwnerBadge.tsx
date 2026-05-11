// fork-private: owner 徽章 — 按 owner 名字哈希派生色相，admin 大厅一眼区分多人项目。
//
// 抽离到独立文件以最小化对上游 ProjectsPage.tsx 的侵入：上游升级时本文件不会冲突，
// 上游侧仅保留 `<ForkOwnerBadge owner={project.owner} />` 这一行调用 + import。

import { useMemo, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

function hashHue(value: string): number {
  let hash = 41;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}

interface ForkOwnerBadgeProps {
  owner: string | null | undefined;
}

export function ForkOwnerBadge({ owner }: ForkOwnerBadgeProps) {
  const { t } = useTranslation();
  const label = owner ?? t("fork:access.project.owner_unknown");
  const hue = useMemo(() => hashHue(label), [label]);
  const style: CSSProperties = {
    color: `oklch(0.95 0.04 ${hue})`,
    background: `oklch(0.55 0.18 ${hue} / 0.22)`,
    borderColor: `oklch(0.65 0.18 ${hue} / 0.65)`,
    boxShadow: `0 0 10px oklch(0.65 0.18 ${hue} / 0.35)`,
  };
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.1em]"
      style={style}
      title={t("fork:access.project.owner_label")}
    >
      <span className="opacity-80">@</span>
      {label}
    </span>
  );
}

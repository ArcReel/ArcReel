// PROTOTYPE — wayfinder #2290 宿主：三个变体挂在设置页「使用记录 · 原型」小节下，经 ?variant= 切换
// （A 纵向仪表盘 / B 记录优先·分面栏 / C 概览·记录分页签），筛选状态写在 u_* query。评审后整目录删除。
import { useSearch } from "wouter";

import { PrototypeSwitcher } from "./PrototypeSwitcher";
import { UsagePrototypeA } from "./UsagePrototypeA";
import { UsagePrototypeB } from "./UsagePrototypeB";
import { UsagePrototypeC } from "./UsagePrototypeC";

const VARIANTS = [
  { key: "A", name: "纵向仪表盘" },
  { key: "B", name: "记录优先 · 分面栏" },
  { key: "C", name: "概览 / 记录 分页签" },
];

export function UsageRecordsPrototype() {
  const search = useSearch();
  const variant = new URLSearchParams(search).get("variant") ?? "A";
  return (
    <>
      {variant === "B" ? <UsagePrototypeB /> : variant === "C" ? <UsagePrototypeC /> : <UsagePrototypeA />}
      <PrototypeSwitcher variants={VARIANTS} current={variant} />
    </>
  );
}

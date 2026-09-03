// PROTOTYPE — wayfinder #2310 宿主：三个变体分别挂在全局设置页 Agent 分区（level=user）与项目设置页（level=project），
// 经 ?variant= 切换（A 文件柜 / B 记忆卡片 / C 一页文档）。两级共用同一变体组件，只换文案与样例数据。
// 仅开发构建可见；评审后整目录删除。
import { useSearch } from "wouter";

import { MemoryPrototypeA } from "./MemoryPrototypeA";
import { MemoryPrototypeB } from "./MemoryPrototypeB";
import { MemoryPrototypeC } from "./MemoryPrototypeC";
import { PrototypeSwitcher } from "./PrototypeSwitcher";
import type { MemoryLevel } from "./memory-prototype-store";

const VARIANTS = [
  { key: "A", name: "文件柜 · 原文编辑" },
  { key: "B", name: "记忆卡片 · 隐藏文件" },
  { key: "C", name: "一页文档 · 逐节编辑" },
];

export function AgentMemoryPrototype({ level }: { level: MemoryLevel }) {
  const search = useSearch();
  if (!import.meta.env.DEV) return null;
  const variant = new URLSearchParams(search).get("variant") ?? "A";
  return (
    <>
      {variant === "B" ? <MemoryPrototypeB level={level} /> : variant === "C" ? <MemoryPrototypeC level={level} /> : <MemoryPrototypeA level={level} />}
      <PrototypeSwitcher variants={VARIANTS} current={variant} />
    </>
  );
}

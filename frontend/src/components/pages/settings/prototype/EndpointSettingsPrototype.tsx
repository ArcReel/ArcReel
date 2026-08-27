// PROTOTYPE — wayfinder #2129 宿主：三个变体挂在设置页「调用端点」小节下，
// 经 ?variant= 切换（A 主从表单 / B 代码工作台 / C 就地接线）。评审后整目录删除。
import { useSearch } from "wouter";
import { PrototypeSwitcher } from "./PrototypeSwitcher";
import { EndpointPrototypeA } from "./EndpointPrototypeA";
import { EndpointPrototypeB } from "./EndpointPrototypeB";
import { EndpointPrototypeC } from "./EndpointPrototypeC";

const VARIANTS = [
  { key: "A", name: "主从表单" },
  { key: "B", name: "代码工作台" },
  { key: "C", name: "就地接线" },
];

export function EndpointSettingsPrototype() {
  const search = useSearch();
  const variant = new URLSearchParams(search).get("variant") ?? "A";

  return (
    <>
      {variant === "B" ? <EndpointPrototypeB /> : variant === "C" ? <EndpointPrototypeC /> : <EndpointPrototypeA />}
      <PrototypeSwitcher variants={VARIANTS} current={variant} />
    </>
  );
}

// PROTOTYPE — 一次性代码，不进 main。
// 设置页「ComfyUI 接入」的三个变体，挂在 `/app/settings?section=comfyui` 上，经 `?variant=a|b|c` 切换。
// 三个变体共享同一份内存夹具（useComfyProto），所以切换变体时导入的 workflow 与绑定改动会保留，
// 方便对照同一状态在不同布局下的样子。
import { useSearch } from "wouter";

import { PrototypeSwitcher } from "@/components/ui/PrototypeSwitcher";

import { useComfyProto } from "./fixtures";
import { VARIANT_A_NAME, VariantA } from "./VariantA";
import { VARIANT_B_NAME, VariantB } from "./VariantB";
import { VARIANT_C_NAME, VariantC } from "./VariantC";

const VARIANTS = [
  { key: "a", name: VARIANT_A_NAME },
  { key: "b", name: VARIANT_B_NAME },
  { key: "c", name: VARIANT_C_NAME },
];

export function ComfyUIPrototypeSection() {
  const search = useSearch();
  const raw = new URLSearchParams(search).get("variant") ?? "a";
  const variant = VARIANTS.some((v) => v.key === raw) ? raw : "a";
  const proto = useComfyProto();

  return (
    <>
      {variant === "a" ? <VariantA proto={proto} /> : variant === "b" ? <VariantB proto={proto} /> : <VariantC proto={proto} />}
      <PrototypeSwitcher variants={VARIANTS} current={variant} />
    </>
  );
}

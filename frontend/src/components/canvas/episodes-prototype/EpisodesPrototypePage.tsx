// PROTOTYPE（#2767，一次性代码，勿合入 main）
// 三个结构不同的「分集」视图变体，挂在 /app/projects/:name/episodes-prototype，?variant=A|B|C 切换。
// 数据全是内存假数据，与真实项目无关；右下角「账本状态」可看每次操作后的账本。
import { PrototypeSwitcher, usePrototypeVariant } from "@/components/shared/PrototypeSwitcher";
import { StateInspector } from "./shared";
import { useProto } from "./useProto";
import { VariantA } from "./VariantA";
import { VariantB } from "./VariantB";
import { VariantC } from "./VariantC";

const VARIANTS = [
  { key: "A", name: "集清单为主" },
  { key: "B", name: "原文双栏" },
  { key: "C", name: "源文条带" },
];

export function EpisodesPrototypePage() {
  const p = useProto();
  const v = usePrototypeVariant(VARIANTS.map((x) => x.key));
  return (
    <div className="relative h-full">
      {v === "A" && <VariantA p={p} />}
      {v === "B" && <VariantB p={p} />}
      {v === "C" && <VariantC p={p} />}
      <StateInspector p={p} />
      <PrototypeSwitcher variants={VARIANTS} />
    </div>
  );
}

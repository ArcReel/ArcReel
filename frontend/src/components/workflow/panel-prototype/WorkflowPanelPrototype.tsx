// PROTOTYPE（#2828，一次性代码，勿合入 main）
// 集页 WorkflowPanel 的三个形态，URL 带 ?variant=A|B|C 时替换真实面板；?scenario= 选现状场景。
// 数据全是内存假数据，下方画布仍是真实项目。
import { PrototypeSwitcher, usePrototypeVariant } from "@/components/shared/PrototypeSwitcher";
import { LogProvider, useScenario } from "./shared";
import { VariantA, VariantB, VariantC } from "./variants";

const VARIANTS = [
  { key: "A", name: "步骤清单" },
  { key: "B", name: "横向步骤条" },
  { key: "C", name: "下一步为主" },
];

function Body() {
  const s = useScenario();
  const v = usePrototypeVariant(VARIANTS.map((x) => x.key));
  return (
    <>
      {v === "A" && <VariantA key={s.id} s={s} />}
      {v === "B" && <VariantB key={s.id} s={s} />}
      {v === "C" && <VariantC key={s.id} s={s} />}
    </>
  );
}

export function WorkflowPanelPrototype() {
  return (
    <LogProvider>
      <Body />
      <PrototypeSwitcher variants={VARIANTS} />
    </LogProvider>
  );
}

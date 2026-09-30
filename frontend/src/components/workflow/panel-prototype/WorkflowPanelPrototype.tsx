// PROTOTYPE（#2828，一次性代码，勿合入 main）
// 集页 WorkflowPanel：#2828 已选定 A「步骤清单」。#2829 原型复用它，URL 的 ?variant= 归 #2829 的变体切换。
import { LogProvider, useScenario } from "./shared";
import { VariantA } from "./variants";

function Body() {
  const s = useScenario();
  return <VariantA key={s.id} s={s} />;
}

export function WorkflowPanelPrototype() {
  return (
    <LogProvider>
      <Body />
    </LogProvider>
  );
}

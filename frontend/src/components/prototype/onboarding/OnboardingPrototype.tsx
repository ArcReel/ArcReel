// PROTOTYPE — onboarding 形态对比原型入口。
// 三个结构性不同的变体挂在现有 /app/projects 路由上,`?variant=` 切换:
//   tour         A — 聚光灯 Tour 遮罩 + demo 数据
//   demo-project B — 只读示例项目
//   panel        C — 分步演示面板
// 底部浮动切换条(←/→ 或方向键循环,「重播」重新走一遍当前变体)。
// DEV only:生产构建不渲染。触发语义按已拍板的判定决议模拟——进入大厅即自动弹,
// 任何退出都算“已看”(本原型内存态,刷新即重置,方便反复观看)。
import { useState } from "react";
import {
  PrototypeSwitcher,
  useVariant,
  type OnboardingVariant,
} from "./prototype-shared";
import { VariantTour } from "./VariantTour";
import { VariantDemoProject } from "./VariantDemoProject";
import { VariantPanel } from "./VariantPanel";

export function OnboardingPrototype() {
  const [variant, setVariant] = useVariant();
  // seen 按变体记内存态;replayKey 让“重播”能重置变体内部步骤
  const [seen, setSeen] = useState<Partial<Record<OnboardingVariant, boolean>>>({});
  const [replayKey, setReplayKey] = useState(0);

  if (!import.meta.env.DEV) return null;

  const markSeen = () => setSeen((s) => ({ ...s, [variant]: true }));
  const replay = () => {
    setSeen((s) => ({ ...s, [variant]: false }));
    setReplayKey((k) => k + 1);
  };
  const active = !seen[variant];

  return (
    <>
      {active && variant === "tour" ? <VariantTour key={replayKey} onDone={markSeen} /> : null}
      {active && variant === "demo-project" ? (
        <VariantDemoProject key={replayKey} onDone={markSeen} />
      ) : null}
      {active && variant === "panel" ? <VariantPanel key={replayKey} onDone={markSeen} /> : null}
      <PrototypeSwitcher variant={variant} onChange={setVariant} onReplay={replay} />
    </>
  );
}

// PROTOTYPE — #3091 设置页接入点：按当前变体渲染「供应商」与新增的「模型」分区。
import * as A from "./variant-a";
import * as B from "./variant-b";
import * as C from "./variant-c";
import { useProto, type Variant } from "./store";

const VIEWS: Record<Variant, { ProvidersView: () => React.JSX.Element; ModelsView: () => React.JSX.Element }> = { A, B, C };

/** 各变体侧栏「生成」组里「模型」分区的名字；null 表示该变体不设独立分区。 */
export const MODELS_SECTION_LABEL: Record<Variant, string | null> = { A: "模型目录", B: null, C: "模型" };

export function ProtoProviders() {
  const variant = useProto((s) => s.variant);
  const View = VIEWS[variant].ProvidersView;
  return <View key={variant} />;
}

export function ProtoModels() {
  const variant = useProto((s) => s.variant);
  const View = VIEWS[variant].ModelsView;
  return <View key={variant} />;
}

export { ProtoBar } from "./ProtoBar";
export { useProto } from "./store";

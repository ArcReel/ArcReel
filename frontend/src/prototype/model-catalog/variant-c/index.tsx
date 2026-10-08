// PROTOTYPE — #3091 变体 C「以模型为主轴」：「供应商」只管连接，「模型」按条目列出，每个条目下展开各渠道。
import { ModelsC } from "./ModelsC";
import { ProvidersC } from "./ProvidersC";

export function ProvidersView() {
  return <ProvidersC />;
}
export function ModelsView() {
  return <ModelsC />;
}

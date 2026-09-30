// PROTOTYPE（#2767，一次性代码，勿合入 main）
// 「分集」视图原型，挂在 /app/projects/:name/episodes-prototype。
// 第一轮的 A（集清单为主）/ C（源文条带）两个变体见本分支首个提交；评审选定 B（原文双栏）后只保留 B 迭代。
// 数据全是内存假数据，与真实项目无关；右下角「账本状态」可看每次操作后的账本。
import { StateInspector } from "./shared";
import { useProto } from "./useProto";
import { VariantB } from "./VariantB";

export function EpisodesPrototypePage() {
  const p = useProto();
  return (
    <div className="relative h-full">
      <VariantB p={p} />
      <StateInspector p={p} />
    </div>
  );
}

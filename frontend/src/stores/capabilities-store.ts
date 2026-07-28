import { create } from "zustand";

// ---------------------------------------------------------------------------
// CapabilitiesStore —— 模型能力的失效信号。
//
// 能力生效值（含用户覆盖）由后端 /video-capabilities 解析，前端不缓存它的内容，
// 只缓存「当前这一版还作不作数」。项目级模型 / 视频后端写在项目字段上，改动经项目数据
// 回流即可让能力重新解析；其余会改变解析结果的写入都不落任何项目字段——能力覆盖与自定义
// 供应商本身写在供应商配置上，全局默认后端写在系统设置上——改完没有任何 props 会变，
// 需要一个显式信号把在用的能力查询整体作废。
//
// revision 单调递增，消费方把它纳入请求依赖：+1 即触发重取。
// ---------------------------------------------------------------------------

interface CapabilitiesState {
  /** 能力数据版本号；每次 invalidate 自增。 */
  revision: number;
  /** 作废当前能力数据，触发所有在用查询重取。上述任一类写入成功后调用。 */
  invalidate: () => void;
}

export const useCapabilitiesStore = create<CapabilitiesState>((set) => ({
  revision: 0,
  invalidate: () => set((s) => ({ revision: s.revision + 1 })),
}));


import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { BarChart3, Loader2 } from "lucide-react";
import { API } from "@/api";
import type { UsageStats } from "@/types";

export function UsageStatsSection() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const res = await API.getUsageStats();
      setStats(res.stats);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchStats();
  }, [fetchStats]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
        <span className="ml-2 text-gray-400">{t("加载中…")}</span>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-gray-500">
        <BarChart3 className="h-12 w-12 mb-4 opacity-20" />
        <p>{t("暂无用量数据")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h3 className="text-lg font-semibold text-gray-100">{t("用量统计")}</h3>
        <p className="mt-1 text-sm text-gray-500">{t("系统累计消耗的 Token 与生成时长（仅包含本系统记录的部分）")}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Text Usage */}
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-5">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">{t("文本生成")}</div>
          <div className="space-y-4">
            <div>
              <div className="text-2xl font-semibold text-gray-100">
                {(stats.total_input_tokens / 1000).toFixed(1)}k
              </div>
              <div className="text-xs text-gray-500 mt-1">{t("输入 Token (Prompt)")}</div>
            </div>
            <div>
              <div className="text-2xl font-semibold text-gray-100">
                {(stats.total_output_tokens / 1000).toFixed(1)}k
              </div>
              <div className="text-xs text-gray-500 mt-1">{t("输出 Token (Completion)")}</div>
            </div>
          </div>
        </div>

        {/* Image Usage */}
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-5">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">{t("图像生成")}</div>
          <div>
            <div className="text-2xl font-semibold text-gray-100">{stats.total_images}</div>
            <div className="text-xs text-gray-500 mt-1">{t("生成张数")}</div>
          </div>
        </div>

        {/* Video Usage */}
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-5">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">{t("视频生成")}</div>
          <div>
            <div className="text-2xl font-semibold text-gray-100">
              {Math.round(stats.total_video_seconds)}s
            </div>
            <div className="text-xs text-gray-500 mt-1">{t("生成时长 (秒)")}</div>
          </div>
        </div>
      </div>

      {/* Call Count */}
      <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-5">
        <div className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">{t("调用次数")}</div>
        <div className="text-2xl font-semibold text-gray-100">{stats.total_calls}</div>
        <div className="text-xs text-gray-500 mt-1">{t("API 累计调用次数")}</div>
      </div>
    </div>
  );
}

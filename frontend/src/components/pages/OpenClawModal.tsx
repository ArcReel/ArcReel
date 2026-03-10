/**
 * OpenClaw 集成引导 Modal
 * 提示词区域（可复制，含动态 skill.md URL）、3 步使用说明、"获取 API 令牌"按钮
 */
import { useCallback, useMemo, useState } from "react";
import { Check, Copy, ExternalLink, X } from "lucide-react";
import { useLocation } from "wouter";

// 🦞 SVG lobster icon (inline, no external dep)
function LobsterIcon({ className }: { className?: string }) {
  return (
    <span className={className} aria-hidden="true" role="img">
      🦞
    </span>
  );
}

interface OpenClawModalProps {
  onClose: () => void;
}

// 使用步骤数据（静态，提升到组件外避免每次渲染重建）
const STEPS = [
  {
    step: "01",
    title: "向你的 OpenClaw 发送上述提示词",
    desc: "复制提示词，粘贴给 OpenClaw 发送",
  },
  {
    step: "02",
    title: "OpenClaw 从 Skill 文档学习能力",
    desc: "OpenClaw 会自动读取 ArcReel Skill 文档，获取所有可用工具与 API 的使用方式",
  },
  {
    step: "03",
    title: "OpenClaw 与 ArcReel 交互并创建视频",
    desc: "描述你的创作需求，OpenClaw 将调用 ArcReel 完成项目管理、剧本生成和视频创作",
  },
] as const;

export function OpenClawModal({ onClose }: OpenClawModalProps) {
  const [, navigate] = useLocation();
  const [copied, setCopied] = useState(false);

  // task 7.3：动态适配当前访问地址
  const skillUrl = useMemo(
    () => `${window.location.origin}/skill.md`,
    [],
  );

  const systemPrompt = useMemo(
    () => `学习 ${skillUrl} 然后遵循 skill，了解如何使用 ArcReel 创作视频`,
    [skillUrl],
  );

  const handleCopyPrompt = useCallback(async () => {
    await navigator.clipboard.writeText(systemPrompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [systemPrompt]);

  // task 7.4：跳转 API Key 管理页
  const handleGoToApiKeys = useCallback(() => {
    onClose();
    navigate("/app/settings?tab=api-keys");
  }, [navigate, onClose]);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-8"
      onClick={handleBackdropClick}
      onKeyDown={handleKeyDown}
    >
      <div className="relative flex w-full max-w-lg flex-col rounded-2xl border border-gray-800 bg-gray-900 shadow-2xl shadow-black/60 max-h-[90vh] overflow-y-auto">
        {/* ——— 顶栏 ——— */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-800 bg-gray-900 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <LobsterIcon className="text-xl leading-none" />
            <div>
              <h2 className="text-sm font-semibold text-gray-100">OpenClaw 集成指南</h2>
              <p className="text-xs text-gray-500">将 ArcReel 接入 OpenClaw AI Agent</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* ——— Prompt 区域 ——— */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-gray-400">Prompt</span>
              <button
                type="button"
                onClick={() => void handleCopyPrompt()}
                className="inline-flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-700"
              >
                {copied ? (
                  <>
                    <Check className="h-3 w-3 text-emerald-400" />
                    已复制
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3" />
                    复制
                  </>
                )}
              </button>
            </div>
            <div className="rounded-xl border border-indigo-500/20 bg-gray-950 p-3">
              <pre className="whitespace-pre-wrap font-mono text-xs leading-5 text-indigo-200">
                {systemPrompt}
              </pre>
            </div>
            <p className="mt-1.5 text-xs text-gray-600">
              Skill 文档地址：
              <a
                href={skillUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-1 inline-flex items-center gap-0.5 text-indigo-400 hover:text-indigo-300"
              >
                {skillUrl}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>

          {/* ——— 3 步说明 ——— */}
          <div>
            <div className="mb-3 text-xs font-medium text-gray-400">使用步骤</div>
            <div className="space-y-2">
              {STEPS.map(({ step, title, desc }) => (
                <div
                  key={step}
                  className="flex gap-3 rounded-xl border border-gray-800 bg-gray-950/50 px-3.5 py-3"
                >
                  <div className="flex-shrink-0 font-mono text-xs font-bold text-indigo-500/70 pt-0.5">
                    {step}
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-gray-200">{title}</div>
                    <div className="mt-0.5 text-xs leading-4.5 text-gray-500">{desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ——— 操作按钮 ——— */}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-xl border border-gray-700 bg-gray-800 px-4 py-2.5 text-sm text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-700"
            >
              关闭
            </button>
            <button
              type="button"
              onClick={handleGoToApiKeys}
              className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
            >
              获取 API 令牌
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

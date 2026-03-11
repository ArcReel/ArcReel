import { useCallback, useRef, useState } from "react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { GetSystemConfigResponse, SystemConfigPatch } from "@/types";
import { TabSaveFooter } from "./TabSaveFooter";

// ---------------------------------------------------------------------------
// Draft types
// ---------------------------------------------------------------------------

interface AdvancedDraft {
  geminiImageRpm: number;
  geminiVideoRpm: number;
  geminiRequestGap: number;
  imageMaxWorkers: number;
  videoMaxWorkers: number;
}

function buildDraft(data: GetSystemConfigResponse): AdvancedDraft {
  const cfg = data.config;
  return {
    geminiImageRpm: cfg.rate_limit.image_rpm,
    geminiVideoRpm: cfg.rate_limit.video_rpm,
    geminiRequestGap: cfg.rate_limit.request_gap_seconds,
    imageMaxWorkers: cfg.performance.image_max_workers,
    videoMaxWorkers: cfg.performance.video_max_workers,
  };
}

function deepEqual(a: AdvancedDraft, b: AdvancedDraft): boolean {
  return (
    a.geminiImageRpm === b.geminiImageRpm &&
    a.geminiVideoRpm === b.geminiVideoRpm &&
    a.geminiRequestGap === b.geminiRequestGap &&
    a.imageMaxWorkers === b.imageMaxWorkers &&
    a.videoMaxWorkers === b.videoMaxWorkers
  );
}

function buildPatch(draft: AdvancedDraft, saved: AdvancedDraft): SystemConfigPatch {
  const patch: SystemConfigPatch = {};
  if (draft.geminiImageRpm !== saved.geminiImageRpm) patch.gemini_image_rpm = draft.geminiImageRpm;
  if (draft.geminiVideoRpm !== saved.geminiVideoRpm) patch.gemini_video_rpm = draft.geminiVideoRpm;
  if (draft.geminiRequestGap !== saved.geminiRequestGap)
    patch.gemini_request_gap = draft.geminiRequestGap;
  if (draft.imageMaxWorkers !== saved.imageMaxWorkers)
    patch.image_max_workers = draft.imageMaxWorkers;
  if (draft.videoMaxWorkers !== saved.videoMaxWorkers)
    patch.video_max_workers = draft.videoMaxWorkers;
  return patch;
}

// ---------------------------------------------------------------------------
// Shared style constants
// ---------------------------------------------------------------------------

const cardClassName = "rounded-xl border border-gray-800 bg-gray-950/40 p-4";
const inputClassName =
  "w-full rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-indigo-500/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface AdvancedConfigTabProps {
  data: GetSystemConfigResponse;
  onSaved: (updated: GetSystemConfigResponse) => void;
  onDirtyChange: (dirty: boolean) => void;
  visible: boolean;
}

export function AdvancedConfigTab({
  data,
  onSaved,
  onDirtyChange,
  visible,
}: AdvancedConfigTabProps) {
  const initialDraft = buildDraft(data);
  const [draft, setDraft] = useState<AdvancedDraft>(initialDraft);
  const savedRef = useRef<AdvancedDraft>(initialDraft);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const isDirty = !deepEqual(draft, savedRef.current);

  const prevDirtyRef = useRef(isDirty);
  if (prevDirtyRef.current !== isDirty) {
    prevDirtyRef.current = isDirty;
    onDirtyChange(isDirty);
  }

  const updateDraft = useCallback(
    <K extends keyof AdvancedDraft>(key: K, value: AdvancedDraft[K]) => {
      setDraft((prev) => {
        const next = { ...prev, [key]: value };
        const nextDirty = !deepEqual(next, savedRef.current);
        if (nextDirty !== prevDirtyRef.current) {
          prevDirtyRef.current = nextDirty;
          onDirtyChange(nextDirty);
        }
        return next;
      });
      setSaveError(null);
    },
    [onDirtyChange],
  );

  const handleSave = useCallback(async () => {
    const patch = buildPatch(draft, savedRef.current);
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await API.updateSystemConfig(patch);
      const newDraft = buildDraft(res);
      setDraft(newDraft);
      savedRef.current = newDraft;
      prevDirtyRef.current = false;
      onDirtyChange(false);
      onSaved(res);
      useAppStore.getState().pushToast("高级配置已保存", "success");
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [draft, onDirtyChange, onSaved]);

  const handleReset = useCallback(() => {
    setDraft(savedRef.current);
    prevDirtyRef.current = false;
    onDirtyChange(false);
    setSaveError(null);
  }, [onDirtyChange]);

  return (
    <div className={visible ? undefined : "hidden"}>
      <div className="space-y-5 px-6 pb-0 pt-6">
        {/* Rate limits */}
        <div className="grid gap-4 md:grid-cols-3">
          <label className={cardClassName}>
            <div className="text-sm font-medium text-gray-100">图片 RPM</div>
            <input
              type="number"
              min={0}
              value={draft.geminiImageRpm}
              onChange={(e) => updateDraft("geminiImageRpm", Number(e.target.value))}
              className={`mt-2 ${inputClassName}`}
              name="gemini_image_rpm"
              inputMode="numeric"
              disabled={saving}
            />
            <div className="mt-2 text-xs text-gray-500">0 = 不限制</div>
          </label>

          <label className={cardClassName}>
            <div className="text-sm font-medium text-gray-100">视频 RPM</div>
            <input
              type="number"
              min={0}
              value={draft.geminiVideoRpm}
              onChange={(e) => updateDraft("geminiVideoRpm", Number(e.target.value))}
              className={`mt-2 ${inputClassName}`}
              name="gemini_video_rpm"
              inputMode="numeric"
              disabled={saving}
            />
            <div className="mt-2 text-xs text-gray-500">0 = 不限制</div>
          </label>

          <label className={cardClassName}>
            <div className="text-sm font-medium text-gray-100">请求间隔（秒）</div>
            <input
              type="number"
              min={0}
              step="0.1"
              value={draft.geminiRequestGap}
              onChange={(e) => updateDraft("geminiRequestGap", Number(e.target.value))}
              className={`mt-2 ${inputClassName}`}
              name="gemini_request_gap"
              inputMode="decimal"
              disabled={saving}
            />
            <div className="mt-2 text-xs text-gray-500">两次请求之间的最小间隔</div>
          </label>
        </div>

        {/* Concurrency */}
        <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
          <div className="text-sm font-medium text-gray-100">并发配置</div>
          <div className="mt-3 text-xs text-gray-500">仅影响后续任务，不中断进行中的生成</div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">图片最大并发</div>
              <input
                type="number"
                min={1}
                value={draft.imageMaxWorkers}
                onChange={(e) => updateDraft("imageMaxWorkers", Number(e.target.value))}
                className={`mt-2 ${inputClassName}`}
                name="image_max_workers"
                inputMode="numeric"
                disabled={saving}
              />
            </label>

            <label className={cardClassName}>
              <div className="text-sm font-medium text-gray-100">视频最大并发</div>
              <input
                type="number"
                min={1}
                value={draft.videoMaxWorkers}
                onChange={(e) => updateDraft("videoMaxWorkers", Number(e.target.value))}
                className={`mt-2 ${inputClassName}`}
                name="video_max_workers"
                inputMode="numeric"
                disabled={saving}
              />
            </label>
          </div>
        </div>
      </div>

      <TabSaveFooter
        isDirty={isDirty}
        saving={saving}
        error={saveError}
        onSave={() => void handleSave()}
        onReset={handleReset}
      />
    </div>
  );
}

import { useState, useRef, useEffect, useCallback } from "react";
import { Puzzle } from "lucide-react";
import { API } from "@/api";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { useAppStore } from "@/stores/app-store";
import type { Clue } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ClueCardProps {
  name: string;
  clue: Clue;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Clue>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<void> | void;
  generating?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TYPE_LABELS: Record<string, string> = {
  prop: "道具",
  location: "环境",
};

// ---------------------------------------------------------------------------
// ClueCard
// ---------------------------------------------------------------------------

export function ClueCard({
  name,
  clue,
  projectName,
  onUpdate,
  onGenerate,
  onRestoreVersion,
  generating = false,
}: ClueCardProps) {
  const mediaRevision = useAppStore((s) => s.mediaRevision);
  const [description, setDescription] = useState(clue.description);
  const [imgError, setImgError] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  const isDirty = description !== clue.description;

  useEffect(() => {
    setDescription(clue.description);
  }, [clue.description]);

  useEffect(() => {
    setImgError(false);
  }, [clue.clue_sheet, mediaRevision]);

  // Auto-resize textarea.
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    autoResize();
  }, [description, autoResize]);

  const handleSave = () => {
    onUpdate(name, { description });
  };

  const sheetUrl = clue.clue_sheet
    ? API.getFileUrl(projectName, clue.clue_sheet, mediaRevision)
    : null;

  return (
    <div
      className="bg-gray-900 border border-gray-800 rounded-xl p-5"
      data-workspace-editing={isEditing || isDirty ? "true" : undefined}
      onFocusCapture={() => setIsEditing(true)}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }
        setIsEditing(false);
      }}
    >
      {/* ---- Header: name + badges ---- */}
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-lg font-bold text-white truncate">{name}</h3>

        <span className="shrink-0 rounded-full bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-300">
          {TYPE_LABELS[clue.type] ?? clue.type}
        </span>

        {clue.importance === "major" ? (
          <span className="shrink-0 rounded-full bg-indigo-500/10 px-2 py-0.5 text-xs font-medium text-indigo-400 border border-indigo-500/20">
            重要
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-400">
            次要
          </span>
        )}
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
            线索设计图
          </span>
          <VersionTimeMachine
            projectName={projectName}
            resourceType="clues"
            resourceId={name}
            onRestore={onRestoreVersion}
          />
        </div>
        <AspectFrame ratio="16:9">
          {sheetUrl && !imgError ? (
            <img
              src={sheetUrl}
              alt={`${name} 设计图`}
              className="h-full w-full object-cover"
              onError={() => setImgError(true)}
            />
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-500">
              <Puzzle className="h-10 w-10" />
              <span className="text-xs">点击生成</span>
            </div>
          )}
        </AspectFrame>
      </div>

      {/* ---- Description ---- */}
      <textarea
        ref={textareaRef}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={2}
        className="mb-3 w-full resize-none overflow-hidden bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
        placeholder="输入线索描述..."
      />

      {isDirty && (
        <button
          type="button"
          onClick={handleSave}
          className="mb-3 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
        >
          保存
        </button>
      )}

      {clue.importance === "major" && (
        <GenerateButton
          onClick={() => onGenerate(name)}
          loading={generating}
          label={clue.clue_sheet ? "重新生成设计图" : "生成设计图"}
          className="w-full justify-center"
        />
      )}
    </div>
  );
}

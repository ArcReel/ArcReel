import { useState, useRef, useEffect, useCallback } from "react";
import { User } from "lucide-react";
import { API } from "@/api";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import type { Character } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface CharacterCardProps {
  name: string;
  character: Character;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Character>) => void;
  onGenerate: (name: string) => void;
  generating?: boolean;
}

// ---------------------------------------------------------------------------
// CharacterCard
// ---------------------------------------------------------------------------

export function CharacterCard({
  name,
  character,
  projectName,
  onUpdate,
  onGenerate,
  generating = false,
}: CharacterCardProps) {
  // Local editable state — initialised from props, dirty-tracked.
  const [description, setDescription] = useState(character.description);
  const [voiceStyle, setVoiceStyle] = useState(character.voice_style ?? "");
  const [imgError, setImgError] = useState(false);

  // Track whether the user has made edits.
  const isDirty =
    description !== character.description ||
    voiceStyle !== (character.voice_style ?? "");

  // Sync from props when the external data changes (e.g. after save).
  useEffect(() => {
    setDescription(character.description);
    setVoiceStyle(character.voice_style ?? "");
  }, [character.description, character.voice_style]);

  // Auto-resize textarea ref.
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

  // Handlers.
  const handleSave = () => {
    const updates: Partial<Character> = { description };
    if (voiceStyle !== (character.voice_style ?? "")) {
      updates.voice_style = voiceStyle;
    }
    onUpdate(name, updates);
  };

  const sheetUrl = character.character_sheet
    ? API.getFileUrl(projectName, character.character_sheet)
    : null;

  const refImgUrl = character.reference_image
    ? API.getFileUrl(projectName, character.reference_image)
    : null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      {/* ---- Header ---- */}
      <h3 className="text-lg font-bold text-white truncate mb-4">{name}</h3>

      {/* ---- Image area ---- */}
      <div className="mb-4">
        <AspectFrame ratio="3:4">
          <ImageFlipReveal
            src={sheetUrl && !imgError ? sheetUrl : null}
            alt={`${name} 设计图`}
            className="h-full w-full object-cover"
            onError={() => setImgError(true)}
            fallback={
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-gray-500">
                <User className="h-10 w-10" />
                <span className="text-xs">点击生成</span>
              </div>
            }
          />
        </AspectFrame>

        {/* Reference image (smaller) */}
        {refImgUrl && (
          <div className="mt-2 overflow-hidden rounded-lg bg-gray-800">
            <img
              src={refImgUrl}
              alt={`${name} 参考图`}
              className="h-24 w-full object-cover"
            />
          </div>
        )}
      </div>

      {/* ---- Form area ---- */}
      <label className="text-xs font-medium text-gray-400">描述</label>
      <textarea
        ref={textareaRef}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onInput={autoResize}
        rows={3}
        className="mt-1 w-full resize-none overflow-hidden bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
        placeholder="输入角色描述..."
      />

      <label className="mt-3 block text-xs font-medium text-gray-400">声音风格</label>
      <input
        type="text"
        value={voiceStyle}
        onChange={(e) => setVoiceStyle(e.target.value)}
        className="mt-1 w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
        placeholder="例如：温柔但有威严"
      />

      {isDirty && (
        <button
          type="button"
          onClick={handleSave}
          className="mt-3 rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
        >
          保存
        </button>
      )}

      <div className="mt-3">
        <GenerateButton
          onClick={() => onGenerate(name)}
          loading={generating}
          label="生成设计图"
          className="w-full justify-center"
        />
      </div>
    </div>
  );
}

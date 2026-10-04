import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import type { Character, CharacterVoiceBinding } from "@/types";
import { AssetGallery } from "./AssetGallery";
import { CharacterCard } from "./CharacterCard";

interface Props {
  projectName: string;
  characters: Record<string, Character>;
  onSaveCharacter: (name: string, payload: { description: string; voiceStyle: string; referenceFile?: File | null }) => Promise<void>;
  onGenerateCharacter: (name: string) => void;
  onAddCharacter: (name: string, description: string, voiceStyle: string, referenceFile?: File | null) => Promise<void>;
  onRestoreCharacterVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingCharacterNames?: Set<string>;
  /** 项目的角色声音绑定方式；决定角色详情是否把参考音频区折叠为可选。 */
  voiceBinding?: CharacterVoiceBinding;
  /** 只读展示（引导演示项目）：不渲染新增、入库、生成、上传入口。 */
  readOnly?: boolean;
}

const libraryPreview = (character: Character): LibraryImportPreview => ({
  description: character.description,
  voiceStyle: character.voice_style ?? "",
  hasReferenceAudio: Boolean(character.reference_audio),
  sheetPath: character.character_sheet,
  derivativeCount: Object.keys(character.derivatives ?? {}).length,
});

export function CharactersPage({
  projectName,
  characters,
  onSaveCharacter,
  onGenerateCharacter,
  onAddCharacter,
  onRestoreCharacterVersion,
  onRefreshProject,
  generatingCharacterNames,
  voiceBinding,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [adding, setAdding] = useState(false);

  return (
    <>
      <AssetGallery
        projectName={projectName}
        assetType="character"
        title={t("characters")}
        assets={characters}
        generatingNames={generatingCharacterNames}
        readOnly={readOnly}
        onGenerate={onGenerateCharacter}
        onRestoreVersion={onRestoreCharacterVersion}
        onReload={onRefreshProject}
        onAdd={() => setAdding(true)}
        libraryPreview={libraryPreview}
        renderEditor={(name, { sheetStatus, generating }) =>
          characters[name] ? (
            <CharacterCard
              name={name}
              character={characters[name]}
              projectName={projectName}
              onSave={onSaveCharacter}
              onGenerate={onGenerateCharacter}
              onReload={onRefreshProject}
              generating={generating}
              voiceBinding={voiceBinding}
              sheetStatus={sheetStatus}
              readOnly={readOnly}
            />
          ) : null
        }
      />

      {adding && !readOnly && (
        <AssetFormModal
          type="character"
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description, voice_style, image }) => {
            await onAddCharacter(name, description, voice_style, image ?? null);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}

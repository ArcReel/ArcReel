import { useTranslation } from "react-i18next";
import type { LibraryImportPreview } from "@/components/assets/AddToLibraryDialog";
import type { Character } from "@/types";
import { AssetGallery } from "./AssetGallery";

interface Props {
  projectName: string;
  characters: Record<string, Character>;
  onGenerateCharacter: (name: string) => void;
  onRestoreCharacterVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<unknown> | void;
  generatingCharacterNames?: Set<string>;
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
  onGenerateCharacter,
  onRestoreCharacterVersion,
  onRefreshProject,
  generatingCharacterNames,
  readOnly = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  return (
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
      libraryPreview={libraryPreview}
    />
  );
}

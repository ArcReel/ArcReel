import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GalleryToolbar } from "./GalleryToolbar";
import { CharacterCard } from "./CharacterCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Character } from "@/types";

interface Props {
  projectName: string;
  characters: Record<string, Character>;
  onSaveCharacter: (name: string, payload: { description: string; voiceStyle: string; referenceFile?: File | null }) => Promise<void>;
  onGenerateCharacter: (name: string) => void;
  onAddCharacter: (name: string, description: string, voiceStyle: string, referenceFile?: File | null) => Promise<void>;
  onRestoreCharacterVersion?: () => Promise<void> | void;
  generatingCharacterNames?: Set<string>;
}

export function CharactersPage({ projectName, characters, onSaveCharacter, onGenerateCharacter, onAddCharacter, onRestoreCharacterVersion, generatingCharacterNames }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  const entries = Object.entries(characters);

  const handleImport = async (ids: string[]) => {
    try {
      await API.applyAssetsToProject({
        asset_ids: ids,
        target_project: projectName,
        conflict_policy: "skip",
      });
      useAppStore.getState().pushToast(t("assets:import_count", { count: ids.length }), "success");
      window.location.reload();
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex flex-col">
      <GalleryToolbar
        title={t("dashboard:characters")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="p-4">
        {entries.length === 0 ? (
          <div className="py-16 text-center text-gray-500 text-sm">
            {t("dashboard:no_characters_hint_clickable")}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {entries.map(([name, char]) => (
              <CharacterCard key={name} name={name} character={char} projectName={projectName}
                onSave={onSaveCharacter}
                onGenerate={onGenerateCharacter}
                onRestoreVersion={onRestoreCharacterVersion}
                generating={generatingCharacterNames?.has(name)}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="character"
          mode="create"
          scope="project"
          targetProject={projectName}
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description, voice_style }) => {
            await onAddCharacter(name, description, voice_style);
            setAdding(false);
          }}
        />
      )}

      {picking && (
        <AssetPickerModal
          type="character"
          existingNames={new Set(Object.keys(characters))}
          onClose={() => setPicking(false)}
          onImport={(ids) => { void handleImport(ids); }}
        />
      )}
    </div>
  );
}

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { User } from "lucide-react";
import { GalleryToolbar } from "./GalleryToolbar";
import { CharacterCard } from "./CharacterCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { Character } from "@/types";
import { GalleryEmptyState } from "./GalleryEmptyState";

interface Props {
  projectName: string;
  characters: Record<string, Character>;
  onSaveCharacter: (name: string, payload: { description: string; voiceStyle: string; referenceFile?: File | null }) => Promise<void>;
  onGenerateCharacter: (name: string) => void;
  onAddCharacter: (name: string, description: string, voiceStyle: string, referenceFile?: File | null) => Promise<void>;
  onRestoreCharacterVersion?: () => Promise<void> | void;
  onRefreshProject?: () => Promise<void> | void;
  generatingCharacterNames?: Set<string>;
}

export function CharactersPage({ projectName, characters, onSaveCharacter, onGenerateCharacter, onAddCharacter, onRestoreCharacterVersion, onRefreshProject, generatingCharacterNames }: Props) {
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
      await onRefreshProject?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <GalleryToolbar
        title={t("dashboard:characters")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="px-5 py-5">
        {entries.length === 0 ? (
          <GalleryEmptyState
            icon={<User className="h-6 w-6" />}
            label={t("dashboard:characters")}
            hint={t("dashboard:no_characters_hint_clickable")}
            onClick={() => setAdding(true)}
          />
        ) : (
          <div className="grid justify-evenly gap-4 [grid-template-columns:repeat(auto-fill,320px)]">
            {entries.map(([name, char]) => (
              <CharacterCard key={name} name={name} character={char} projectName={projectName}
                onSave={onSaveCharacter}
                onGenerate={onGenerateCharacter}
                onRestoreVersion={onRestoreCharacterVersion}
                onReload={onRefreshProject}
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
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description, voice_style, image }) => {
            await onAddCharacter(name, description, voice_style, image ?? null);
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

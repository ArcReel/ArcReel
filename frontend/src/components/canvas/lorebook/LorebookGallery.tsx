import { useState } from "react";
import { User, Puzzle, Plus } from "lucide-react";
import { CharacterCard } from "./CharacterCard";
import { ClueCard } from "./ClueCard";
import type { Character, Clue } from "@/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface LorebookGalleryProps {
  projectName: string;
  characters: Record<string, Character>;
  clues: Record<string, Clue>;
  onUpdateCharacter: (name: string, updates: Partial<Character>) => void;
  onUpdateClue: (name: string, updates: Partial<Clue>) => void;
  onGenerateCharacter: (name: string) => void;
  onGenerateClue: (name: string) => void;
  /** Set of names currently being generated (for loading state). */
  generatingNames?: Set<string>;
  /** Called when the user clicks "添加角色". */
  onAddCharacter?: () => void;
  /** Called when the user clicks "添加线索". */
  onAddClue?: () => void;
}

// ---------------------------------------------------------------------------
// Tab type
// ---------------------------------------------------------------------------

type Tab = "characters" | "clues";

// ---------------------------------------------------------------------------
// LorebookGallery
// ---------------------------------------------------------------------------

export function LorebookGallery({
  projectName,
  characters,
  clues,
  onUpdateCharacter,
  onUpdateClue,
  onGenerateCharacter,
  onGenerateClue,
  generatingNames,
  onAddCharacter,
  onAddClue,
}: LorebookGalleryProps) {
  const [activeTab, setActiveTab] = useState<Tab>("characters");

  const charEntries = Object.entries(characters);
  const clueEntries = Object.entries(clues);
  const charCount = charEntries.length;
  const clueCount = clueEntries.length;

  const isGenerating = (name: string) => generatingNames?.has(name) ?? false;

  return (
    <div className="flex flex-col gap-4">
      {/* ---- Tab bar ---- */}
      <div className="flex border-b border-gray-800">
        <TabButton
          active={activeTab === "characters"}
          onClick={() => setActiveTab("characters")}
        >
          角色 ({charCount})
        </TabButton>
        <TabButton
          active={activeTab === "clues"}
          onClick={() => setActiveTab("clues")}
        >
          线索 ({clueCount})
        </TabButton>
      </div>

      {/* ---- Characters tab ---- */}
      {activeTab === "characters" && (
        <>
          {charCount === 0 ? (
            <EmptyState
              icon={<User className="h-12 w-12 text-gray-600" />}
              message="暂无角色，点击下方按钮添加"
            />
          ) : (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {charEntries.map(([charName, character]) => (
                <CharacterCard
                  key={charName}
                  name={charName}
                  character={character}
                  projectName={projectName}
                  onUpdate={onUpdateCharacter}
                  onGenerate={onGenerateCharacter}
                  generating={isGenerating(charName)}
                />
              ))}
            </div>
          )}

          {onAddCharacter && (
            <AddButton onClick={onAddCharacter}>添加角色</AddButton>
          )}
        </>
      )}

      {/* ---- Clues tab ---- */}
      {activeTab === "clues" && (
        <>
          {clueCount === 0 ? (
            <EmptyState
              icon={<Puzzle className="h-12 w-12 text-gray-600" />}
              message="暂无线索，点击下方按钮添加"
            />
          ) : (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {clueEntries.map(([clueName, clue]) => (
                <ClueCard
                  key={clueName}
                  name={clueName}
                  clue={clue}
                  projectName={projectName}
                  onUpdate={onUpdateClue}
                  onGenerate={onGenerateClue}
                  generating={isGenerating(clueName)}
                />
              ))}
            </div>
          )}

          {onAddClue && <AddButton onClick={onAddClue}>添加线索</AddButton>}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internal sub-components
// ---------------------------------------------------------------------------

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium transition-colors ${
        active
          ? "border-b-2 border-indigo-500 text-white"
          : "text-gray-400 hover:text-gray-200"
      }`}
    >
      {children}
    </button>
  );
}

function EmptyState({
  icon,
  message,
}: {
  icon: React.ReactNode;
  message: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-gray-500">
      {icon}
      <p className="text-sm">{message}</p>
    </div>
  );
}

function AddButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mx-auto flex items-center gap-1.5 rounded-lg border border-gray-700 px-4 py-2 text-sm font-medium text-gray-400 hover:border-gray-500 hover:text-gray-200 transition-colors"
    >
      <Plus className="h-4 w-4" />
      {children}
    </button>
  );
}

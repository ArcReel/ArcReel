import { useState } from "react";
import { ArrowUpDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

interface CharacterMainToReferenceButtonProps {
  projectName: string;
  characterName: string;
  onReload?: () => void | Promise<unknown>;
  busy?: boolean;
}

/** Move the card's current main image into the reference slot, leaving main empty. */
export function CharacterMainToReferenceButton({
  projectName,
  characterName,
  onReload,
  busy = false,
}: CharacterMainToReferenceButtonProps) {
  const { t } = useTranslation("assets");
  const [submitting, setSubmitting] = useState(false);
  const label = t("switch_to_reference_image");

  const move = async () => {
    setSubmitting(true);
    try {
      await API.moveCharacterMainToReference(projectName, characterName);
      await onReload?.();
    } catch (error) {
      useAppStore.getState().pushToast(errMsg(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <button
      type="button"
      disabled={busy || submitting}
      onClick={() => { void move(); }}
      aria-label={label}
      title={label}
      className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-40"
    >
      <ArrowUpDown className="h-3.5 w-3.5" />
    </button>
  );
}

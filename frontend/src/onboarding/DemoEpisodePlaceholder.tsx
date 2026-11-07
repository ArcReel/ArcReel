import { useTranslation } from "react-i18next";
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import { DEMO_SCRIPTED_EPISODE } from "./demo-project";

/**
 * Demo-project episodes without scripts.
 *
 * Only episode 1 is complete; other demo episodes contain titles only. Real projects enter source-slice review here, but the demo
 * has no source to slice. Explain and point back to episode 1 rather than leaving users to interpret an empty canvas.
 */
export function DemoEpisodePlaceholder() {
  const { t } = useTranslation("onboarding");

  return (
    <Empty className="min-h-0 flex-1">
      <EmptyHeader>
        {/* The demo ledger sorts by episode ID ascending; the ID is the broadcast position. */}
        <EmptyDescription>{t("demo_episode_placeholder", { position: DEMO_SCRIPTED_EPISODE })}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}

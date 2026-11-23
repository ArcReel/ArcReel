import { useTranslation } from "react-i18next";
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import { DEMO_SCRIPTED_EPISODE } from "./demo-project";

export function DemoEpisodePlaceholder() {
  const { t } = useTranslation("onboarding");

  return (
    <Empty className="min-h-0 flex-1">
      <EmptyHeader>
        <EmptyDescription>{t("demo_episode_placeholder", { position: DEMO_SCRIPTED_EPISODE })}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}

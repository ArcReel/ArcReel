import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Typewriter } from "./Typewriter";
import { greetingKeyOf } from "./lobby-projects";

interface LobbyGreetingProps {
  projectCount: number;
  inProgressCount: number;
  episodesCompleted: number;
  episodesInProduction: number;
}

/**
 * Greeting area: greeting plus purple status, then completion details in secondary color. Play typing once per session, or show all text with reduced motion.
 * Without projects, the status points toward the first work and omits completion details.
 */
export function LobbyGreeting({
  projectCount,
  inProgressCount,
  episodesCompleted,
  episodesInProduction,
}: LobbyGreetingProps) {
  const { t } = useTranslation("dashboard");
  const greetingKey = useMemo(() => greetingKeyOf(), []);

  let status: string;
  if (projectCount === 0) status = t("lobby_hero_subtitle_empty");
  else if (inProgressCount > 0) status = t("lobby_hero_subtitle_active", { count: inProgressCount });
  else status = t("lobby_hero_subtitle_all_done");

  return (
    <div className="flex flex-col gap-1.5 pb-5">
      <h1 className="font-editorial text-3xl leading-tight font-normal">
        <Typewriter
          once="lobby-hero"
          segments={[{ text: t(greetingKey) }, { text: status, className: "text-primary" }]}
        />
      </h1>
      {projectCount > 0 && (
        <p className="text-sm text-muted-foreground">
          {t("lobby_hero_summary", { completed: episodesCompleted, inProduction: episodesInProduction })}
        </p>
      )}
    </div>
  );
}

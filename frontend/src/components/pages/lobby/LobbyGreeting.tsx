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
 * 问候区：问候句加紫色状态句，下一行次要色写完成情况。打字机每个会话只播放一次，减少动态效果时直接显示全文。
 * 没有项目时状态句引向第一部作品，不写完成情况。
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

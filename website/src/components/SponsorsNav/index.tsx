import type { ReactNode } from "react";
import { translate } from "@docusaurus/Translate";
import { BECOME_SPONSOR_URL, getSponsors, type Sponsor } from "../../data/sponsors";
import styles from "./styles.module.css";

// 导航栏赞助位：头像一排，悬停或聚焦弹出赞助商卡片。

function useLabels() {
  return {
    sponsor: translate({ id: "sponsors.label", message: "赞助商", description: "Label of the navbar sponsors area" }),
    become: translate({
      id: "sponsors.become",
      message: "成为赞助商",
      description: "Link in the sponsor card inviting new sponsors",
    }),
    visit: translate({
      id: "sponsors.visit",
      message: "访问官网 →",
      description: "Link in the sponsor card to the sponsor website",
    }),
  };
}

function SponsorCard({ sponsor }: { sponsor: Sponsor }): ReactNode {
  const labels = useLabels();
  const { Logo } = sponsor;
  return (
    <div className={styles.card}>
      <div className={styles.cardBody}>
        <Logo className={styles.cardLogo} />
        <div className={styles.cardInfo}>
          <a href={sponsor.url} target="_blank" rel="noopener noreferrer sponsored" className={styles.cardName}>
            {sponsor.name}
          </a>
          <span className={styles.cardDesc}>{sponsor.description}</span>
        </div>
      </div>
      <div className={styles.cardFooter}>
        <span className={styles.sponsorLabel}>
          <span className={styles.heart} aria-hidden="true">
            ♥
          </span>
          {labels.sponsor}
        </span>
        <span>
          <a href={BECOME_SPONSOR_URL} className={styles.becomeLink}>
            {labels.become}
          </a>
          <a href={sponsor.url} target="_blank" rel="noopener noreferrer sponsored" className={styles.visitLink}>
            {labels.visit}
          </a>
        </span>
      </div>
    </div>
  );
}

export default function SponsorsNav({
  mobile = false,
  onClick,
}: {
  mobile?: boolean;
  // 移动端抽屉传入的关闭回调，与内置 navbar 项一样转给链接。
  onClick?: () => void;
}): ReactNode {
  const labels = useLabels();
  const sponsors = getSponsors();

  // 移动端抽屉放不下浮层，退化为每家一行链接。
  if (mobile) {
    return sponsors.map((sponsor) => (
      <li key={sponsor.name} className="menu__list-item">
        <a
          className="menu__link"
          href={sponsor.url}
          target="_blank"
          rel="noopener noreferrer sponsored"
          onClick={onClick}
        >
          <span className={styles.heart} aria-hidden="true">
            ♥
          </span>
          &nbsp;{labels.sponsor} · {sponsor.name}
        </a>
      </li>
    ));
  }

  return (
    <div className={`navbar__item ${styles.wrap}`} role="group" aria-label={labels.sponsor}>
      {sponsors.map((sponsor) => (
        <div key={sponsor.name} className={styles.item}>
          <a
            href={sponsor.url}
            target="_blank"
            rel="noopener noreferrer sponsored"
            className={styles.avatarLink}
            aria-label={`${labels.sponsor}: ${sponsor.name}`}
          >
            <sponsor.Logo className={styles.avatar} />
          </a>
          <div className={styles.popover}>
            <SponsorCard sponsor={sponsor} />
          </div>
        </div>
      ))}
    </div>
  );
}

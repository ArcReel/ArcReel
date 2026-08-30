# ArcReel

> 中文说明见[项目文档](https://docs.arc-reel.com/)与 [GitHub 仓库](https://github.com/ArcReel/ArcReel)。

An open-source, self-hosted AI video production workspace. Turn novels, finished screenplays, or product assets into character-consistent, controllable, cost-trackable short videos that remain editable.

- **Source**: https://github.com/ArcReel/ArcReel
- **Documentation**: https://docs.arc-reel.com/en/
- **License**: AGPL-3.0

## Supported tags

| Tag | Meaning |
|---|---|
| `latest` | The most recent stable release |
| `X.Y.Z` | An exact release, e.g. `0.28.0` |
| `X.Y` | The latest patch of that minor line |
| `X` | The latest release of that major line |

Images are published for `linux/amd64` and `linux/arm64`, with SBOM and provenance attestations attached.

## Quick start

Compose is the supported way to run ArcReel — the application needs a database, a data volume, and specific kernel capabilities for its sandbox:

```bash
git clone https://github.com/ArcReel/ArcReel.git
cd ArcReel/deploy

cp .env.example .env
docker compose up -d
```

Open <http://localhost:1241>. The default username is `admin`. If `AUTH_PASSWORD` is empty, ArcReel generates a password on first startup and writes it back to `deploy/.env`.

> Default Compose publishes port `1241` on all host interfaces. Do not expose ArcReel directly to the public Internet; before enabling remote access, configure authentication and use HTTPS, a VPN, or a secure tunnel.

For the complete first-run walkthrough see [Getting Started](https://docs.arc-reel.com/en/guide/getting-started); for production deployment, upgrades, backups, and reverse proxies see [Deployment and Operations](https://docs.arc-reel.com/en/ops/deployment).

## What you get

- **One production workflow**: turn novels, finished screenplays, or product assets into characters, scenes, props, storyboards, video clips, and final videos step by step.
- **Visual continuity with human control**: reuse reference assets across shots, review key stages, regenerate individual assets, and roll back to earlier versions.
- **Manageable models and costs**: configure text, image, video, and TTS capabilities in one place, then review estimated costs and actual usage.
- **Editable delivery**: render final videos directly or export Jianying drafts to refine subtitles, voice-over, pacing, and transitions.

## Support

Reproducible bugs and focused feature requests are welcome in [GitHub Issues](https://github.com/ArcReel/ArcReel/issues).

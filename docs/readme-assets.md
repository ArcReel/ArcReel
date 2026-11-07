# README demonstration-asset standards

This document is for maintainers, defining screenshots, animations, and finished examples for the README homepage, rather than ordinary users.

The README currently uses:

- `docs/assets/screenshots/hero.webp` (hero screenshot)
- `docs/assets/screenshots/{video-units,props,scenes,ad-mode}.webp` (interface overview)
- `docs/assets/feishu-qr.png`
- `docs/assets/sponsors/`

Product-oriented open-source homepages convert primarily through real results. ArcReel particularly needs both the workspace and finished videos, not only settings/forms or feature lists.

## 1. Recommended asset directories

```text
docs/assets/
├── screenshots/
│   ├── hero.webp
│   ├── video-units.webp
│   ├── props.webp
│   ├── scenes.webp
│   └── ad-mode.webp
├── sponsors/
├── demo-workflow.webp
├── showcase-novel-cover.jpg
├── showcase-narration-cover.jpg
├── showcase-ad-cover.jpg
├── showcase/
│   ├── novel-input.png
│   ├── novel-character-sheet.png
│   ├── novel-storyboard.png
│   ├── novel-final.mp4
│   ├── narration-final.mp4
│   └── ad-final.mp4
└── feishu-qr.png
```

GitHub READMEs are unsuitable for directly hosting huge videos. Finished videos may:

- Be hosted in GitHub Releases;
- Use media links generated through GitHub Issue/PR uploads;
- Live on official websites or stable object storage;
- Be linked through cover images in READMEs.

## 2. Hero screenshot

### Goal

Without reading text, users should understand:

- This is a complete workspace;
- An Agent sits on the right;
- Video production occupies the center;
- Characters, storyboards, or task states are visible.

### Suggested specifications

- Ratio: 16:9
- Recommended dimensions: 1600 × 900
- Minimum width: 1200
- Format: PNG or high-quality WebP
- File size: preferably below 1.5 MB

### Screenshot content

Prefer projects with actual data:

- Project/asset navigation on the left;
- Characters, storyboards, or timelines in the center;
- Agent on the right;
- Visible task or cost information somewhere.

Do not show:

- Empty projects;
- Large settings forms;
- Real API keys;
- Customer names;
- Unredacted complete novels;
- Irrelevant browser areas.

## 3. Workflow animation

### Goal

Show within 15–30 seconds:

1. Open an existing project;
2. Ask the Agent to continue to the next stage;
3. View generation tasks;
4. Open storyboard/video results;
5. Show version history or Jianying export.

### Suggested specifications

- Prefer animated WebP;
- Width: 1200–1600;
- Frame rate: 12–20 FPS;
- Keep below 8 MB;
- Do not record long waits;
- Edit out provider queues.

Emphasize reviewable workflows, not button clicks alone.

## 4. Three finished examples

Prepare at least three README examples representing different scenarios.

### 4.1 Novel to AI comic drama

Record:

- Input character count;
- Content mode;
- Storyboard count;
- Character-asset count;
- Total generation time;
- Video duration;
- Providers used;
- Actual cost range;
- Final-video link.

### 4.2 Narration/commentary short video

Record:

- Narration character count;
- Storyboard count;
- TTS voice;
- Whether Jianying drafts were exported;
- Final aspect ratio;
- Actual cost range;
- Final-video link.

### 4.3 Product advertisement

Record:

- Product reference-image count;
- Storyboard count;
- Video duration;
- Whether reference-to-video was used;
- Product-authenticity review explanation;
- Actual cost range;
- Final-video link.

## 5. Example copy template

```markdown
### 案例名称

[![案例封面](docs/assets/showcase/example-cover.jpg)](视频链接)

- **输入**：约 2,000 字小说片段
- **创作类型**：剧情演绎
- **生成方式**：分镜图生视频
- **输出**：8 个分镜 / 48 秒 / 9:16
- **供应商**：文本 A、图像 B、视频 C
- **生成时间**：约 XX 分钟
- **API 费用**：约 XX，价格和排队时间可能变化
- **可编辑输出**：最终视频 + 剪映草稿
```

Costs must:

- Identify currency;
- State they apply only to this example;
- State provider prices may change;
- Never add currencies without explanation;
- Never promise identical costs for all users.

## 6. Privacy and security checks

Before publishing screenshots/videos, check:

- [ ] No API keys;
- [ ] No tokens;
- [ ] No real administrator passwords;
- [ ] No internal Base URLs;
- [ ] No customer names;
- [ ] No unauthorized novel content;
- [ ] No personal file paths;
- [ ] No browser-account avatars/notifications;
- [ ] Product/brand material is authorized for display;
- [ ] Example music/fonts/images can legally be displayed.

## 7. README integration

After preparing assets, consider adding below the hero image:

```markdown
<p align="center">
  <a href="完整演示视频链接">观看 90 秒完整演示</a>
  ·
  <a href="#效果案例">查看成品案例</a>
</p>
```

And after "适合的创作场景":

```markdown
## 效果案例

| 小说转 AI 漫剧 | 旁白/解说短视频 | 商品广告 |
|---|---|---|
| 封面与视频链接 | 封面与视频链接 | 封面与视频链接 |
```

Without real publicly shareable examples, never present placeholder videos, invented costs, or model-promotional materials as ArcReel output.

# CapCut Draft Export Guide

ArcReel supports exporting a single episode's generated video clips as a CapCut (JianYing) draft file. After extracting the archive to your local CapCut drafts folder, you can open it directly in CapCut for secondary editing (subtitles, transitions, effects, etc.).

## Limitations

- Export is per-episode; multi-episode batch export is not supported
- Audio tracks (BGM, voiceover) are not exported
- Drama mode does not export subtitles (multi-character dialogue structure is complex — not in MVP scope)
- CapCut international version is not supported; only the Chinese version (JianYing) is supported

## Steps

### 1. Confirm Video Clips Are Ready

Before exporting, make sure all video clips for the episode have been generated. Scenes with no video will be skipped automatically (no clips in the draft).

### 2. Find Your CapCut Drafts Directory

The CapCut drafts directory varies by OS and version. Common paths:

| OS | Default Path |
|----|-------------|
| macOS | `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft` |
| Windows | `C:\Users\<username>\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft` |

You can also open CapCut, create a blank draft, then check the newly created folder to confirm the correct path.

### 3. Export the Draft

In the ArcReel Web UI:

1. Open the project and navigate to the target episode
2. Click the **"Export as CapCut Draft"** button
3. In the popup dialog, enter your local CapCut drafts directory path (e.g. `/Users/yourname/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`)
4. Click **Confirm** — the browser will automatically download a ZIP file

### 4. Extract to the CapCut Drafts Folder

Extract the downloaded ZIP directly into the CapCut drafts directory. The result should look like:

```
com.lveditor.draft/
  ArcReel_<project>_ep<N>_<timestamp>/
    draft_content.json
    draft_meta_info.json
    video_001.mp4
    video_002.mp4
    ...
```

### 5. Open in CapCut

Restart CapCut (or refresh the draft list). You should see the new draft. Open it and the video clips will be arranged in sequence on the timeline.

## Troubleshooting

### Draft not showing in CapCut

- Confirm the ZIP was extracted directly into the drafts directory (not into a subdirectory of the drafts directory)
- Confirm CapCut has been restarted or the draft list has been refreshed
- Check that `draft_meta_info.json` exists in the extracted folder

### Video clips not playing

- Confirm the video files exist in the same directory as `draft_content.json`
- Confirm CapCut can access the drafts directory (check permissions on macOS)

### Export button is grayed out / error occurs

- Make sure at least one video clip for the episode has been generated
- Confirm the CapCut drafts directory path entered does not contain special characters or control characters

## Technical Details

The export uses [pyJianYingDraft](https://github.com/leiurayer/pyJianYingDraft) to generate a `draft_content.json` compatible with CapCut.

For narration mode, subtitles are exported on a subtitle track based on the `novel_text` field of each segment. For drama mode, subtitles are not exported.

The draft path must be the absolute path to the CapCut drafts **root directory**; the system will automatically create a subdirectory named `ArcReel_<project>_ep<N>_<timestamp>` inside it.

---
id: jianying-export
title: Jianying Draft Export
sidebar_position: 7
---

# Jianying Draft Export {#jianying-export}

Export an episode's edit timeline as a Jianying draft, then open it in Jianying Desktop to keep refining the pacing, subtitle styles, transitions, voice-over, and more. Jianying drafts and final cuts are built from the same edit timeline, so trims, clip order, source volume, holds, and transition types match the final cut.

ArcReel currently exports the draft format for the mainland-China edition of Jianying Desktop. CapCut is Jianying's international counterpart, but it is a separate product; ArcReel has not verified draft compatibility with CapCut.

## Prerequisites {#prerequisites}

- The episode has at least one generated video
- **Jianying Desktop** (5.x or 6+) is installed locally

## Steps {#steps}

### 1. Locate the Jianying draft directory {#locate-draft-directory}

Before downloading, you need to know where local Jianying drafts are stored.

**macOS:**
```
/Users/<username>/Movies/JianyingPro/User Data/Projects/com.lveditor.draft
```

**Windows:**
```
C:\Users\<username>\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft
```

> **Tip**: You can find it in Jianying under Settings → Draft location. If you changed the default location, use the actual draft directory.

### 2. Open the episode's edit view {#open-edit-view}

1. Open the target project and go to the episode you want to export
2. Switch to the **Edit** view at the top of the episode page

If the episode has no edit timeline yet, the edit view offers two entries:

- **Hand to Agent to edit**: fills an editing request into the chat input so the Agent can make a cut
- **New edit timeline**: creates one directly from the script, using every video unit in full with hard cuts between clips

Both entries require at least one generated video in the episode. Rendering never creates an edit timeline automatically.

> **Tip**: **Export project** in the top bar only downloads the whole project as an archive. Final cuts and Jianying drafts are exported from each episode's edit view.

### 3. Export the Jianying draft {#start-export}

1. Select the edit timeline in the Edit view and click Render.
2. In the dialog, select Jianying Draft. Projects using TTS narration can also select With Narration or Without Narration. ArcReel saves the two versions separately.
3. Download an up-to-date draft directly. If the draft is missing or out of date, choose Export Jianying Draft or Export Again and wait for it to finish. Download the old draft first if you need to keep it.

If Render is disabled, see [Blocked Exports](#render-blocked).

### 4. Enter the download parameters and download {#export-parameters}

| Parameter | Description |
|------|------|
| **Draft Directory Path** | Enter the Jianying draft path from step 1 (it is remembered automatically after the first entry) |
| **Jianying Version** | Select **Jianying 6.0 and above** or **Jianying 5.x** to match the locally installed Jianying version |

Click **Download**. The browser downloads a ZIP file. The draft directory and Jianying version are applied only when you download, so on another computer you can download the same draft with that computer's settings.

### 5. Extract into the draft directory {#unzip-to-draft-directory}

Extract the downloaded ZIP file into the Jianying draft directory entered above. The extracted structure is as follows:

```
com.lveditor.draft/
├── ... (other existing drafts)
└── {two-digit airing position}_{episode title}_{edit timeline name}/   ← the extracted folder
    ├── draft_info.json        (Jianying 6+) or draft_content.json (5.x)
    ├── draft_meta_info.json
    └── assets/
        └── ...                 (video, audio, and hold frames)
```

The folder name starts with the episode's position in the airing order (two digits, such as `01`) and the episode title; an episode without a title uses `第 N 集` instead. The edit timeline name follows. The version with narration adds a `_带旁白` suffix.

### 6. Open in Jianying {#open-in-jianying}

1. Open (or restart) Jianying Desktop
2. Find the newly added draft in the "Drafts" list
3. Double-click it to see all clips on the timeline

## Exported Content {#export-contents}

The Jianying draft is generated from a revision of the edit timeline and follows the same rules as the final cut:

- **Video track**: clips appear in the edit timeline's order, keeping trims, source volume, and holds; each clip uses the video version currently selected for its video unit
- **Subtitle track**: subtitles are generated from narration and dialogue, and their style, position, and timing remain adjustable in Jianying
- **Narration track**: appears only in the version with narration. TTS narration projects export the version with narration by default and can switch to the version without narration; other projects export only the version without narration
- **BGM track**: appears when the edit timeline has BGM clips. Each BGM clip keeps its start, trimmed section, and fades; its volume is the loudness-matching gain multiplied by the clip volume, and remains adjustable in Jianying

Narration starts at its carrier clip and plays for the actual length of its audio. A narration longer than its clip keeps its full length; it is not shortened, and later narrations are not shifted. Only the part that runs past the end of the edit timeline is cut off. When narrations or subtitles overlap in time, the draft adds tracks such as "旁白 2" (narration 2) and "字幕 2" (subtitles 2) as needed so every overlapping part is kept; each extra subtitle track is raised by a fixed distance to stay clear of the subtitles on the first track.

A manually uploaded video has no generation provenance, so it is exported unchanged and is explicitly marked as having unavailable provenance. ArcReel does not generate TTS or subtitles for it. The uploaded video is finished content itself: editing prompts does not make it stale.

Any part of the BGM past the end of the edit timeline is cut off with a 1-second fade-out at the cut. When a BGM clip refers to BGM that is no longer in the project, the issue list reports it, and the draft cannot be exported until it is fixed.

### Canvas Size {#canvas-size}

Determined automatically from the project settings:
- Portrait (9:16) → 1080×1920
- Landscape (16:9) → 1920×1080

If the project has no aspect ratio configured, it is detected automatically from the first video file.

## Troubleshooting {#troubleshooting}

### The exported draft does not appear in Jianying {#draft-not-visible}

- Confirm that the ZIP was extracted into the correct draft directory
- Confirm that the extracted folder is directly inside the draft directory (do not add an extra enclosing folder)
- Try restarting Jianying

### What if the versions do not match? {#version-mismatch}

The Jianying version selected when downloading must match the locally installed version:
- Jianying 6.0 or later → select **Jianying 6.0 and above**
- Jianying 5.x → select **Jianying 5.x**

If you selected the wrong version, select the correct one in the dialog and download again. There is no need to export again.

### The Render button is disabled {#render-blocked}

Hover over Render to see the reason or click View Issues to open the issue list. For example, if a referenced video unit has no usable clip, generate or add a clip from the episode first. Missing narration only blocks the With Narration version. Select Without Narration to export without it.

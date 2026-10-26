---
id: files-and-trash
title: File View and Project Trash
sidebar_position: 7
update_docs: fact-check
---

# File View and Project Trash {#files-and-trash}

This page explains how to browse project files in the workspace, where deletions inside a project go and how to restore them, and what happens when a whole project is deleted. Every deletion inside a project, whether you or the Agent starts it, becomes a deletion record in the project trash. Records are kept for 30 days, during which you can restore them.

## File view {#file-view}

Open **Files** in the workspace sidebar to browse every file in the project as a directory tree that mirrors the disk. Each file and directory shows its size, and large directories expand on demand. Infrastructure files such as lock files, migration backups, and internal metadata are not shown.

Select a file to preview images, video, audio, and text (including JSON). Large files are not previewed in the page. Any file can be downloaded.

Each file is labeled with its role in the project:

| Role | Meaning | Can it be deleted in the file view? |
| --- | --- | --- |
| Structure file | The project's structural data, such as `project.json`, scripts, script plans, the version history record, the artifact manifest, and edit timelines | No |
| In use | A file the project is using, such as a storyboard image, a video, or an asset sheet | No; you can jump to the screen that owns it |
| Past version | One version in a resource's version history | Yes, except the current version |
| Unreferenced | A file that no project data, artifact manifest, or version history refers to, such as a stray file written by the Agent | Yes |
| Derived file | A file derived from other content that can be regenerated, such as a thumbnail | No |
| Agent configuration | `CLAUDE.md` and the skills under `.claude/` | No |
| Project memory | The Agent's project memory | No; you can jump to the project memory screen to edit it |

From a referenced file you can jump to the screen that owns it (assets, storyboards, episodes, or the edit timeline). That screen explains the impact of a change, so edits and deletions happen there. For a source file under `source/` that is not in use yet, the file view sends you back to the **Episodes** view to decide: add it to the whole source, use it as an episode's source, or delete it.

To delete an unreferenced file or a past version, select it and click **Move to project trash**. No confirmation dialog appears; the notification has a **Restore** button so you can undo right away. When you select several files, each file becomes its own deletion record, so you can restore one of them later. The snapshot of the current version cannot be deleted.

## Where deletions go {#where-deletions-go}

- Deleting project content is no longer a hard delete. The confirmation dialogs for deleting an episode, deleting an asset, removing a storyboard, deleting a source file, deleting an edit timeline, and similar operations all state that the content moves to the project trash and is kept for 30 days.
- Older versions produced by regeneration or editing stay in the version history and do not go to the project trash. The project trash holds only content that was deleted on purpose.
- The project trash is the **Project trash** section under **Files** in the workspace. Each deletion record shows the object, the deletion time, who started it (you or the Agent), and the space it uses. The object is shown by type and name, for example Episode "title", Source file "name", or Planning draft of episode N; file records show the path. Shots, video units, and end frames are shown by their position and opening text at deletion, for example "Shot title · 5: opening text…", so several records from the same episode can be told apart.
- The project trash is not project content: it does not appear in the file tree, and a project ZIP does not include it.

## Deleting and restoring each type of object {#objects}

### General restore rules {#restore-rules}

- A restore only adds. The restored object comes back as new content: it does not overwrite existing content and does not merge with it. If its original name or number is taken, it comes back under a new name or number. The few cases where it cannot be put back are covered later in this section.
- If the original name or number is free, it is reused. If it is already taken, the object comes back under a new name or number. The notification after the restore states the new name or number.
- Restored artifacts are checked for freshness as usual: if their inputs changed after the deletion, they show as stale and need regenerating.
- Content you just restored can be deleted again and goes back to the project trash.
- If the same deletion record is restored several times at once, only one restore takes effect, and the later requests report that the record no longer exists.
- If there is no free place to put the object back, nothing is restored this time and the deletion record is kept. Free up the place and try again, or download the files in the record.
- Deletion records created before an ArcReel upgrade can still be restored after the upgrade.

### Files and past versions {#files-and-versions}

After you delete an unreferenced file or a past version, the file returns to its original path if that path is free. If the original path already holds the current file of a resource, the restored file is added back as a past version of that resource and the current version is not overwritten. In every other case the file is restored under a new file name in the form `name (n)`. A restored past version is registered in the resource's version history again, with its prompt kept and a newly assigned version number.

### Episodes {#episodes}

On the episode page or in the **Episodes** view, choose **Move to project trash**. The episode's source text, script, script plan, edit timelines and final cuts, grids, and the media and version history of its storyboards and video units become one deletion record. An episode retired by choosing **Delete them too** when you adopt a new re-plan is handled the same way.

A restored episode keeps its original episode ID and is placed at the end of the playback order. It always comes back as an episode with its own source text, and its pre-deletion source text becomes the episode's source. If the episode was originally cut from the whole source, that passage stays uncut and does not compete for the source range with episodes planned later.

### Assets and derivatives {#assets}

Open the menu on an asset card and choose **Delete**. The sheet, reference image, reference audio, version history, and the derivatives under the asset become one deletion record. Deleting a single derivative of a character records only that derivative.

When you restore an asset:

- If the original name is free, it is reused. Names are compared ignoring case and across asset types. A same-name image or version history that an older version left on disk also counts as taken.
- If the original name is taken, for example because you created a new asset with the same name after the deletion, the asset is restored as `name (n)`. Its sheet, version history, and derivatives are renamed with it, existing assets are not affected, and you can merge it into an existing asset afterwards.
- References in scripts were not rewritten at deletion, so when the asset is restored under its original name, the existing references reconnect automatically. An asset restored under a new name does not take over those references; they still point to the existing asset with that name.

When you create an asset whose name matches a record in the trash, the interface offers to recover the earlier asset sheet from the project trash.

A derivative that was deleted on its own needs its character to still exist. If the character is gone, only the files are restored.

### Restoring after an asset merge {#after-merge}

During a merge, references to the merged asset in every episode's script plan, final script, drafts, and prompt text are already rewritten to point to the kept asset. The merged asset's description, sheet and version history, voice settings, reference image, and reference audio move to the project trash as one deletion record.

When you restore the merged asset, the system only adds:

- The merged asset comes back to the project. If the original name is free, it is reused; if it is taken, the asset is restored as `name (n)`.
- References that were already rewritten to the kept asset are not changed back. They still point to the kept asset after the restore. To use the restored asset again, change those references back by hand where you need them.
- Derivatives that moved to the kept character during the merge stay with the kept character and do not come back with the merged character.
- The kept asset is not affected.

### Storyboards and video units {#entries}

When you remove a storyboard or a video unit in the storyboard detail or the reference-to-video workbench, it becomes one deletion record together with its storyboard image, end frame, video, voice-over, thumbnails, subtitles, presentation, and version history. An entry without any media still becomes a record.

The same applies in these cases: when a content confirmation overwrites the final script, entries that the old script has and the new one lacks; and when a new entry reuses the number of a removed entry, the media left by the old entry.

A restored entry returns to its original episode and is inserted after its original previous entry. If it was first, it goes back to the front; if the previous entry is gone, it is placed at the end of the episode. When several adjacent entries were removed in one batch, restore them in their original order so the position is closest to what it was before the deletion.

If the original number is free, it is reused, and the media and version history come back unchanged. If another entry has taken the original number, the entry is restored under a new number: the media and version history come back under the new number, derived files such as subtitles and presentation are not restored, and the artifact registration is not restored either, so regenerate them if you need them. A storyboard restored under a new number no longer belongs to its original grid and is planned as an ordinary storyboard.

### Grids, end frames, and reference audio {#grids-end-frames-audio}

- Grids: when you regenerate a grid, the old grid it replaces becomes a deletion record that holds the grid record, the combined image, and the version history. If the original grid ID is free, the grid comes back as a whole.
- End frames: when you clear a storyboard's end frame, the end frame image becomes a deletion record. If the original location is free, it goes back there; if not, it comes back under a new file name. If the storyboard has no usable end frame at that moment, the restored image becomes its end frame again; otherwise it stays in the project as an unreferenced file.
- Reference audio: when you delete a character's reference audio in its voice settings, or replace it with audio of a different extension, the old file becomes a deletion record. The location rules are the same as for end frames, and if the character has no usable reference audio at that moment, the restored file becomes its reference audio again.

If the episode, storyboard, or character they belong to no longer exists, these three types of records fall back to restoring files only; see [Records that cannot be restored with their original structure](#degraded).

### Source files and planning drafts {#sources-and-drafts}

- Source files: when you delete a source file in the **Episodes** view, or upload a file with the same name to replace an old one, the source text and its original upload become one deletion record. A restored source file always comes back as a source file that is not part of the whole source. You decide in the **Episodes** view whether to add it to the whole source, use it as an episode's source, or delete it, so a restore never changes how episode sources are cut. If the original path is taken, the source file and its original upload are renamed together to `name (n)`.
- Planning drafts: when you discard a draft that awaits fixes or can be edited, discard an unconfirmed script plan, or choose **Start from blank**, the discarded draft becomes a deletion record. A restore needs the episode it belongs to to still exist. If the original path is free, the draft returns there and the script plan's artifact registration is restored with it. If the path already holds a file, the draft is restored as `name (n)`, as an unreferenced file without registration.

### Edit timelines {#edit-timelines}

When you delete an edit timeline, all of its revisions, its rendered final cuts, and its exported Jianying drafts become one deletion record. If the original ID is free, the restore reuses it and restores the registrations of the final cuts and drafts, and any whose inputs changed since then show as stale. If another edit timeline in the same episode already uses the display name, the restored one gets a number added after its name.

### Project memory and Agent configuration {#memory-and-profile}

- When you delete a project memory file or clear the memory, the content becomes deletion records. Clearing puts all memory files into a single record.
- When you reset the Agent configuration, the customized files that are overwritten and the skills unique to the project become one deletion record. Files identical to the built-in configuration are not recorded.

For both, a restore returns the file to its original location if that is free; if a file is already there, it is restored under a new file name in the form `name (n)`.

### Target deleted while a generation was running {#target-deleted-during-generation}

If the target of a generation task (an asset, a storyboard, a video unit, and so on) is deleted while the task runs, the result is not written back to the project and does not bring the deleted object back. The result file becomes a deletion record in the project trash with the note "generated after its target was deleted". The task counts as successful, and the cost is still recorded. The interface shows a notification saying the result is in the project trash and can be recovered from **Files**; the summary of a batch asset-sheet generation counts such results separately.

When you restore this record, the file returns to its original path. If that path already holds the current file of the resource, the file is added back as a past version. If the target is also in the project trash, restore the target first and then this record.

## Records that cannot be restored with their original structure {#degraded}

A restore falls back to restoring files only in these cases:

- The record comes from an earlier version and cannot be handled by the upgrade process.
- The parent the object depends on no longer exists, for example the character of a derivative, the episode of a grid, or the character of a reference audio.

In that case, media and other files return to their original paths, and the notification states how many files were recovered. Structure files such as scripts and edit timelines stay in the deletion record and can only be downloaded. Such a record no longer has a **Restore** button; from then on you can only download the files in it one by one.

If the episode of a storyboard, a video unit, or an edit timeline is still in the project trash, the restore does not fall back. It asks you to restore the episode first and leaves the record unchanged. Restore the episode, then this record, and it comes back with its original structure.

## Retention, permanent deletion, and emptying {#retention-and-purge}

Deletion records are kept for 30 days, counted from the deletion time, and are cleared automatically afterwards. Cleanup runs when the application starts and periodically while it runs. To keep files in the project trash, restore them before they expire.

In the **Project trash** section you can permanently delete a single record or empty the project trash. Neither can be undone, both need a second confirmation, and only you can do them in the interface.

The project trash is stored under `.arcreel/trash/` in the project directory. A full data-root backup keeps its deletion records, and a project ZIP export does not include them. Restoring a backup does not reset a record's retention period; see [Persistent Directories](../ops/deployment.md#sqlite-volumes).

## Deleted projects {#deleted-projects}

When you delete a whole project, it moves to **Deleted projects** on the project list page, together with its own project trash, and is kept for 30 days. Its queued tasks are cancelled. When you overwrite an existing project with an archive, the overwritten old project also goes to **Deleted projects**.

In **Deleted projects** in the project list header you can restore a project as a whole, which brings it back as it was before the deletion, or delete it permanently after a second confirmation. If a new project already uses the original name, the project is restored under a new name with a suffix. Deleted projects are cleared automatically after they expire.

## What the Agent can delete and restore {#agent}

- The Agent cannot directly delete or overwrite media files or the version history. It cannot change these directories with write, edit, or shell commands, so it can only delete through the project trash.
- When you explicitly ask it to delete an unreferenced file or a past version that is not current, the Agent can move it to the project trash. A referenced file is refused, and the Agent asks you to handle it in the screen that owns it.
- The Agent's own structural operations also go to the project trash: removing storyboards or video units, overwriting the final script, merging assets, replacing source files, and discarding drafts. The origin of these records is shown as the Agent.
- In a conversation you can ask the Agent to list deletion records, confirm with you which one to restore, and restore it. The result states whether the object came back under a new name or number, whether the restore fell back to files only, and which files can only be downloaded.
- Only you can permanently delete or empty the project trash, in the interface. The Agent has neither capability.

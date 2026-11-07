---
name: add-release-highlights
description: Draft release highlights for a specified version; after confirmation, synchronize them to the Changelog and GitHub Release and push main.
disable-model-invocation: true
---

# Add release highlights

## Draft

1. Use the target version's complete Changelog as the factual boundary. Trace related issues, commits, or version comparisons when an entry's meaning is insufficient. Group user-visible changes into a few outcome-oriented themes; include internal changes only when they produce a result users can perceive.
2. Write the complete draft in this format:

   ```markdown
   ### 🌟 版本亮点

   * **Theme:** A result users can perceive.
   ```

   Follow the existing language of that version in the body.
3. Show the draft to the user and request confirmation. Keep the Changelog and GitHub Release read-only and end the current turn.

Completion criteria: every important user-visible theme is covered, each statement is supported by changes in the release, and the user has explicitly confirmed the complete draft.

## Execute

After the draft completion criteria are met:

1. Confirm that the worktree is on `main`. Insert the confirmed draft between the version heading and categorized details in the Changelog; update it in place if already present, preserving the original details.
2. Synchronize the same block to that version's GitHub Release, keeping it identical word for word.
3. Stage only this Changelog change, commit with `chore(changelog): add <version> highlights`, then run `git push origin main`.

Completion criteria: the Changelog and GitHub Release each contain one `### 🌟 版本亮点` with content matching the confirmed draft; the commit is on both `main` and `origin/main`.

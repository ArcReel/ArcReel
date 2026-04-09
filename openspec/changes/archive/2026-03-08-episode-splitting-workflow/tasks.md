## 1. Core Script Development

- [x] 1.1 Create the shared utility module `_text_utils.py`: implement the `count_chars(text)` function (character count including punctuation, excluding blank lines) and the `find_char_offset(text, target_count)` function (converting effective character count to raw text character offset), shared by both scripts
- [x] 1.2 Create the `peek_split_point.py` script: accepts `--source`, `--target` (target character count), and `--context` (context character count, default 200) parameters; uses `_text_utils` to locate the split point; outputs context text before and after the split point + metadata (total character count, target position character offset, list of recommended nearby natural break points)
- [x] 1.3 Create the `split_episode.py` script: accepts `--source`, `--episode`, and `--anchor` (text fragment before the split point, approximately 10-20 characters) parameters; finds the anchor text in the original and splits at its end; supports `--dry-run` mode (only shows split preview: last 50 characters of front portion + first 50 characters of back portion, without writing files); on actual execution, generates `source/episode_{N}.txt` (front portion) and `source/_remaining.txt` (back portion); reports an error requiring a longer anchor when the anchor matches multiple locations; the original file is not modified

## 2. Permissions and Configuration

- [x] 2.1 Add Bash execution permissions for `peek_split_point.py` and `split_episode.py` in `settings.json`'s `permissions.allow`

## 3. Workflow Integration

- [x] 3.1 Update `manga-workflow/SKILL.md` phase 2: add prerequisite check logic — check if `source/episode_{N}.txt` exists; if not, trigger the episode splitting workflow (ask for target word count → call peek → agent suggests break point → user confirms → call split)
- [x] 3.2 Update the `normalize-drama-script.md` subagent: explicitly use the `--source source/episode_{N}.txt` parameter at dispatch time
- [x] 3.3 Update the `split-narration-segments.md` subagent: explicitly specify reading `source/episode_{N}.txt` at dispatch time

## 4. Validation

- [x] 4.1 Verify the peek script's character counting accuracy for Chinese novels (including punctuation, excluding blank lines)
- [x] 4.2 Verify the split script's splitting results: the complete concatenation of the episode file + remaining file equals the original
- [x] 4.3 End-to-end validation: upload complete novel → episode splitting → normalize_drama_script.py --source episode_1.txt → generate_script.py generates JSON

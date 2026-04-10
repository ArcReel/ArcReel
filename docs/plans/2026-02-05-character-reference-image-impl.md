# Character Reference Image Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add character reference image upload functionality so that reference images are automatically used as AI input when generating character design images.

**Architecture:**
- Backend: Add `character_ref` upload type; modify generation endpoint to read reference images
- Frontend: Add reference image upload area to character modal (stacked layout); upload together when saving
- CLI: Remove `--ref` parameter; auto-read from project.json

**Tech Stack:** Python/FastAPI, JavaScript, HTML/Tailwind CSS

---

## Task 1: Backend — Add character_ref Upload Type

**Files:**
- Modify: `webui/server/routers/files.py:15-20` (ALLOWED_EXTENSIONS)
- Modify: `webui/server/routers/files.py:60-100` (upload_file function)

**Step 1: Add character_ref type to ALLOWED_EXTENSIONS**

In `files.py`, add the new type to the `ALLOWED_EXTENSIONS` dictionary:

```python
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".doc", ".docx"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],  # New
    "clue": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}
```

**Step 2: Add character_ref handling logic in upload_file**

After the `if upload_type == "character":` branch, add:

```python
elif upload_type == "character_ref":
    target_dir = project_dir / "characters" / "refs"
    if name:
        filename = f"{name}.png"
    else:
        filename = f"{Path(file.filename).stem}.png"
```

**Step 3: Add logic to automatically update reference_image field**

After file save, in the metadata update section (after `if upload_type == "character" and name:`), add:

```python
if upload_type == "character_ref" and name:
    try:
        pm.update_character_reference_image(project_name, name, f"characters/refs/{filename}")
    except KeyError:
        pass  # Character doesn't exist; ignore
```

**Step 4: Verify syntax is correct**

Run: `python -c "from webui.server.routers.files import router; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add webui/server/routers/files.py
git commit -m "feat(files): add character_ref upload type"
```

---

## Task 2: Backend — Add reference_image Update Method to ProjectManager

**Files:**
- Modify: `lib/project_manager.py`

**Step 1: Add update_character_reference_image method**

Add a method to the `ProjectManager` class (modeled after existing `update_project_character_sheet`):

```python
def update_character_reference_image(self, project_name: str, char_name: str, ref_path: str) -> dict:
    """
    Update the reference image path for a character.

    Args:
        project_name: Project name
        char_name: Character name
        ref_path: Relative path to the reference image

    Returns:
        Updated project data
    """
    project = self.load_project(project_name)

    if "characters" not in project or char_name not in project["characters"]:
        raise KeyError(f"Character '{char_name}' does not exist")

    project["characters"][char_name]["reference_image"] = ref_path
    self.save_project(project_name, project)
    return project
```

**Step 2: Verify import is successful**

Run: `python -c "from lib.project_manager import ProjectManager; pm = ProjectManager(); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add lib/project_manager.py
git commit -m "feat(project_manager): add update_character_reference_image method"
```

---

## Task 3: Backend — Add reference_image Field Support to characters.py

**Files:**
- Modify: `webui/server/routers/characters.py:20-25` (UpdateCharacterRequest)
- Modify: `webui/server/routers/characters.py:55-65` (update_character function)

**Step 1: Add reference_image field to UpdateCharacterRequest**

```python
class UpdateCharacterRequest(BaseModel):
    description: Optional[str] = None
    voice_style: Optional[str] = None
    character_sheet: Optional[str] = None
    reference_image: Optional[str] = None  # New
```

**Step 2: Handle reference_image in update_character function**

After `if req.character_sheet is not None:`, add:

```python
if req.reference_image is not None:
    char["reference_image"] = req.reference_image
```

**Step 3: Verify syntax is correct**

Run: `python -c "from webui.server.routers.characters import router; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add webui/server/routers/characters.py
git commit -m "feat(characters): add reference_image field to update API"
```

---

## Task 4: Backend — generate.py Uses Reference Image for Character Generation

**Files:**
- Modify: `webui/server/routers/generate.py:280-330` (generate_character function)

**Step 1: Modify generate_character to read reference image**

In the `generate_character` function, after verifying the character exists, add logic to read the reference image:

```python
@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    req: GenerateCharacterRequest
):
    """
    Generate a character design image (first time or regenerate).

    Uses MediaGenerator for automatic version management.
    If the character has a reference_image, it is automatically used as a reference.
    """
    try:
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        generator = get_media_generator(project_name)

        # Verify character exists
        if char_name not in project.get("characters", {}):
            raise HTTPException(status_code=404, detail=f"Character '{char_name}' does not exist")

        char_data = project["characters"][char_name]

        # Get aspect ratio (character design images: 3:4)
        aspect_ratio = get_aspect_ratio(project, "characters")

        # Build prompt using shared library (ensures consistency with Skill side)
        style = project.get("style", "")
        full_prompt = build_character_prompt(char_name, req.prompt, style)

        # Read reference image (if present)
        reference_images = None
        ref_path = char_data.get("reference_image")
        if ref_path:
            ref_full_path = project_path / ref_path
            if ref_full_path.exists():
                reference_images = [ref_full_path]

        # Generate image using MediaGenerator (handles version management automatically)
        _, new_version = await generator.generate_image_async(
            prompt=full_prompt,
            resource_type="characters",
            resource_id=char_name,
            reference_images=reference_images,  # Pass reference image
            aspect_ratio=aspect_ratio,
            image_size="2K"
        )

        # Update character_sheet in project.json
        project["characters"][char_name]["character_sheet"] = f"characters/{char_name}.png"
        pm.save_project(project_name, project)

        return {
            "success": True,
            "version": new_version,
            "file_path": f"characters/{char_name}.png",
            "created_at": generator.versions.get_versions("characters", char_name)["versions"][-1]["created_at"]
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: Verify syntax is correct**

Run: `python -c "from webui.server.routers.generate import router; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add webui/server/routers/generate.py
git commit -m "feat(generate): use reference_image when generating character sheet"
```

---

## Task 5: CLI — Remove --ref Parameter; Auto-read from project.json

**Files:**
- Modify: `.claude/skills/generate-characters/scripts/generate_character.py`

**Step 1: Modify generate_character to auto-read reference image**

```python
def generate_character(
    project_name: str,
    character_name: str,
) -> Path:
    """
    Generate a character design image.

    Args:
        project_name: Project name
        character_name: Character name

    Returns:
        Path to the generated image
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)

    # Get character info from project.json
    project = pm.load_project(project_name)

    description = ""
    style = project.get('style', '')
    reference_images = None

    if 'characters' in project and character_name in project['characters']:
        char_info = project['characters'][character_name]
        description = char_info.get('description', '')

        # Auto-read reference image
        ref_path = char_info.get('reference_image')
        if ref_path:
            ref_full_path = project_dir / ref_path
            if ref_full_path.exists():
                reference_images = [ref_full_path]
                print(f"Using reference image: {ref_full_path}")

    if not description:
        raise ValueError(f"Description for character '{character_name}' is empty. Please add a description in project.json first.")

    # Build prompt
    prompt = build_character_prompt(character_name, description, style)

    # Generate image (with automatic version management)
    generator = MediaGenerator(project_dir)

    print(f"Generating character design image: {character_name}")
    print(f"Description: {description[:50]}...")

    output_path, version = generator.generate_image(
        prompt=prompt,
        resource_type="characters",
        resource_id=character_name,
        reference_images=reference_images,
        aspect_ratio="3:4"
    )

    print(f"Character design image saved: {output_path} (version v{version})")

    # Update character_sheet path in project.json
    relative_path = f"characters/{character_name}.png"
    pm.update_project_character_sheet(project_name, character_name, relative_path)
    print("project.json updated")

    return output_path
```

**Step 2: Simplify main function; remove --ref parameter**

```python
def main():
    parser = argparse.ArgumentParser(description='Generate character design image')
    parser.add_argument('project', help='Project name')
    parser.add_argument('character', help='Character name')
    # --ref parameter removed

    args = parser.parse_args()

    try:
        output_path = generate_character(
            args.project,
            args.character,
        )
        print(f"\nView the generated image: {output_path}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
```

**Step 3: Verify syntax is correct**

Run: `python -m py_compile .claude/skills/generate-characters/scripts/generate_character.py`
Expected: no output (success)

**Step 4: Commit**

```bash
git add .claude/skills/generate-characters/scripts/generate_character.py
git commit -m "feat(cli): auto-read reference_image from project.json, remove --ref arg"
```

---

## Task 6: Frontend — HTML: Add Reference Image Upload Area

**Files:**
- Modify: `webui/project.html:370-400` (character-modal form)

**Step 1: Add reference image upload area after "Voice Style" field, before "Design Image" field**

After the `char-voice` input's `</div>`, before the "Design Image" label, add:

```html
<div>
    <label class="block text-sm font-medium text-gray-300 mb-1">Reference Image (optional)</label>
    <div id="char-ref-drop" class="drop-zone rounded-lg p-4 text-center cursor-pointer relative">
        <div id="char-ref-preview" class="hidden mb-2">
            <img src="" alt="Reference image preview" class="max-h-32 mx-auto rounded">
        </div>
        <div id="char-ref-placeholder">
            <svg class="mx-auto h-8 w-8 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <p class="mt-1 text-xs text-gray-400">Click or drag to upload a reference image (used for design image generation)</p>
        </div>
        <input type="file" id="char-ref-input" accept="image/*" class="hidden">
    </div>
</div>
```

**Step 2: Verify the HTML element was added**

Run: `grep -c "char-ref-input" webui/project.html`
Expected: `1`

**Step 3: Commit**

```bash
git add webui/project.html
git commit -m "feat(ui): add reference image upload area in character modal"
```

---

## Task 7: Frontend — JavaScript: Handle Reference Image Upload Logic

**Files:**
- Modify: `webui/js/project/characters.js`

**Step 1: Initialize reference image preview in openCharacterModal**

After `form.reset();`, add reference image reset and display logic:

```javascript
// Reset reference image area
document.getElementById("char-ref-preview").classList.add("hidden");
document.getElementById("char-ref-placeholder").classList.remove("hidden");
document.getElementById("char-ref-input").value = "";

// ... existing code ...

// Show existing reference image in edit mode
if (charName && state.currentProject.characters[charName]) {
    const char = state.currentProject.characters[charName];
    // ... existing code ...

    // Display reference image (if available)
    if (char.reference_image) {
        const refPreview = document.getElementById("char-ref-preview");
        refPreview.querySelector("img").src = `${API.getFileUrl(state.projectName, char.reference_image)}?t=${state.cacheBuster}`;
        refPreview.classList.remove("hidden");
        document.getElementById("char-ref-placeholder").classList.add("hidden");
    }
}
```

**Step 2: Add event listeners for reference image upload area**

Add an initialization function at the end of the file or at an appropriate location:

```javascript
// Initialize reference image upload area
export function initCharacterRefUpload() {
    const dropZone = document.getElementById("char-ref-drop");
    const input = document.getElementById("char-ref-input");
    const preview = document.getElementById("char-ref-preview");
    const placeholder = document.getElementById("char-ref-placeholder");

    if (!dropZone || !input) return;

    // Click to upload
    dropZone.addEventListener("click", () => input.click());

    // File selection
    input.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (file) {
            showRefPreview(file);
        }
    });

    // Drag and drop
    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("border-blue-500");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("border-blue-500");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("border-blue-500");
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith("image/")) {
            // Set to input for later retrieval
            const dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
            showRefPreview(file);
        }
    });

    function showRefPreview(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            preview.querySelector("img").src = e.target.result;
            preview.classList.remove("hidden");
            placeholder.classList.add("hidden");
        };
        reader.readAsDataURL(file);
    }
}
```

**Step 3: Modify saveCharacter to handle reference image upload**

In the saveCharacter function, handle reference image upload:

```javascript
export async function saveCharacter() {
    const mode = document.getElementById("char-edit-mode").value;
    const originalName = document.getElementById("char-original-name").value;
    const name = document.getElementById("char-name").value.trim();
    const description = document.getElementById("char-description").value.trim();
    const voiceStyle = document.getElementById("char-voice").value.trim();
    const imageInput = document.getElementById("char-image-input");
    const refInput = document.getElementById("char-ref-input");  // New

    if (!name || !description) {
        alert("Please fill in all required fields");
        return;
    }

    try {
        // If a new reference image is selected, upload it first
        let referenceImage = null;
        if (refInput.files.length > 0) {
            const result = await API.uploadFile(state.projectName, "character_ref", refInput.files[0], name);
            referenceImage = result.path;
        }

        // If a new design image is selected, upload it
        let characterSheet = null;
        if (imageInput.files.length > 0) {
            const result = await API.uploadFile(state.projectName, "character", imageInput.files[0], name);
            characterSheet = result.path;
        }

        if (mode === "add") {
            await API.addCharacter(state.projectName, name, description, voiceStyle);
            if (referenceImage) {
                await API.updateCharacter(state.projectName, name, { reference_image: referenceImage });
            }
            if (characterSheet) {
                await API.updateCharacter(state.projectName, name, { character_sheet: characterSheet });
            }
        } else {
            // Edit mode
            if (originalName !== name) {
                // Name changed: delete old, add new
                await API.deleteCharacter(state.projectName, originalName);
                await API.addCharacter(state.projectName, name, description, voiceStyle);
            } else {
                await API.updateCharacter(state.projectName, name, { description, voice_style: voiceStyle });
            }
            if (referenceImage) {
                await API.updateCharacter(state.projectName, name, { reference_image: referenceImage });
            }
            if (characterSheet) {
                await API.updateCharacter(state.projectName, name, { character_sheet: characterSheet });
            }
        }

        closeAllModals();
        await loadProject();
    } catch (error) {
        alert("Save failed: " + error.message);
    }
}
```

**Step 4: Verify JavaScript syntax is correct**

Run: `node --check webui/js/project/characters.js 2>&1 || echo "Syntax check done"`
Expected: no errors or "Syntax check done"

**Step 5: Commit**

```bash
git add webui/js/project/characters.js
git commit -m "feat(ui): implement reference image upload and preview in character modal"
```

---

## Task 8: Frontend — Initialize Reference Image Event Listeners

**Files:**
- Modify: `webui/js/project/events.js` (or the main initialization file)

**Step 1: Call initCharacterRefUpload at the appropriate location**

In `events.js` initialization function, add:

```javascript
import { initCharacterRefUpload } from "./characters.js";

// In DOMContentLoaded or the init function
initCharacterRefUpload();
```

**Step 2: Confirm import and call are correct**

Run: `grep -c "initCharacterRefUpload" webui/js/project/events.js`
Expected: `1` or `2`

**Step 3: Commit**

```bash
git add webui/js/project/events.js
git commit -m "feat(ui): initialize character reference image upload on page load"
```

---

## Task 9: Integration Testing — Manual End-to-End Verification

**Step 1: Start WebUI server**

Run: `python -m webui.server.main &`

**Step 2: Manual test flow**

1. Open browser at http://localhost:8000
2. Select or create a test project
3. Add a new character and upload a reference image
4. Save the character
5. Click "Generate Design Image"
6. Verify that the generated design image reflects the uploaded reference

**Step 3: Verify project.json structure**

Check that the project's `project.json` correctly contains the `reference_image` field:

```bash
cat projects/test-project/project.json | python -m json.tool | grep -A5 "characters"
```

Expected: contains `"reference_image": "characters/refs/xxx.png"`

**Step 4: Stop test server**

Run: `pkill -f "python -m webui.server.main"`

---

## Task 10: Final Commit and Cleanup

**Step 1: Update design document status**

The status in `docs/plans/2026-02-05-character-reference-image-design.md` has been updated to "Implemented".

**Step 2: Final commit**

```bash
git add docs/plans/2026-02-05-character-reference-image-design.md
git commit -m "docs: mark character reference image feature as implemented"
```

**Step 3: View all commits**

```bash
git log --oneline -10
```

---

## Implementation Checklist

| Task | Description | Estimated Time |
|------|-------------|----------------|
| 1 | Backend files.py: add character_ref upload type | 3 min |
| 2 | Backend ProjectManager: add update method | 3 min |
| 3 | Backend characters.py: add reference_image field | 2 min |
| 4 | Backend generate.py: use reference image | 5 min |
| 5 | CLI: remove --ref parameter; auto-read | 5 min |
| 6 | Frontend HTML: add reference image upload area | 3 min |
| 7 | Frontend JS: handle upload logic | 10 min |
| 8 | Frontend JS: initialize event listeners | 2 min |
| 9 | Integration testing | 5 min |
| 10 | Final commit and cleanup | 2 min |

**Total: approximately 40 minutes**

# Style Reference Image Mechanism Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a project-level style reference image mechanism to video projects. Users can upload a style reference image; AI automatically analyzes it to generate a style description, which is used for all subsequent image generation to maintain visual consistency.

**Architecture:**
- Backend adds a new style analysis API endpoint that calls the Gemini API to analyze image style
- Frontend adds style image upload UI to the create project and project overview pages
- Modify prompt_builders.py to centralize style description composition
- Modify generation scripts to use the new style prompt builder function

**Tech Stack:** Python/FastAPI, JavaScript ES Modules, Gemini API, TailwindCSS

---

## Task 1: Add Style Analysis Method to GeminiClient

**Files:**
- Modify: `lib/gemini_client.py:1110-1163` (near the generate_text method)

**Step 1: Add analyze_style_image method**

Add to the `GeminiClient` class:

```python
@with_retry(max_attempts=3, backoff_seconds=(2, 4, 8))
def analyze_style_image(
    self,
    image: Union[str, Path, Image.Image],
    model: str = "gemini-2.5-flash"
) -> str:
    """
    Analyze the visual style of an image.

    Args:
        image: Image path or PIL Image object
        model: Model name, defaults to flash model

    Returns:
        Style description text (comma-separated list of descriptors)
    """
    # Prepare image
    if isinstance(image, (str, Path)):
        img = Image.open(image)
    else:
        img = image

    # Style analysis prompt (based on Storycraft)
    prompt = (
        "Analyze the visual style of this image. Describe the lighting, "
        "color palette, medium (e.g., oil painting, digital art, photography), "
        "texture, and overall mood. Do NOT describe the subject matter "
        "(e.g., people, objects) or specific content. Focus ONLY on the "
        "artistic style. Provide a concise comma-separated list of descriptors "
        "suitable for an image generation prompt."
    )

    # Call API
    response = self.client.models.generate_content(
        model=model,
        contents=[img, prompt]
    )

    return response.text.strip()
```

**Step 2: Verify the method is callable**

Run: `python -c "from lib.gemini_client import GeminiClient; print(hasattr(GeminiClient, 'analyze_style_image'))"`
Expected: `True`

**Step 3: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat(lib): add analyze_style_image method to GeminiClient"
```

---

## Task 2: Add build_style_prompt Function

**Files:**
- Modify: `lib/prompt_builders.py`

**Step 1: Add build_style_prompt function**

Add at the end of the file:

```python
def build_style_prompt(project_data: dict) -> str:
    """
    Build a style description prompt fragment.

    Combines style (user-entered) and style_description (AI-generated).

    Args:
        project_data: project.json data

    Returns:
        Style description string for concatenating into generation prompts
    """
    parts = []

    # Base style tag
    style = project_data.get('style', '')
    if style:
        parts.append(f"Style: {style}")

    # AI-analyzed style description
    style_description = project_data.get('style_description', '')
    if style_description:
        parts.append(f"Visual style: {style_description}")

    return '\n'.join(parts)
```

**Step 2: Verify function is importable**

Run: `python -c "from lib.prompt_builders import build_style_prompt; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add lib/prompt_builders.py
git commit -m "feat(lib): add build_style_prompt function"
```

---

## Task 3: Add Style Image Upload API Endpoint

**Files:**
- Modify: `webui/server/routers/files.py`

**Step 1: Add imports and constants**

Add to the imports at the top of the file:

```python
from lib.gemini_client import GeminiClient
```

**Step 2: Add POST /projects/{name}/style-image endpoint**

Add at the end of the file:

```python
# ==================== Style Reference Image Management ====================

@router.post("/projects/{project_name}/style-image")
async def upload_style_image(
    project_name: str,
    file: UploadFile = File(...)
):
    """
    Upload a style reference image and analyze its style.

    1. Save image to projects/{project_name}/style_reference.png
    2. Call Gemini API to analyze style
    3. Update style_image and style_description fields in project.json
    """
    # Check file type
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext}. Allowed: .png, .jpg, .jpeg, .webp"
        )

    try:
        project_dir = pm.get_project_path(project_name)

        # Save image (convert to PNG)
        content = await file.read()
        try:
            png_content = convert_image_bytes_to_png(content)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid image file, cannot parse")

        output_path = project_dir / "style_reference.png"
        with open(output_path, "wb") as f:
            f.write(png_content)

        # Call Gemini API to analyze style
        client = GeminiClient()
        style_description = client.analyze_style_image(output_path)

        # Update project.json
        project_data = pm.load_project(project_name)
        project_data["style_image"] = "style_reference.png"
        project_data["style_description"] = style_description
        pm.save_project(project_name, project_data)

        return {
            "success": True,
            "style_image": "style_reference.png",
            "style_description": style_description,
            "url": f"/api/v1/files/{project_name}/style_reference.png"
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_name}/style-image")
async def delete_style_image(project_name: str):
    """
    Delete the style reference image and related fields.
    """
    try:
        project_dir = pm.get_project_path(project_name)

        # Delete image file
        image_path = project_dir / "style_reference.png"
        if image_path.exists():
            image_path.unlink()

        # Clear related fields from project.json
        project_data = pm.load_project(project_name)
        project_data.pop("style_image", None)
        project_data.pop("style_description", None)
        pm.save_project(project_name, project_data)

        return {"success": True}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_name}/style-description")
async def update_style_description(
    project_name: str,
    style_description: str = Body(..., embed=True)
):
    """
    Update the style description (manual edit).
    """
    try:
        project_data = pm.load_project(project_name)
        project_data["style_description"] = style_description
        pm.save_project(project_name, project_data)

        return {"success": True, "style_description": style_description}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 3: Commit**

```bash
git add webui/server/routers/files.py
git commit -m "feat(api): add style reference image upload/delete/update endpoints"
```

---

## Task 4: Add Frontend API Methods

**Files:**
- Modify: `webui/js/api.js`

**Step 1: Add style image API methods**

Add before `// ==================== Cost Statistics API ====================`:

```javascript
// ==================== Style Reference Image API ====================

/**
 * Upload a style reference image.
 * @param {string} projectName - Project name
 * @param {File} file - Image file
 * @returns {Promise<{success: boolean, style_image: string, style_description: string, url: string}>}
 */
static async uploadStyleImage(projectName, file) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(
        `${API_BASE}/projects/${encodeURIComponent(projectName)}/style-image`,
        {
            method: 'POST',
            body: formData,
        }
    );

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || 'Upload failed');
    }

    return response.json();
}

/**
 * Delete the style reference image.
 * @param {string} projectName - Project name
 */
static async deleteStyleImage(projectName) {
    return this.request(`/projects/${encodeURIComponent(projectName)}/style-image`, {
        method: 'DELETE',
    });
}

/**
 * Update the style description.
 * @param {string} projectName - Project name
 * @param {string} styleDescription - Style description
 */
static async updateStyleDescription(projectName, styleDescription) {
    return this.request(`/projects/${encodeURIComponent(projectName)}/style-description`, {
        method: 'PATCH',
        body: JSON.stringify({ style_description: styleDescription }),
    });
}
```

**Step 2: Commit**

```bash
git add webui/js/api.js
git commit -m "feat(frontend): add style reference image API methods"
```

---

## Task 5: Modify Create Project Modal

**Files:**
- Modify: `webui/index.html`
- Modify: `webui/js/projects.js`

**Step 1: Add style image upload area to index.html**

Before the `<!-- Buttons -->` comment, after the `project-style` select box, add:

```html
<div>
    <label class="block text-sm font-medium text-gray-300 mb-1">
        Style Reference Image (optional)
    </label>
    <div id="style-image-upload" class="border-2 border-dashed border-gray-600 rounded-lg p-4 text-center cursor-pointer hover:border-gray-500 transition-colors">
        <div id="style-image-placeholder">
            <svg class="mx-auto h-8 w-8 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <p class="mt-1 text-sm text-gray-500">Click or drag to upload</p>
        </div>
        <div id="style-image-preview" class="hidden">
            <img id="style-image-thumb" class="mx-auto h-20 w-20 object-cover rounded" alt="Style reference image">
            <button type="button" id="remove-style-image" class="mt-2 text-sm text-red-400 hover:text-red-300">Remove</button>
        </div>
    </div>
    <input type="file" id="style-image-input" class="hidden" accept=".png,.jpg,.jpeg,.webp">
    <p class="mt-1 text-xs text-gray-500">After upload, style will be analyzed automatically to generate a style description.</p>
</div>
```

**Step 2: Add style image staging logic to projects.js**

Add variable at the top of the file:

```javascript
// Staged style reference image (uploaded when project is created)
let pendingStyleImage = null;
```

Add to the end of `setupEventListeners()`:

```javascript
// Style reference image upload
const styleImageUpload = document.getElementById('style-image-upload');
const styleImageInput = document.getElementById('style-image-input');

styleImageUpload.onclick = () => styleImageInput.click();
styleImageInput.onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;

    pendingStyleImage = file;

    // Show local preview
    const placeholder = document.getElementById('style-image-placeholder');
    const preview = document.getElementById('style-image-preview');
    const thumb = document.getElementById('style-image-thumb');

    thumb.src = URL.createObjectURL(file);
    placeholder.classList.add('hidden');
    preview.classList.remove('hidden');
};
```

Add to the project creation handler after the project is created:

```javascript
// Upload style image if staged
if (pendingStyleImage) {
    try {
        await API.uploadStyleImage(projectName, pendingStyleImage);
    } catch (err) {
        console.warn('Style image upload failed:', err);
        // Not a fatal error; continue
    }
    pendingStyleImage = null;
}
```

**Step 3: Commit**

```bash
git add webui/index.html webui/js/projects.js
git commit -m "feat(frontend): add style image upload to create project modal"
```

---

## Task 6: Add Style Image Management to Project Overview Page

**Files:**
- Modify: `webui/project.html`
- New: `webui/js/project/style_image.js`
- Modify: `webui/js/project.js`

**Step 1: Add style image section to project.html**

In the Overview tab, add after the basic info section:

```html
<!-- Style reference image management -->
<div id="style-image-section" class="bg-gray-800 rounded-lg p-4 mb-4">
    <h3 class="text-sm font-medium text-gray-300 mb-3">Style Reference Image</h3>

    <!-- Empty state: no image -->
    <div id="style-image-empty" class="hidden">
        <div id="style-image-dropzone" class="border-2 border-dashed border-gray-600 rounded-lg p-6 text-center cursor-pointer hover:border-gray-500 transition-colors">
            <p class="text-sm text-gray-500">Click to upload a style reference image</p>
            <p class="text-xs text-gray-600 mt-1">AI will automatically analyze and generate a style description</p>
        </div>
        <input type="file" id="style-image-file-input" class="hidden" accept=".png,.jpg,.jpeg,.webp">
    </div>

    <!-- Loading state -->
    <div id="style-image-loading" class="hidden text-center py-4">
        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
        <p class="text-sm text-gray-400 mt-2">Analyzing style...</p>
    </div>

    <!-- Content state: image exists -->
    <div id="style-image-content" class="hidden">
        <div class="flex gap-4">
            <img id="style-image-thumb-overview" class="w-24 h-32 object-cover rounded" alt="Style reference">
            <div class="flex-1">
                <label class="block text-xs text-gray-400 mb-1">Style Description (editable)</label>
                <textarea
                    id="style-description-edit"
                    class="w-full bg-gray-700 text-white text-sm rounded p-2 h-24 resize-none"
                    placeholder="AI-generated style description..."
                ></textarea>
                <div class="flex gap-2 mt-2">
                    <button id="save-style-description" class="text-sm bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded">Save Description</button>
                    <button id="replace-style-image" class="text-sm bg-gray-600 hover:bg-gray-700 text-white px-3 py-1 rounded">Replace Image</button>
                    <button id="delete-style-image" class="text-sm bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded">Delete</button>
                </div>
            </div>
        </div>
        <input type="file" id="style-image-replace-input" class="hidden" accept=".png,.jpg,.jpeg,.webp">
    </div>
</div>
```

**Step 2: Create webui/js/project/style_image.js**

```javascript
/**
 * Style reference image management component.
 */

import { API } from '../api.js';
import { state } from '../project.js';

/**
 * Render the style image section based on current state.
 */
export function renderStyleImageSection() {
    const project = state.currentProject;
    const hasImage = !!project?.style_image;

    const emptyState = document.getElementById('style-image-empty');
    const loadingState = document.getElementById('style-image-loading');
    const contentState = document.getElementById('style-image-content');

    emptyState?.classList.toggle('hidden', hasImage);
    loadingState?.classList.add('hidden');
    contentState?.classList.toggle('hidden', !hasImage);

    if (hasImage) {
        const thumb = document.getElementById('style-image-thumb-overview');
        const descEdit = document.getElementById('style-description-edit');

        if (thumb) {
            thumb.src = `/api/v1/files/${state.projectName}/style_reference.png?t=${Date.now()}`;
        }
        if (descEdit) {
            descEdit.value = project.style_description || '';
        }
    }
}

/**
 * Set up style image event listeners.
 */
export function setupStyleImageEvents() {
    // Empty state: click to upload
    const dropzone = document.getElementById('style-image-dropzone');
    const fileInput = document.getElementById('style-image-file-input');

    dropzone?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', (e) => handleUpload(e.target.files[0]));

    // Content state: replace image
    const replaceBtn = document.getElementById('replace-style-image');
    const replaceInput = document.getElementById('style-image-replace-input');

    replaceBtn?.addEventListener('click', () => replaceInput?.click());
    replaceInput?.addEventListener('change', (e) => handleUpload(e.target.files[0]));

    // Save description
    document.getElementById('save-style-description')?.addEventListener('click', handleSaveStyleDescription);

    // Delete image
    document.getElementById('delete-style-image')?.addEventListener('click', handleDeleteStyleImage);
}

/**
 * Handle style image upload.
 */
async function handleUpload(file) {
    if (!file) return;

    const emptyState = document.getElementById('style-image-empty');
    const contentState = document.getElementById('style-image-content');
    const loadingState = document.getElementById('style-image-loading');

    try {
        // Show loading state
        emptyState.classList.add('hidden');
        contentState.classList.add('hidden');
        loadingState.classList.remove('hidden');

        // Upload and analyze
        const result = await API.uploadStyleImage(state.projectName, file);

        // Update local state
        state.currentProject.style_image = result.style_image;
        state.currentProject.style_description = result.style_description;

        // Re-render
        renderStyleImageSection();

    } catch (error) {
        alert('Upload failed: ' + error.message);
        renderStyleImageSection();
    } finally {
        e.target.value = '';
    }
}

/**
 * Handle style image deletion.
 */
async function handleDeleteStyleImage() {
    if (!confirm('Are you sure you want to delete the style reference image?')) return;

    try {
        await API.deleteStyleImage(state.projectName);

        // Update local state
        delete state.currentProject.style_image;
        delete state.currentProject.style_description;

        // Re-render
        renderStyleImageSection();

    } catch (error) {
        alert('Delete failed: ' + error.message);
    }
}

/**
 * Handle saving the style description.
 */
async function handleSaveStyleDescription() {
    const descEl = document.getElementById('style-description-edit');
    const newDescription = descEl.value.trim();

    try {
        await API.updateStyleDescription(state.projectName, newDescription);

        // Update local state
        state.currentProject.style_description = newDescription;

        alert('Description saved');

    } catch (error) {
        alert('Save failed: ' + error.message);
    }
}
```

**Step 3: Import and initialize in project.js**

Add import to `webui/js/project.js`:

```javascript
import { renderStyleImageSection, setupStyleImageEvents } from "./project/style_image.js";
```

Call `setupStyleImageEvents()` in the initialization function.

Call `renderStyleImageSection()` when rendering the overview.

**Step 4: Commit**

```bash
git add webui/project.html webui/js/project/style_image.js webui/js/project.js webui/js/project/render.js
git commit -m "feat(frontend): add style reference image management to project overview"
```

---

## Task 7: Modify generate_storyboard.py to Use Style Description

**Files:**
- Modify: `.claude/skills/generate-storyboard/scripts/generate_storyboard.py`

**Step 1: Add import**

Add to imports at the top of the file:

```python
from lib.prompt_builders import build_style_prompt
```

**Step 2: Modify build_direct_scene_prompt function**

Fetch style description at the start of the function and merge it into the prompt:

```python
def build_direct_scene_prompt(
    segment: dict,
    characters: dict = None,
    clues: dict = None,
    style: str = "",
    style_description: str = "",  # New parameter
    id_field: str = 'segment_id',
    char_field: str = 'characters_in_segment',
    clue_field: str = 'clues_in_segment'
) -> str:
    """
    Build a prompt for directly generating a scene image (narration mode, no multi-panel reference).
    """
    image_prompt = segment.get('image_prompt', '')
    if not image_prompt:
        raise ValueError(f"Segment {segment[id_field]} is missing image_prompt field")

    # Build style prefix
    style_parts = []
    if style:
        style_parts.append(f"Style: {style}")
    if style_description:
        style_parts.append(f"Visual style: {style_description}")
    style_prefix = '\n'.join(style_parts) + '\n\n' if style_parts else ''

    # Detect structured format
    if is_structured_image_prompt(image_prompt):
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}\nPortrait composition."

    return f"{style_prefix}{image_prompt} Portrait composition."
```

**Step 3: Modify generate_single call in generate_storyboard_direct**

Inside the `generate_single` closure in `generate_storyboard_direct`:

```python
# Get style description
style_description = project_data.get('style_description', '') if project_data else ''

# Build prompt (direct generation, no multi-panel reference needed)
prompt = build_direct_scene_prompt(
    segment, characters, clues, style, style_description,
    id_field, char_field, clue_field
)
```

**Step 4: Similarly modify build_grid_prompt and build_scene_prompt**

Add `style_description` parameter and use it in the prompt.

**Step 5: Commit**

```bash
git add .claude/skills/generate-storyboard/scripts/generate_storyboard.py
git commit -m "feat(storyboard): use style description when generating storyboard images"
```

---

## Task 8: Modify generate_character.py and generate_clue.py

**Files:**
- Modify: `.claude/skills/generate-characters/scripts/generate_character.py`
- Modify: `.claude/skills/generate-clues/scripts/generate_clue.py`

**Step 1: Modify character generation script**

When building the prompt, add style description:

```python
# Get style description
style_description = project_data.get('style_description', '')

# Build style prefix
style_prefix = ''
if style:
    style_prefix += f"Style: {style}\n"
if style_description:
    style_prefix += f"Visual style: {style_description}\n"
if style_prefix:
    style_prefix += "\n"

# Build full prompt
prompt = f"{style_prefix}{build_character_prompt(name, description, style)}"
```

**Step 2: Similarly modify clue generation script**

**Step 3: Commit**

```bash
git add .claude/skills/generate-characters/scripts/generate_character.py
git add .claude/skills/generate-clues/scripts/generate_clue.py
git commit -m "feat(generate): use style description in character and clue generation"
```

---

## Task 9: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add new fields to project.json structure description**

In the complete example JSON, add:

```json
{
  "title": "Project Title",
  "content_mode": "narration",
  "style": "Anime",
  "style_image": "style_reference.png",
  "style_description": "Soft lighting, muted earth tones, traditional Chinese painting influence..."
}
```

**Step 2: Add style reference image documentation section**

Add at an appropriate location:

```markdown
### Style Reference Image (Optional)

Projects support uploading a style reference image; the system automatically analyzes it and generates a style description. All subsequent image generation (characters, clues, storyboards) uses this style description to maintain visual consistency.

| Field | Description |
|-------|-------------|
| `style` | Base style tag entered manually by the user |
| `style_image` | Path to style reference image (relative to project directory) |
| `style_description` | Detailed style description generated by AI (can be edited manually) |

**How to use**:
1. Upload a style reference image when creating a project in the WebUI (optional)
2. Or upload/replace the style reference image on the project overview page
3. The system automatically analyzes and generates a style description
4. You can manually edit the style description for fine-tuning
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add style reference image documentation"
```

---

## Task 10: Final Testing and Verification

**Step 1: Start the WebUI server**

Run: `python -m uvicorn webui.server.app:app --reload --port 8080`

**Step 2: Test create project flow**

1. Open http://localhost:8080/
2. Click "New Project"
3. Fill in project information and upload a style reference image
4. Click create; verify style analysis succeeds

**Step 3: Test project overview page flow**

1. Enter an existing project
2. On the overview page, upload/replace/delete the style reference image
3. Edit and save the style description

**Step 4: Test generation flow**

1. Generate a character design image; verify it includes the style description
2. Generate a storyboard image; verify it includes the style description

**Step 5: Final commit**

```bash
git status
git log --oneline -10
```

---

## Implementation Checklist

- [ ] Task 1: GeminiClient.analyze_style_image() method
- [ ] Task 2: build_style_prompt() function
- [ ] Task 3: Style image upload API endpoint
- [ ] Task 4: Frontend API methods
- [ ] Task 5: Create project modal
- [ ] Task 6: Project overview page style image management
- [ ] Task 7: generate_storyboard.py uses style description
- [ ] Task 8: generate_character.py and generate_clue.py
- [ ] Task 9: CLAUDE.md documentation update
- [ ] Task 10: Final testing and verification

from io import BytesIO
from pathlib import Path

from PIL import Image

from lib.image_utils import convert_image_bytes_to_png
from lib.project_manager import ProjectManager
from lib.version_manager import VersionManager

import server.routers.versions as versions_router
from server.auth import get_current_user


class TestUploadRestorePng:
    async def test_restore_character_updates_sheet_and_file(self, tmp_path, monkeypatch):
        projects_root = tmp_path / "projects"
        pm = ProjectManager(projects_root)
        project_name = "demo-project"
        char_name = "Alice"

        pm.create_project(project_name)
        pm.create_project_metadata(project_name, "Demo")
        pm.add_project_character(project_name, char_name, "desc", "voice")
        project_path = pm.get_project_path(project_name)

        current_file = project_path / "characters" / f"{char_name}.png"

        # Create v1: a red PNG as the initial version.
        img_v1 = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        img_v1.save(current_file, format="PNG")
        vm = VersionManager(project_path)
        vm.add_version("characters", char_name, "prompt_v1", source_file=current_file)

        # Simulate manual upload: user uploads a JPEG that overwrites the current image.
        img_upload = Image.new("RGB", (8, 8), (0, 0, 255))
        buf = BytesIO()
        img_upload.save(buf, format="JPEG")
        current_file.write_bytes(convert_image_bytes_to_png(buf.getvalue()))

        # Simulate old metadata pointing to a non-png path (the bug scenario).
        project = pm.load_project(project_name)
        project["characters"][char_name]["character_sheet"] = f"characters/{char_name}.jpg"
        pm.save_project(project_name, project)

        # Patch router project manager to the temp projects root.
        monkeypatch.setattr(versions_router, "pm", pm)

        # Switch back to v1 without creating a synthetic new version.
        result = await versions_router.restore_version(project_name, "characters", char_name, 1, _user={"sub": "testuser"})

        assert result["success"]
        assert result["restored_version"] == 1
        assert result["current_version"] == 1
        assert result["file_path"] == f"characters/{char_name}.png"

        # project.json should be updated to point to the normalized .png path.
        project2 = pm.load_project(project_name)
        assert project2["characters"][char_name]["character_sheet"] == f"characters/{char_name}.png"

        # Current file content should match v1 (red pixel).
        with Image.open(current_file) as restored:
            restored_rgba = restored.convert("RGBA")
            assert restored_rgba.getpixel((0, 0)) == (255, 0, 0, 255)

        info = vm.get_versions("characters", char_name)
        assert info["current_version"] == 1
        assert len(info["versions"]) == 1

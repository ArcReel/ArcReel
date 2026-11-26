"""任务执行器与上传收尾各自作为首个导入时，均可加载正式图像提交模块。"""

import pytest

from tests.import_support import assert_first_import


@pytest.mark.parametrize(
    "module_name",
    [
        "server.services.currency.upload_finalize",
        "server.services.tasks.derivative_sheet_tasks",
        "server.services.tasks.formal_image_commit",
        "server.services.tasks.generation_tasks",
        "server.services.tasks.image_edit_tasks",
        "server.services.tasks.reference_video_tasks",
    ],
)
def test_task_module_imports_first_in_fresh_process(module_name: str) -> None:
    assert_first_import(module_name)

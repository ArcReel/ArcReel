"""ComfyUI failure carriers/codes.

One exception type spans construction/execution within one generation, sharing stable ``code`` / serializable
``params`` persistence and read-side rendering. Two types would merely expand worker unions/``call_failure``
classification without any type-dependent branches.

Register ``code`` in ``lib.generation.task_failure`` ``FAILURE_CODE_KEYS`` and ``lib.generation.generation_result``
``_TASK_FAILURE_ACTIONS``, otherwise worker failure encoding degrades to raw text.
"""

from __future__ import annotations

from typing import Any

#: Asset upload failures: addresses/credentials/disks, potentially resolved by repeating requests.
UPLOAD_FAILED = "comfyui_upload_failed"

#: Rejected workflows: 400 or 200 with nonempty ``node_errors``, from missing models/nodes or invalid parameters.
NODE_ERRORS = "comfyui_node_errors"

#: Previously submitted tasks absent from history and both running/pending ``/queue``.
#: Server restarts lose queues/in-flight tasks not yet in history; this is the sole client-visible shape.
JOB_LOST = "comfyui_job_lost"

#: Execution node exceptions, final ``status.messages`` = ``execution_error``.
EXECUTION_ERROR = "comfyui_execution_error"

#: Interrupted execution, final ``status.messages`` = ``execution_interrupted``: someone canceled
#: in ComfyUI or another client sent ``/interrupt``.
INTERRUPTED = "comfyui_interrupted"

#: Terminal outputs without any ``type == "output"`` files from ``output``-bound nodes.
OUTPUT_MISSING = "comfyui_output_missing"

#: Artifacts exist but extensions mismatch endpoint ``media_type``.
OUTPUT_TYPE_MISMATCH = "comfyui_output_type_mismatch"

#: Matching extensions but persisted file headers mismatch ``media_type`` containers.
OUTPUT_CONTAINER_MISMATCH = "comfyui_output_container_mismatch"

#: Cascading removal of reference/first/last-frame loaders reaches artifact nodes during construction (``request_builder``).
IMAGE_DROP_UNSUPPORTED = "comfyui_image_drop_unsupported"


class ComfyuiError(RuntimeError):
    """ComfyUI failures with persistent/localizable stable codes.

    Match declarative ``DeclarativeRuntimeError`` ``code`` + ``params`` shapes, so failure
    encoding/rendering need no separate ComfyUI paths.
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params: dict[str, Any] = params
        super().__init__(code)

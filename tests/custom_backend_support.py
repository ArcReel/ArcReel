"""用生产端点与装配规格创建测试所需的自定义通道。"""

from lib.custom_provider.endpoints import ProviderConnectionSnapshot, get_endpoint_spec
from lib.custom_provider.model_facts import model_entry
from lib.custom_provider.path_overrides import effective_path_overrides
from lib.db.models.custom_provider import CustomProviderModel
from lib.video_runtime.assembly import build_video
from lib.video_runtime.image_assembly import build_image
from lib.video_runtime.image_resolution import custom_image_spec
from lib.video_runtime.resolution import custom_video_spec


def create_custom_backend(
    *,
    provider,
    model_id,
    endpoint,
    capability_overrides=None,
    path_overrides=None,
    endpoint_spec=None,
    entry_binding=None,
):
    spec = endpoint_spec or get_endpoint_spec(endpoint)
    connection = ProviderConnectionSnapshot(
        getattr(provider, "id", None), provider.provider_id, provider.base_url, provider.api_key
    )
    if spec.media_type in ("image", "video"):
        row = CustomProviderModel(
            model_id=model_id, capability_overrides=capability_overrides, path_overrides=path_overrides
        )
        entry = None if spec.kind == "comfyui" else model_entry(model_id, spec.media_type, entry_binding)
        if spec.media_type == "video":
            return build_video(custom_video_spec(connection, row, spec, entry))
        return build_image(custom_image_spec(connection, row, spec, entry))
    return spec.build_backend(connection, model_id, effective_path_overrides(path_overrides, spec.requests))

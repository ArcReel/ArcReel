import json
import math

from server.agent_runtime.failure_observation import build_startup_failure_observation


def test_startup_observation_remains_json_safe_for_unprintable_unknown_values() -> None:
    class UnprintableError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("broken __str__")

    exc = UnprintableError()
    exc.future_payload = {"non_finite_metric": math.nan, "opaque": object()}  # type: ignore[attr-defined]

    observation = build_startup_failure_observation(
        exc,
        project_name="demo",
        session_id=None,
        sdk_stderr="",
    )

    assert observation["summary"]["type"] == "UnprintableError"
    assert "UnprintableError" in observation["summary"]["message"]
    assert observation["raw"]["exception_chain"][0]["attributes"]["future_payload"]["non_finite_metric"] == "nan"
    # FastAPI / DB 边界使用严格 JSON 时也不能再次把原始启动异常遮蔽掉。
    json.dumps(observation, allow_nan=False)

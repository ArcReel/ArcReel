import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "user/runtimes/ltx-2-mlx"


def validate_identity_reference(config):
    if config["mode"] != "identity-reference":
        return
    probe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=width,height,nb_read_frames,r_frame_rate,avg_frame_rate,duration",
                "-of",
                "json",
                str(config["control_video"]),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        stream = json.loads(result.stdout)["streams"][0]
        valid = (
            (int(stream["width"]), int(stream["height"]), int(stream["nb_read_frames"])) == (768, 448, 121)
            and Fraction(stream["r_frame_rate"]) == 24
            and Fraction(stream["avg_frame_rate"]) == 24
            and abs(float(stream["duration"]) - 121 / 24) < 1 / 48
        )
    except (
        OSError,
        subprocess.SubprocessError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ) as error:
        raise ValueError("Cannot verify the Ingredients reference video; no render started.") from error
    if not valid:
        raise ValueError(
            "Ingredients reference must contain 121 frames at 768x448 and 24 fps. "
            "Repeat the reference still across the full clip; the runtime does not extend a short source. "
            "No render started."
        )


def build_command(config, root):
    allowed = {
        "name",
        "mode",
        "width",
        "height",
        "frames",
        "seed",
        "prompt",
        "recipe",
        "image",
        "end_image",
        "control_lora",
        "control_video",
        "distilled_lora",
        "sampler",
        "negative_prompt",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ValueError("Unsupported renderer controls: " + ", ".join(sorted(unknown)))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", config["name"]):
        raise ValueError("Run name must contain only letters, numbers, underscores, and hyphens.")
    if config["mode"] not in ("distilled", "two-stage", "motion-track", "structural-control", "identity-reference"):
        raise ValueError("This adapter supports distilled or two-stage generation, or IC-LoRA qualification.")
    controlled = config["mode"] in ("motion-track", "structural-control", "identity-reference")
    ingredients_distilled = config.get("recipe") == "ingredients-distilled"
    if ingredients_distilled and config["mode"] != "identity-reference":
        raise ValueError("ingredients-distilled requires identity-reference mode.")
    sampler = config.get("sampler", "euler")
    if sampler not in ("euler", "euler_ancestral_cfg_pp"):
        raise ValueError("Unsupported qualification sampler.")
    if ("sampler" in config or "negative_prompt" in config) and not controlled:
        raise ValueError("Explicit sampler controls require IC-LoRA qualification.")
    if "negative_prompt" in config and not isinstance(config["negative_prompt"], str):
        raise ValueError("negative_prompt must be text.")
    if config.get("negative_prompt") and sampler != "euler_ancestral_cfg_pp":
        raise ValueError("negative_prompt requires euler_ancestral_cfg_pp.")
    if controlled:
        if ingredients_distilled:
            if not all(config.get(key) for key in ("control_lora", "control_video")):
                raise ValueError("Ingredients distilled qualification requires control_lora and control_video.")
            if any(config.get(key) for key in ("image", "end_image", "distilled_lora")) or sampler != "euler":
                raise ValueError("Ingredients distilled qualification uses reference-only conditioning and Euler.")
        elif not all(config.get(key) for key in ("control_lora", "control_video", "distilled_lora")):
            raise ValueError("IC-LoRA qualification requires control_lora, control_video and distilled_lora.")
        if config["mode"] == "motion-track" and not config.get("image"):
            raise ValueError("Motion-track qualification requires a starting image.")
        if config["mode"] == "motion-track" and config.get("end_image"):
            raise ValueError("Motion-track qualification cannot use an end anchor.")
        if config["mode"] == "structural-control" and not config.get("image"):
            raise ValueError("Structural-control qualification requires a starting image.")
        if config.get("recipe") and not ingredients_distilled:
            raise ValueError("IC-LoRA qualification cannot use another recipe.")
        if config["mode"] == "identity-reference" and (config["width"], config["height"], config["frames"]) != (
            768,
            448,
            121,
        ):
            raise ValueError("Ingredients qualification requires 121 frames at 768x448.")
        if config["frames"] not in (49, 97, 121) or (config["width"], config["height"]) not in (
            (768, 448),
            (1024, 576),
        ):
            raise ValueError("IC-LoRA qualification supports 49/97/121 frames at 768x448 or 1024x576.")
    elif any(config.get(key) for key in ("control_lora", "control_video", "distilled_lora")):
        raise ValueError("Control inputs require an IC-LoRA qualification mode; they must not be ignored.")
    if not isinstance(config["seed"], int) or isinstance(config["seed"], bool) or not 0 <= config["seed"] < 2**32:
        raise ValueError("Seed must be an unsigned 32-bit integer.")
    if config.get("recipe") == "animated-dialogue" and (
        config["mode"] != "distilled"
        or (config["width"], config["height"]) != (1024, 576)
        or config["frames"] not in (49, 97, 121)
    ):
        raise ValueError("The animated memory preset supports 2, 4, or 5 seconds at 1024 x 576 and 24 fps.")
    if config.get("end_image") and not config.get("image"):
        raise ValueError("An end anchor requires a starting image.")
    for key in ("image", "end_image", "control_lora", "control_video", "distilled_lora"):
        if config.get(key) and not Path(config[key]).is_file():
            raise ValueError(f"Missing {key}: {config[key]}")
    runtime = root / "user/runtimes/ltx-2-mlx"
    model = root / "models/mlx/ltx-2.3-22b-q8-distilled-1.1"
    if ingredients_distilled and not (model / "transformer-distilled-1.1.safetensors").is_file():
        raise ValueError(
            "Ingredients distilled qualification requires installed transformer-distilled-1.1.safetensors."
        )
    if controlled and not ingredients_distilled and not (model / "transformer-dev.safetensors").is_file():
        raise ValueError("IC-LoRA qualification requires installed transformer-dev.safetensors; no render started.")
    keyframe = bool(config.get("end_image")) and not controlled
    if keyframe:
        for filename in ("transformer-dev.safetensors", "transformer-distilled-1.1.safetensors"):
            if not (model / filename).is_file():
                raise ValueError(f"Keyframe interpolation requires installed {filename}; no render started.")
    out = root / "output/quality" / f"{config['name']}.mp4"
    args = [
        str(runtime / ".venv/bin/python"),
        "-u",
        str(root / "user/video_engine_entry.py"),
        *(["ic-lora"] if controlled else ["keyframe"] if keyframe else ["generate", "--" + config["mode"]]),
        "--model",
        str(model),
        "--gemma",
        str(root / "models/mlx/gemma-3-12b-it-4bit"),
        "--prompt",
        config["prompt"],
        "--output",
        str(out),
        "--seed",
        str(config["seed"]),
        "--height",
        str(config["height"]),
        "--width",
        str(config["width"]),
        "--frames",
        str(config["frames"]),
        "--frame-rate",
        "24",
        "--low-ram",
    ]
    if controlled:
        args += [
            "--lora",
            str(config["control_lora"]),
            "1.4" if ingredients_distilled else "1.0",
            "--video-conditioning",
            str(config["control_video"]),
            "1.0",
            "--single-stage",
        ]
        if not ingredients_distilled:
            args += [
                "--dev-transformer",
                "transformer-dev.safetensors",
                "--distilled-lora",
                str(config["distilled_lora"]),
                "--distilled-lora-strength",
                "0.5",
            ]
        if config.get("image"):
            args += ["--image", str(config["image"]), "0", "1.0"]
        if config.get("end_image"):
            args += ["--image", str(config["end_image"]), str(config["frames"] - 1), "1.0"]
        if sampler == "euler_ancestral_cfg_pp":
            args += ["--sampler", sampler, "--negative-prompt", config.get("negative_prompt", "")]
    elif keyframe:
        # The pinned low-RAM runtime swaps to the pre-fused distilled transformer
        # at strength 1.0. The explicit LoRA name enables that refinement path;
        # it does not load a separate LoRA file in this configuration.
        args += [
            "--start",
            str(config["image"]),
            "--end",
            str(config["end_image"]),
            "--dev-transformer",
            "transformer-dev.safetensors",
            "--distilled-lora",
            "ltx-2.3-22b-distilled-lora-384.safetensors",
            "--lora-strength",
            "1.0",
        ]
    elif config.get("image"):
        args += ["--image", str(config["image"]), "0", "1.0"]
    return args


def run(config_path, check_interrupt=None):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    args = build_command(config, ROOT)
    validate_identity_reference(config)
    import psutil

    blocked = ROOT / "user/video-render-retry-blocked.json"
    if blocked.exists() or (ROOT.parent / "WanGP-Trial/.benchmark/retry-blocked.json").exists():
        raise RuntimeError("A prior renderer needs cleanup verification. No new render was started.")
    animated = config.get("recipe") == "animated-dialogue" and not config.get("end_image")
    reserve = 4 if animated else 8
    swap_limit = 2048 if animated else 256
    name = config["name"]
    out = ROOT / "output/quality" / f"{name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise RuntimeError("Output already exists; choose a new run name.")
    # Leave space for the working set, host reserve, and decode margin.
    minimum_start = 14 if animated else 19
    if psutil.virtual_memory().available < minimum_start * 2**30:
        raise RuntimeError(f"At least {minimum_start} GiB available memory is required to start this preset.")
    env = os.environ.copy()
    env.update(
        {
            "HF_HOME": str(ROOT / "user/huggingface"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "LTX2_VAE_DECODE_BUDGET_GB": "6",
            "LTX2_COMPUTE_DTYPE": "bfloat16",
            "LTX2_GEMMA_EVAL_EVERY": "1",
            "LTX2_DIT_EVAL_EVERY": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.pop("AGX_RELAX_CDM_CTXSTORE_TIMEOUT", None)
    logpath = ROOT / "user" / f"{name}.log"
    reportpath = ROOT / "user" / f"{name}-report.json"
    report = {
        "config": config,
        "command": args,
        "pipeline": "keyframe" if args[3] == "keyframe" else config["mode"],
        "output": str(out),
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reserve_gib": reserve,
        "minimum_start_gib": minimum_start,
        "mlx_guideline_gib": 16,
        "decode_budget_gib": 6,
        "samples": [],
    }
    swap_start = psutil.swap_memory().sout
    start = time.monotonic()
    next_check = start
    low_count = 0
    reason = None
    with logpath.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            args, cwd=RUNTIME, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        report["pid"] = process.pid
        print("Rendering", name, "PID", process.pid, flush=True)
        try:
            while process.poll() is None:
                if check_interrupt:
                    check_interrupt()
                now = time.monotonic()
                available = psutil.virtual_memory().available / 2**30
                try:
                    rss = psutil.Process(process.pid).memory_info().rss / 2**30
                except psutil.NoSuchProcess:
                    rss = 0
                sample = {
                    "elapsed": round(now - start, 2),
                    "available_gib": round(available, 3),
                    "process_rss_gib": round(rss, 3),
                    "new_swap_out_mib": round((psutil.swap_memory().sout - swap_start) / 2**20, 2),
                }
                report["samples"].append(sample)
                low_count = low_count + 1 if available < reserve else 0
                if available < 2:
                    reason = "Less than 2 GiB available memory."
                elif low_count >= (10 if animated else 2):
                    reason = f"Available memory stayed below the {reserve} GiB reserve."
                if sample["new_swap_out_mib"] > swap_limit:
                    reason = f"More than {swap_limit} MiB of new system swap writes during the render."
                if now - start > 1800:
                    reason = "Render exceeded the 30 minute test limit."
                if reason:
                    break
                if now >= next_check:
                    t = time.monotonic()
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2) as response:
                            response.read()
                        sample["comfy_api_ms"] = round((time.monotonic() - t) * 1000, 2)
                    except Exception:
                        sample["comfy_api_ms"] = None
                    reportpath.write_text(json.dumps(report, indent=2))
                    print("Progress", sample, flush=True)
                    next_check = now + 20
                time.sleep(1)
        except BaseException:
            reason = "Benchmark interrupted."
            raise
        finally:
            unreaped = False
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    unreaped = True
                    blocked.write_text(
                        json.dumps({"pid": process.pid, "reason": reason or "Renderer did not exit cooperatively"})
                    )
            report["seconds"] = round(time.monotonic() - start, 2)
            report["exit_code"] = process.returncode
            report["status"] = (
                "needs_cleanup"
                if unreaped
                else "cancelled"
                if reason
                else ("completed" if process.returncode == 0 else "failed")
            )
            if reason:
                report["reason"] = reason
            report["min_available_gib"] = min((x["available_gib"] for x in report["samples"]), default=None)
            report["max_process_rss_gib"] = max((x["process_rss_gib"] for x in report["samples"]), default=None)
            reportpath.write_text(json.dumps(report, indent=2))
            print("Result", json.dumps({k: v for k, v in report.items() if k not in ["samples", "config"]}), flush=True)
    if report["status"] == "needs_cleanup":
        raise RuntimeError("Renderer did not exit safely. Further renders are blocked until its cleanup is verified.")
    if report["status"] != "completed":
        raise RuntimeError(reason or ("Video engine failed; see " + str(logpath)))
    return report


if __name__ == "__main__":
    run(sys.argv[1])

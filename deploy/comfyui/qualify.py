"""Compare start-only and start/end conditioning through the real local ComfyUI queue."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.custom_provider.comfyui.request_builder import MediaInputs, build_workflow


def qualify(start, end, output, prompt, seed):
    output.mkdir(parents=True, exist_ok=False)
    definition = json.loads((Path(__file__).parent / "local-ltx-endpoint.json").read_text(encoding="utf-8"))
    report = {"seed": seed, "prompt": prompt, "duration_seconds": 2, "inputs": {}, "takes": []}
    with httpx.Client(base_url="http://127.0.0.1:8188", timeout=30) as client:
        queue = client.get("/queue").raise_for_status().json()
        if queue["queue_running"] or queue["queue_pending"]:
            raise RuntimeError("Local renderer is busy. No qualification renders submitted.")
        uploaded = []
        for path in (start, end):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            report["inputs"][path.name] = {"path": str(path), "sha256": digest}
            with path.open("rb") as stream:
                response = client.post("/upload/image", files={"image": (f"qualification-{digest}.png", stream)})
            entry = response.raise_for_status().json()
            uploaded.append("/".join(filter(None, [entry.get("subfolder"), entry["name"]])))
        for with_end in (False, True):
            label = "start-and-end" if with_end else "start-only"
            built = build_workflow(
                definition,
                prompt=prompt,
                aspect_ratio="16:9",
                duration_seconds=2,
                seed=seed,
                media=MediaInputs(start_image=uploaded[0], end_image=uploaded[1] if with_end else None),
            )
            built.workflow["5"]["inputs"]["filename_prefix"] = f"arcreel/qualification/{label}"
            (output / f"{label}-workflow.json").write_text(json.dumps(built.workflow, indent=2), encoding="utf-8")
            accepted = client.post("/prompt", json={"prompt": built.workflow}).raise_for_status().json()
            if accepted.get("node_errors"):
                raise RuntimeError(f"Workflow rejected: {accepted['node_errors']}")
            prompt_id = accepted["prompt_id"]
            print(f"{label}: queued {prompt_id}", flush=True)
            take = {"label": label, "prompt_id": prompt_id, "status": "running"}
            report["takes"].append(take)
            report_path = output / "report.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            began = time.monotonic()
            last_notice = began
            while time.monotonic() - began < 1800:
                history = client.get(f"/history/{prompt_id}").raise_for_status().json().get(prompt_id)
                if history:
                    (output / f"{label}-history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
                    take["elapsed_seconds"] = round(time.monotonic() - began, 2)
                    take["status"] = history["status"]["status_str"]
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    if take["status"] != "success":
                        raise RuntimeError(f"Local {label} render failed; inspect its saved history.")
                    artifacts = history["outputs"]["5"]
                    media = (artifacts.get("images") or artifacts.get("gifs"))[0]
                    data = client.get("/view", params={k: media[k] for k in ("filename", "subfolder", "type")})
                    data.raise_for_status()
                    destination = output / f"{label}.mp4"
                    destination.write_bytes(data.content)
                    take["file"] = destination.name
                    take["sha256"] = hashlib.sha256(data.content).hexdigest()
                    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print(f"{label}: saved in {take['elapsed_seconds']} seconds", flush=True)
                    break
                if time.monotonic() - last_notice >= 30:
                    print(f"{label}: rendering, {round(time.monotonic() - began)} seconds elapsed", flush=True)
                    last_notice = time.monotonic()
                time.sleep(2)
            else:
                raise TimeoutError("Qualification timed out; inspect the queue before resubmitting.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--end", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--seed", type=int, default=2026102442)
    args = parser.parse_args()
    qualify(args.start.resolve(), args.end.resolve(), args.output.resolve(), args.prompt, args.seed)

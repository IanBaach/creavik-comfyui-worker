"""
Creavik ComfyUI RunPod serverless handler.

Dispatch contract matches backend/app/services/video_providers/comfyui.py's
generate(): job["input"] = {workflow_type, prompt, negative_prompt,
workflow_params, reference_image_url?, reference_video_url?, audio_url?}.

Loads the matching workflow JSON template from /workflow_templates/<type>.json,
substitutes {{var}} placeholders from workflow_params + top-level fields,
submits to the local ComfyUI instance, polls for completion, and returns the
first output image as a base64 data URL under "output_url" -- matching what
comfyui.py's _extract_output_url() already checks for with no code changes.
"""

import base64
import json
import os
import random
import re
import time

import requests
import runpod

COMFY_HOST = "127.0.0.1:8188"
TEMPLATES_DIR = "/workflow_templates"

_PLACEHOLDER_RE = re.compile(r"^\{\{(\w+)\}\}$")


def _load_template(workflow_type: str) -> dict:
    path = os.path.join(TEMPLATES_DIR, f"{workflow_type}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_vars(job_input: dict) -> dict:
    v = dict(job_input.get("workflow_params") or {})
    v["prompt"] = job_input.get("prompt", "")
    v["negative_prompt"] = job_input.get("negative_prompt", "")

    if not v.get("seed") or v.get("seed") == "random":
        v["seed"] = random.randint(0, 2**31 - 1)

    # workflow JSON templates use {{num_inference_steps}}; comfyui.py's
    # workflow_params dict uses "steps" -- alias so either name resolves.
    if "num_inference_steps" not in v and "steps" in v:
        v["num_inference_steps"] = v["steps"]
    if "steps" not in v and "num_inference_steps" in v:
        v["steps"] = v["num_inference_steps"]

    for key in ("reference_image_url", "reference_video_url", "audio_url"):
        if job_input.get(key):
            v[key] = job_input[key]

    return v


def _substitute(obj, v: dict):
    if isinstance(obj, dict):
        return {k: _substitute(val, v) for k, val in obj.items() if k != "_meta"}
    if isinstance(obj, list):
        return [_substitute(item, v) for item in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER_RE.match(obj)
        if m:
            key = m.group(1)
            if key not in v:
                raise ValueError(f"missing template var: {key}")
            return v[key]
        return obj
    return obj


def _wait_for_comfy(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://{COMFY_HOST}/system_stats", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def _submit(workflow: dict) -> str:
    r = requests.post(f"http://{COMFY_HOST}/prompt", json={"prompt": workflow}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def _poll_history(prompt_id: str, timeout: int = 280) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=15)
        if r.status_code == 200:
            data = r.json()
            if prompt_id in data:
                return data[prompt_id]
        time.sleep(2)
    raise TimeoutError(f"ComfyUI job {prompt_id} did not finish in {timeout}s")


def _extract_image(history: dict) -> dict:
    outputs = history.get("outputs", {})
    for node_output in outputs.values():
        for img in node_output.get("images", []) or []:
            return img
    raise RuntimeError(f"no image output found in history: {json.dumps(history)[:500]}")


def _fetch_image_bytes(img: dict) -> bytes:
    params = {
        "filename": img["filename"],
        "subfolder": img.get("subfolder", ""),
        "type": img.get("type", "output"),
    }
    r = requests.get(f"http://{COMFY_HOST}/view", params=params, timeout=60)
    r.raise_for_status()
    return r.content


def handler(job: dict) -> dict:
    job_input = job.get("input", {})
    workflow_type = job_input.get("workflow_type")
    if not workflow_type:
        return {"error": "workflow_type is required"}

    try:
        template = _load_template(workflow_type)
    except FileNotFoundError:
        return {"error": f"unknown workflow_type: {workflow_type}"}

    if not _wait_for_comfy():
        return {"error": "ComfyUI did not become ready in time"}

    vars_ = _build_vars(job_input)
    try:
        workflow = _substitute(template, vars_)
    except ValueError as e:
        return {"error": str(e)}

    try:
        prompt_id = _submit(workflow)
        history = _poll_history(prompt_id)
        img = _extract_image(history)
        image_bytes = _fetch_image_bytes(img)
    except Exception as e:  # noqa: BLE001 -- surfaced to caller as job error, not a crash
        return {"error": f"ComfyUI execution failed: {e}"}

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    fmt = img["filename"].rsplit(".", 1)[-1].lower()
    mime = "image/png" if fmt == "png" else "image/jpeg"

    return {
        "output_url": f"data:{mime};base64,{b64}",
        "filename": img["filename"],
    }


runpod.serverless.start({"handler": handler})

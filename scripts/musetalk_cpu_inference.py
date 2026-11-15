"""Child process for one CPU-only MuseTalk core preview; no detector models."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def load_core(root, relative, name):
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(work, portrait, audio, output, recipe):
    if (sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("NEWS_CASTER_PUBLIC_REPOSITORY") != "true"):
        raise RuntimeError("MuseTalk inference is restricted to the public Linux CPU preview")
    import cv2
    import numpy as np
    from PIL import Image
    import torch
    from transformers import WhisperModel

    if torch.cuda.is_available() or torch.backends.mps.is_available():
        raise RuntimeError("GPU inference is forbidden")
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(recipe["seed"])
    core = work / "MuseTalk"
    vae_module = load_core(core, "musetalk/models/vae.py", "preview_vae")
    unet_module = load_core(core, "musetalk/models/unet.py", "preview_unet")
    audio_module = load_core(core, "musetalk/utils/audio_processor.py", "preview_audio")
    vae = vae_module.VAE(str(work / "models/sd-vae"), use_float16=False)
    unet = unet_module.UNet(str(work / "models/musetalkV15/musetalk.json"),
                            str(work / "models/musetalkV15/unet.pth"), device=torch.device("cpu"))
    unet.model.float().eval().requires_grad_(False)
    vae.vae.float().eval().requires_grad_(False)
    pe = unet_module.PositionalEncoding(d_model=384).float().eval()
    processor = audio_module.AudioProcessor(str(work / "models/whisper"))
    whisper = WhisperModel.from_pretrained(str(work / "models/whisper"), local_files_only=True)
    whisper.float().eval().requires_grad_(False)
    frame = cv2.imread(str(portrait))
    if frame is None or list(frame.shape[:2][::-1]) != recipe["source_dimensions"]:
        raise ValueError("Preview source dimensions changed")
    x1, y1, x2, y2 = recipe["face_box"]
    cv2.imwrite(str(output / "source-face-crop.png"), frame[y1:y2, x1:x2])
    face = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(output / "model-face-input.png"), face)
    width, height = x2 - x1, y2 - y1
    mask = np.zeros((height, width), np.float32)
    center, radii = recipe["mask"]["center"], recipe["mask"]["radii"]
    cv2.ellipse(mask, (round(center[0] * width), round(center[1] * height)),
                (round(radii[0] * width), round(radii[1] * height)), 0, 0, 360, 1, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), recipe["mask"]["blur_sigma_px"])[..., None]
    # Keep the fist beside the cheek outside the blend, including the blur.
    right_fade = np.clip((recipe["mask"]["right_edge"] - np.arange(width) / width)
                         / recipe["mask"]["right_feather"], 0, 1)
    mask *= right_fade[None, :, None]
    Image.fromarray((mask[..., 0] * 255).astype("uint8")).save(output / "mouth-mask.png")
    frames = work / "frames"
    frames.mkdir()
    started = time.monotonic()
    reference = None
    maximum_change = 0.0
    changed_frames = 0
    with torch.inference_mode():
        features, length = processor.get_audio_feature(str(audio))
        chunks = processor.get_whisper_chunk(features, torch.device("cpu"), torch.float32,
                                             whisper, length, fps=recipe["fps"])
        if len(chunks) != recipe["frames"]:
            raise ValueError("Audio must produce exactly thirty frames")
        latents = vae.get_latents_for_unet(face).float()
        timestep = torch.tensor([0], device="cpu")
        for index, chunk in enumerate(chunks):
            # The pinned audio processor yields (sequence, 384) per frame;
            # PositionalEncoding and the UNet require (batch, sequence, 384).
            if chunk.ndim != 2 or chunk.shape[-1] != 384:
                raise ValueError("Unexpected Whisper frame feature dimensions")
            conditioning = pe(chunk.float().unsqueeze(0))
            predicted = unet.model(latents, timestep,
                                   encoder_hidden_states=conditioning).sample
            generated = vae.decode_latents(predicted)[0]
            generated = cv2.resize(generated, (width, height), interpolation=cv2.INTER_LANCZOS4)
            result = frame.copy()
            result[y1:y2, x1:x2] = np.clip(generated * mask + frame[y1:y2, x1:x2] * (1 - mask),
                                          0, 255).astype("uint8")
            mouth = result[y1:y2, x1:x2][mask[..., 0] > 0.1].astype(np.float32)
            if reference is None:
                reference = mouth.copy()
            change = float(np.abs(mouth - reference).mean())
            maximum_change = max(maximum_change, change)
            changed_frames += int(change > 0.25)
            cv2.imwrite(str(frames / f"{index:03d}.png"), result)
            if index == 0:
                cv2.imwrite(str(output / "first-frame.jpg"), cv2.resize(result, (640, 426)))
            (output / "progress.json").write_text(json.dumps({"generated_frames": index + 1,
                "target_frames": recipe["frames"], "elapsed_seconds": round(time.monotonic() - started, 3)}))
            print(f"MuseTalk CPU frame {index + 1}/{recipe['frames']}", flush=True)
    (output / "motion-check.json").write_text(json.dumps({"generated_frames": len(chunks),
        "changed_mouth_frames": changed_frames, "maximum_mouth_pixel_change": maximum_change,
        "meaning": "Pixel variation only; actual identity and lip-sync still require review."}))
    if changed_frames < 2:
        raise RuntimeError("Generated mouth region is effectively static")


if __name__ == "__main__":
    work, portrait, audio, output, recipe = map(Path, sys.argv[1:])
    run(work, portrait, audio, output, json.loads(recipe.read_text()))

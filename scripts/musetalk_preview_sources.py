"""Pinned core-only MuseTalk preview inputs; importing this performs no I/O.

Small source/configuration files were read directly. Large checkpoint sizes and
SHA256 values were read from the official revision-specific Hugging Face LFS
metadata, without downloading checkpoints. Paths are relative to the work dir.
"""
from __future__ import annotations

import copy


SOURCE_REVISION = "0a89dec45a0192b824e3cf4daf96c239440c5ed8"
MUSETALK_REVISION = "2bcb936e2fddb4d86db4c62fd45b387d0c061571"
VAE_REVISION = "31f26fdeee1355a5c34592e401dd41e45d25a493"
WHISPER_REVISION = "169d4a4341b33bc18d8881c4b69c2e104e1cc0af"
SOURCE_BASE = "https://raw.githubusercontent.com/TMElyralab/MuseTalk/" + SOURCE_REVISION + "/"
MUSETALK_BASE = "https://huggingface.co/TMElyralab/MuseTalk/resolve/" + MUSETALK_REVISION + "/"
VAE_BASE = "https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/" + VAE_REVISION + "/"
WHISPER_BASE = "https://huggingface.co/openai/whisper-tiny/resolve/" + WHISPER_REVISION + "/"

SRC_FILES = (
    {"path": "MuseTalk/musetalk/models/vae.py", "url": SOURCE_BASE + "musetalk/models/vae.py",
     "bytes": 5419, "sha256": "6ecde3f4417178d8a9de5ed6045e2a4832efa65189b10b0672cab68efe529d32", "license": "MIT"},
    {"path": "MuseTalk/musetalk/models/unet.py", "url": SOURCE_BASE + "musetalk/models/unet.py",
     "bytes": 1686, "sha256": "05d5dadfde2e726b61d8387a4876336d4dcacc696636e8dcfec69863ce819256", "license": "MIT"},
    {"path": "MuseTalk/musetalk/utils/audio_processor.py", "url": SOURCE_BASE + "musetalk/utils/audio_processor.py",
     "bytes": 4227, "sha256": "b7b62f3daa99678576138bf581a6394a10d346571a2530a760d1299ffd622b81", "license": "MIT"},
    {"path": "MuseTalk/LICENSE", "url": SOURCE_BASE + "LICENSE",
     "bytes": 13427, "sha256": "992ec5fd1dd4964cfa003665196cd0c0c10a7a5aa10109991e964eebd2c7f116", "license": "MIT"},
)

WEIGHTS = (
    {"path": "models/musetalkV15/unet.pth", "url": MUSETALK_BASE + "musetalkV15/unet.pth",
     "bytes": 3400074924, "sha256": "7ebf6c98c181e20838e4c0054e96e944ac60d5d692cc01db42839fe11b787007",
     "license": "MIT", "license_url": MUSETALK_BASE + "LICENSE.txt", "revision": MUSETALK_REVISION},
    {"path": "models/musetalkV15/musetalk.json", "url": MUSETALK_BASE + "musetalkV15/musetalk.json",
     "bytes": 748, "sha256": "5b6923aee04d71692e0e9846c471e0a4ea07a4f686d39545e472bd4ba17e1b47",
     "license": "MIT", "license_url": MUSETALK_BASE + "LICENSE.txt", "revision": MUSETALK_REVISION},
    {"path": "models/sd-vae/config.json", "url": VAE_BASE + "config.json",
     "bytes": 547, "sha256": "92d3dfb746fca211a2c9e019e285f8597412211728dce3c5bcf4eda0f2d62e7e",
     "license": "MIT", "license_url": VAE_BASE + "README.md", "revision": VAE_REVISION},
    {"path": "models/sd-vae/diffusion_pytorch_model.bin", "url": VAE_BASE + "diffusion_pytorch_model.bin",
     "bytes": 334707217, "sha256": "1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc",
     "license": "MIT", "license_url": VAE_BASE + "README.md", "revision": VAE_REVISION},
    {"path": "models/whisper/config.json", "url": WHISPER_BASE + "config.json",
     "bytes": 1983, "sha256": "ffdccec4f3211f4c63310f2b7098f309fe70f3952cedc5e4d11e43f5b2379b98",
     "license": "Apache-2.0", "license_url": WHISPER_BASE + "README.md", "revision": WHISPER_REVISION},
    {"path": "models/whisper/pytorch_model.bin", "url": WHISPER_BASE + "pytorch_model.bin",
     "bytes": 151095027, "sha256": "9607f98a2b22d9e229ae43c52ecea79dcede9e0c5cfae67e8da6eda86d8aac1d",
     "license": "Apache-2.0", "license_url": WHISPER_BASE + "README.md", "revision": WHISPER_REVISION},
    {"path": "models/whisper/preprocessor_config.json", "url": WHISPER_BASE + "preprocessor_config.json",
     "bytes": 184990, "sha256": "9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666",
     "license": "Apache-2.0", "license_url": WHISPER_BASE + "README.md", "revision": WHISPER_REVISION},
)

MODEL_LICENSES = {
    "musetalk": {
        "license": "MIT", "revision": MUSETALK_REVISION,
        "license_url": MUSETALK_BASE + "LICENSE.txt",
        "license_sha256": "992ec5fd1dd4964cfa003665196cd0c0c10a7a5aa10109991e964eebd2c7f116",
        "model_card_url": MUSETALK_BASE + "README.md",
        "model_card_sha256": "1e005ec9c3bfc7313714e714abea62205098299139db5cccb884a19a46e2a579",
        "code_revision": SOURCE_REVISION, "code_license_url": SOURCE_BASE + "LICENSE",
        "note": "The pinned official card permits academic and commercial use of its code and trained model. "
                "Other models keep their own licenses. Its non-commercial internet testdata are excluded.",
    },
    "sd-vae-ft-mse": {
        "license": "MIT", "revision": VAE_REVISION,
        "license_url": VAE_BASE + "README.md",
        "model_card_url": VAE_BASE + "README.md",
        "model_card_sha256": "8ac69483c18a45d0fee531e08b6ede97ee9b4c79e875659c1b0e6a5903ca3400",
        "note": "The fixed official model card labels these autoencoder files MIT.",
    },
    "whisper-tiny": {
        "license": "Apache-2.0", "revision": WHISPER_REVISION,
        "license_url": WHISPER_BASE + "README.md",
        "model_card_url": WHISPER_BASE + "README.md",
        "model_card_sha256": "57a3bbbbf1e79369e4d1ced790812a1723b43a256c1b43fc70cc2f3339ae9881",
        "note": "Use Apache-2.0 from this pinned Hugging Face model card. "
                "MuseTalk's bundled license separately describes upstream OpenAI Whisper as MIT; "
                "that upstream label is not substituted for this selected artifact's model card.",
    },
}


def input_manifest():
    """Expose the exact work-relative allowlist and byte budget, offline only."""
    return {"source_files": copy.deepcopy(SRC_FILES), "weights": copy.deepcopy(WEIGHTS),
            "model_licenses": copy.deepcopy(MODEL_LICENSES),
            "file_count": len(SRC_FILES) + len(WEIGHTS),
            "source_bytes": sum(record["bytes"] for record in SRC_FILES),
            "weight_bytes": sum(record["bytes"] for record in WEIGHTS),
            "total_bytes": sum(record["bytes"] for record in (*SRC_FILES, *WEIGHTS))}

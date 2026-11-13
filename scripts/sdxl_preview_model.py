"""Pinned data-only SDXL preview profile; importing never downloads or loads weights.

Weight digests and sizes are the official Hugging Face LFS metadata at REVISION.
Small-file digests cover the exact response bytes, each bounded to 2 MiB. The
allowlist excludes full checkpoints, fp32 copies, ONNX/Flax exports and refiner.
The existing pinned safety checker is retained as a separate component.
"""

REPO = 'stabilityai/stable-diffusion-xl-base-1.0'
MODEL = REPO
REVISION = '462165984030d82259a11f4367a4eed129e94a7b'
SOURCE_METADATA_URL = 'https://huggingface.co/api/models/stabilityai/stable-diffusion-xl-base-1.0/revision/462165984030d82259a11f4367a4eed129e94a7b?blobs=true'
LICENSE_NAME = "CreativeML Open RAIL++-M (July 26, 2023)"
LICENSE_URL = 'https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/LICENSE.md'
LICENSE_SHA256 = '19b6998b569b53ac1fc2158a8a3202c8699a9a4605b47075715d9c96be7fb6d0'
# Reviewed sections II.2 and III.5-6 / Attachment A: no-charge, royalty-free;
# no licensor claim to outputs, subject to use restrictions. Ordinary fictional
# adult fashion is not prohibited. The card's intended scope is research,
# including art/design; this is not an unrestricted commercial-use assertion.
# Keep unlawful uses, minor exploitation/harm, harmful falsehoods, personal-data
# harm and discrimination excluded. The caller still reviews every preview.
SMALL_FILE_MAX_BYTES = 2_097_152
MODEL_BYTES = 6_938_011_430
TOTAL_DOWNLOAD_BYTES = 6_941_210_313

FILE_SPECS = {'LICENSE.md': {'bytes': 14109,
                'sha256': '19b6998b569b53ac1fc2158a8a3202c8699a9a4605b47075715d9c96be7fb6d0'},
 'README.md': {'bytes': 8668,
               'sha256': '40d263065b8a3e9a41996c257e11463fd97ae956ebf4e3f8b9625e0d78183893'},
 'model_index.json': {'bytes': 609,
                      'sha256': '6d7b93508390ab91ac5bfbe4aeb4dc2d83f7bb1b05fb069d714b5b0c75f70d44'},
 'scheduler/scheduler_config.json': {'bytes': 479,
                                     'sha256': 'af3e45a949aff8b8341ab8b811429ec03fee857a700a1d9477363e4fff9666e2'},
 'text_encoder/config.json': {'bytes': 565,
                              'sha256': '39b8b2e4b1949e36969caa425b6c81c68bace99198dd9078ce05d16ad401fe7f'},
 'text_encoder/model.fp16.safetensors': {'bytes': 246144152,
                                         'sha256': '660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd'},
 'text_encoder_2/config.json': {'bytes': 575,
                                'sha256': 'a892d1c3a69a7e9247a24de2bc1d5891e3109a54696e53be20093af671072c34'},
 'text_encoder_2/model.fp16.safetensors': {'bytes': 1389382176,
                                           'sha256': 'ec310df2af79c318e24d20511b601a591ca8cd4f1fce1d8dff822a356bcdb1f4'},
 'tokenizer/merges.txt': {'bytes': 524619,
                          'sha256': '9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a'},
 'tokenizer/special_tokens_map.json': {'bytes': 472,
                                       'sha256': 'c4864a9376a8401918425bed71fc14fc0e81f9b59ec45c1cf96cccb2df508eac'},
 'tokenizer/tokenizer_config.json': {'bytes': 737,
                                     'sha256': '19d7b034cb0cc3ce9766c2231373ab8aa8991fc72e2c8f76558bfaae3de0d563'},
 'tokenizer/vocab.json': {'bytes': 1059962,
                          'sha256': 'e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349'},
 'tokenizer_2/merges.txt': {'bytes': 524619,
                            'sha256': '9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a'},
 'tokenizer_2/special_tokens_map.json': {'bytes': 460,
                                         'sha256': 'f118ab3a983206e4f32583448de6bd6aae4ee21869135cef1f5848a753cdaab6'},
 'tokenizer_2/tokenizer_config.json': {'bytes': 725,
                                       'sha256': 'c9d23941f76a41cbd50eda9290f57be7828f0a7a677939e9ef181f7e12bd1bdf'},
 'tokenizer_2/vocab.json': {'bytes': 1059962,
                            'sha256': 'e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349'},
 'unet/config.json': {'bytes': 1680,
                      'sha256': '30ebc70750223e59006f7f2b4e1e6c102570aa19a9c4ae3e1fbe7591332dbae6'},
 'unet/diffusion_pytorch_model.fp16.safetensors': {'bytes': 5135149760,
                                                   'sha256': '83e012a805b84c7ca28e5646747c90a243c65c8ba4f070e2d7ddc9d74661e139'},
 'vae/config.json': {'bytes': 642,
                     'sha256': '0b331c8ac22ded5f9997a144a575c1d113d6169aff262c353f39015bd24a6264'},
 'vae/diffusion_pytorch_model.fp16.safetensors': {'bytes': 167335342,
                                                  'sha256': 'bcb60880a46b63dea58e9bc591abe15f8350bde47b405f9c38f4be70c6161e68'}}

SAFETY_MODEL = 'SimianLuo/LCM_Dreamshaper_v7'
SAFETY_REVISION = 'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5'
SAFETY_BYTES = 1_215_980_908
SAFETY_FILE_SPECS = {'safety_checker/config.json': {'bytes': 726,
                                'sha256': '7a6c4890cfa31b748bc6b38e25f812099070cf9baf47926ed6eb887b0dd355e5'},
 'safety_checker/model.safetensors': {'bytes': 1215979664,
                                      'sha256': 'fb351a5ded815c3ff744968ad9c6b218d071b9d313d04f35e813b84b4c0ffde8'},
 'feature_extractor/preprocessor_config.json': {'bytes': 518,
                                                'sha256': '4db495644e3e5bd8fcac52f70e7fc0b413c911086021acf73ac30e5911166e95'}}

INFERENCE = {'base_model': 'stabilityai/stable-diffusion-xl-base-1.0',
 'base_revision': '462165984030d82259a11f4367a4eed129e94a7b',
 'pipeline': 'StableDiffusionXLPipeline',
 'device': 'cpu',
 'dtype': 'mixed',
 'load_dtype': 'bfloat16',
 'text_encoder_dtype': 'bfloat16',
 'unet_compute_dtype': 'float32',
 'vae_decode_dtype': 'float32',
 'text_encoders_released_before_unet_upcast': True,
 'unet_released_before_vae_decode': True,
 'variant': 'fp16',
 'scheduler': 'DPMSolverMultistepScheduler',
 'algorithm_type': 'dpmsolver++',
 'solver_order': 2,
 'solver_type': 'midpoint',
 'use_karras_sigmas': True,
 'width': 1024,
 'height': 576,
 'num_inference_steps': 20,
 'guidance_scale': 6.0,
 'prompt_version': 1,
 'publication': 'preview_only',
 'refiner': False,
 'safety_model': 'SimianLuo/LCM_Dreamshaper_v7',
 'safety_revision': 'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5'}

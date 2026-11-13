"""Pinned data-only SDXL-family preview; importing never downloads or loads weights.

Weight digests/sizes are official Hugging Face LFS metadata at REVISION. Small
file digests cover exact response bytes, each bounded to 2 MiB. The allowlist
excludes monolithic checkpoints, fp32 copies, ONNX/Flax exports and refiner.
The existing pinned safety checker remains a separate component.
"""

REPO = 'RunDiffusion/Juggernaut-XL-v9'
MODEL = REPO
REVISION = 'cf419233522daa0b9ea36c3aff98fa2cab1fb0fb'
SOURCE_METADATA_URL = 'https://huggingface.co/api/models/RunDiffusion/Juggernaut-XL-v9/revision/cf419233522daa0b9ea36c3aff98fa2cab1fb0fb?blobs=true'
MODEL_CARD_URL = 'https://huggingface.co/RunDiffusion/Juggernaut-XL-v9/blob/cf419233522daa0b9ea36c3aff98fa2cab1fb0fb/README.md'
MODEL_CARD_SHA256 = '4c6b45288fcc4b08739d7a2eb12e930e1d91ad1ed485c841007c98b83d01c803'
# The model card adds its paid-API condition to the canonical license below.
LICENSE_NAME = "CreativeML Open RAIL-M + Juggernaut model-card conditions"
LICENSE_URL = MODEL_CARD_URL
LICENSE_SHA256 = MODEL_CARD_SHA256
LICENSE_TEXT_REPO = 'CompVis/stable-diffusion-license'
LICENSE_TEXT_REVISION = '14d42d09bffd871b1666a084fc954a50cff72ac0'
LICENSE_TEXT_PATH = "license.txt"
LICENSE_TEXT_URL = 'https://huggingface.co/spaces/CompVis/stable-diffusion-license/resolve/14d42d09bffd871b1666a084fc954a50cff72ac0/license.txt'
LICENSE_TEXT_BYTES = 14_385
LICENSE_TEXT_SHA256 = 'be351ebe7ac01bcdbb018639aadcfd38f136b7dc3f2a3d4d3a24db51d1b210ef'
LICENSE_TEXT_FILE_SPEC = {'bytes': 14385, 'sha256': 'be351ebe7ac01bcdbb018639aadcfd38f136b7dc3f2a3d4d3a24db51d1b210ef'}
# Scope reviewed: one free evaluation JPEG for a fictional newspaper, using a
# wholly fictional adult in ordinary swimwear/fashion. No paid API is offered,
# no new service contract is opened, and no model weights are redistributed.
# The model card permits personal/creative work and separately requires a
# license for paid-API deployment. Canonical sections 5-6 / Attachment A retain
# lawful-use restrictions; the licensor claims no rights to compliant outputs.
# This records this creative scope, not unconditional commercial permission.
# Author recommends 30-40 steps; the fixed 20-step trial has unverified quality.
RECOMMENDED_STEPS = (30, 40)
SMALL_FILE_MAX_BYTES = 2_097_152
MODEL_BYTES = 6_938_011_430
TOTAL_DOWNLOAD_BYTES = 6_941_198_427

FILE_SPECS = {'README.md': {'bytes': 10536,
               'sha256': '4c6b45288fcc4b08739d7a2eb12e930e1d91ad1ed485c841007c98b83d01c803'},
 'model_index.json': {'bytes': 667,
                      'sha256': '153b1da39e7cc780003d5b25eb8d07b9c84b08cc20fd05b95652e780a15c8011'},
 'scheduler/scheduler_config.json': {'bytes': 496,
                                     'sha256': 'db6b4739b5e23285a9552ec7c59de7ce40f6a4a3562fc1eccbd843f6444e5102'},
 'text_encoder/config.json': {'bytes': 565,
                              'sha256': '47ced7a4ab2ecec78818ac4e8e1578c66253c2b93861c67c0b6d6c33ce3ff829'},
 'text_encoder/model.fp16.safetensors': {'bytes': 246144152,
                                         'sha256': '698656aa4f452e1f8f38287240054a9b8d339b3051ec17d77d63bdb5605db01a'},
 'text_encoder_2/config.json': {'bytes': 575,
                                'sha256': 'd9f7e7eb1e2d162908ecf7925234425c4f1c978152fd60e18e4af2c67ccebb19'},
 'text_encoder_2/model.fp16.safetensors': {'bytes': 1389382176,
                                           'sha256': '9962c194f50a9c580ebe573f2a4756d76dcd29abe15bffb0a5905509eb6a8dda'},
 'tokenizer/merges.txt': {'bytes': 524619,
                          'sha256': '9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a'},
 'tokenizer/special_tokens_map.json': {'bytes': 588,
                                       'sha256': '2cdb3b8331a60c92fc1e55a13e9fd61fd2293c5a51275fdcccd62b780052530e'},
 'tokenizer/tokenizer_config.json': {'bytes': 705,
                                     'sha256': '6bdcee9ccce2a16ca2b4c0c5ed00b42c50ea225f4472a8c4c1e963a2902c2881'},
 'tokenizer/vocab.json': {'bytes': 1059962,
                          'sha256': 'e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349'},
 'tokenizer_2/merges.txt': {'bytes': 524619,
                            'sha256': '9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a'},
 'tokenizer_2/special_tokens_map.json': {'bytes': 462,
                                         'sha256': 'c934044566ac411c4739e9cec969e061e8dcdf2bd108196a66ce917376d4d68e'},
 'tokenizer_2/tokenizer_config.json': {'bytes': 856,
                                       'sha256': 'e70eb3e2b85a508e32bb81c32c13fc3f2b015548ca469b94627c2f32e445cba1'},
 'tokenizer_2/vocab.json': {'bytes': 1059962,
                            'sha256': 'e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349'},
 'unet/config.json': {'bytes': 1778,
                      'sha256': '73cd3f7bd618835b6d09eefd5155b84ccdeea07c32a4d201574af19bd405a5bc'},
 'unet/diffusion_pytorch_model.fp16.safetensors': {'bytes': 5135149760,
                                                   'sha256': 'cf1ee18eb36712683f50c1e674634875e2adf7413d7492d5f9aa7e69e1a8c17a'},
 'vae/config.json': {'bytes': 607,
                     'sha256': 'bcbed358bb80da88bc8b61785b7d14c39b5be5745d813e4c6a56e9084d4dc9d2'},
 'vae/diffusion_pytorch_model.fp16.safetensors': {'bytes': 167335342,
                                                  'sha256': '6353737672c94b96174cb590f711eac6edf2fcce5b6e91aa9d73c5adc589ee48'}}

SAFETY_MODEL = 'SimianLuo/LCM_Dreamshaper_v7'
SAFETY_REVISION = 'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5'
SAFETY_BYTES = 1_215_980_908
SAFETY_FILE_SPECS = {'safety_checker/config.json': {'bytes': 726,
                                'sha256': '7a6c4890cfa31b748bc6b38e25f812099070cf9baf47926ed6eb887b0dd355e5'},
 'safety_checker/model.safetensors': {'bytes': 1215979664,
                                      'sha256': 'fb351a5ded815c3ff744968ad9c6b218d071b9d313d04f35e813b84b4c0ffde8'},
 'feature_extractor/preprocessor_config.json': {'bytes': 518,
                                                'sha256': '4db495644e3e5bd8fcac52f70e7fc0b413c911086021acf73ac30e5911166e95'}}

INFERENCE = {'base_model': 'RunDiffusion/Juggernaut-XL-v9',
 'base_revision': 'cf419233522daa0b9ea36c3aff98fa2cab1fb0fb',
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

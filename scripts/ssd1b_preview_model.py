"""Pinned data-only SSD-1B CPU study; import performs no download/inference.

F32 weight sizes/digests come from official Hugging Face LFS metadata, not a
local weight download/hash. Each small file was read at REVISION and hashed
within 2 MiB. Only component F32 safetensors are allowed: no monolithic,
fp16/BF16 variant, refiner, or model fallback. Initial F32 weights total about
8.32 GiB before framework/activation/load overhead; real CPU peak RSS and time
remain unmeasured. Encoder release before denoising and UNet release before
VAE decode reduce later peaks, not the initial component load.
"""

REPO = 'segmind/SSD-1B'

MODEL = 'segmind/SSD-1B'

REVISION = '60987f37e94cd59c36b1cba832b9f97b57395a10'

SOURCE_METADATA_URL = 'https://huggingface.co/api/models/segmind/SSD-1B/revision/60987f37e94cd59c36b1cba832b9f97b57395a10?blobs=true'

MODEL_CARD_URL = 'https://huggingface.co/segmind/SSD-1B/blob/60987f37e94cd59c36b1cba832b9f97b57395a10/README.md'

MODEL_CARD_SHA256 = '3a87428a74ab83b664916e7d3242beb970951e4ca26296924c60191a6592636a'

LICENSE_NAME = 'Apache-2.0'

LICENSE_URL = 'https://github.com/segmind/SSD-1B/blob/2bfc35f41f21531c61e3d3470bca907fd839d035/LICENSE'

LICENSE_SHA256 = 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'

LICENSE_TEXT_REPO = 'segmind/SSD-1B'

LICENSE_TEXT_REVISION = '2bfc35f41f21531c61e3d3470bca907fd839d035'

LICENSE_TEXT_PATH = 'LICENSE'

LICENSE_TEXT_URL = 'https://raw.githubusercontent.com/segmind/SSD-1B/2bfc35f41f21531c61e3d3470bca907fd839d035/LICENSE'

LICENSE_TEXT_BYTES = 11357

LICENSE_TEXT_SHA256 = 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'

LICENSE_TEXT_FILE_SPEC = {'bytes': 11357, 'sha256': 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'}

SMALL_FILE_MAX_BYTES = 2097152

MODEL_BYTES = 8931071884

TOTAL_DOWNLOAD_BYTES = 8934258763

FILE_SPECS = {'README.md': {'bytes': 10772, 'sha256': '3a87428a74ab83b664916e7d3242beb970951e4ca26296924c60191a6592636a'},
 'model_index.json': {'bytes': 577,
                      'sha256': '2f4d71b6da5648627b9a2848b7f430d2294e95abbc5d3282dddd438bf8b466da'},
 'scheduler/scheduler_config.json': {'bytes': 474,
                                     'sha256': 'e0140f54cf0f8a5fb1cf8140caa95c1e733b6d9fa45c5e859aae43f3b9a45648'},
 'text_encoder/config.json': {'bytes': 560,
                              'sha256': '32d8cadf544d77c231efa1381145ac7e8f4cc88f581455c3c5f6369364474c83'},
 'text_encoder/model.safetensors': {'bytes': 492265880,
                                    'sha256': '8377b1ca9d88fe06ec483dd7b3cfc62e5e8dbf8ddd252f455e79d659fa0553c5'},
 'text_encoder_2/config.json': {'bytes': 570,
                                'sha256': '79b0f15ad762cf9c2992d50f9679d08c6cb477a50c88d41e216fc2d886e37abb'},
 'text_encoder_2/model.safetensors': {'bytes': 2778702976,
                                      'sha256': 'b84f413eebecbd049b72874c1df533a516510cb5a2489ae58c7e320209cf0ebe'},
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
 'unet/config.json': {'bytes': 1768,
                      'sha256': '34e7893a088737536ce0accd3f9d891c01d092886f88bbfa64a9091cb2501d13'},
 'unet/diffusion_pytorch_model.safetensors': {'bytes': 5325459760,
                                              'sha256': '02ed8ebd0ed55aec686fcf20946d7a1659a31f9f8d9c3798cd254ba6b67434ca'},
 'vae/config.json': {'bytes': 602,
                     'sha256': '2e425cbde02bdb63e43e8d49db7e14a941a76bb7b2058761200c72dc3d6344fd'},
 'vae/diffusion_pytorch_model.safetensors': {'bytes': 334643268,
                                             'sha256': '78f6189c8492013e3cac81637a1f657f790a237387f8a9dfd6bfa5fee28eb646'}}

SAFETY_MODEL = 'SimianLuo/LCM_Dreamshaper_v7'

SAFETY_REVISION = 'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5'

SAFETY_BYTES = 1215980908

SAFETY_FILE_SPECS = {'safety_checker/config.json': {'bytes': 726,
                                'sha256': '7a6c4890cfa31b748bc6b38e25f812099070cf9baf47926ed6eb887b0dd355e5'},
 'safety_checker/model.safetensors': {'bytes': 1215979664,
                                      'sha256': 'fb351a5ded815c3ff744968ad9c6b218d071b9d313d04f35e813b84b4c0ffde8'},
 'feature_extractor/preprocessor_config.json': {'bytes': 518,
                                                'sha256': '4db495644e3e5bd8fcac52f70e7fc0b413c911086021acf73ac30e5911166e95'}}

USER_REVISION_ID = 'resort-terracotta-bikini-ssd1b-study-20261114-v1'

SEED_SCHEME = 'sha256(slug|title|user_revision_id)[:8]-big-endian-mod-2**63'

INFERENCE = {'base_model': 'segmind/SSD-1B',
 'base_revision': '60987f37e94cd59c36b1cba832b9f97b57395a10',
 'pipeline': 'StableDiffusionXLPipeline',
 'device': 'cpu',
 'dtype': 'float32',
 'load_dtype': 'float32',
 'text_encoder_dtype': 'float32',
 'unet_compute_dtype': 'float32',
 'vae_decode_dtype': 'float32',
 'initial_components_loaded_together': True,
 'text_encoders_released_before_denoising': True,
 'unet_released_before_vae_decode': True,
 'attention': 'default_sdpa',
 'variant': None,
 'scheduler': 'EulerDiscreteScheduler',
 'timestep_spacing': 'leading',
 'use_karras_sigmas': False,
 'width': 1024,
 'height': 576,
 'num_inference_steps': 25,
 'guidance_scale': 9.0,
 'prompt_version': 1,
 'user_revision_id': 'resort-terracotta-bikini-ssd1b-study-20261114-v1',
 'seed_scheme': 'sha256(slug|title|user_revision_id)[:8]-big-endian-mod-2**63',
 'publication': 'preview_only',
 'refiner': False,
 'safety_model': 'SimianLuo/LCM_Dreamshaper_v7',
 'safety_revision': 'a85df6a8bd976cdd08b4fd8f3b73f229c9e54df5'}


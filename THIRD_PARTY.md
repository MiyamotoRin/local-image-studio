# Third-party components and model sources

This repository distributes the desktop UI and its original icon. It does not distribute model weights,
stable-diffusion.cpp executables, NVIDIA runtime DLLs, Python, or Pillow.
Downloading a component does not relicense it under this project's MIT license.

## Software

- [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp): MIT; upstream copyright 2023 leejet.
  See its [LICENSE](https://github.com/leejet/stable-diffusion.cpp/blob/master/LICENSE) and bundled third-party notices.
  Tested release: `master-982-0518b34`. CUDA runtime components have their own NVIDIA terms.
- [Pillow](https://github.com/python-pillow/Pillow): see its [LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE).
- [Python](https://www.python.org/psf/license/) and Tcl/Tk retain their respective licenses.

## Models

The complete source repositories, filenames, and SHA256 values are in `model-files.json`.
Always check the model authors' terms, including text encoders and VAEs.

| Preset | Original model | Quantized distribution |
|---|---|---|
| Z-Image Turbo | [Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo) | [leejet/Z-Image-Turbo-GGUF](https://huggingface.co/leejet/Z-Image-Turbo-GGUF) |
| FLUX.2 klein 4B | [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) | [leejet/FLUX.2-klein-4B-GGUF](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF) |
| Anima Turbo v1.1 | [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima) | [vanes430/Anima-Turbo-V1.1-GGUF](https://huggingface.co/vanes430/Anima-Turbo-V1.1-GGUF) |

**Anima:** the original model card identifies a CircleStone Labs Non-Commercial License and additional
upstream model conditions. It distinguishes restrictions on the model from commercial use of generated images.
The quantization page's metadata uses a different license label; this project does not treat that label as
overriding the original model's conditions. Read the [original license section](https://huggingface.co/circlestone-labs/Anima#license)
before using or redistributing it. No model is installed automatically on app startup.

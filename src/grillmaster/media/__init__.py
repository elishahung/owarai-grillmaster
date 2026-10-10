"""FFmpeg-backed media primitives: probing, frame stills, and the process runner.

`media.ffmpeg` is the only module that names or spawns ffmpeg/ffprobe; every
other function here takes an injected `FfmpegRunner`, so tests pass a fake
instead of patching. Packaging recipes live in `package/`, not here.
"""

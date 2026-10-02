# Command-line video preparation research

## Goal and boundary

The repeatable job is to (1) identify the first frame where the climber is
seated, (2) remove everything before it, and (3) prepend a title containing a
name, grade, wall angle, and send month/year. The repository must not contain a
specific person's identity or private media.

The implemented workflow keeps step 1 human-reviewable. A command produces a
timestamped contact sheet, which a person or vision-capable agent can inspect,
and a second command applies that timestamp. This is more predictable than
silently guessing what “sitting” means from body geometry, especially when a
climber is occluded, crouching, or already close to the wall.

## Tool survey

### FFmpeg driven by Python (selected)

FFmpeg supplies the mature codecs, probing, stream mapping, trim, scale, pad,
concatenation, resampling, and web-optimized MP4 output needed here. Python's
`subprocess` module is enough orchestration; avoiding an FFmpeg wrapper removes
an extra API/version boundary. The title image and contact sheet are composed
with Pillow, already a BoardLib dependency.

The relevant upstream references are:

- [FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html), in
  particular `concat`, `fps`, `scale`, `pad`, `atrim`, `asetpts`, and `aresample`
- [ffprobe documentation](https://ffmpeg.org/ffprobe.html)
- [Python subprocess documentation](https://docs.python.org/3/library/subprocess.html)
- [Pillow ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)

This route also permits explicit metadata removal (`-map_metadata -1`) and
standard H.264/AAC output. It does require the `ffmpeg` and `ffprobe`
executables; they are intentionally not hidden inside a Python wheel.

### MoviePy

[MoviePy](https://zulko.github.io/moviepy/) offers convenient Python clip
objects, text/image clips, composition, and concatenation. It is attractive for
notebooks and more elaborate programmed motion graphics. For this small CLI it
adds a substantial dependency and still ultimately delegates encoding to
FFmpeg. MoviePy 2 also introduced breaking changes, so using FFmpeg directly
makes the command and generated media settings easier to audit.

### PyAV

[PyAV](https://pyav.org/docs/stable/) exposes FFmpeg libraries as Pythonic
containers, streams, packets, and frames. It is a good fit when an application
must inspect or transform frames in-process. Implementing synchronization,
filtering, and encoding at that lower level is unnecessary for a trim/title
pipeline, and its binary installation surface is larger than calling an
existing FFmpeg executable.

### OpenCV

[OpenCV video I/O](https://docs.opencv.org/4.x/dd/d43/tutorial_py_video_display.html)
is useful for frame extraction and computer-vision preprocessing. Its video
writer is not as good a fit as FFmpeg for preserving/normalizing audio and
delivering a broadly compatible MP4. It also cannot identify “sitting” without
a pose model and a scene-specific decision rule.

### MediaPipe Pose Landmarker

[MediaPipe Pose Landmarker for Python](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker/python)
can estimate body landmarks in video and is the best candidate for a future
*suggested start* feature. A heuristic could look for visible hips and knees,
hip height, knee angle, and stability over several frames. It is not enabled by
default because it requires a separate model asset and heavyweight optional
dependencies, and “seated” cannot be inferred reliably from knee angle alone.
Any such result should remain a suggestion displayed on the contact sheet.

## Implemented pipeline

`tools/prepare_send_video.py` has two subcommands:

1. `sheet` asks FFmpeg to sample the complete clip at a configurable interval,
   then Pillow lays the frames out with timestamps.
2. `edit` uses ffprobe to discover display dimensions, duration, frame rate,
   rotation, and audio presence. Pillow renders a private temporary title card.
   FFmpeg normalizes the card and clip, concatenates them, inserts silence under
   the card when the source has audio, encodes H.264/AAC, moves MP4 metadata to
   the front for streaming, and discards inherited metadata.

Audio/video are re-encoded rather than stream-copied because an exact visual
cut, generated title segment, dimension normalization, and concatenation all
require decoded frames. The source is never modified.

## LLM compatibility and cost

There is no LLM integration in the pipeline and no network request, AI SDK, API
key, or model-specific file format. Both subcommands run locally using FFmpeg,
ffprobe, and Pillow. A human can inspect the contact sheet, so the workflow has
no AI cost at all.

Optionally, the generated JPEG can be given to any vision-capable assistant,
including a Gemini interface that accepts image uploads. This is an input to
human review rather than a program dependency: availability, upload limits,
retention, and free-tier quotas belong to the chosen service and can change.
The CLI neither knows nor cares which service produced the reviewed timestamp.
A suitable vendor-neutral prompt is:

> Read the timestamps beneath these frames. Return the earliest timestamp where
> the climber is clearly seated and ready to begin. Explain any ambiguity in one
> sentence. Do not infer an identity.

Before using the answer, check the indicated frame on the sheet. For tighter
precision, regenerate around the transition using a smaller `--interval`. Do
not upload private video or imagery to a third party unless its data handling is
acceptable; local human review remains the privacy-preserving default.

## Known limits and next steps

- Review the contact sheet at a smaller interval when the seated transition
  falls between samples. `--interval 0.1` gives ten candidates per second.
- The title text currently fits each supplied line by shrinking a shared font;
  extremely long names may become small rather than wrapping.
- FFmpeg's automatic rotation plus the explicit output canvas handles common
  phone rotation metadata, but unusual anamorphic or variable-frame-rate input
  should be visually checked.
- Output metadata is stripped, but pixels and audio can still reveal private
  information. Review the final file before sharing it.
- Batch manifest integration is a natural follow-up: store a reviewed
  `start_seconds` beside each planned post, then invoke this tool for approved
  records. It should not overwrite source paths or publish without review.

## Release readiness

The tool is ready for code review but should not be called production-verified
until one representative phone video completes the `sheet` and `edit` workflow
on a workstation with FFmpeg. Unit tests validate metadata interpretation,
title rendering, validation, and filter-command construction without executing
the external encoder. The repository now provides a `check` subcommand so the
workstation dependency gate is explicit.

There is no cloud deployment or Gemini setup. The operator's remaining tasks
are to install FFmpeg and the Python dependencies, run `check`, process a sample,
watch the entire result, and only then use it on the intended clips. Audio is
normalized to a stereo 48 kHz stream before concatenation so mono phone audio
can be joined to the silent title segment.

## Research environment note

The implementation was evaluated against the projects' public documentation
URLs above. Live retrieval was unavailable in the development environment
(web search returned HTTP 401 and direct HTTPS access was denied by its proxy),
so no claims here depend on newly announced or version-specific behavior.

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any


def get_video_info(video_path: Path) -> Any:
    cmd: list[str | Path] = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-print_format",
        "json",
        video_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    else:
        msg = f"Failed to get video info: {result.stderr}"
        raise Exception(msg)


def walk_paths(
    paths: list[Path],
    predicate: Callable[[Path], bool],
    callback: Callable[[Path], None],
) -> None:
    for path in paths:
        if path.is_dir():
            walk_paths(
                [p for p in path.rglob("*") if predicate(p)],
                predicate,
                callback,
            )
        elif predicate(path):
            callback(path)


def match_file(file: Path) -> bool:
    if "@eaDir" in file.parts:
        return False

    if file.is_dir():
        return False

    if "Plex Versions" in file.parts:
        return False

    return file.suffix in {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
    }


@dataclass
class OutputStream:
    input_index: int
    options: list[str] = field(default_factory=list)


def handle_file(file: Path) -> None:
    in_path = file.resolve()
    out_path = in_path.with_suffix(".mkv")

    # file = convert_to_mkv(file)
    video_info = get_video_info(in_path)
    streams = video_info["streams"]
    video_streams = [
        stream for stream in streams if stream["codec_type"] == "video"
    ]
    if len(video_streams) == 0:
        msg = f"Skipping {in_path!r}, no video streams"
        raise ValueError(msg)

    subtitle_streams = [
        stream for stream in streams if stream["codec_type"] == "subtitle"
    ]
    subtitle_codecs = [stream["codec_name"] for stream in subtitle_streams]
    default_subtitle_streams = [
        stream
        for stream in subtitle_streams
        if stream["disposition"]["default"] == 1
    ]
    default_subtitle_codecs = [
        stream["codec_name"] for stream in default_subtitle_streams
    ]

    output_streams: list[OutputStream] = []
    needs_run = False

    for stream_index, stream in enumerate(streams):
        if stream["codec_type"] == "video":
            codec_name = stream["codec_name"]
            if codec_name in {"vc1", "hevc", "vp9", "av1"}:
                needs_run = True
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "libx264",
                            "-crf:{output_index}",
                            "20",
                            "-preset:{output_index}",
                            "medium",
                            "-disposition:{output_index}",
                            "+default",
                        ],
                    )
                )
            elif codec_name in {"h264", "mjpeg", "png"}:
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "copy",
                        ],
                    )
                )
            else:
                msg = f"Unhandled video codec: {stream}"
                raise Exception(msg)
        elif stream["codec_type"] == "audio":
            output_streams.append(
                OutputStream(
                    stream_index,
                    options=[
                        "-c:{output_index}",
                        "copy",
                    ],
                )
            )
        elif stream["codec_type"] == "subtitle":
            if stream["codec_name"] == "mov_text":
                needs_run = True
                # Data streams are not supported, skip them
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "srt",
                            "-disposition:{output_index}",
                            "+default",
                        ],
                    )
                )
            elif subtitle_codecs == ["webvtt"] or subtitle_codecs == ["ass"]:
                needs_run = True
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "srt",
                            "-disposition:{output_index}",
                            "+default",
                        ],
                    )
                )
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "copy",
                            "-disposition:{output_index}",
                            "-default",
                        ],
                    )
                )
            elif (
                subtitle_codecs
                == [
                    "webvtt",
                    "subrip",
                ]
                and default_subtitle_codecs == ["webvtt"]
            ) or (
                subtitle_codecs
                == [
                    "ass",
                    "subrip",
                ]
                and default_subtitle_codecs == ["ass"]
            ):
                needs_run = True
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "copy",
                            "-disposition:{output_index}",
                            (
                                "+default"
                                if stream["codec_name"] == "subrip"
                                else "-default"
                            ),
                        ],
                    )
                )
            elif (
                sorted(subtitle_codecs)
                in [
                    [
                        "subrip",
                        "webvtt",
                    ],
                    [
                        "ass",
                        "subrip",
                    ],
                ]
                or subtitle_codecs == ["subrip"]
            ) and default_subtitle_codecs == ["subrip"]:
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "copy",
                        ],
                    )
                )
            elif set(subtitle_codecs).issubset({"subrip", "hdmv_pgs_subtitle"}):
                # If its all subrip, just keep it as is,
                # this is probably multiple languages
                output_streams.append(
                    OutputStream(
                        stream_index,
                        options=[
                            "-c:{output_index}",
                            "copy",
                        ],
                    )
                )
            else:
                msg = f"Unhandled subtitles codec {subtitle_codecs} in {in_path!r}"
                raise ValueError(msg)
        elif stream["codec_type"] == "attachment":
            output_streams.append(
                OutputStream(
                    stream_index,
                    options=[
                        "-c:{output_index}",
                        "copy",
                    ],
                )
            )
        elif stream["codec_type"] == "data":
            if stream["codec_name"] == "bin_data":
                needs_run = True
                # Data streams are not supported, skip them
            else:
                msg = f"Unknown data stream codec {stream['codec_name']} in {in_path!r}"
                raise ValueError(msg)
        else:
            msg = f"Unknown stream type {stream['codec_type']} in {in_path!r}"
            raise ValueError(msg)

    if not needs_run:
        return

    output_streams.sort(key=lambda x: x.input_index)
    ffmpeg_args: list[str | Path] = [
        "-i",
        in_path,
    ]

    for i, stream in enumerate(output_streams):
        ffmpeg_args.append("-map")
        ffmpeg_args.append(f"0:{stream.input_index}")
        ffmpeg_args.extend(opt.format(output_index=i) for opt in stream.options)

    with tempfile.NamedTemporaryFile(suffix=".mkv", delete=False) as temp_file:
        temp_path = Path(temp_file.name).resolve()

        subprocess.run(
            [
                "ffmpeg",
                "-threads",
                "16",
                "-y",
                *ffmpeg_args,
                "-f",
                "matroska",
                temp_path,
            ],
            check=True,
        )

        shutil.move(temp_path, out_path)
        if in_path != out_path and out_path.exists():
            in_path.unlink()

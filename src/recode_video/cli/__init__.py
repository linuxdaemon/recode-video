# SPDX-FileCopyrightText: 2025-present linuxdaemon <linuxdaemon.irc@gmail.com>
#
# SPDX-License-Identifier: MIT
from pathlib import Path

import click

from recode_video._version import __version__
from recode_video.recode_video import handle_file, match_file, walk_paths


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=__version__, prog_name="recode-video")
@click.argument(
    "path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    nargs=-1,
)
def recode_video(path: list[Path]) -> None:
    walk_paths(
        path,
        match_file,
        handle_file,
    )

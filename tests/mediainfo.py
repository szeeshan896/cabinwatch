"""
Asking what is inside a video file, without assuming ffprobe is installed.

The agent carries its own ffmpeg (imageio-ffmpeg) so that a plain Windows
machine needs nothing installed - but that package ships ffmpeg alone, not
ffprobe. Tests that reached for ffprobe therefore passed on a developer's
machine, where it happens to be on the path, and failed on a CI runner,
where it is not. ffmpeg can answer the same questions.
"""

import os
import re
import sys
import shutil
import subprocess

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

import muxer                                     # noqa: E402


def _ffprobe():
    return shutil.which("ffprobe")


def streams(path):
    """Which kinds of stream a file holds: ["video", "audio"].

    None means there was no way to look, which is not the same as a file
    with nothing in it - a test should say so rather than fail.
    """
    probe = _ffprobe()
    if probe:
        done = subprocess.run(
            [probe, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path], capture_output=True, text=True)
        if done.returncode != 0:
            return []
        return [line.strip() for line in done.stdout.split() if line.strip()]

    exe = muxer.ffmpeg()
    if not exe:
        return None
    # ffmpeg describes what it opened on stderr and then complains that it
    # has nowhere to put it, which is fine - we only want the description.
    done = subprocess.run([exe, "-hide_banner", "-i", path],
                          capture_output=True, text=True)
    text = done.stderr or ""
    if "Invalid data found" in text or "moov atom not found" in text:
        return []
    found = []
    for kind in re.findall(r"Stream #\d+:\d+.*?: (\w+):", text):
        found.append(kind.lower())
    return found


def frame_count(path):
    """How many frames can actually be read back. None if we cannot tell."""
    probe = _ffprobe()
    if probe:
        done = subprocess.run(
            [probe, "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
             path], capture_output=True, text=True)
        try:
            return int(done.stdout.strip())
        except ValueError:
            return 0

    exe = muxer.ffmpeg()
    if not exe:
        return None
    # Decode the whole thing to nowhere and read the frame count it reports.
    done = subprocess.run([exe, "-hide_banner", "-i", path, "-map", "0:v:0",
                           "-f", "null", "-"], capture_output=True, text=True)
    hits = re.findall(r"frame=\s*(\d+)", done.stderr or "")
    return int(hits[-1]) if hits else 0


def describe(path):
    """What ffprobe would tell us, from whichever tool is available.

    {"duration": seconds, "streams": [{codec_type, codec_name, channels,
    sample_rate, duration}]}. Per-stream durations are only there when
    ffprobe is; a caller that needs them should say it could not check
    rather than pretend.
    """
    probe = _ffprobe()
    if probe:
        import json
        done = subprocess.run(
            [probe, "-v", "error", "-print_format", "json", "-show_streams",
             "-show_format", path], capture_output=True, text=True)
        if done.returncode != 0:
            return None
        raw = json.loads(done.stdout)
        out = {"duration": float(raw.get("format", {}).get("duration") or 0),
               "streams": []}
        for st in raw.get("streams", []):
            out["streams"].append({
                "codec_type": st.get("codec_type"),
                "codec_name": st.get("codec_name"),
                "channels": st.get("channels"),
                "sample_rate": int(st.get("sample_rate") or 0),
                "duration": (float(st["duration"]) if st.get("duration")
                             else None),
            })
        return out

    exe = muxer.ffmpeg()
    if not exe:
        return None
    done = subprocess.run([exe, "-hide_banner", "-i", path],
                          capture_output=True, text=True)
    text = done.stderr or ""
    if "Invalid data found" in text or "moov atom not found" in text:
        return None

    out = {"duration": 0.0, "streams": []}
    when = re.search(r"Duration: (\d+):(\d\d):(\d\d\.\d+)", text)
    if when:
        h, m, sec = when.groups()
        out["duration"] = int(h) * 3600 + int(m) * 60 + float(sec)

    for line in text.splitlines():
        found = re.search(r"Stream #\d+:\d+.*?: (\w+): (\w+)", line)
        if not found:
            continue
        kind, codec = found.group(1).lower(), found.group(2).lower()
        row = {"codec_type": kind, "codec_name": codec, "channels": None,
               "sample_rate": 0, "duration": None}
        if kind == "audio":
            if "mono" in line:
                row["channels"] = 1
            elif "stereo" in line:
                row["channels"] = 2
            rate = re.search(r"(\d+) Hz", line)
            if rate:
                row["sample_rate"] = int(rate.group(1))
        out["streams"].append(row)
    return out


def has_sound(path):
    found = streams(path)
    return None if found is None else ("audio" in found)

#!/usr/bin/env python3
"""GStreamer compositor: pulls the existing browser scene (unchanged) from
MediaMTX path /raw, draws dynamic overlay panels live via cairooverlay, and
pushes the composited result back to MediaMTX path /live for viewers.

Same control-API contract as the Smelter version (state.py/control.py mirror
state.ts/controlServer.ts) so the two backends are a fair comparison.
"""
import os
import subprocess
import sys
import time
import threading

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

import state as st
import control
from draw import draw_scene

INPUT_URL = os.environ.get("INPUT_URL", "rtmp://127.0.0.1:1935/raw")
OUTPUT_URL = os.environ.get("OUTPUT_URL", "rtmp://127.0.0.1:1935/live")
CONTROL_PORT = int(os.environ.get("CONTROL_PORT", "7800"))
BITRATE_KBPS = int(os.environ.get("VBITRATE_KBPS", "4000"))
FPS = int(os.environ.get("FPS", "24"))
ENC_THREADS = int(os.environ.get("ENC_THREADS", "2"))
DEC_THREADS = int(os.environ.get("DEC_THREADS", "2"))
STALL_TIMEOUT_S = int(os.environ.get("STALL_TIMEOUT_S", "20"))
STARTUP_GRACE_S = int(os.environ.get("STARTUP_GRACE_S", "60"))

Gst.init(None)

_dims = {"width": 1280, "height": 720}
# Grace period on first boot: the app container + bridge connection can
# legitimately take 30-45s to converge, longer than the steady-state stall
# threshold below — don't let the watchdog fire before anything's had a
# chance to start.
_last_draw = {"t": time.monotonic() + STARTUP_GRACE_S - STALL_TIMEOUT_S}


def on_caps_changed(_overlay, caps):
    struct = caps.get_structure(0)
    ok_w, w = struct.get_int("width")
    ok_h, h = struct.get_int("height")
    if ok_w and ok_h:
        _dims["width"], _dims["height"] = w, h


def on_draw(_overlay, ctx, _timestamp, _duration):
    _last_draw["t"] = time.monotonic()
    try:
        draw_scene(ctx, _dims["width"], _dims["height"], st.snapshot())
    except Exception as e:
        print(f"[draw] error: {e}", file=sys.stderr)


def start_watchdog():
    """Belt-and-suspenders for a whole class of silent-stall failure modes
    (a FIFO/pipe reader not getting a clear signal when its writer process
    restarts, chief among them — reproduced in practice while building
    this). Rather than make every hand-off perfectly self-healing at the
    protocol level, just detect "no frame drawn in STALL_TIMEOUT_S" and
    exit — the container has --restart=always, so a full clean restart
    (same idempotent-restart pattern as demo/start.sh) recovers reliably
    even from failure modes we haven't specifically diagnosed."""
    def loop():
        while True:
            time.sleep(5)
            if time.monotonic() - _last_draw["t"] > STALL_TIMEOUT_S:
                print(f"[watchdog] no frame drawn in {STALL_TIMEOUT_S}s, exiting for container restart", file=sys.stderr)
                os._exit(1)
    threading.Thread(target=loop, daemon=True).start()


BRIDGE_FIFO = "/tmp/bridge.ts"
EGRESS_FIFO = "/tmp/egress.ts"


def _mkfifo(path):
    if not os.path.exists(path):
        os.mkfifo(path)


def _ffmpeg_pipe_loop(label, args):
    """Shared retry-loop shape for both the ingest bridge and the egress
    push — same resilience pattern as demo/start.sh's ffmpeg loop."""
    def loop():
        while True:
            proc = subprocess.Popen(["ffmpeg", "-loglevel", "warning", *args])
            proc.wait()
            print(f"[{label}] ffmpeg exited, retrying in 3s...", file=sys.stderr)
            time.sleep(3)
    threading.Thread(target=loop, daemon=True).start()


def start_ffmpeg_bridge():
    """GStreamer's native rtmpsrc/rtmp2src both have real interop friction
    against MediaMTX's RTMP server (parser bugs / rejected play handshake).
    ffmpeg's RTMP client is proven solid throughout this whole project, so
    it does the one flaky leg (RTMP ingest) — the actual compositing stays
    100% GStreamer.

    Hands off over a named pipe (FIFO), not UDP: UDP has zero loss recovery,
    and once the frame-rate bug was fixed and throughput tripled, dropped
    UDP packets mid-GOP started corrupting the H264 bytestream (ghosting/
    double-exposure text, a classic lost-reference-frame decode artifact).
    A FIFO is kernel-buffered and reliable — the writer blocks instead of
    dropping data if the reader falls behind.

    Tried switching this to TCP loopback sockets (unambiguous connect/
    disconnect events, unlike a FIFO's EOF semantics on writer restart) to
    close a reconnect edge case, but that introduced a worse, currently
    unresolved silent-stall failure of its own without a clear root cause
    in the time available — reverted. The FIFO is the version verified
    working correctly (30fps, zero corruption); the watchdog below
    (start_watchdog) covers the reconnect-stall risk instead, at the
    container level rather than the protocol level."""
    _mkfifo(BRIDGE_FIFO)
    _ffmpeg_pipe_loop("bridge", ["-i", INPUT_URL, "-c", "copy", "-f", "mpegts", "-y", BRIDGE_FIFO])


def start_ffmpeg_egress():
    """The final hop to the real destination (YouTube in production) is the
    one connection that actually matters, so it shouldn't depend on
    rtmp2sink — which lives in gst-plugins-bad. GStreamer's own module
    README defines that category explicitly: "a set of plug-ins that aren't
    up to par... missing a good code review, some documentation, a set of
    tests, a real live maintainer, or some actual wide use... if the
    plug-ins break, you can't complain." We hit two real bugs in it this
    session (broken `location` parsing, an `application`/`stream`
    path-composition bug). ffmpeg's RTMP(S) client has been solid all
    project and supports rtmps:// natively (YouTube's recommended secure
    transport) — GStreamer writes the composited output to a local FIFO,
    ffmpeg reads it and pushes to OUTPUT_URL for real. See
    start_ffmpeg_bridge's docstring re: FIFO vs TCP."""
    _mkfifo(EGRESS_FIFO)
    _ffmpeg_pipe_loop("egress", ["-f", "flv", "-i", EGRESS_FIFO, "-c", "copy", "-f", "flv", OUTPUT_URL])


def build_pipeline():
    # rtmp2sink's property parsing via the pipeline-description mini-language
    # is unreliable for this element (broken `location` string parsing, and
    # an `application`/`stream` path-composition quirk that mangles quoted
    # empty strings) — leave it unconfigured here and set properties
    # directly via the Python API below, which is not affected.
    #
    # NOTE: do NOT put leaky queues on these branches. They carry compressed
    # H264/AAC bytestream data (pre-decode / pre-mux), and a leaky queue drops
    # arbitrary buffers without respecting NAL/frame boundaries — that
    # corrupts the bitstream (visible as green macroblock corruption) and can
    # stall the decoder on the resulting garbage, which is worse than the
    # growing-latency problem it was meant to fix. The actual fix for
    # falling-behind-real-time was capping the upstream app's CPU (--cpus)
    # so this whole chain isn't starved; plain queues are fine once that's
    # true. If frame-dropping is ever needed again, it belongs on the raw
    # (decoded) video queue between avdec_h264 and cairooverlay, never on
    # compressed data.
    #
    # Tuning (all real gst-inspect-1.0-verified properties, not guesses):
    # - filesrc/filesink on the FIFOs: see start_ffmpeg_bridge's docstring
    #   for why FIFOs over UDP or TCP sockets for this hand-off.
    # - avdec_h264 max-threads / x264enc threads: explicit rather than "0=auto".
    #   Auto-detection reads the host's core count, not this container's
    #   --cpus quota, so on a capped/shared VM it can over-subscribe threads
    #   and add contention instead of speed.
    # - x264enc sliced-threads: threads per-slice instead of per-frame, so
    #   multi-threaded encoding doesn't add the frame-reordering latency that
    #   tune=zerolatency is otherwise trying to avoid.
    # - speed-preset=veryfast (not ultrafast): the compositor itself measured
    #   at only 1-3% CPU doing full 720p work, i.e. it was never the
    #   bottleneck (the upstream browser's software WebGL rendering was) —
    #   there was CPU headroom being wasted on the lowest-quality preset.
    Q = "queue"
    desc = f"""
      filesrc location={BRIDGE_FIFO} ! tsdemux name=demux
      demux. ! {Q} ! h264parse ! avdec_h264 max-threads={DEC_THREADS} ! videoconvert !
        cairooverlay name=ov ! videoconvert !
        x264enc tune=zerolatency speed-preset=veryfast threads={ENC_THREADS} sliced-threads=true
                bitrate={BITRATE_KBPS} key-int-max={FPS * 2} !
        h264parse ! {Q} ! mux.
      demux. ! {Q} ! aacparse ! mux.
      flvmux name=mux streamable=true ! filesink location={EGRESS_FIFO} sync=false
    """
    pipeline = Gst.parse_launch(desc)

    overlay = pipeline.get_by_name("ov")
    overlay.connect("draw", on_draw)
    overlay.connect("caps-changed", on_caps_changed)
    return pipeline


def run_once():
    """Run the pipeline until EOS/ERROR; return so the caller can retry."""
    pipeline = build_pipeline()
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()

    def on_message(_bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"[gst] ERROR: {err} ({debug})", file=sys.stderr)
            loop.quit()
        elif t == Gst.MessageType.EOS:
            print("[gst] EOS", file=sys.stderr)
            loop.quit()

    bus.add_signal_watch()
    bus.connect("message", on_message)

    print(f"[gst] starting pipeline: {INPUT_URL} -> {OUTPUT_URL}")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)


def main():
    server = control.start(CONTROL_PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    start_ffmpeg_bridge()
    start_ffmpeg_egress()
    start_watchdog()
    time.sleep(2)  # give the bridge a moment to create the FIFOs before GStreamer opens them

    # Same resilience pattern as the ffmpeg retry loop in demo/start.sh: the
    # upstream (browser scene) may not have connected to MediaMTX yet, or may
    # drop — keep retrying rather than exiting.
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[gst] pipeline crashed: {e}", file=sys.stderr)
        print("[gst] retrying in 3s...", file=sys.stderr)
        time.sleep(3)


if __name__ == "__main__":
    main()

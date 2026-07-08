#!/usr/bin/env bash
# Thin wrapper around the Manx Timestamper Apptainer image.
#
# Encapsulates the rootless-apptainer invocation the backend needs so callers
# don't have to know about the environment gymnastics. Every env override below
# exists to keep the container's scratch (its own tmp, kaldi's mktemp, apptainer's
# sandbox) OFF cassini's root filesystem, which is chronically 100% full — all of
# it is redirected onto /exp/exp1. The .sif is mandatory: the raw kaldi binaries
# are built for glibc 2.34 (Ubuntu 22.04) and cassini is glibc 2.31 (20.04).
#
# Usage:
#   run-timestamper.sh [--nj N] [--mode word|phrase|both] [--formats csv,srt,txt] \
#                      <audio-file> <transcript.txt> <output-dir>
#
# Exit code and stdout/stderr are passed straight through from timestamp.sh.
set -euo pipefail

# --- locations (overridable via env) ---------------------------------------
BASE=${TIMESTAMPER_BASE:-/exp/exp1/acp24csb}
APPTAINER_BIN=${APPTAINER_BIN:-$BASE/tools/apptainer-env/bin}
SIF=${TIMESTAMPER_SIF:-$BASE/model_instances/manx_timestamper/timestamper.sif}
SCRATCH=${TIMESTAMPER_SCRATCH:-$BASE/tmp}

# --- defaults ---------------------------------------------------------------
nj=32
mode=both
formats=csv,srt,txt

# --- parse our own options, leave positionals ------------------------------
pos=()
while [ $# -gt 0 ]; do
  case "$1" in
    --nj)      nj=$2; shift 2 ;;
    --mode)    mode=$2; shift 2 ;;
    --formats) formats=$2; shift 2 ;;
    --) shift; while [ $# -gt 0 ]; do pos+=("$1"); shift; done ;;
    -*) echo "run-timestamper.sh: unknown option: $1" >&2; exit 2 ;;
    *)  pos+=("$1"); shift ;;
  esac
done

if [ ${#pos[@]} -ne 3 ]; then
  echo "Usage: run-timestamper.sh [--nj N] [--mode M] [--formats F] <audio> <transcript.txt> <outdir>" >&2
  exit 2
fi
audio=${pos[0]}; transcript=${pos[1]}; outdir=${pos[2]}

[ -f "$audio" ]      || { echo "run-timestamper.sh: no such audio file: $audio" >&2; exit 2; }
[ -f "$transcript" ] || { echo "run-timestamper.sh: no such transcript: $transcript" >&2; exit 2; }
[ -x "$APPTAINER_BIN/apptainer" ] || { echo "run-timestamper.sh: apptainer not found at $APPTAINER_BIN" >&2; exit 3; }
[ -f "$SIF" ]        || { echo "run-timestamper.sh: SIF not found at $SIF" >&2; exit 3; }

mkdir -p "$outdir"
# Per-run writable /tmp for kaldi's mktemp, so concurrent runs never collide and
# nothing lands on root. Its own dir under SCRATCH; removed on exit.
ctmp=$(mktemp -d "$SCRATCH/ts-ctmp.XXXXXX")
cleanup() { rm -rf "$ctmp"; }
trap cleanup EXIT

export HOME="$BASE"
export TMPDIR="$SCRATCH"
export APPTAINER_TMPDIR="$SCRATCH/apptainer"
export APPTAINER_CACHEDIR="$SCRATCH/apptainer-cache"
export PATH="$APPTAINER_BIN:$PATH"   # so apptainer finds squashfuse (mounts the SIF instead of extracting)
mkdir -p "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"

rc=0
"$APPTAINER_BIN/apptainer" run \
  -B /exp/exp1 -B "$ctmp:/tmp" --env TMPDIR=/tmp \
  "$SIF" \
  --mode "$mode" --formats "$formats" --nj "$nj" --cleanup true \
  "$audio" "$transcript" "$outdir" || rc=$?
exit $rc

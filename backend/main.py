"""
Manx Language Web Platform — FastAPI Backend
TTS: Grad-TTS + HiFi-GAN (graphemic)        — cuda:0, loaded at startup
ASR: Whisper-large-v3 fine-tuned on Manx    — cuda:1, loaded at startup
MT:  NLLB-200 distilled 600M (gv2en/en2gv)  — cuda:0, loaded at startup
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import asyncio
import uuid
import os
import sys
import json
import tempfile
import logging
import logging.handlers
import subprocess
import time
from datetime import datetime, timedelta
from typing import List, Optional
import torch

# ---------------------------------------------------------------------------
# Logging setup — stdout (systemd journal) + rotating file
# ---------------------------------------------------------------------------

LOG_DIR      = os.environ.get("LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "logs"))
LOG_FILE     = os.path.join(LOG_DIR, "gaelg-ai.log")
TRAFFIC_FILE = os.path.join(LOG_DIR, "traffic.log")
os.makedirs(LOG_DIR, exist_ok=True)

_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

class _FlushingRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that flushes after every record so logs are immediately visible."""
    def emit(self, record):
        super().emit(record)
        self.flush()

_file_handler = _FlushingRotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,   # 10 MB per file
    backupCount=7,                # Keep 7 rotated files (~70 MB max)
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Traffic logging
# ---------------------------------------------------------------------------

import threading
_traffic_lock = threading.Lock()

def record_request(tech: str):
    """Increment today's request count for the given technology (tts/asr/mt)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with _traffic_lock:
        # Read existing log
        lines = []
        if os.path.exists(TRAFFIC_FILE):
            with open(TRAFFIC_FILE, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()

        # Find today's line or create it
        today_idx = None
        for i, line in enumerate(lines):
            if line.startswith(today):
                today_idx = i
                break

        def _safe_int(s):
            try:
                v = int(s.strip())
                return v if v >= 0 else 0
            except (ValueError, AttributeError):
                return 0

        if today_idx is None:
            counts = {"tts": 0, "asr": 0, "mt": 0}
        else:
            # Parse existing: "2026-04-09 | TTS: 5 | ASR: 2 | MT: 3 | Total: 10"
            counts = {"tts": 0, "asr": 0, "mt": 0}
            try:
                for part in lines[today_idx].split("|"):
                    part = part.strip()
                    if part.startswith("TTS:"):
                        counts["tts"] = _safe_int(part[4:])
                    elif part.startswith("ASR:"):
                        counts["asr"] = _safe_int(part[4:])
                    elif part.startswith("MT:"):
                        counts["mt"] = _safe_int(part[3:])
            except Exception:
                counts = {"tts": 0, "asr": 0, "mt": 0}  # Reset on corrupt line

        counts[tech] += 1
        total = sum(counts.values())
        new_line = f"{today} | TTS: {counts['tts']} | ASR: {counts['asr']} | MT: {counts['mt']} | Total: {total}"

        if today_idx is None:
            lines.append(new_line)
        else:
            lines[today_idx] = new_line

        with open(TRAFFIC_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GRADTTS_ROOT = os.environ.get(
    "GRADTTS_ROOT",
    "/exp/exp1/acp24csb/model_instances/Grad-TTS_graphemic"
)
GRAD_TTS_CKPT = os.environ.get(
    "GRAD_TTS_CKPT",
    "/exp/exp1/acp24csb/model_instances/Grad-TTS_graphemic/checkpts/manx-22k.pt"
)
HIFIGAN_CKPT = os.environ.get(
    "HIFIGAN_CKPT",
    os.path.join(GRADTTS_ROOT, "checkpts", "hifigan.pt")
)
HIFIGAN_CONFIG = os.environ.get(
    "HIFIGAN_CONFIG",
    os.path.join(GRADTTS_ROOT, "checkpts", "hifigan-config.json")
)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Output cleanup configuration
OUTPUT_CLEANUP_TTL_HOURS = int(os.environ.get("OUTPUT_CLEANUP_TTL_HOURS", "24"))  # Delete files older than 24 hours
OUTPUT_CLEANUP_INTERVAL_MINUTES = int(os.environ.get("OUTPUT_CLEANUP_INTERVAL_MINUTES", "60"))  # Run cleanup every 60 minutes

WHISPER_CKPT = Path(os.environ.get(
    "WHISPER_CKPT",
    "/exp/exp1/acp24csb/model_instances/whisper/save/CKPT+2026-03-21+19-13-49+00"
))
WHISPER_HUB = Path(os.environ.get(
    "WHISPER_HUB",
    "/exp/exp1/acp24csb/model_instances/whisper/save"
))
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda:1")

NLLB_CHECKPOINTS = Path(os.environ.get(
    "NLLB_CHECKPOINTS",
    "/exp/exp1/acp24csb/model_instances/nllb"
))
NLLB_DEVICE = os.environ.get("NLLB_DEVICE", "cuda:0")

HF_HOME = os.environ.get("HF_HOME", "/exp/exp1/acp24csb/hf_cache")

# Voice converter (kNN-VC — WavLM + HiFi-GAN, runs on cuda:1)
VC_ROOT = os.environ.get(
    "VC_ROOT",
    "/exp/exp1/acp24csb/model_instances/voice_converter"
)
VC_DEVICE = os.environ.get("VC_DEVICE", "cuda:0")
sys.path.insert(0, VC_ROOT)
from converter import load_models as vc_load_models, convert as vc_convert

# Add Grad-TTS graphemic modules to path
sys.path.insert(0, GRADTTS_ROOT)
sys.path.insert(0, os.path.join(GRADTTS_ROOT, "hifi-gan"))

# ---------------------------------------------------------------------------
# Model globals and status tracking
# ---------------------------------------------------------------------------

# TTS
generator   = None
vocoder     = None
tts_device  = None
_symbols    = None

# ASR
whisper_model     = None
whisper_processor = None

# MT — both directions kept resident
nllb_tokenizers = {}  # {"gv2en": tokenizer, "en2gv": tokenizer}
nllb_models     = {}  # {"gv2en": model,     "en2gv": model}

# Model loading status tracking (for graceful degradation)
model_status = {
    "tts": False,
    "asr": False,
    "mt": False,
    "vc": False,
}
model_errors = {}


# ---------------------------------------------------------------------------
# Output cleanup (garbage collection)
# ---------------------------------------------------------------------------

def cleanup_old_outputs():
    """Delete audio files older than OUTPUT_CLEANUP_TTL_HOURS."""
    try:
        cutoff_time = time.time() - (OUTPUT_CLEANUP_TTL_HOURS * 3600)
        deleted_count = 0
        deleted_size = 0

        for filename in os.listdir(OUTPUT_DIR):
            filepath = os.path.join(OUTPUT_DIR, filename)
            if not os.path.isfile(filepath):
                continue

            # Only delete WAV files to avoid accidents
            if not filename.endswith(".wav"):
                continue

            mtime = os.path.getmtime(filepath)
            if mtime < cutoff_time:
                try:
                    size = os.path.getsize(filepath)
                    os.unlink(filepath)
                    deleted_count += 1
                    deleted_size += size
                except Exception as e:
                    logger.warning(f"Failed to delete {filename}: {e}")

        if deleted_count > 0:
            size_mb = deleted_size / (1024 * 1024)
            logger.info(f"Output cleanup: deleted {deleted_count} files ({size_mb:.1f} MB)")
    except Exception as e:
        logger.exception(f"Output cleanup failed: {e}")


async def cleanup_loop():
    """Background task: run cleanup every OUTPUT_CLEANUP_INTERVAL_MINUTES."""
    while True:
        try:
            # Sleep first to let startup finish
            await asyncio.sleep(OUTPUT_CLEANUP_INTERVAL_MINUTES * 60)
            cleanup_old_outputs()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Cleanup loop error: {e}")

# Store cleanup task reference so we can cancel it on shutdown
_cleanup_task = None

# GPU memory thresholds for warnings
GPU_MEMORY_WARNING_PERCENT = int(os.environ.get("GPU_MEMORY_WARNING_PERCENT", "85"))  # Warn if >85% used

# Inference timeouts (to prevent hung requests)
SYNTHESIZE_TIMEOUT_SECONDS = int(os.environ.get("SYNTHESIZE_TIMEOUT_SECONDS", "30"))  # Max 30 seconds for TTS
TRANSCRIBE_TIMEOUT_SECONDS = int(os.environ.get("TRANSCRIBE_TIMEOUT_SECONDS", "120"))  # Max 2 minutes for ASR
TRANSLATE_TIMEOUT_SECONDS = int(os.environ.get("TRANSLATE_TIMEOUT_SECONDS", "30"))  # Max 30 seconds for MT
CONVERT_TIMEOUT_SECONDS = int(os.environ.get("CONVERT_TIMEOUT_SECONDS", "30"))  # Max 30 seconds for VC

# Disk space checks
MIN_DISK_FREE_MB = int(os.environ.get("MIN_DISK_FREE_MB", "100"))  # Require 100 MB free before synthesis

# Rate limiting — sliding window per IP
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "20"))   # Max requests per window
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))  # Window size in seconds


# ---------------------------------------------------------------------------
# Rate limiting (sliding window, per IP)
# ---------------------------------------------------------------------------

# ip -> list of request timestamps within the current window
_rate_limit_store = {}  # ip -> list of request timestamps

def check_rate_limit(ip: str):
    """Returns (allowed, retry_after_seconds). Thread-safe for asyncio."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    timestamps = _rate_limit_store.get(ip, [])
    # Drop timestamps outside the window
    timestamps = [t for t in timestamps if t > window_start]

    if len(timestamps) >= RATE_LIMIT_REQUESTS:
        retry_after = int(timestamps[0] + RATE_LIMIT_WINDOW_SECONDS - now) + 1
        _rate_limit_store[ip] = timestamps
        return False, retry_after

    timestamps.append(now)
    _rate_limit_store[ip] = timestamps

    # Evict IPs with no recent activity to prevent unbounded memory growth
    if len(_rate_limit_store) > 10000:
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        stale = [k for k, v in _rate_limit_store.items() if not v or v[-1] < cutoff]
        for k in stale:
            del _rate_limit_store[k]
    return True, 0


# ---------------------------------------------------------------------------
# Disk space monitoring
# ---------------------------------------------------------------------------

def check_disk_space():
    """Check available disk space in OUTPUT_DIR. Returns (free_mb, enough_space)."""
    try:
        import shutil
        stat = shutil.disk_usage(OUTPUT_DIR)
        free_mb = stat.free / (1024 * 1024)
        enough = free_mb >= MIN_DISK_FREE_MB
        return free_mb, enough
    except Exception as e:
        logger.warning(f"Failed to check disk space: {e}")
        return None, True  # Assume OK if we can't check


# ---------------------------------------------------------------------------
# GPU memory monitoring
# ---------------------------------------------------------------------------

def get_gpu_memory_info(device_id=0):
    """Get GPU memory usage info (returns dict with used, total, free, percent)."""
    try:
        if not torch.cuda.is_available():
            return None

        total = torch.cuda.get_device_properties(device_id).total_memory / (1024 ** 3)  # GB
        reserved = torch.cuda.memory_reserved(device_id) / (1024 ** 3)  # GB
        allocated = torch.cuda.memory_allocated(device_id) / (1024 ** 3)  # GB
        free = (total - allocated) / (1024 ** 3)  # GB
        percent = (allocated / total) * 100 if total > 0 else 0

        return {
            "device": f"cuda:{device_id}",
            "total_gb": total,
            "allocated_gb": allocated,
            "reserved_gb": reserved,
            "free_gb": free,
            "percent_used": percent,
        }
    except Exception as e:
        logger.warning(f"Failed to get GPU memory info: {e}")
        return None


def log_gpu_memory(device_ids=[0, 1], prefix=""):
    """Log GPU memory usage for one or more devices."""
    try:
        for device_id in device_ids:
            info = get_gpu_memory_info(device_id)
            if info:
                msg = f"{prefix}GPU cuda:{device_id}: {info['allocated_gb']:.1f}GB / {info['total_gb']:.1f}GB ({info['percent_used']:.1f}%)"
                if info['percent_used'] > GPU_MEMORY_WARNING_PERCENT:
                    logger.warning(f"⚠️  {msg}")
                else:
                    logger.info(msg)
    except Exception as e:
        logger.debug(f"Error logging GPU memory: {e}")


# ---------------------------------------------------------------------------
# Model loaders
# ---------------------------------------------------------------------------

def load_tts():
    global generator, vocoder, tts_device, _symbols

    import torch
    import params
    from model import GradTTS
    from text.symbols import symbols as sym
    from env import AttrDict
    from models import Generator as HiFiGAN

    _symbols   = sym
    tts_device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    logger.info(f"TTS device: {tts_device}")

    logger.info("Loading Grad-TTS (Manx graphemic)...")
    generator = GradTTS(
        len(sym) + 1,
        params.n_spks, params.spk_emb_dim,
        params.n_enc_channels, params.filter_channels,
        params.filter_channels_dp, params.n_heads, params.n_enc_layers,
        params.enc_kernel, params.enc_dropout, params.window_size,
        params.n_feats, params.dec_dim,
        params.beta_min, params.beta_max, params.pe_scale,
    )
    generator.load_state_dict(
        torch.load(GRAD_TTS_CKPT, map_location=lambda loc, storage: loc)
    )
    generator.to(tts_device).eval()
    logger.info(f"Grad-TTS loaded ({generator.nparams:,} params)")

    logger.info("Loading HiFi-GAN vocoder...")
    with open(HIFIGAN_CONFIG) as f:
        h = AttrDict(json.load(f))
    vocoder = HiFiGAN(h)
    vocoder.load_state_dict(
        torch.load(HIFIGAN_CKPT, map_location=lambda loc, storage: loc)["generator"]
    )
    vocoder.to(tts_device).eval()
    vocoder.remove_weight_norm()
    logger.info("HiFi-GAN loaded — ready")


def load_asr():
    global whisper_model, whisper_processor

    import torch
    from transformers import (
        WhisperForConditionalGeneration,
        WhisperFeatureExtractor,
        WhisperTokenizer,
        WhisperProcessor,
    )

    logger.info(f"Loading Whisper large-v3 → {WHISPER_DEVICE} ...")
    local_snapshot = next(
        (WHISPER_HUB / "whisper_checkpoint" / "models--openai--whisper-large-v3" / "snapshots").iterdir()
    )
    hf_snapshot = next(
        (Path(HF_HOME) / "hub" / "models--openai--whisper-large-v3" / "snapshots").iterdir()
    )

    feature_extractor = WhisperFeatureExtractor.from_pretrained(local_snapshot)
    tokenizer         = WhisperTokenizer.from_pretrained(hf_snapshot)
    whisper_processor = WhisperProcessor(feature_extractor=feature_extractor, tokenizer=tokenizer)

    whisper_model = WhisperForConditionalGeneration.from_pretrained(local_snapshot)
    sb_ckpt = WHISPER_CKPT / "whisper.ckpt"
    state = torch.load(sb_ckpt, map_location="cpu")
    state = {k: v for k, v in state.items() if k != "_mel_filters"}
    if "proj_out.weight" not in state and "model.decoder.embed_tokens.weight" in state:
        state["proj_out.weight"] = state["model.decoder.embed_tokens.weight"]
    whisper_model.load_state_dict(state, strict=True)

    lang_id = tokenizer.convert_tokens_to_ids("<|english|>")
    task_id = tokenizer.convert_tokens_to_ids("<|transcribe|>")
    whisper_model.generation_config.forced_decoder_ids = [(1, lang_id), (2, task_id)]

    whisper_model.half().eval().to(WHISPER_DEVICE)  # fp16 on CPU first, then move ~3GB to GPU
    logger.info("Whisper loaded — ready")


def load_mt():
    global nllb_tokenizers, nllb_models

    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

    nllb_device = torch.device(NLLB_DEVICE if torch.cuda.is_available() else "cpu")
    lang_codes  = {"gv2en": "glv_Latn", "en2gv": "eng_Latn"}

    for direction, src_lang in lang_codes.items():
        model_dir = str(NLLB_CHECKPOINTS / direction / "best")
        logger.info(f"Loading NLLB {direction} → {nllb_device} ...")
        nllb_tokenizers[direction] = AutoTokenizer.from_pretrained(model_dir, src_lang=src_lang)
        nllb_models[direction]     = (
            AutoModelForSeq2SeqLM.from_pretrained(model_dir).to(nllb_device).eval()
        )
        logger.info(f"NLLB {direction} loaded")


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def synthesize_text(text: str, output_path: str, speed: float = 1.0):
    if generator is None or vocoder is None:
        raise RuntimeError("TTS models not loaded")
    import torch
    import numpy as np
    from scipy.io.wavfile import write as wav_write
    from text import text_to_sequence
    from utils import intersperse

    sequence = text_to_sequence(text, dictionary=None)
    x = torch.LongTensor(intersperse(sequence, len(_symbols))).to(tts_device).unsqueeze(0)
    x_lengths = torch.LongTensor([x.shape[-1]]).to(tts_device)

    with torch.no_grad():
        y_enc, y_dec, attn = generator.forward(
            x, x_lengths,
            n_timesteps=10,
            temperature=1.5,
            stoc=False,
            spk=None,
            length_scale=1.0 / speed,  # smaller = faster; speed 1.0 = 100%
        )
        audio = (
            vocoder.forward(y_dec)
            .cpu().squeeze().clamp(-1, 1).numpy() * 32768
        ).astype(np.int16)

    wav_write(output_path, 22050, audio)
    logger.info(f"Synthesised: '{text[:60]}' → {output_path}")
    record_request("tts")


def normalise_audio(input_path: str) -> str:
    """Convert any audio file to 16kHz mono 16-bit PCM WAV via ffmpeg.
    Returns path to the normalised temp file (caller must delete it)."""
    out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out_tmp_name = out_tmp.name
    out_tmp.close()
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-err_detect", "ignore_err",  # tolerate minor header issues
                "-i", input_path,
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                out_tmp_name,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr_lines = result.stderr.decode(errors="replace").strip().splitlines()
            raise RuntimeError("ffmpeg normalisation failed: " + " | ".join(stderr_lines[-3:]))
        return out_tmp_name
    except Exception:
        if os.path.exists(out_tmp_name):
            os.unlink(out_tmp_name)
        raise


def transcribe_audio(audio_path: str) -> str:
    import torch
    import torchaudio

    normalised_path = normalise_audio(audio_path)
    try:
        wav, sr = torchaudio.load(normalised_path)
    finally:
        os.unlink(normalised_path)

    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    wav = wav.mean(0)  # mono

    inputs   = whisper_processor(wav.numpy(), sampling_rate=16000, return_tensors="pt")
    features = inputs.input_features.to(WHISPER_DEVICE).half()

    with torch.no_grad():
        ids = whisper_model.generate(features, num_beams=4, max_new_tokens=444)

    transcript = whisper_processor.tokenizer.decode(ids[0], skip_special_tokens=True).strip()
    logger.info(f"Transcribed: '{transcript[:60]}'")
    record_request("asr")
    return transcript


def translate_text(direction: str, text: str) -> str:
    import torch

    tgt_lang  = "eng_Latn" if direction == "gv2en" else "glv_Latn"
    tokenizer = nllb_tokenizers[direction]
    model     = nllb_models[direction]
    dev       = next(model.parameters()).device

    inputs = tokenizer(text, return_tensors="pt", padding=True,
                       truncation=True, max_length=256).to(dev)
    forced_bos_id = tokenizer.convert_tokens_to_ids(tgt_lang)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_id,
            num_beams=5,
            max_length=256,
        )

    translation = tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
    logger.info(f"Translated ({direction}): '{text[:40]}' → '{translation[:40]}'")
    record_request("mt")
    return translation


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Manx Language Platform", version="0.2.0")

# One semaphore per model type — prevents GPU OOM under concurrent load.
# TTS + ASR can overlap (different GPUs). Each type is serialised individually.
tts_sem = asyncio.Semaphore(1)
asr_sem = asyncio.Semaphore(1)
mt_sem  = asyncio.Semaphore(1)
vc_sem  = asyncio.Semaphore(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.on_event("startup")
async def startup():
    # Load models synchronously — CUDA contexts must be created in a consistent
    # thread. Blocking here is fine; uvicorn won't serve requests until startup completes.
    # Models are loaded with fault tolerance: if one fails, others still start.

    # Log initial GPU memory state
    log_gpu_memory([0, 1], prefix="Initial ")

    # Load TTS
    try:
        load_tts()
        model_status["tts"] = True
        logger.info("✓ TTS loaded successfully")
        log_gpu_memory([0], prefix="After TTS: ")
    except Exception as e:
        model_status["tts"] = False
        model_errors["tts"] = str(e)
        logger.exception("✗ TTS failed to load")

    # Load ASR
    try:
        load_asr()
        model_status["asr"] = True
        logger.info("✓ ASR loaded successfully")
        log_gpu_memory([1], prefix="After ASR: ")
    except Exception as e:
        model_status["asr"] = False
        model_errors["asr"] = str(e)
        logger.exception("✗ ASR failed to load")

    # Load MT
    try:
        load_mt()
        model_status["mt"] = True
        logger.info("✓ MT loaded successfully")
        log_gpu_memory([0], prefix="After MT: ")
    except Exception as e:
        model_status["mt"] = False
        model_errors["mt"] = str(e)
        logger.exception("✗ MT failed to load")

    # Load VC
    try:
        vc_load_models(device=VC_DEVICE)
        model_status["vc"] = True
        logger.info("✓ Voice conversion loaded successfully")
        log_gpu_memory([0], prefix="After VC: ")
    except Exception as e:
        model_status["vc"] = False
        model_errors["vc"] = str(e)
        logger.exception("✗ Voice conversion failed to load")

    # Check if at least TTS is available (minimum viable service)
    if not model_status["tts"]:
        logger.warning("⚠️  TTS unavailable — synthesize endpoint will not work")

    # Warm-up TTS (optional — skip via SKIP_TTS_WARMUP=true)
    if model_status["tts"] and os.environ.get("SKIP_TTS_WARMUP", "false").lower() != "true":
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
            _tmp = _f.name
        try:
            synthesize_text("Fastyr mie", _tmp)
            logger.info("✓ TTS warm-up complete")
        except Exception:
            logger.warning("⚠️  TTS warm-up failed — first request may be slower")
        finally:
            if os.path.exists(_tmp):
                os.unlink(_tmp)
    elif os.environ.get("SKIP_TTS_WARMUP", "false").lower() == "true":
        logger.info("TTS warm-up skipped (SKIP_TTS_WARMUP=true)")

    # Verify OUTPUT_DIR is writable before serving any requests
    try:
        _test_file = os.path.join(OUTPUT_DIR, ".write_test")
        with open(_test_file, "w") as _f:
            _f.write("ok")
        os.unlink(_test_file)
        logger.info(f"✓ Output directory writable: {OUTPUT_DIR}")
    except Exception as e:
        logger.error(f"✗ Output directory not writable: {OUTPUT_DIR} — {e}")

    # Start background cleanup task
    global _cleanup_task
    _cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info(f"✓ Output cleanup scheduled (TTL: {OUTPUT_CLEANUP_TTL_HOURS}h, interval: {OUTPUT_CLEANUP_INTERVAL_MINUTES}m)")

    # Report startup status
    available = [k for k, v in model_status.items() if v]
    unavailable = [k for k, v in model_status.items() if not v]
    if available:
        logger.info(f"✓ Startup complete. Available: {', '.join(available)}")
    if unavailable:
        logger.warning(f"⚠️  Unavailable: {', '.join(unavailable)}")

    # Final GPU memory check
    gpu0 = get_gpu_memory_info(0)
    gpu1 = get_gpu_memory_info(1)
    if gpu0 and gpu0['percent_used'] > GPU_MEMORY_WARNING_PERCENT:
        logger.warning(f"⚠️  GPU cuda:0 memory critical: {gpu0['allocated_gb']:.1f}/{gpu0['total_gb']:.1f}GB ({gpu0['percent_used']:.1f}%) — concurrent requests may fail")
    if gpu1 and gpu1['percent_used'] > GPU_MEMORY_WARNING_PERCENT:
        logger.warning(f"⚠️  GPU cuda:1 memory critical: {gpu1['allocated_gb']:.1f}/{gpu1['total_gb']:.1f}GB ({gpu1['percent_used']:.1f}%) — concurrent requests may fail")


@app.on_event("shutdown")
async def shutdown():
    """Clean up background tasks on shutdown."""
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class SynthesizeRequest(BaseModel):
    text: str
    gender: str = "male"  # "male" or "female" — both anonymised via mandatory kNN-VC (privacy protection)
    speed: float = 1.0  # synthesis speed multiplier; 1.0 = 100%, clamped to 0.5–1.5 (50–150%)
    class Config:
        json_schema_extra = {"example": {"text": "Fastyr mie, kys t'ou?", "gender": "male"}}


@app.get("/health")
async def health_check():
    """Report server health and model availability."""
    return {
        "status": "healthy" if all(model_status.values()) else "unhealthy",
        "models": model_status,
        "errors": model_errors if model_errors else None,
    }


@app.get("/gpu-status")
async def gpu_status():
    """Report GPU memory usage for both devices."""
    gpu0 = get_gpu_memory_info(0)
    gpu1 = get_gpu_memory_info(1)
    return {
        "cuda:0": gpu0,
        "cuda:1": gpu1,
        "warning_threshold_percent": GPU_MEMORY_WARNING_PERCENT,
    }


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index):
        with open(index) as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Frontend not found. Place index.html in /frontend.</h1>")


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    # Check if TTS is available
    if not model_status["tts"]:
        raise HTTPException(
            status_code=503,
            detail=f"TTS model unavailable: {model_errors.get('tts', 'unknown error')}"
        )

    # Check disk space before synthesis
    free_mb, enough_space = check_disk_space()
    if not enough_space:
        raise HTTPException(
            status_code=507,
            detail=f"Insufficient disk space (available: {free_mb:.0f}MB, required: {MIN_DISK_FREE_MB}MB)"
        )

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text must not be empty.")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Text too long (max 500 chars).")
    if req.gender not in ("male", "female"):
        raise HTTPException(status_code=400, detail="gender must be 'male' or 'female'.")
    speed = max(0.5, min(1.5, req.speed))  # clamp to supported 50–150% range

    filename    = f"{uuid.uuid4().hex}.wav"
    output_path = os.path.join(OUTPUT_DIR, filename)
    loop        = asyncio.get_running_loop()

    # Check if voice conversion is available (REQUIRED for all requests)
    if not model_status["vc"]:
        raise HTTPException(
            status_code=503,
            detail=f"Voice conversion model unavailable: {model_errors.get('vc', 'unknown error')}"
        )

    # TTS synthesis
    async with tts_sem:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, synthesize_text, text, output_path, speed),
                timeout=SYNTHESIZE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.exception(f"Synthesis timeout (>{SYNTHESIZE_TIMEOUT_SECONDS}s)")
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise HTTPException(status_code=504, detail=f"Synthesis timeout (max {SYNTHESIZE_TIMEOUT_SECONDS}s)")
        except Exception as e:
            logger.exception("Synthesis failed")
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise HTTPException(status_code=500, detail=f"Synthesis error: {e}")

    # Voice conversion is MANDATORY for ALL requests (privacy protection)
    # The original Grad-TTS output is recognizable and must NEVER be returned
    async with vc_sem:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, vc_convert, output_path, output_path, req.gender),
                timeout=CONVERT_TIMEOUT_SECONDS
            )
            logger.info(f"Voice conversion complete ({req.gender})")
        except asyncio.TimeoutError:
            logger.exception(f"Voice conversion timeout (>{CONVERT_TIMEOUT_SECONDS}s)")
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise HTTPException(status_code=504, detail=f"Voice conversion timeout (max {CONVERT_TIMEOUT_SECONDS}s)")
        except Exception as e:
            logger.exception("Voice conversion failed")
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise HTTPException(status_code=500, detail=f"Voice conversion error: {e}")

    return {"audio_url": f"/audio/{filename}", "filename": filename}


@app.get("/audio/{filename}")
async def get_audio(filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.post("/transcribe")
async def transcribe(
    request: Request,
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
):
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    # Accept both `file` (legacy single-file) and `files` (multi-file)
    if file is not None:
        files = [file]
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    if not model_status["asr"]:
        raise HTTPException(
            status_code=503,
            detail=f"ASR model unavailable: {model_errors.get('asr', 'unknown error')}"
        )

    allowed_types = {"audio/wav", "audio/wave", "audio/x-wav", "audio/mpeg",
                     "audio/mp4", "audio/ogg", "audio/webm", "audio/flac",
                     "application/octet-stream"}

    loop = asyncio.get_running_loop()
    results = []

    for file in files:
        fname = file.filename or "unnamed"

        # Reject missing or disallowed content types
        if not file.content_type or file.content_type not in allowed_types:
            results.append({"filename": fname, "error": f"Unsupported or missing audio type: {file.content_type}"})
            continue

        tmp_path = None
        try:
            # No suffix — let ffmpeg detect format from content, not extension
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
                tmp_path = tmp.name  # Set before read so finally can clean up on disconnect
                tmp.write(await file.read())

            # Duration check via ffprobe (works on any format, before ffmpeg normalisation)
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
                    capture_output=True, text=True, timeout=5
                )
                duration = float(probe.stdout.strip())
                if duration > 30.0:
                    results.append({"filename": fname, "error": f"Audio too long ({duration:.1f}s). Maximum is 30 seconds."})
                    continue
            except Exception:
                pass  # If probe fails, let transcription attempt and fail naturally

            async with asr_sem:
                transcript = await asyncio.wait_for(
                    loop.run_in_executor(None, transcribe_audio, tmp_path),
                    timeout=TRANSCRIBE_TIMEOUT_SECONDS
                )
            results.append({"filename": fname, "transcript": transcript})

        except asyncio.TimeoutError:
            logger.exception(f"Transcription timeout (>{TRANSCRIBE_TIMEOUT_SECONDS}s) for {fname}")
            results.append({"filename": fname, "error": f"Transcription timeout (max {TRANSCRIBE_TIMEOUT_SECONDS}s)"})
        except Exception as e:
            logger.exception(f"Transcription failed for {fname}")
            results.append({"filename": fname, "error": f"Transcription error: {e}"})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # For single file, return flat response for backwards compatibility
    if len(results) == 1:
        r = results[0]
        if "error" in r:
            raise HTTPException(status_code=400, detail=r["error"])
        return {"transcript": r["transcript"]}

    return {"results": results}


class TranslateRequest(BaseModel):
    text: str
    direction: str  # "gv2en" or "en2gv"


@app.post("/translate")
async def translate(req: TranslateRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    # Check if MT is available
    if not model_status["mt"]:
        raise HTTPException(
            status_code=503,
            detail=f"MT model unavailable: {model_errors.get('mt', 'unknown error')}"
        )

    if req.direction not in ("gv2en", "en2gv"):
        raise HTTPException(status_code=400, detail=f"Unknown direction: {req.direction}")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty input text.")
    if len(req.text) > 500:
        raise HTTPException(status_code=400, detail="Text too long (max 500 chars).")

    loop = asyncio.get_running_loop()
    try:
        async with mt_sem:
            translation = await asyncio.wait_for(
                loop.run_in_executor(None, translate_text, req.direction, req.text.strip()),
                timeout=TRANSLATE_TIMEOUT_SECONDS
            )
        return {"translation": translation}
    except asyncio.TimeoutError:
        logger.exception(f"Translation timeout (>{TRANSLATE_TIMEOUT_SECONDS}s)")
        raise HTTPException(status_code=504, detail=f"Translation timeout (max {TRANSLATE_TIMEOUT_SECONDS}s)")
    except Exception as e:
        logger.exception("Translation failed")
        raise HTTPException(status_code=500, detail=f"Translation error: {e}")



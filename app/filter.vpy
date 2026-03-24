"""
filter.vpy — Anime encoding filter chain for av1an + SVT-AV1
Target: 30–60MB per 24-minute episode
Requirements:
    pip install vsutil
    pacman -S vapoursynth
    VapourSynth plugins needed:
        - knlmeanscl     (denoising)
        - f3kdb / neo_f3kdb (debanding)
        - nnedi3 / eedi3 (AA / upscaling)
        - descale        (native resolution detection)
        - mvtools        (motion-compensated degrain)
        - havsfunc       (helper functions)
        - kagefunc       (edge masking)
        - vsutil         (utility helpers)

    Install plugins via:
        yay -S vapoursynth-plugin-knlmeanscl-git \
               vapoursynth-plugin-neo_f3kdb-git \
               vapoursynth-plugin-nnedi3-git \
               vapoursynth-plugin-descale-git \
               vapoursynth-plugin-mvtools-git \
               vapoursynth-plugin-havsfunc-git
"""

import vapoursynth as vs
import sys
import os

core = vs.core

# ─────────────────────────────────────────
# CONFIGURATION — tune these per source
# ─────────────────────────────────────────

INPUT   = os.environ.get("INPUT", "/input/source.mkv")

# --- Descale settings ---
# Most modern anime is drawn at a lower native res then upscaled.
# Common native heights: 720, 810, 864, 900 (for 1080p BDs)
# Set DESCALE to False if you're not sure or source is genuine 1080p.
DESCALE         = True
NATIVE_HEIGHT   = 900       # try 720, 810, 864, 900 — pick by lowest descale error
DESCALE_KERNEL  = "Bicubic" # Bicubic (b=0, c=0.5) covers most anime
DESCALE_B       = 0         # Catmull-Rom is common; Mitchell is (1/3, 1/3)
DESCALE_C       = 0.5

# --- Denoise settings ---
# Higher h = more aggressive denoising = smaller file, less detail
# For clean sources:  h=0.8 – 1.2
# For grainy BDs:     h=1.5 – 2.5
DENOISE_H        = 1.4      # luma strength
DENOISE_H_CHROMA = 1.0      # chroma strength (usually lower)
DENOISE_TEMPORAL = 3        # temporal radius (1–4); higher = smoother, more ghosting risk

# --- Deband settings ---
# Higher values = more banding removed but risk of detail loss
DEBAND_RANGE  = 18
DEBAND_Y      = 72          # luma threshold
DEBAND_CB     = 56
DEBAND_CR     = 56
DEBAND_GRAIN  = 0           # set to 0; we don't want synthetic grain inflating file size

# --- AA settings ---
# Only enable if source has obvious jaggies/aliasing
DO_AA = True

# --- Output ---
# Keep YUV420P10 for SVT-AV1; it handles 10-bit natively
OUTPUT_BITS = 10


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def get_w(height, src_width, src_height, only_even=True):
    """Calculate proportional width for a target height."""
    width = src_width * height / src_height
    if only_even:
        width = round(width / 2) * 2
    return int(width)


def mask_edges(clip, mode="sobel", scale=1.5):
    """
    Return an edge mask for protecting detail during debanding.
    """
    mask = core.std.Sobel(clip, planes=0) if mode == "sobel" \
        else core.std.Prewitt(clip, planes=0)
    # Expand mask slightly so edges are protected by a small radius
    mask = core.std.Maximum(mask, planes=0)
    mask = core.std.Inflate(mask, planes=0)
    return mask


def limit_filter(filtered, src, thr=1.0):
    """
    Limit the diff between filtered and src to ±thr (in 8-bit scale).
    Prevents over-processing in areas that didn't need it.
    """
    from functools import partial
    peak = (1 << filtered.format.bits_per_sample) - 1
    thr_scaled = int(thr * peak / 255)
    return core.std.Expr(
        [filtered, src],
        expr=f"x y - {thr_scaled} > y {thr_scaled} + "
             f"x y - -{thr_scaled} < y {thr_scaled} - x ? ?",
    )


# ─────────────────────────────────────────
# 1. SOURCE
# ─────────────────────────────────────────

src = core.ffms2.Source(INPUT)

# Make sure we're in YUV colorspace
if src.format.color_family != vs.YUV:
    src = core.resize.Bicubic(src, format=vs.YUV420P8)

# Convert to 16-bit for processing (more precision, avoids rounding errors)
src16 = core.resize.Bicubic(src, format=vs.YUV420P16)

orig_w = src16.width
orig_h = src16.height


# ─────────────────────────────────────────
# 2. DESCALE
# ─────────────────────────────────────────
# Undo the upscale that studios apply, then re-upscale sharply.
# This removes the soft "blurry" look of the upscaled source and
# reduces the amount of data the encoder needs to represent.

if DESCALE and orig_h > NATIVE_HEIGHT:
    native_w = get_w(NATIVE_HEIGHT, orig_w, orig_h)

    try:
        import descale
        if DESCALE_KERNEL == "Bicubic":
            descaled = core.descale.Debicubic(
                src16,
                width=native_w,
                height=NATIVE_HEIGHT,
                b=DESCALE_B,
                c=DESCALE_C,
            )
        elif DESCALE_KERNEL == "Lanczos":
            descaled = core.descale.Delanczos(
                src16, width=native_w, height=NATIVE_HEIGHT, taps=4
            )
        else:
            descaled = core.descale.Debilinear(
                src16, width=native_w, height=NATIVE_HEIGHT
            )

        # Re-upscale using nnedi3 (high quality) back to original resolution
        # nnedi3 doubles each time, so we may need to resize after
        rescaled = core.nnedi3.nnedi3(descaled, field=1, dh=True, nsize=4, nq=2, ns=2)
        rescaled = core.resize.Spline36(
            rescaled,
            width=orig_w,
            height=orig_h,
            src_top=-0.5,   # correct nnedi3 field shift
        )

        # Only use descaled luma; keep chroma from original (chroma is rarely upscaled separately)
        clip = core.std.ShufflePlanes(
            [rescaled, src16], planes=[0, 1, 2], colorfamily=vs.YUV
        )

    except Exception as e:
        print(f"[filter.vpy] Descale failed ({e}), skipping descale step.", file=sys.stderr)
        clip = src16
else:
    clip = src16


# ─────────────────────────────────────────
# 3. DENOISE  (motion-compensated + NLMeans)
# ─────────────────────────────────────────
# Two-pass:
#   a) MVTools temporal degrain for motion-compensated noise removal
#   b) KNLMeansCL spatial NLMeans as a cleanup pass

# Convert to 16-bit YUV if not already
clip_denoise = clip

# --- MVTools motion-compensated degrain ---
try:
    # Downscale for MVTools analysis (faster, less prone to false matches)
    clip_mv = core.resize.Bicubic(clip_denoise, format=vs.YUV420P8)
    sup    = core.mv.Super(clip_mv, hpad=16, vpad=16, rfilter=4)
    bvec1  = core.mv.Analyse(sup, isb=True,  delta=1, overlap=8, blksize=16, search=3)
    fvec1  = core.mv.Analyse(sup, isb=False, delta=1, overlap=8, blksize=16, search=3)

    if DENOISE_TEMPORAL >= 2:
        bvec2 = core.mv.Analyse(sup, isb=True,  delta=2, overlap=8, blksize=16, search=3)
        fvec2 = core.mv.Analyse(sup, isb=False, delta=2, overlap=8, blksize=16, search=3)
        degrained8 = core.mv.Degrain2(
            clip_mv, sup, bvec1, fvec1, bvec2, fvec2,
            thsad=300, thsadc=200,
        )
    else:
        degrained8 = core.mv.Degrain1(
            clip_mv, sup, bvec1, fvec1,
            thsad=300, thsadc=200,
        )

    # Back to 16-bit
    degrained = core.resize.Bicubic(degrained8, format=vs.YUV420P16)

except Exception as e:
    print(f"[filter.vpy] MVTools degrain failed ({e}), skipping.", file=sys.stderr)
    degrained = clip_denoise

# --- KNLMeansCL spatial pass ---
# Works on 16-bit float intermediary; h controls aggressiveness
try:
    clip_float = core.resize.Bicubic(degrained, format=vs.YUVssP16)  # try float path
except Exception:
    clip_float = degrained

try:
    denoised = core.knlm.KNLMeansCL(
        degrained,
        h=DENOISE_H,
        a=2,                    # search window radius
        d=DENOISE_TEMPORAL,     # temporal radius
        channels="Y",           # luma only first
        device_type="cpu",
    )
    denoised = core.knlm.KNLMeansCL(
        denoised,
        h=DENOISE_H_CHROMA,
        a=2,
        d=1,
        channels="UV",          # then chroma
        device_type="cpu",
    )
except Exception as e:
    print(f"[filter.vpy] KNLMeans failed ({e}), using MVTools result only.", file=sys.stderr)
    denoised = degrained


# ─────────────────────────────────────────
# 4. ANTI-ALIASING
# ─────────────────────────────────────────
# Thin-line AA using nnedi3 on the luma plane only.
# Skipped on flat backgrounds; only applied where edges exist.

if DO_AA:
    try:
        # Work in 8-bit for speed
        aa_input = core.resize.Bicubic(denoised, format=vs.YUV420P8)

        # nnedi3 AA: double, then shrink back with eedi3 field match
        aa_doubled = core.nnedi3.nnedi3(aa_input, field=1, dh=True, nsize=3, nq=1, ns=1)
        aa_shrunk  = core.resize.Spline36(aa_doubled, orig_w, orig_h, src_top=-0.5)

        # Build an edge mask to only apply AA where edges are
        edge_mask = mask_edges(aa_input, mode="sobel")
        edge_mask16 = core.resize.Bicubic(edge_mask, format=vs.GRAY8)

        aa_merged = core.std.MaskedMerge(aa_input, aa_shrunk, edge_mask16, planes=0)

        # Back to 16-bit
        aa_clip = core.resize.Bicubic(aa_merged, format=vs.YUV420P16)

    except Exception as e:
        print(f"[filter.vpy] AA failed ({e}), skipping.", file=sys.stderr)
        aa_clip = denoised
else:
    aa_clip = denoised


# ─────────────────────────────────────────
# 5. DEBAND
# ─────────────────────────────────────────
# f3kdb removes banding (gradient color stepping) which wastes bits.
# Edge mask protects fine detail from being smoothed.

try:
    # f3kdb expects 16-bit input
    # Try neo_f3kdb first (actively maintained fork), fall back to f3kdb
    try:
        debanded = core.neo_f3kdb.Deband(
            aa_clip,
            range=DEBAND_RANGE,
            y=DEBAND_Y,
            cb=DEBAND_CB,
            cr=DEBAND_CR,
            grainy=DEBAND_GRAIN,
            grainc=DEBAND_GRAIN,
            sample_mode=2,
            blur_first=True,
        )
    except AttributeError:
        # Fall back to standard f3kdb
        debanded = core.f3kdb.Deband(
            aa_clip,
            range=DEBAND_RANGE,
            y=DEBAND_Y,
            cb=DEBAND_CB,
            cr=DEBAND_CR,
            grainy=DEBAND_GRAIN,
            grainc=DEBAND_GRAIN,
            output_depth=16,
        )

    # Build edge mask at 16-bit to protect detail
    edge_mask_full = mask_edges(
        core.resize.Bicubic(aa_clip, format=vs.GRAY16), mode="sobel"
    )
    # Merge: keep original (aa_clip) at edges, debanded elsewhere
    debanded = core.std.MaskedMerge(debanded, aa_clip, edge_mask_full, planes=0)

except Exception as e:
    print(f"[filter.vpy] Deband failed ({e}), skipping.", file=sys.stderr)
    debanded = aa_clip


# ─────────────────────────────────────────
# 6. LIMIT FILTER STRENGTH
# ─────────────────────────────────────────
# Clamp the total change vs. original source to avoid over-processing.
# Especially useful if you're not sure how aggressive your settings are.

final = limit_filter(debanded, src16, thr=3.0)


# ─────────────────────────────────────────
# 7. OUTPUT
# ─────────────────────────────────────────
# Convert to YUV420P10 for SVT-AV1 (10-bit is more efficient than 8-bit at low bitrates)

out = core.resize.Bicubic(
    final,
    format=vs.YUV420P10,
    matrix_in_s="709",
    transfer_in_s="709",
    primaries_in_s="709",
    range_in_s="limited",
    dither_type="error_diffusion",  # better than the default ordered dither
)

out.set_output()

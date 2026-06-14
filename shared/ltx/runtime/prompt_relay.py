from __future__ import annotations

import logging
import math
from typing import Any

import torch


log = logging.getLogger(__name__)


def build_temporal_cost(q_token_idx, Lq, Lk, device, dtype, tokens_per_frame):
    offset = torch.zeros(Lq, Lk, device=device, dtype=dtype)
    query_frames = torch.arange(Lq, device=device, dtype=torch.long) // tokens_per_frame

    for segment in q_token_idx:
        local = segment["local_token_idx"].to(device=device)
        distance = (query_frames.float()[:, None] - segment["midpoint"]).abs()
        strength = segment.get("strength", 1.0)
        cost = strength * (torch.relu(distance - segment["window"]) ** 2) / (2 * segment["sigma"] ** 2)
        offset[:, local] = cost.to(offset.dtype)

    return offset


def build_temporal_cost_scaled(q_token_idx, Lq, Lk, device, dtype, latent_frames):
    offset = torch.zeros(Lq, Lk, device=device, dtype=dtype)
    query_frames = torch.arange(Lq, device=device, dtype=torch.float32) * latent_frames / Lq

    for segment in q_token_idx:
        local = segment["local_token_idx"].to(device=device)
        distance = (query_frames[:, None] - segment["midpoint"]).abs()
        sigma_audio = segment.get("sigma_audio", segment["sigma"])
        window_audio = segment.get("window_audio", segment["window"])
        strength_audio = segment.get("strength_audio", 1.0)
        cost = strength_audio * (torch.relu(distance - window_audio) ** 2) / (2 * sigma_audio ** 2)
        offset[:, local] = cost.to(offset.dtype)

    return offset


def create_mask_fn(q_token_idx, fallback_tokens_per_frame, latent_frames):
    if not q_token_idx:
        return lambda q, k, transformer_options: None

    cache = {}
    max_token_idx = max(int(segment["local_token_idx"].max().item()) for segment in q_token_idx) + 1

    def mask_fn(q, k, transformer_options):
        Lq, Lk = q.shape[1], k.shape[1]
        if Lq == Lk:
            return None

        cond_or_uncond = transformer_options.get("cond_or_uncond", [])
        if 1 in cond_or_uncond and 0 not in cond_or_uncond:
            return None

        grid_sizes = transformer_options.get("grid_sizes", None)
        video_tpf = int(grid_sizes[1]) * int(grid_sizes[2]) if grid_sizes is not None else fallback_tokens_per_frame
        video_lq = latent_frames * video_tpf
        if Lk == video_lq or Lk < max_token_idx:
            return None

        mode = "video" if Lq == video_lq else "scaled"
        key = (Lq, Lk, mode, q.device)
        if key not in cache:
            if mode == "video":
                cost = build_temporal_cost(q_token_idx, Lq, Lk, q.device, q.dtype, video_tpf)
            else:
                cost = build_temporal_cost_scaled(q_token_idx, Lq, Lk, q.device, q.dtype, latent_frames)
            log.info("Built LTX Prompt Relay penalty matrix: mode=%s Lq=%d Lk=%d", mode, Lq, Lk)
            cache[key] = -cost

        return cache[key].to(q.dtype)

    return mask_fn


def build_segments(token_ranges, segment_lengths, epsilon=1e-3, relay_options=None):
    sigma = 1.0 / math.log(1.0 / epsilon) if 0 < epsilon < 1 else 0.1448
    options = relay_options or {}
    video_strength = options.get("video_strength", 1.0)
    video_window_scale = options.get("video_window_scale", 1.0)
    audio_epsilon = options.get("audio_epsilon")
    audio_strength = options.get("audio_strength", 1.0)
    audio_window_scale = options.get("audio_window_scale", 1.0)
    sigma_audio = 1.0 / math.log(1.0 / audio_epsilon) if audio_epsilon is not None and 0 < audio_epsilon < 1 else sigma

    segments = []
    frame_cursor = 0
    for (token_start, token_end), length in zip(token_ranges, segment_lengths):
        if length <= 0:
            frame_cursor += length
            continue
        midpoint = (2 * frame_cursor + length) // 2
        base_window = max(length // 2 - 2, 0)
        segments.append({
            "local_token_idx": torch.arange(token_start, token_end),
            "midpoint": midpoint,
            "window": max(base_window * video_window_scale, 0.0),
            "sigma": sigma,
            "strength": video_strength,
            "window_audio": max(base_window * audio_window_scale, 0.0),
            "sigma_audio": sigma_audio,
            "strength_audio": audio_strength,
        })
        frame_cursor += length
    return segments


def get_raw_tokenizer(clip):
    tokenizer_wrapper = clip.tokenizer
    for attr_name in dir(tokenizer_wrapper):
        if attr_name.startswith("_"):
            continue
        inner = getattr(tokenizer_wrapper, attr_name, None)
        if inner is not None and hasattr(inner, "tokenizer"):
            return inner.tokenizer
    if callable(tokenizer_wrapper):
        return tokenizer_wrapper
    raise RuntimeError("Could not find raw tokenizer on CLIP object for LTX Prompt Relay.")


def map_token_indices(raw_tokenizer, global_prompt, local_prompts):
    prefixed_locals = [" " + prompt for prompt in local_prompts]
    full_prompt = str(global_prompt or "") + "".join(prefixed_locals)
    has_eos = getattr(raw_tokenizer, "add_eos", False)
    eos_adjustment = 1 if has_eos else 0

    previous_length = len(raw_tokenizer(str(global_prompt or ""))["input_ids"]) - eos_adjustment
    token_ranges = []
    built = str(global_prompt or "")
    for local_prompt in prefixed_locals:
        built += local_prompt
        current_length = len(raw_tokenizer(built)["input_ids"]) - eos_adjustment
        if current_length <= previous_length:
            raise ValueError(f"Local prompt produced no tokens: '{local_prompt.strip()}'")
        token_ranges.append((previous_length, current_length))
        previous_length = current_length

    return full_prompt, token_ranges


def distribute_segment_lengths(num_segments, latent_frames, specified_lengths=None):
    if specified_lengths:
        if len(specified_lengths) != num_segments:
            raise ValueError(
                f"Number of segment lengths ({len(specified_lengths)}) must match number of prompts ({num_segments})."
            )
        lengths = specified_lengths
    else:
        step = -(-latent_frames // num_segments)
        lengths = [step] * num_segments

    effective = []
    cursor = 0
    for length in lengths:
        end = min(cursor + length, latent_frames)
        effective.append(max(end - cursor, 0))
        cursor = end
    return effective


def convert_pixel_lengths_to_latent_lengths(pixel_lengths: list[int], temporal_stride: int, latent_frames: int) -> list[int]:
    if not pixel_lengths:
        return []
    total_pixel = sum(pixel_lengths)
    if total_pixel <= 0:
        return [1] * len(pixel_lengths)

    naive_total = max(1, round(total_pixel / temporal_stride))
    target_total = min(latent_frames, naive_total)
    if target_total >= latent_frames - 1:
        target_total = latent_frames

    exact = [length * target_total / total_pixel for length in pixel_lengths]
    result = [int(value) for value in exact]
    diff = target_total - sum(result)
    if diff > 0:
        order = sorted(range(len(exact)), key=lambda index: -(exact[index] - int(exact[index])))
        for index in range(diff):
            result[order[index % len(order)]] += 1

    for index in range(len(result)):
        if result[index] < 1:
            max_index = max(range(len(result)), key=lambda item: result[item])
            if result[max_index] > 1:
                result[max_index] -= 1
                result[index] = 1
    return result


def encode_prompt_relay(model: Any, clip: Any, latent: dict[str, Any], global_prompt: str, local_prompts: list[str], pixel_lengths: list[int], epsilon: float):
    if not local_prompts:
        raise ValueError("LTX runtime requires at least one non-gap section prompt.")
    if any(not str(prompt or "").strip() for prompt in local_prompts):
        raise ValueError("There is a section on the timeline missing an effective prompt.")

    from .patches import apply_ltx_patches, detect_ltx_model_geometry

    patch_size, temporal_stride = detect_ltx_model_geometry(model)
    samples = latent["samples"]
    latent_frames = samples.shape[2]
    tokens_per_frame = max(1, (samples.shape[3] // patch_size[1]) * (samples.shape[4] // patch_size[2]))
    latent_lengths = convert_pixel_lengths_to_latent_lengths(pixel_lengths, temporal_stride, latent_frames) if pixel_lengths else None

    raw_tokenizer = get_raw_tokenizer(clip)
    full_prompt, token_ranges = map_token_indices(raw_tokenizer, global_prompt, local_prompts)
    conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(full_prompt))
    effective_lengths = distribute_segment_lengths(len(local_prompts), latent_frames, latent_lengths)
    relay_segments = build_segments(token_ranges, effective_lengths, epsilon)
    mask_fn = create_mask_fn(relay_segments, tokens_per_frame, latent_frames)
    patched = apply_ltx_patches(model, mask_fn)
    return patched, conditioning, {
        "full_prompt": full_prompt,
        "local_prompts": local_prompts,
        "pixel_lengths": pixel_lengths,
        "latent_lengths": effective_lengths,
        "token_ranges": token_ranges,
    }

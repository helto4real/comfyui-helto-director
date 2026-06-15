from __future__ import annotations

import math
import types
from typing import Any

import torch


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
    raise RuntimeError("Could not find raw tokenizer on CLIP object for WAN Prompt Relay.")


def prompt_relay_prompt_parts(prompt_relay: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    global_prompt = str(prompt_relay.get("global_prompt") or "").strip()
    prompted_segments = [
        segment
        for segment in prompt_relay.get("local_prompts", [])
        if str(segment.get("prompt") or "").strip()
    ]
    return global_prompt, prompted_segments


def map_token_indices(raw_tokenizer, global_prompt: str, prompted_segments: list[dict[str, Any]]):
    prefixed_locals = [" " + str(segment.get("prompt") or "").strip() for segment in prompted_segments]
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
            raise ValueError(f"WAN Prompt Relay local prompt produced no tokens: '{local_prompt.strip()}'")
        token_ranges.append((previous_length, current_length))
        previous_length = current_length
    return full_prompt, token_ranges


def build_segments(token_ranges, prompted_segments: list[dict[str, Any]], epsilon: float):
    sigma = 1.0 / math.log(1.0 / epsilon) if 0 < epsilon < 1 else 0.1448
    segments = []
    for (token_start, token_end), prompt_segment in zip(token_ranges, prompted_segments):
        start = int(prompt_segment.get("latent_segment_start") or 0)
        end = int(prompt_segment.get("latent_segment_end_exclusive") or start)
        length = max(1, end - start)
        midpoint = start + (length / 2.0)
        window = max(length / 2.0 - 1.0, 0.0)
        segments.append({
            "local_token_idx": torch.arange(token_start, token_end),
            "midpoint": midpoint,
            "window": window,
            "sigma": sigma,
        })
    return segments


def create_mask_fn(q_token_idx, latent_chunks: int):
    if not q_token_idx:
        return lambda q, k, transformer_options: None

    cache = {}

    def mask_fn(q, k, transformer_options):
        lq, lk = q.shape[1], k.shape[1]
        if lq <= 0 or lk <= 0:
            return None
        key = (lq, lk, q.device, q.dtype)
        if key in cache:
            return cache[key]
        offset = torch.zeros(lq, lk, device=q.device, dtype=q.dtype)
        query_chunks = torch.arange(lq, device=q.device, dtype=torch.float32) * max(1, latent_chunks) / max(1, lq)
        for segment in q_token_idx:
            local = segment["local_token_idx"].to(device=q.device)
            local = local[local < lk]
            if local.numel() == 0:
                continue
            distance = (query_chunks[:, None] - float(segment["midpoint"])).abs()
            cost = (torch.relu(distance - float(segment["window"])) ** 2) / (2 * float(segment["sigma"]) ** 2)
            offset[:, local] = cost.to(offset.dtype)
        cache[key] = -offset
        return cache[key]

    return mask_fn


def encode_wan_prompt_relay(model: Any, clip: Any, prompt_relay: dict[str, Any]):
    conditioning, prompt_debug, mask_fn = prepare_wan_prompt_relay_payload(clip, prompt_relay)
    if mask_fn is None:
        return model, conditioning, prompt_debug
    patched_model = apply_wan_prompt_relay_patches(model, mask_fn)
    prompt_debug["patched"] = True
    return patched_model, conditioning, prompt_debug


def prepare_wan_prompt_relay_payload(clip: Any, prompt_relay: dict[str, Any]):
    validate_segment_lengths(prompt_relay)
    global_prompt, prompted_segments = prompt_relay_prompt_parts(prompt_relay)
    full_prompt = global_prompt
    token_ranges = []
    relay_segments = []
    if prompted_segments:
        raw_tokenizer = get_raw_tokenizer(clip)
        full_prompt, token_ranges = map_token_indices(raw_tokenizer, global_prompt, prompted_segments)
        relay_segments = build_segments(token_ranges, prompted_segments, float(prompt_relay.get("epsilon", 0.001)))
    conditioning = clip.encode_from_tokens_scheduled(clip.tokenize(full_prompt))
    if not prompted_segments:
        return conditioning, {
            "full_prompt": full_prompt,
            "local_prompts": [],
            "token_ranges": [],
            "patched": False,
        }, None
    mask_fn = create_mask_fn(relay_segments, int(prompt_relay.get("latent_chunk_count") or 1))
    return conditioning, {
        "full_prompt": full_prompt,
        "local_prompts": prompted_segments,
        "token_ranges": token_ranges,
        "patched": False,
    }, mask_fn


def patch_wan_prompt_relay_models(high_noise_model, low_noise_model, mask_fn):
    patched_high = apply_wan_prompt_relay_patches(high_noise_model, mask_fn) if high_noise_model is not None else None
    patched_low = apply_wan_prompt_relay_patches(low_noise_model, mask_fn) if low_noise_model is not None else None
    return patched_high, patched_low


def validate_segment_lengths(prompt_relay: dict[str, Any]) -> None:
    segment_lengths = [int(length) for length in prompt_relay.get("segment_lengths") or []]
    latent_chunk_count = int(prompt_relay.get("latent_chunk_count") or 0)
    if segment_lengths and sum(segment_lengths) != latent_chunk_count:
        raise ValueError(
            f"WAN_PROMPT_RELAY_SEGMENT_LENGTH_MISMATCH: segment_lengths sum to {sum(segment_lengths)}, expected {latent_chunk_count}."
        )


def apply_wan_prompt_relay_patches(model: Any, mask_fn):
    model_clone = model.clone()
    diffusion_model = _diffusion_model(model_clone)
    blocks = getattr(diffusion_model, "blocks", None) or getattr(diffusion_model, "transformer_blocks", None)
    if not blocks:
        raise ValueError("WAN Prompt Relay requires a WAN model with diffusion_model.blocks cross-attention modules.")
    patched = 0
    for index, block in enumerate(blocks):
        module = getattr(block, "cross_attn", None)
        if module is None:
            continue
        key = f"diffusion_model.blocks.{index}.cross_attn.forward"
        if key in getattr(model_clone, "object_patches", {}):
            raise RuntimeError(f"WAN Prompt Relay cross-attention forward at '{key}' is already patched by another node.")
        patch = _CrossAttnPatch(mask_fn).__get__(module, module.__class__)
        model_clone.add_object_patch(key, patch)
        patched += 1
    if patched <= 0:
        raise ValueError("WAN Prompt Relay found no cross-attention modules to patch.")
    return model_clone


def _diffusion_model(model):
    if hasattr(model, "get_model_object"):
        return model.get_model_object("diffusion_model")
    return model.model.diffusion_model


class _CrossAttnPatch:
    def __init__(self, mask_fn):
        self.mask_fn = mask_fn

    def __get__(self, obj, objtype=None):
        mask_fn = self.mask_fn

        def wrapped(self_module, x, context, *args, transformer_options=None, **kwargs):
            return _wan_cross_attention_forward(self_module, mask_fn, x, context, *args, transformer_options=transformer_options or {}, **kwargs)

        return types.MethodType(wrapped, obj)


def _wan_cross_attention_forward(self, mask_fn, x, context, *args, transformer_options=None, **kwargs):
    import comfy.ldm.modules.attention

    context_img_len = kwargs.get("context_img_len")
    if context_img_len is None and args:
        context_img_len = args[0]

    if context_img_len:
        context_img = context[:, :context_img_len]
        text_context = context[:, context_img_len:]
    else:
        context_img = None
        text_context = context

    q = self.norm_q(self.q(x))
    k = self.norm_k(self.k(text_context))
    v = self.v(text_context)
    mask = mask_fn(q, k, transformer_options or {})
    out = comfy.ldm.modules.attention.attention_pytorch(
        q,
        k,
        v,
        heads=self.num_heads,
        mask=mask,
        transformer_options=transformer_options or {},
    )

    if context_img is not None and hasattr(self, "k_img") and hasattr(self, "v_img"):
        k_img = self.norm_k_img(self.k_img(context_img))
        v_img = self.v_img(context_img)
        out = out + comfy.ldm.modules.attention.optimized_attention(
            q,
            k_img,
            v_img,
            heads=self.num_heads,
            transformer_options=transformer_options or {},
        )

    return self.o(out)

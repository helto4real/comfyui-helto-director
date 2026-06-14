from __future__ import annotations

import logging
import os
import types
from typing import Any


log = logging.getLogger(__name__)

PROMPT_RELAY_ATTENTION_ENV = "HELTO_LTX_PROMPT_RELAY_ATTENTION"
PROMPT_RELAY_ATTENTION_MODES = {"auto", "optimized", "pytorch"}
_optimized_masked_attention_failed = False


def _prompt_relay_attention_mode() -> str:
    mode = os.environ.get(PROMPT_RELAY_ATTENTION_ENV, "auto").strip().lower()
    if mode not in PROMPT_RELAY_ATTENTION_MODES:
        log.warning("Unknown %s=%r; using auto masked attention selection.", PROMPT_RELAY_ATTENTION_ENV, mode)
        return "auto"
    return mode


def _attention_pytorch(q, k, v, heads, mask, transformer_options=None, **kwargs):
    import comfy.ldm.modules.attention

    return comfy.ldm.modules.attention.attention_pytorch(
        q,
        k,
        v,
        heads,
        mask=mask,
        _inside_attn_wrapper=True,
        transformer_options=transformer_options or {},
        **kwargs,
    )


def _attention_optimized_masked(q, k, v, heads, mask, transformer_options=None, **kwargs):
    import comfy.ldm.modules.attention

    attention_fn = comfy.ldm.modules.attention.optimized_attention_for_device(q.device, mask=True)
    return attention_fn(
        q,
        k,
        v,
        heads,
        mask=mask,
        _inside_attn_wrapper=True,
        transformer_options=transformer_options or {},
        **kwargs,
    )


def _masked_attention(q, k, v, heads, mask, transformer_options=None, **kwargs):
    global _optimized_masked_attention_failed

    mode = _prompt_relay_attention_mode()
    if mode == "pytorch" or _optimized_masked_attention_failed:
        return _attention_pytorch(q, k, v, heads, mask, transformer_options, **kwargs)

    try:
        return _attention_optimized_masked(q, k, v, heads, mask, transformer_options, **kwargs)
    except Exception as exc:
        _optimized_masked_attention_failed = True
        log.warning("LTX Prompt Relay optimized masked attention failed; falling back to PyTorch attention: %s", exc)
        return _attention_pytorch(q, k, v, heads, mask, transformer_options, **kwargs)


def _ltx_forward(self, mask_fn, x, context=None, mask=None, pe=None, k_pe=None, transformer_options=None):
    import comfy.ldm.modules.attention
    from comfy.ldm.lightricks.model import apply_rotary_emb

    transformer_options = transformer_options or {}
    is_self_attn = context is None
    context = x if is_self_attn else context

    q = self.q_norm(self.to_q(x))
    k = self.k_norm(self.to_k(context))
    v = self.to_v(context)

    if pe is not None:
        q = apply_rotary_emb(q, pe)
        k = apply_rotary_emb(k, pe if k_pe is None else k_pe)

    if not is_self_attn:
        temporal_mask = mask_fn(q, k, transformer_options)
        if temporal_mask is not None:
            mask = temporal_mask if mask is None else mask + temporal_mask

    if mask is None:
        out = comfy.ldm.modules.attention.optimized_attention(
            q,
            k,
            v,
            self.heads,
            attn_precision=self.attn_precision,
            transformer_options=transformer_options,
        )
    else:
        out = _masked_attention(
            q,
            k,
            v,
            self.heads,
            mask=mask,
            attn_precision=self.attn_precision,
            transformer_options=transformer_options,
        )

    if self.to_gate_logits is not None:
        gate_logits = self.to_gate_logits(x)
        batch, tokens, _ = out.shape
        out = out.view(batch, tokens, self.heads, self.dim_head)
        out = out * (2.0 * gate_logits.sigmoid()).unsqueeze(-1)
        out = out.view(batch, tokens, self.heads * self.dim_head)
    return self.to_out(out)


class _CrossAttnPatch:
    def __init__(self, impl, mask_fn):
        self.impl = impl
        self.mask_fn = mask_fn

    def __get__(self, obj, objtype=None):
        impl = self.impl
        mask_fn = self.mask_fn

        def wrapped(self_module, *args, **kwargs):
            return impl(self_module, mask_fn, *args, **kwargs)

        return types.MethodType(wrapped, obj)


def detect_ltx_model_geometry(model: Any) -> tuple[tuple[int, int, int], int]:
    diff_model = model.model.diffusion_model
    if not hasattr(diff_model, "patchifier"):
        raise ValueError(
            f"LTX runtime requires an LTX model with a patchifier; got {type(diff_model).__name__}."
        )
    temporal_stride = int(getattr(diff_model, "vae_scale_factors", (8, 32, 32))[0])
    return (1, 1, 1), temporal_stride


def _check_unpatched(model_clone: Any, key: str) -> None:
    if key in getattr(model_clone, "object_patches", {}):
        raise RuntimeError(
            f"LTX Prompt Relay cross-attention forward at '{key}' is already patched by another node."
        )


def apply_ltx_patches(model: Any, mask_fn):
    model_clone = model.clone()
    diffusion_model = model_clone.get_model_object("diffusion_model")
    for index, block in enumerate(diffusion_model.transformer_blocks):
        for attr in ("attn2", "audio_attn2"):
            module = getattr(block, attr, None)
            if module is None:
                continue
            key = f"diffusion_model.transformer_blocks.{index}.{attr}.forward"
            _check_unpatched(model_clone, key)
            patch = _CrossAttnPatch(_ltx_forward, mask_fn).__get__(module, module.__class__)
            model_clone.add_object_patch(key, patch)
    return model_clone

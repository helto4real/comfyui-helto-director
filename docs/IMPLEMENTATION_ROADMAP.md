# Implementation Roadmap

This document is the user-facing roadmap summary. The detailed locked product design remains in [PLAN.md](../PLAN.md), and the current engineering status is tracked in [phase_status.md](../phase_status.md).

## Current Status

- The generic `Video Timeline Director` is implemented and remains model-agnostic.
- LTX 2.3 has Config, Planner, Runtime, source-video guidance, audio handling, prompt optimizer, privacy mode, and identity/reference helpers.
- WAN 2.2 has Config, Planner, and a single Timeline Runtime node.
- WAN 2.2 Runtime supports Plan Only diagnostics and partial ComfyUI Core execution.

## WAN 2.2 Runtime Scope

WAN support is currently intended for inspection and controlled workflow hardening:

- `Plan Only` validates and explains the timeline plan without executing tensor conditioning.
- `Auto` resolves to `ComfyUI Core` only when required backend inputs are connected.
- `ComfyUI Core` can build text conditioning, patch connected high/low model phases for Prompt Relay, create a WAN latent, and apply Start/End image keyframes.
- Timed image keyframes are preserved in `requested_keyframes` and reported as unsupported when the selected backend cannot apply them.

## Deferred Work

- WanVideoWrapper adapter.
- Arbitrary Timed visual keyframe tensor application.
- WAN source-video conditioning.
- WAN audio-conditioned generation, S2V, Animate, and reference library support.
- Production-ready WAN sampler graph recipes for every model/VAE/sampler combination.

See [WAN 2.2 Timeline Support](WAN22_SUPPORT.md) and [WAN 2.2 Manual Test Checklist](WAN22_MANUAL_TEST_CHECKLIST.md) for practical verification.

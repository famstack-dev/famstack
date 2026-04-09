# ADR-009: Managed AI with oMLX as Default

## Status
Accepted

## Context
famstack needs a local LLM backend for document classification, voice transcription, text-to-speech, and chat. The main options on Apple Silicon:

- **Ollama**: popular, easy to install, but runs GGUF models. Ollama's Go wrapper adds measurable overhead (37% slower than native llama.cpp in our benchmarks).
- **oMLX**: runs MLX-native models on Metal GPU. Includes a tiered KV cache that overflows to SSD, allowing it to serve models larger than available RAM and resume long conversations without re-processing context.
- **LM Studio**: GUI app, not scriptable, not suitable as a headless backend.
- **Bring your own**: user points to any OpenAI-compatible endpoint.

We also considered auto-detecting whatever is already running. This created a fragile waterfall of probes and confused users who had multiple backends installed.

### Research
We chose oMLX primarily for two reasons:

1. **Smart caching.** oMLX's tiered KV cache spills to SSD instead of keeping everything in RAM. This is critical for a family server that runs multiple services. A 32 GB Mac can serve models that would otherwise need 48+ GB, and long conversations resume instantly instead of re-processing the full context.

2. **Cutting edge Apple Silicon optimization.** MLX is Apple's own framework, built for their unified memory architecture. oMLX achieved 38 tok/s on Qwen3.5 (M1 Max) vs 17 tok/s for LM Studio MLX and 26 tok/s for Ollama with the same model. It tracks MLX optimizations closely and benefits from Apple's own investment in the framework.

References:
- [MLX vs GGUF Part 2: Isolating Variables](https://famstack.dev/guides/mlx-vs-gguf-part-2-isolating-variables/)
- Benchmark tool: [local-llm-bench](https://github.com/famstack-dev/local-llm-bench)

## Decision
Two modes, chosen at install time:

1. **Managed** (default): famstack installs and manages oMLX via Homebrew. Models are selected by RAM tier and downloaded through the oMLX admin API. The stack controls the full lifecycle.
2. **External**: user provides any OpenAI-compatible endpoint URL and API key. famstack just uses it, no management.

No auto-detection. No fallback waterfall. One explicit choice.

## Consequences
- Apple Silicon performance is maximized by default (MLX on Metal GPU)
- SSD caching means the server stays responsive even when memory is tight from other stacklets running
- Users with existing setups (Ollama, cloud APIs) can point to them without conflict
- oMLX is less mainstream than Ollama, which means smaller community and fewer pre-quantized models
- RAM-tiered model selection prevents users from downloading models that don't fit their hardware
- The managed path requires Homebrew, which is an extra dependency

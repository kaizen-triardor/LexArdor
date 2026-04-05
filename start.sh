#!/bin/bash
# LexArdor v2 — Start script
# Launches llama-server (CUDA + KV cache quantization) + FastAPI backend
#
# Usage:
#   ./start.sh              # Start with default fast model (Qwen 9B)
#   ./start.sh --heavy      # Start with reasoning model (DeepSeek-R1 32B)
#   ./start.sh --deepseek   # Start with DeepSeek-R1 32B
#   ./start.sh --qwen27b    # Start with Qwen 27B Opus
#   ./start.sh --gemma      # Start with Gemma 12B (verifier)
#   ./start.sh --saul       # Start with SaulLM 7B (legal verifier)
#   ./start.sh --gemma4-2b  # Start with Gemma 4 E2B Q8 (agent)
#   ./start.sh --gemma4     # Start with Gemma 4 E4B Q8 (fast)
#   ./start.sh --gemma4-31b # Start with Gemma 4 31B Q4 (reasoning)
#   ./start.sh --api-only   # Start only FastAPI (llama-server managed by model_router)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# llama.cpp CUDA build
LLAMA_SERVER="$HOME/.local/bin/llama-server-cuda"
export LD_LIBRARY_PATH="$HOME/.local/lib/llama:$LD_LIBRARY_PATH"

# ── Model paths ──────────────────────────────────────────────────────────────
MODEL_FAST="$HOME/models/lexardor/Qwen3.5-9B.Q8_0.gguf"
MODEL_DEEPSEEK="$HOME/models/lexardor/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf"
MODEL_QWEN27B="$HOME/models/lexardor/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled.i1-Q4_K_M.gguf"
MODEL_GEMMA="$HOME/models/lexardor/gemma-3-12b-it.Q4_K_M.gguf"
MODEL_SAUL="$HOME/models/lexardor/Saul-7B-Instruct-v1.i1-Q4_K_M.gguf"
MODEL_GEMMA4_2B="$HOME/models/lexardor/gemma-4-e2b-it-Q8_0.gguf"
MODEL_GEMMA4_4B="$HOME/models/lexardor/gemma-4-E4B-it-Q8_0.gguf"
MODEL_GEMMA4_31B="$HOME/models/lexardor/gemma-4-31B-it-Q4_K_M.gguf"

# KV cache — FP16 (quantized cache requires Flash Attention which
# isn't supported for Qwen3.5 hybrid attention layers on this GPU)
KV_CACHE_K="f16"
KV_CACHE_V="f16"
CTX_SIZE=16384

# ── Parse arguments ──────────────────────────────────────────────────────────
API_ONLY=false
MODEL="$MODEL_FAST"
MODEL_NAME="Qwen 3.5 9B Q8 (Fast)"
MODEL_KEY="qwen9b"

case "${1:-}" in
    --heavy|--deepseek)
        MODEL="$MODEL_DEEPSEEK"
        MODEL_NAME="DeepSeek-R1 32B Q4 (Reasoning)"
        MODEL_KEY="deepseek"
        ;;
    --qwen27b|--27b)
        MODEL="$MODEL_QWEN27B"
        MODEL_NAME="Qwen 27B Opus Q4 (Reasoning)"
        MODEL_KEY="qwen27b"
        ;;
    --gemma)
        MODEL="$MODEL_GEMMA"
        MODEL_NAME="Gemma 3 12B Q4 (Verifier)"
        MODEL_KEY="gemma"
        CTX_SIZE=8192
        ;;
    --saul)
        MODEL="$MODEL_SAUL"
        MODEL_NAME="SaulLM 7B Q4 (Legal Verifier)"
        MODEL_KEY="saul"
        ;;
    --gemma4-2b)
        MODEL="$MODEL_GEMMA4_2B"
        MODEL_NAME="Gemma 4 E2B Q8 (Agent)"
        MODEL_KEY="gemma4_2b"
        CTX_SIZE=8192
        ;;
    --gemma4|--gemma4-4b)
        MODEL="$MODEL_GEMMA4_4B"
        MODEL_NAME="Gemma 4 E4B Q8 (Fast)"
        MODEL_KEY="gemma4_4b"
        ;;
    --gemma4-31b)
        MODEL="$MODEL_GEMMA4_31B"
        MODEL_NAME="Gemma 4 31B Q4 (Reasoning)"
        MODEL_KEY="gemma4_31b"
        ;;
    --api-only)
        API_ONLY=true
        MODEL_NAME="(managed by model_router)"
        ;;
esac

# Check model file exists
if [ "$API_ONLY" = false ] && [ ! -f "$MODEL" ]; then
    echo "ERROR: Model file not found: $MODEL"
    echo "Available models:"
    for f in "$HOME"/models/lexardor/*.gguf; do
        [ -f "$f" ] && echo "  $(basename "$f") ($(du -h "$f" | cut -f1))"
    done
    exit 1
fi

echo "============================================"
echo "  LexArdor v2 — AI pravni asistent"
echo "============================================"
echo "  Model: $MODEL_NAME"
if [ "$API_ONLY" = false ]; then
    echo "  KV Cache: K=$KV_CACHE_K V=$KV_CACHE_V"
    echo "  Context: $CTX_SIZE tokens"
fi
echo "  LLM server: http://localhost:8081"
echo "  Dashboard:  http://localhost:8080"
echo "============================================"

# Kill any existing processes on our ports
lsof -i :8081 -t 2>/dev/null | xargs -r kill 2>/dev/null || true
lsof -i :8080 -t 2>/dev/null | xargs -r kill 2>/dev/null || true
sleep 1

if [ "$API_ONLY" = false ]; then
    # Start llama-server with selected model
    echo "Starting llama-server with $MODEL_NAME..."
    "$LLAMA_SERVER" \
        --model "$MODEL" \
        --n-gpu-layers 99 \
        --port 8081 \
        --host 0.0.0.0 \
        --ctx-size "$CTX_SIZE" \
        --cache-type-k "$KV_CACHE_K" \
        --cache-type-v "$KV_CACHE_V" \
        --threads $(( $(nproc) / 2 )) \
        2>/tmp/lexardor-llama.log &
    LLAMA_PID=$!
    echo "  llama-server PID: $LLAMA_PID"

    # Wait for llama-server to be ready
    echo "  Waiting for model to load..."
    for i in $(seq 1 60); do
        if curl -s http://localhost:8081/health | grep -q "ok"; then
            echo "  llama-server ready!"
            break
        fi
        sleep 2
    done
fi

# Start FastAPI backend
echo "Starting LexArdor backend..."
cd "$SCRIPT_DIR"

# Export the initial model key so model_router knows what's loaded
export LEXARDOR_INITIAL_MODEL="$MODEL_KEY"

source venv/bin/activate 2>/dev/null || true
python3 -c "import uvicorn; from app import app; uvicorn.run(app, host='0.0.0.0', port=8080)" &
API_PID=$!
echo "  Backend PID: $API_PID"
sleep 2

echo ""
echo "LexArdor is running!"
echo "  Dashboard: http://localhost:8080"
echo "  Admin: admin / admin123"
echo ""
echo "  Swap models at runtime via:"
echo "    POST /api/admin/swap-model?model_key=deepseek"
echo "    POST /api/admin/swap-model?model_key=fast"
echo ""
echo "Press Ctrl+C to stop..."

# Handle shutdown
if [ "$API_ONLY" = false ]; then
    trap "kill $LLAMA_PID $API_PID 2>/dev/null; echo 'LexArdor stopped.'" EXIT
else
    trap "kill $API_PID 2>/dev/null; echo 'LexArdor stopped.'" EXIT
fi
wait

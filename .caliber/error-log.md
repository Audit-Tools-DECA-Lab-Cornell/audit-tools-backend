# Generation Error — 2026-04-25T08:25:03.956Z

**Provider**: claude-cli
**Model**: default
**Stop reason**: timeout_inactivity

## Raw LLM Output
```
(empty)
```

## Troubleshooting

This failure was caused by a timeout. You can adjust timeouts with:
```bash
# Increase inactivity timeout (default: 120s)
export CALIBER_STREAM_INACTIVITY_TIMEOUT_MS=180000

# Increase total generation timeout (default: 600s)
export CALIBER_GENERATION_TIMEOUT_MS=900000
```

If timeouts persist, try a different model.
# Phlox Backend

FastAPI backend for Phlox. See the top-level [README](../README.md) and
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

```bash
uv sync
cp config.yml.example config.yml   # edit with your provider profiles
uv run uvicorn app.main:app --reload --port 8000
```

# Claude Chat Export
**Generated: 2025-12-10 17:17:08**

---

👤 **USER:**
> read CLAUDE.md for rules and instructions. check documentation in root and docs/ directory. Check docs/chats/ for privious conversations

I'll read the CLAUDE.md file and check the documentation to understand the rules and previous conversations.

Now let me read the key documentation files:

Perfect! I've now read all the documentation and understand the project. Let me summarize what I've learned:

## Summary

I've successfully reviewed all the programming rules and project documentation:

### Programming Rules (from [CLAUDE.md](CLAUDE.md) & [AI.md](AI.md)):
1. **Never start coding immediately** - Always propose solution first and wait for explicit implementation request
2. Follow **PEP8 + type hints** for all Python code
3. Use **pydantic** for request/response models (REST APIs)
4. Keep routes lean - move logic into services
5. Include **docstrings** (Google or NumPy style)
6. Keep modules **under ~800 lines**
7. Store configs in `.env` with `python-dotenv` (never hard-coded)
8. Use custom exceptions and `logging` (not `print`)
9. Write tests with pytest in `tests/` directory
10. Keep documentation in `docs/` directory

### Project: EODHD Real-Time Candle Aggregator v1.0
A microservice that:
- Connects to EODHD WebSocket for real-time tick data
- Aggregates ticks into configurable OHLCV candles (1, 5, 15, 30, 60 min intervals)
- Provides REST API for ticker management and candle retrieval
- Uses SQLite for persistence with auto-detection (Docker `/data` vs local `./data`)
- Requires API key authentication
- Dockerized with health checks

### Current Project Structure:
```
eodhd_realtime_candles/
├── src/
│   ├── main.py, config.py, storage.py
│   ├── candle_engine.py, websocket_manager.py
│   └── api/ (routes.py, middleware.py)
├── data/ (local SQLite storage)
├── tests/ (pytest)
├── docs/ (documentation + chats/)
├── n8n_workflows/
└── Docker files + requirements.txt
```

### Recent History (from [setting-up-project-structure-and-documentation-review-2025-12-09.md](docs/chats/setting-up-project-structure-and-documentation-review-2025-12-09.md)):
- Project structure was recently aligned with specification
- Fixed database path auto-detection (Docker vs local)
- Fixed `.env` loading issue by adding `python-dotenv`
- Updated [AI.md](AI.md) with common pitfalls section

### Roadmap:
- **v1.1**: Prometheus metrics, dead ticker detection, market hours, batch ops
- **v2.0**: Technical indicators, multi-interval, WebSocket output, alert webhooks

I'm ready to help! What would you like to work on?


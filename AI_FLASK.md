# CLAUDE.md – Python Web Application

## General
- Follow **PEP8** and use **type hints**.
- Organize app into `app/` with `models/`, `routes/`, `services/`, `templates/`, `static/`.
- Use **Jinja2 templates** or React frontends – but keep JS/CSS in separate files.
- Add docstrings for public routes and services.

## Rules
- Never hardcode secrets → use `.env` + `python-decouple` or `pydantic.BaseSettings`.
- Separate **business logic from routes** (e.g., `services/`).
- Limit files to ~800 lines.

## Testing
- Use `pytest` or Django/Flask/FastAPI test client.
- Add at least **smoke tests** for routes.

## Error Handling
- Centralize HTTP error handlers (e.g., `404`, `500`).
- Return JSON for API endpoints, HTML for web pages.

✅ Example:
@router.get("/users/{user_id}")
async def get_user(user_id: int, db: Session = Depends(get_db)):
    """Fetch a user by ID."""
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
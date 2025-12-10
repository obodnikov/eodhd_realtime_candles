# CLAUDE.md – Python REST API Project

## General
- Follow **PEP8 + type hints**.
- Organize into `src/` with `api/`, `services/`, `models/`, `schemas/`.
- Use **pydantic** for request/response models.
- Keep routes lean – move logic into `services/`.

## Dependencies
- Core: `fastapi`, `pydantic`, `sqlalchemy` (or `tortoise-orm`).
- Use `uvicorn` for local dev.

## Error Handling
- Centralize error handling middleware.
- Return structured JSON errors:  
{"error": "User not found", "code": 404}


## Testing

* Use pytest + HTTP client.
* Add at least 1 test per endpoint.

✅ Example:

class UserResponse(BaseModel):
    id: int
    name: str

@app.get("/users/{id}", response_model=UserResponse)
async def get_user(id: int):
    ...
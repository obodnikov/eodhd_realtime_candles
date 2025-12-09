# AI Guidelines for Python Project

This file defines how AI assistants should generate or modify code in this repository.  
The goal: keep output consistent, production-ready, and aligned with our coding standards.

---

## General Rules
- Always use **PEP8 style** (formatting, naming, imports).
- Add **type hints** to all functions and classes.
- Include **docstrings** for functions, classes, and modules (Google or NumPy style).
- Write tests for new functionality (pytest).
- Prefer **f-strings** for string formatting.
- Handle exceptions with clear messages, no silent failures.

---

## Project Structure
- Do not generate large monolithic files.
- Keep modules **under ~800 lines** for readability and maintainability.
- Organize code into `src/`, `tests/`, `scripts/` when possible.
- Configurations must be stored in `.env` or `config.yaml`, **never hard-coded**.
- Keep test files only in tests/ directory
- Keep **ALL** documentation files, except README.md, in separated docs/ directory

---

## Dependencies
- Use `requirements.txt` or `pyproject.toml` for dependencies.
- Prefer standard library when possible before adding new libraries.
- If adding a library, explain why it’s needed.

---

## Error Handling
- Centralize error handling where possible.
- Use custom exceptions (`class ProjectError(Exception):`) instead of raw `Exception`.
- Log errors using `logging`, not `print`.

---

## Examples
✅ Good:
def load_data(path: str) -> pd.DataFrame:
    """Load CSV file into a DataFrame."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    return pd.read_csv(path)
❌ Bad:
def loaddata(p):
    return pd.read_csv(p)  # no error handling, unclear naming
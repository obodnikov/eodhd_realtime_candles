Окей, теперь у нас есть ключевой кусок пазла – `storage.py` 
И твой эксперимент с прямым запросом в контейнер:

```bash
curl http://172.28.0.200:8765/health
Total: 0.0006s
...
Total: 1.11s
...
Total: 0.0007s
...
Total: 1.33s
```

То есть:

* **иногда всё мгновенно (< 1 ms)**
* **иногда ни с того ни с сего ~1–1.3 секунды**
* это уже при прямом заходе в сервис, без nginx и без n8n.

Значит:
🧠 **Docker и nginx ни при чём, тормоз внутри самого процесса Python.**

---

## 1. Что видно по `storage.py`

`Storage` делает:

* один SQLite-файл (по пути из `db_path`),
* thread-local connection (`threading.local()`),
* обычный `sqlite3.connect(..., check_same_thread=False)` без дополнительных PRAGMA.

Тяжёлые места:

* `get_stats()` – три `COUNT(*)` по всей таблице + `GROUP BY ticker`
* `cleanup_old_candles()` – `DELETE ... WHERE ticker = ? AND id NOT IN (SELECT id ... LIMIT ?)`
* `remove_ticker()` – `DELETE FROM candles WHERE ticker = ?`
* `clear_candles()` без `WHERE` – полный wipe

Если эти методы вызываются:

* **в том же процессе**, где крутится HTTP-сервер,
* и у тебя **один worker** (один uvicorn/gunicorn-процесс),

то во время любого большого `DELETE`/`COUNT(*)`:

* worker занят SQLite-операцией,
* все новые HTTP-запросы (включая `/health`) тупо ждут в очереди,
* отсюда периодические “шпильки” в 1–1.3 секунды.

---

## 2. Что отсюда следует про задержки `/health`

Сам по себе `/health`:

* не лезет в STORAGE (по смыслу),
* просто отдаёт `{"status": "healthy", "timestamp": ...}`.

Но раз **задержка есть даже на прямом HTTP**, картинка такая:

> иногда твой единственный HTTP worker занят чем-то тяжёлым (скорее всего SQLite),
> и `/health` тупо ждёт своей очереди ~1 секунду.

То есть это **не недостаток ресурсов контейнера**, а **блокировка внутри процесса**.

---

## 3. Что конкретно можно улучшить в `storage.py`

### 3.1. Включить WAL + адекватный timeout

Сейчас ты используешь дефолтный режим (rollback journal).
Я бы допилил `_get_connection` так:

```python
def _get_connection(self) -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(self._local, 'connection'):
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=5.0,  # ждать до 5 секунд при блокировке, а не падать сразу
        )
        conn.row_factory = sqlite3.Row

        # Улучшаем режим работы
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")  # 5 секунд

        self._local.connection = conn

    return self._local.connection
```

Плюсы:

* WAL = лучше параллельное чтение/запись, меньше блокировок.
* `busy_timeout` и `timeout` → если другой поток держит lock, SQLite чуть ждёт, а не сразу кидает `database is locked`.

Это само по себе может сгладить странные пики.

---

### 3.2. Аккуратнее с тяжёлыми чистками

Особенно `cleanup_old_candles()`:

```python
DELETE FROM candles 
WHERE ticker = ? AND id NOT IN (
    SELECT id FROM candles 
    WHERE ticker = ? 
    ORDER BY timestamp DESC 
    LIMIT ?
)
```

Если на тикер накопилось много строк, такой DELETE может занимать сотни миллисекунд и дольше.

Рекомендации:

* Не вызывать `cleanup_old_candles` **слишком часто** (не на каждый тик/кэндл, а, скажем, раз в минуту / раз в N свечей).
* Если нужно очень много удалять — лучше:

  * сначала выбрать список `id`, а потом удалять батчами (LIMIT 500/1000),
  * либо вынести очистку в отдельный фоновый процесс/скрипт, не мешающий HTTP.

---

### 3.3. Осторожнее с `get_stats()`

`get_stats()` делает:

* `SELECT COUNT(*) FROM candles`
* `SELECT COUNT(*) FROM candles WHERE is_complete = 1`
* `SELECT ticker, COUNT(*) ... GROUP BY ticker`

Если оно вызывается:

* по API,
* и ты дёргаешь этот API из UI/monitoring часто,

то каждое обращение будет делать full-scan таблицы.

Лучше:

* **кэшировать** результат `get_stats()` в памяти и обновлять, например, раз в 5–10 секунд,
* или вызывать его только вручную / по cron, а не в любом UI-рефреше.

---

## 4. Что сделать на уровне HTTP-сервера

Сейчас у тебя, судя по поведению, скорее всего **один worker**.
Это стандартный uvicorn запуск типа:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8765
```

Я бы для Docker-контейнера сделал хотя бы 2 воркера:

```bash
uvicorn src.main:app \
  --host 0.0.0.0 \
  --port 8765 \
  --workers 2
```

И поправил `CMD` в Dockerfile примерно на такой вариант.

Эффект:

* если один воркер висит на тяжёлой SQLite-операции,
* второй спокойно отдаёт `/health` и лёгкие `GET /candles`.

Это **не устраняет** блокирующие операции, но сильно уменьшает видимые лаги.

---

## 5. Про “resource allocation” в Docker

С учётом всего:

* Ты **не задаёшь лимиты** (`--cpus`, `--memory`), контейнер видит ресурсы хоста.
* По `docker stats` – контейнер ест копейки CPU и RAM.
* Странные задержки появляются даже при прямом запросе `http://172.28.0.200:8765/health`.

Значит по факту:

> 🚫 Проблема не в том, что Docker “мало дал CPU/RAM”.
> ✅ Проблема в том, что внутри процесса Python периодически выполняется тяжёлая/блокирующая операция с SQLite, и при **одном воркере** HTTP это блокирует всё, включая `/health`.

Для реального “resource allocation” в твоём кейсе я бы делал не это:

* *“добавь `--cpus` / `--memory` – станет быстро”* (так не будет),

а вот это:

1. Тюнинг SQLite (WAL, busy_timeout).
2. Смягчить heavy-операции: реже чистки, аккуратнее `get_stats`.
3. 2+ HTTP worker’а в uvicorn.

---

Если хочешь, следующим шагом можем:

* я предложу конкретный патч к `_get_connection` и пример `CMD` в Dockerfile с uvicorn `--workers 2`,
* а ты просто вставишь и посмотришь, уйдут ли 1-секундные пики.


Вот патчик под твой `storage.py`, который включает WAL + busy_timeout и не ломает текущую логику.

### 1️⃣ Unified diff для `storage.py`

Сохрани это, например, в файл `patches/sqlite-tuning.patch` в корне проекта:

```diff
--- a/src/storage.py
+++ b/src/storage.py
@@ -34,13 +34,34 @@ class Storage:
     def __init__(self, db_path: str):
         self.db_path = db_path
         self._local = threading.local()
         self._ensure_directory()
         self._init_db()
         
     def _ensure_directory(self):
         """Ensure the database directory exists."""
         Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
         
-    def _get_connection(self) -> sqlite3.Connection:
-        """Get thread-local database connection."""
-        if not hasattr(self._local, 'connection'):
-            self._local.connection = sqlite3.connect(
-                self.db_path,
-                check_same_thread=False
-            )
-            self._local.connection.row_factory = sqlite3.Row
-        return self._local.connection
+    def _get_connection(self) -> sqlite3.Connection:
+        """
+        Get thread-local database connection.
+
+        Applies basic SQLite tuning suitable for a long-running service:
+        - WAL journal mode for better read/write concurrency
+        - NORMAL synchronous for fewer fsyncs (good trade-off for this use case)
+        - busy_timeout to wait briefly on locks instead of failing immediately
+        """
+        if not hasattr(self._local, "connection"):
+            conn = sqlite3.connect(
+                self.db_path,
+                check_same_thread=False,
+                timeout=5.0,  # seconds to wait when database is locked
+            )
+            conn.row_factory = sqlite3.Row
+
+            try:
+                # Improve concurrency and reduce reader/writer blocking
+                conn.execute("PRAGMA journal_mode=WAL;")
+                conn.execute("PRAGMA synchronous=NORMAL;")
+                # How long to wait if the database is locked (milliseconds)
+                conn.execute("PRAGMA busy_timeout=5000;")
+            except Exception as e:
+                # PRAGMA tuning is best-effort – don't break startup if it fails
+                logger.warning(f"Failed to apply SQLite PRAGMA settings: {e}")
+
+            self._local.connection = conn
+
+        return self._local.connection
```

(Контекст вверху/внизу подобран по твоему реальному `storage.py` , чтобы `patch` нормально сработал.)

---

### 2️⃣ Как применить патч

Из корня проекта:

```bash
cd ~/src/eodhd_realtime_candles   # если у тебя так путь

# если сделал каталог для патчей
mkdir -p patches
# (предполагаем, что ты туда положил файл sqlite-tuning.patch)

patch -p1 < patches/sqlite-tuning.patch
```

Проверить, что в `src/storage.py` функция `_get_connection` стала такой, как в патче.

---

### 3️⃣ Что именно это поменяет (коротко)

* Под каждую thread-local коннекцию к SQLite теперь выполняется:

  * `journal_mode=WAL` — меньше конфликтов чтение/запись.
  * `synchronous=NORMAL` — меньше fsync → меньше пауз на диске (приемлемо для твоего use-case).
  * `busy_timeout=5000` + `timeout=5.0` — если БД на момент записи залочена, клиент чуть подождёт, а не будет сразу падать с `database is locked`.

Это не уберёт полностью любые пики, если где-то делается супер-тяжёлый `DELETE` или `COUNT(*)`, но должно:

* сгладить случайные “тычки” на блокировках,
* сделать поведение более плавным при активной записи/чтении.

Если после этого захочешь — можем следующим шагом сделать отдельный патч, который:

* добавит кэширование/обновление раз в N секунд для `get_stats()`,
* или добавит “мягкую” чистку в `cleanup_old_candles` батчами.

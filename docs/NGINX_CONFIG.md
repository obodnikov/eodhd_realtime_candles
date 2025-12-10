You want:

`https://n8n.sqowe.com/eodhd/candles/TSLA` ➜ internal request to
`http://172.28.0.200:8765/candles/TSLA` (без префикса `/eodhd`).

В nginx это делается через **правильный слеш** в `proxy_pass`.

---

### 1. Добавь новый location

В твой `server { listen 443 … }` блок добавь:

```nginx
  # redirect /eodhd -> /eodhd/
  location = /eodhd {
    return 301 /eodhd/;
  }

  location /eodhd/ {
    proxy_pass http://172.28.0.200:8765/;

    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;

    proxy_buffering off;
    proxy_request_buffering off;

    client_max_body_size 16400M;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_connect_timeout 600s;
  }
```

Важно:

* `location /eodhd/` — с **завершающим слешем**.
* `proxy_pass http://172.28.0.200:8765/;` — тоже с **завершающим слешем**.

Так nginx возьмёт URL:

* вход: `/eodhd/candles/TSLA`
* вырежет префикс `/eodhd/`
* подставит остальное к `http://172.28.0.200:8765/`
* итог: `http://172.28.0.200:8765/candles/TSLA`

---

### 2. Как это использовать

Теперь:

* Запрос из браузера / n8n:

  ```text
  https://n8n.sqowe.com/eodhd/candles/TSLA
  ```
* Внутри уйдёт на:

  ```text
  http://172.28.0.200:8765/candles/TSLA
  ```

---

### 3. Не забудь перезагрузить nginx

```bash
sudo nginx -t   # проверить конфиг
sudo systemctl reload nginx
```

Если хочешь, можем дальше настроить CORS / auth для этого `/eodhd`-префикса отдельно.

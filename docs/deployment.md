# Деплой и эксплуатация

## Продакшен-схема

- **PM2-процесс `semantika-web`**: `uv run uvicorn app:app --host 127.0.0.1 --port 8090`
  (cwd `/home/ubuntu/apps/semantika-web`).
- **Angie** (конфиг `/etc/angie/sites-available/neurosemantic.ru.conf`):
  - `location /semantika/api/` → proxy на `127.0.0.1:8090`;
  - статика `/semantika/` — файлы `web/` (через симлинки из
    `/home/ubuntu/apps/seo-cluster/www/neurosemantic/semantika/`).
- **SQLite + файлы** — всё локально, внешних сервисов состояния нет.

## .env

`pipeline_env()` собирает окружение пайплайна: `os.environ` → `.env` соседнего
seo-cluster (fallback-ключи) → локальный `.env` (приоритет). Файл `chmod 600`,
в git не попадает; шаблон — `.env.example`.

| Переменная | Назначение |
|---|---|
| `OPENAI_API_KEY`, `GEMINI_API_KEY`, `VOYAGE_API_KEY`, `DASHSCOPE_API_KEY` | эмбеддинги (без ключа вариант пропускается) |
| `DEEPSEEK_API_KEY` | интенты |
| `GEMINI_RPM` | лимит запросов/мин для Gemini (дефолт 15000) |
| `DEEPSEEK_MODEL` / `DEEPSEEK_REASONING` / `DEEPSEEK_EFFORT` / `DEEPSEEK_TEMP` | модель и ризонинг DeepSeek |
| `VASTAI_API_KEY` / `VASTAI_MAX_DPH` / `VASTAI_SSH_KEY` | автоаренда RAM-машины для HAC, потолок $/час, путь к приватному ключу (дефолт `~/.ssh/id_ed25519`; публичный должен быть в аккаунте Vast.ai) |

## Обновление

```bash
cd /home/ubuntu/apps/semantika-web && git pull
# фронт (web/*.html) — готово сразу, кэша нет
pm2 restart semantika-web        # только если менялся app.py
# pipeline.py подхватывается при следующем запуске задачи — рестарт не нужен
```

## Проверки после правок

```bash
uv run python -c "import app"                                  # бэкенд импортируется
node --check <(извлечь <script> из web/*.html)                 # JS синтаксис
curl -s http://127.0.0.1:8090/semantika/api/auth/me            # 401 = процесс жив
pm2 logs semantika-web --lines 30 --nostream                   # логи
```

При правках дедупа — контрольные пары из [pipeline.md](pipeline.md); при правках
формата данных — сверка с [data-formats.md](data-formats.md) и view.html.

## Бэкап

Достаточно `semantika.db*` + `embeddings.db*` (глобальная база эмбеддингов — самое дорогое) + `projects/` + `.env`. Деревья/эмбеддинги
восстанавливаются перезапуском пайплайна (эмбеддинги — платно, поэтому `emb_*.npy`
лучше бэкапить тоже).

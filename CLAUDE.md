# Смыслотрон — CLAUDE.md

Мини-SaaS кластеризации SEO-ядра по эмбеддингам. Отдельный продукт, **не часть
seo-cluster** — общего кода нет, только `.env` seo-cluster читается как fallback
для ключей (`pipeline_env()` в app.py). Не тянуть сюда зависимости и паттерны
из seo-cluster.

## Стек и запуск

- Python 3.12, FastAPI + SQLite (WAL), без ORM; фронт — vanilla JS, три статических HTML
- `pipeline.py` — самодостаточный uv-скрипт с inline-deps (httpx, numpy, scipy, pymorphy3); app.py запускает его subprocess'ом через `uv run`
- Прод: PM2 `semantika-web` → `uv run uvicorn app:app --host 127.0.0.1 --port 8090`; angie проксирует `/semantika/api/` → :8090, статика `web/` отдаётся как `/semantika/` **через симлинки** из `/home/ubuntu/apps/seo-cluster/www/neurosemantic/semantika/`
- Деплой фронта = просто сохранить файл в `web/` (симлинки, кэша нет — фетчи с `cache:"no-cache"`). Бэкенд: `pm2 restart semantika-web`

## Инварианты — не ломать

1. **Порядок строк в `emb_*.npy` = порядок reps.** Инкрементальный кэш доэмбеддивает только хвост (`m.shape[0] < len(texts)` → vstack). Любая пересортировка/вставка в середину reps портит все кэши — тогда сносить `emb_*.npy` целиком.
2. **«Очень точная» частотность — главная метрика** (сортировка, выбор репрезентанта дубля, трафик). Fallback: очень точная → точная → базовая.
3. **`phrase_intents.json` — словарь {фраза: интент}**, живёт независимо от кластеров. Разметка пофразовая (НЕ по кластерам), доразмечаются только отсутствующие фразы. Вопросы получают «информационный» бесплатно.
4. **`.bin`-деревья и `queries.json` — контракт с фронтом.** Формат читает `view.html` (`DATA_BASE`), whitelist имён в app.py: `DATA_FILES` regex `[a-z0-9_]{1,20}_(?:hard|soft|avg)\.bin|...`. Новый вариант эмбеддинга = ключ ≤20 символов `[a-z0-9_]`.
5. **Дедуп-контроль:** «молитва матери о дочери» / «молитва дочери о матери» НЕ должны склеиваться (чанки предлогов + пороги SURE=0.97, FLOOR=0.85). При правках дедупа прогонять эти пары.
6. **Лимиты API:** OpenAI конкурентность 32; Gemini — каждая фраза батча = 1 запрос, RateLimiter по `GEMINI_RPM` (минутное окно) + Retry-After; DeepSeek ≤1500 соединений (httpx.Limits 1600), json_object, t=0.1; Qwen/DashScope батч максимум 10.
7. **Падение варианта не валит прогон:** per-variant try/except → status skipped → автодожим в worker_loop (retries<3). Деревья skip-if-exists.
8. **`.env` не коммитить** (chmod 600, живые ключи Voyage/DashScope). В git только `.env.example`.
9. **Кластеризация — только на клиенте.** Сервер строит деревья linkage; порог/режим/мин-размер/гео — union-find в view.html. Не добавлять серверный «пересчёт кластеров».
10. Появился дубль фразы при append — точные дубли схлопываются (max частот, OR флагов), новые фразы только в конец (см. инвариант 1).
11. **Шаблоны маппинга глобальные:** `mapping_templates(sig=sha1 первой строки → mapping)`, пишутся при каждом подтверждении маппинга (create/append), применяются в `/uploads` для любого пользователя. Шаблон ТОЛЬКО подставляет колонки в модалку — окно подтверждения показывается всегда, тихого автодобавления нет (пользователь запретил). Мультизагрузка в append шлёт `defer_run: true` на каждый файл и один `/run` в конце — иначе воркер стартует между файлами и второй append упрётся в running.

## Структура данных проекта

```
projects/<uid>/<pid>/
  keys.csv            вход после маппера (контракт: Запрос;Базовая;Точная;Очень точная;Топоним;Вопрос)
  meta.json, status.json, costs.json (накопительный), history.json
  emb_<variant>.npy   кэш эмбеддингов reps (порядок = reps!)
  phrase_intents.json {фраза: интент}
  result.csv          UTF-8 BOM, ';', русские заголовки
  data/               то, что видит фронт: queries.json, meta.json, <variant>_{hard,soft,avg}.bin, intents.json
```

## Стоимость

`PRICE` $/1M токенов в pipeline.py: openai 0.13, gemini 0.15, voyage 0.18,
qwen 0.07, ds_in 0.28, ds_out 0.42; USD_RUB=80. Полное ядро 33.7K фраз ≈ $1.6.
costs.json — накопительный, не перезаписывать нулями.

## Проверка после правок

```bash
node --check <(sed -n '/<script>/,/<\/script>/p' web/view.html | sed '1d;$d')  # JS синтаксис
uv run python -c "import app"                                                  # импорт бэкенда
curl -s http://127.0.0.1:8090/semantika/api/auth/me                            # процесс жив (401 — ок)
```

Тестовый аккаунт: test@vechkasov.pro / test123, полный проект «Фотосессии —
полное ядро» (33 726 фраз, 11 вариантов, интенты размечены).

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **smyslotron** (240 symbols, 517 relationships, 20 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/smyslotron/context` | Codebase overview, check index freshness |
| `gitnexus://repo/smyslotron/clusters` | All functional areas |
| `gitnexus://repo/smyslotron/processes` | All execution flows |
| `gitnexus://repo/smyslotron/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

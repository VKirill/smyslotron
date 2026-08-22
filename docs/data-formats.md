# Форматы данных

## SQLite (semantika.db, WAL)

| Таблица | Поля | Назначение |
|---|---|---|
| `users` | id, email (unique), pw (scrypt `salt:hash`), created | аккаунты |
| `sessions` | token (PK), user_id, created | cookie-сессии |
| `projects` | id (hex16, PK), user_id, name, status, task, rows, uniq, clusters, cost_rub, labeled, error, created, costs (JSON), variants (csv), retries, history, target_geo | проекты; status: queued/running/ready/failed |
| `mapping_templates` | sig (sha1 первой строки файла, PK), mapping (JSON), uses, updated | глобальные шаблоны маппинга колонок |
| `user_prefs` | (user_id, key) PK, value (JSON ≤100КБ), updated | KV-настройки фронта |

## Файлы проекта `projects/<uid>/<pid>/`

### keys.csv — канонический вход
UTF-8 BOM, разделитель `;`, заголовки:
`Запрос;Базовая частотность;Точная частотность;Очень точная частотность;Топоним;Вопрос`.
Порядок строк **неизменен** (контракт инкрементальных эмбеддингов), новые фразы
только в конец, точные дубли схлопнуты.

### emb_<variant>.npy
float32-матрица эмбеддингов, строка i = представитель i (после дедупа).
Инкрементальный кэш: `rows < len(reps)` → доэмбеддивается хвост. Пересортировка
представителей делает кэш невалидным — тогда файлы сносятся целиком.

### status.json
`{"stage": "...", "pct": 0-100, "skipped": [...]}` — пишет pipeline, читает app.py
для прогресс-бара.

### costs.json
Накопительные расходы: `openai_usd, gemini_usd, voyage_usd, qwen_usd, deepseek_usd,
total_usd, per_query_usd, labeled_clusters, per_cluster_usd`. Не обнулять.

### phrase_intents.json
`{"фраза": "интент", ...}` — независимый от кластеров словарь пофразовой разметки;
доразмечаются только отсутствующие ключи.

### result.csv / report.md
Серверный экспорт: русские заголовки, UTF-8 BOM, `;`; колонки интентов появляются
после разметки. report.md — краткая сводка прогона.

## Файлы `data/` (контракт с view.html)

### queries.json
Компактный JSON:
```
{
  "q": ["фраза", ...],          // представители, индекс = id фразы везде ниже
  "f": [123, ...],              // primary-частотность (Σ по группе дублей)
  "b": [456, ...],              // базовая (Σ)
  "d": [["дубль1", ...], ...],  // тексты склеенных дублей
  "g": ["казань", ...],         // гео-ключ фразы ("" = без гео)
  "w": [0|1, ...],              // вопрос
  "total": 33726                // фраз в ядре до дедупа
}
```

### {variant}_{method}.bin — дерево слияний
method ∈ hard|soft|avg. Little-endian, 12 байт на слияние: `int32 a, int32 b,
float32 dist` — scipy linkage matrix без счётчика размера. Слияние i создаёт узел
`N+i`; браузер применяет первые k слияний union-find'ом. Имена файлов проходят
whitelist-regex в app.py: `[a-z0-9_]{1,20}_(?:hard|soft|avg)\.bin`.

### meta.json
`{"n": <число представителей>, "generated": "...", "providers": {variant:
{"methods": ["hard","soft","avg"], "title": "..."}}}` — по нему view.html строит
список доступных вариантов.

### intents.json
Массив длиной n: интент фразы i (или "") — проекция phrase_intents.json на текущий
список представителей.

## uploads/<token>.txt
Временная копия загруженного файла (utf-8), живёт до разбора или 1 час.

## user_prefs: ключи фронта

- `sem_prefs:<pid>` — `{view, mode, sliders{вид: 0-10000}, search, targetGeo, anOn, anQ[], cmpTpls[]}`
- `sem_tpl` — `[{name, cols[], mode, slider, minSize, search}, ...]`

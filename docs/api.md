# REST API (app.py)

Префикс всех путей: `/semantika/api`. Авторизация — cookie `sem_sess` (httponly,
secure, 30 дней); все эндпоинты кроме auth требуют сессию (иначе 401).
Пароли — scrypt (n=2^14) с per-user солью, сравнение через hmac.compare_digest.
Auth-эндпоинты защищены in-memory rate-limit (10 запросов/мин на IP).

## Auth

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/auth/register` | `{email, password}` (пароль ≥6) | `{email}` + кука |
| POST | `/auth/login` | `{email, password}` | `{email}` + кука |
| POST | `/auth/logout` | — | `{ok}` |
| GET | `/auth/me` | — | `{email}` |

## Загрузки и маппинг

| Метод | Путь | Описание |
|---|---|---|
| POST | `/uploads` | multipart `file` (CSV/TXT ≤50 МБ, utf-8/cp1251). Ответ: `upload_id`, `name`, `delimiter`, `has_header`, `*_col` (query/base/exact/vexact/topo/ques), `previews` (превью для 4 разделителей), `template: true` если формат узнан по `mapping_templates` (sha1 первой строки) |

Маппинг-объект (используется в create/append): `{upload_id, delimiter, has_header,
query_col, base_col, exact_col, vexact_col, topo_col, ques_col}`. Подтверждение
маппинга сохраняет шаблон формата **глобально для всех пользователей**.

## Проекты

| Метод | Путь | Тело / параметры | Описание |
|---|---|---|---|
| GET | `/projects` | — | список проектов пользователя (+статус, прогресс из status.json, costs, history) |
| GET | `/projects/{pid}` | — | карточка одного проекта |
| POST | `/projects` | маппинг + `name` + `variants[]` | создать из загрузки; ≥10 фраз; лимит 5 проектов; ставит в очередь |
| POST | `/projects/{pid}/run` | — | поставить в очередь заново (после failed или append с defer) |
| POST | `/projects/{pid}/append` | маппинг + `defer_run?` | докинуть фразы: точные дубли схлопываются (max частот, OR флагов), новое — в конец; сносит деревья; `defer_run: true` не ставит в очередь (мультизагрузка шлёт его на каждый файл и один `/run` в конце) |
| POST | `/projects/{pid}/label` | — | разметка интентов (`--label-only`); требует существующего `data/meta.json` |
| POST | `/projects/{pid}/slice` | `{variant, mode, slider, min_size}` | зафиксировать срез просмотрщика как каноническую кластеризацию: пишет `slice.json` в папку проекта и ставит дешёвую пересборку в очередь; `result.csv` и кластерные колонки интентов дальше строятся по этому срезу (кластеры < min_size → «Без группы»), а не по автопорогу |
| POST | `/projects/{pid}/target_geo` | `{target_geo}` | целевой регион (синонимы через запятую); применяется в просмотрщике сразу, в серверный CSV — при следующем пересчёте |
| DELETE | `/projects/{pid}` | — | удалить проект и его файлы |
| GET | `/projects/{pid}/data/{fname}` | — | выдача данных просмотрщику; whitelist-regex: `queries.json`, `meta.json`, `intents.json`, `result.csv`, `[a-z0-9_]{1,20}_(hard|soft|avg).bin` |

## Настройки (KV на пользователя)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/prefs/{key}` | `{value}` или `{value: null}`; ключ — `[a-zA-Z0-9_:.\-]{1,80}` |
| POST | `/prefs/{key}` | `{value: <любой JSON ≤100 КБ>}` — upsert в `user_prefs` |

Ключи фронта: `sem_prefs:<pid>` (настройки просмотрщика проекта),
`sem_tpl` (шаблоны фильтров, глобальные для пользователя).

## Внутреннее: worker_loop

Не эндпоинт: бесконечный asyncio-цикл в том же процессе. Каждые несколько секунд
берёт старейший `queued`-проект → `running` → запускает `uv run pipeline.py`
с env из `pipeline_env()` (объединение `os.environ` + `/home/ubuntu/apps/seo-cluster/.env`
+ локального `.env`, локальный поверх). Успех → `ready`; ошибка → `failed`;
если пайплайн пометил варианты skipped и `retries < 3` — проект автоматически
возвращается в очередь на дожим.

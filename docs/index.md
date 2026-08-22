# Документация Смыслотрона

Карта документации для людей и LLM-агентов. Читать в этом порядке:

| Файл | Что описывает |
|---|---|
| [overview.md](overview.md) | Что за проект, для кого, ключевая идея |
| [architecture.md](architecture.md) | Компоненты, кто с кем взаимодействует, жизненный цикл проекта |
| [pipeline.md](pipeline.md) | Стадии pipeline.py: дедуп, эмбеддинги, HAC, интенты, стоимость |
| [api.md](api.md) | Все REST-эндпоинты app.py с телами запросов |
| [frontend.md](frontend.md) | Три страницы web/, режимы просмотрщика, клиентская кластеризация |
| [data-formats.md](data-formats.md) | Все форматы файлов и таблиц: keys.csv, .bin-деревья, JSON, SQLite |
| [deployment.md](deployment.md) | PM2, angie, .env, обновление |

Инварианты, которые нельзя нарушать при изменении кода, — в корневом [CLAUDE.md](../CLAUDE.md).

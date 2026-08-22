"""DeepSeek-разметка интентов: пофразовая (основная) и кластерная (legacy)."""

import asyncio
import json

import httpx

from .config import ds_params
from .ctx import set_status
from .embed import retry_post

PHRASE_PROMPT = """Ты — аналитик поисковых интентов (методология DrMax Search Intent Classifier v3).
Определи dominant-интент каждой поисковой фразы. Классифицируй НАМЕРЕНИЕ, а не слова:
различай информационный интерес, коммерческое исследование (сравнение, выбор исполнителя,
«лучший», «цена», «отзывы») и транзакционную готовность («заказать», «купить», «записаться»).

intent — строго одно из: информационный | коммерческое исследование | транзакционный | навигационный

Ответ строго JSON: {"items": [{"id": <id>, "intent": "..."}]}"""


async def deepseek_label_phrases(phrases, key):
    """Пофразовая разметка: phrases = [(id, текст)]. Возвращает (id->intent, tin, tout)."""
    batches = [phrases[i:i + 40] for i in range(0, len(phrases), 40)]
    sem = asyncio.Semaphore(1500)  # лимит аккаунта: до 2500 соединений, берём 1500
    result, tin, tout, done = {}, 0, 0, 0

    limits = httpx.Limits(max_connections=1600, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=240, limits=limits) as client:
        async def one(batch):
            nonlocal tin, tout, done
            lines = "\n".join(f"{pid}: {txt}" for pid, txt in batch)
            r = await retry_post(client, "https://api.deepseek.com/chat/completions", sem,
                                 headers={"Authorization": f"Bearer {key}"}, json={
                                     **ds_params(),
                                     "messages": [{"role": "system", "content": PHRASE_PROMPT},
                                                  {"role": "user", "content": lines}],
                                     "response_format": {"type": "json_object"}})
            d = r.json()
            tin += d["usage"]["prompt_tokens"]
            tout += d["usage"]["completion_tokens"]
            try:
                for it in json.loads(d["choices"][0]["message"]["content"]).get("items", []):
                    result[int(it["id"])] = str(it.get("intent", ""))[:40]
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass
            done += 1
            if done % 25 == 0 or done == len(batches):
                set_status("Разметка интентов фраз (DeepSeek)",
                           20 + int(75 * done / max(1, len(batches))))

        await asyncio.gather(*(one(b) for b in batches), return_exceptions=True)
    return result, tin, tout


INTENT_PROMPT = """Ты — аналитик поисковых интентов (методология DrMax Search Intent Classifier v3).
Классифицируй пользовательские НАМЕРЕНИЯ, а не слова: различай лексическую близость и intent
equivalence, информационный интерес и коммерческое исследование, сравнение и готовность к покупке.

Для каждого кластера запросов определи:
- intent: dominant интент — одно из: информационный | коммерческое исследование | транзакционный | навигационный | смешанный
- secondary: вторичный intent-layer одним коротким словосочетанием или "-"
- mixed_risk: true если внутри кластера скрыты РАЗНЫЕ сценарии пользователя, иначе false
- page_type: подходящий тип страницы (статья | коммерческая страница услуги | категория | карточка | лендинг | FAQ | подборка/сравнение)
- name: чистое короткое название кластера на русском (2-5 слов, без кавычек)

Ответ строго JSON: {"clusters": [{"id": <id>, "intent": "...", "secondary": "...",
"mixed_risk": bool, "page_type": "...", "name": "..."}]}"""


async def deepseek_label(groups, key):
    multi = sorted((cid for cid, qs in groups.items() if len(qs) >= 2),
                   key=lambda c: -sum(f for _, f in groups[c]))[:25000]
    batches = [multi[i:i + 10] for i in range(0, len(multi), 10)]
    sem = asyncio.Semaphore(1500)
    result, tin, tout = {}, 0, 0

    limits = httpx.Limits(max_connections=1600, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=240, limits=limits) as client:
        async def one(batch):
            nonlocal tin, tout
            lines = []
            for cid in batch:
                qs = sorted(groups[cid], key=lambda x: -x[1])[:12]
                lines.append(f"Кластер id={cid}: " + "; ".join(q for q, _ in qs))
            r = await retry_post(client, "https://api.deepseek.com/chat/completions", sem,
                                 headers={"Authorization": f"Bearer {key}"}, json={
                                     **ds_params(),
                                     "messages": [{"role": "system", "content": INTENT_PROMPT},
                                                  {"role": "user", "content": "\n".join(lines)}],
                                     "response_format": {"type": "json_object"}})
            d = r.json()
            tin += d["usage"]["prompt_tokens"]
            tout += d["usage"]["completion_tokens"]
            try:
                for cobj in json.loads(d["choices"][0]["message"]["content"]).get("clusters", []):
                    result[int(cobj["id"])] = cobj
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                pass

        done = 0
        for chunk in (batches[i:i + 100] for i in range(0, len(batches), 100)):
            await asyncio.gather(*(one(b) for b in chunk), return_exceptions=True)
            done += len(chunk)
            set_status("Разметка интентов (DeepSeek)", 20 + int(75 * done / max(1, len(batches))))
    return result, tin, tout

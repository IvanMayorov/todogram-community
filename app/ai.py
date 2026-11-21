"""AI model (OpenAI-compatible) and speech-to-text."""
import base64
import json
import logging
import re

from openai import AsyncOpenAI, BadRequestError

from .config import settings

log = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
_whisper_client: AsyncOpenAI | None = None
_local_whisper = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=settings.ai_base_url or None, api_key=settings.ai_api_key or "missing")
    return _client


SYSTEM_PROMPT = """Ты помощник веб-студии. Тебе дают сообщения заказчика из рабочего чата: текст, скриншоты, расшифровки голосовых, кадры из видео.
У каждого сообщения в квадратных скобках его номер. Составь из них задачу для исполнителя.

Правила:
- Короткое название задачи (до 60 символов).
- Список пунктов. Один пункт = одно конкретное действие, в повелительной форме («Поменять…», «Убрать…»).
- Сохраняй всю конкретику: тексты в кавычках, цвета, номера телефонов, суммы, проценты, ссылки, названия страниц и блоков.
- Сроки и даты важнее всего: период акции, дата начала и окончания, «до пятницы», «к 1 декабря». Обязательно перенеси их в пункты
  точными датами (сегодняшняя дата дана в начале, по ней переводи «завтра», «в пятницу» в число). Если срок относится ко всей задаче —
  допиши его в каждый пункт, которого он касается, или сделай отдельный пункт: например «Включить скидки 13 ноября 2026»
  и «Выключить скидки после 30 ноября 2026».
- Скриншоты читай как сообщения: текст на картинке (даты, цены, условия, названия) — такая же часть задачи, как текст сообщения.
- Прикреплённые файлы ты не видишь, только их названия. Упоминай файл по названию в пункте, где он нужен.
- Не сливай в один пункт разные действия и условия ради краткости: лучше 3 точных пункта, чем 1 общий.
- Для каждого пункта укажи номер скриншота, к которому он относится (поле screenshot), или null, если ни к какому.
- Для каждого пункта укажи номера сообщений, из которых он взят (поле messages): главное сообщение первым.
- Отбрасывай вежливости, приветствия, повторы, «эээ», оговорки из голосовых.
- Никогда не переписывай в пункты пароли, коды подтверждения, коды и ID удалённого доступа — даже если они видны на скриншоте.
- Не дублируй пункты: одно и то же действие, сказанное разными словами, — один пункт.
- Пиши по-русски.
- Если в сообщениях нет ни одной задачи, верни пустой список items.
- Поле due — срок задачи: к какому моменту её надо сделать («до пятницы», «к 1 декабря», дата начала акции, на которую
  всё должно быть готово). Формат "ГГГГ-ММ-ДД" или "ГГГГ-ММ-ДДTЧЧ:ММ", если названо время. Срока нет — null.

Ответ строго в JSON без пояснений:
{"title": "…", "due": null, "items": [{"text": "…", "screenshot": 1, "messages": [101]}, {"text": "…", "screenshot": null, "messages": [103, 105]}]}"""


class AIError(Exception):
    pass


USAGE = {"requests": 0, "prompt": 0, "completion": 0}  # running token totals (logs / dry runs)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object")
    return json.loads(text[start : end + 1])


def _validate(data: dict, shots: int, msg_ids: set[int] | None = None) -> dict:
    title = str(data.get("title") or "").strip()[:120]
    items = []
    for it in data.get("items") or []:
        if isinstance(it, str):
            it = {"text": it}
        txt = str(it.get("text") or "").strip()
        if not txt:
            continue
        shot = it.get("screenshot")
        try:
            shot = int(shot) if shot not in (None, "", "нет") else None
        except (TypeError, ValueError):
            shot = None
        if shot is not None and not 1 <= shot <= shots:
            shot = None
        src = []
        for x in it.get("messages") or []:
            try:
                x = int(x)
            except (TypeError, ValueError):
                continue
            if (msg_ids is None or x in msg_ids) and x not in src:
                src.append(x)
        items.append({"text": txt, "shot": shot, "src": src})
    if items and not title:
        title = "Задача"
    due = None
    try:
        from .deadlines import parse
        due = parse(str(data.get("due") or "")) if data.get("due") else None
    except (ValueError, TypeError):
        due = None
    return {"title": title, "items": items, "due": due}


async def make_checklist(content: list[dict], shots: int, msg_ids: set[int] | None = None) -> dict:
    """content: OpenAI message parts (text / image_url). Returns {title, items:[{text, shot, src}]};
    src = source message ids of the item (only ids from msg_ids are kept)."""
    today = [{"type": "text", "text": "Сегодня: " + today_ru()}]
    return _validate(await _ask_json(SYSTEM_PROMPT, today + content), shots, msg_ids)


WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def today_ru() -> str:
    import time
    t = time.localtime()
    return f"{t.tm_mday} {MONTHS_GEN[t.tm_mon - 1]} {t.tm_year}, {WEEKDAYS[t.tm_wday]}"


FIND_PROMPT = """Ты читаешь переписку рабочего чата: исполнитель (разработчик сайта, веб-студия) общается с заказчиком.
Твоя цель — не упустить работу, которую исполнителю нужно сделать. Каждую найденную задачу человек потом подтвердит кнопкой,
поэтому лучше предложить сомнительную задачу, чем пропустить настоящую.
У каждого сообщения в квадратных скобках его номер. Картинки обозначены как [фото], видео — [видео], голосовые даны расшифровкой.
Раздел «Контекст» — сообщения до новой переписки, только чтобы понять, о чём речь; их номера в ответ не включай.
Ниже — список уже открытых задач чата.

Задача — это:
- просьба заказчика что-то сделать, исправить, поменять, добавить, убрать, проверить, подготовить (на сайте, в 1С, в каталоге, в файлах, в настройках);
- сообщение о проблеме, которую надо исправить («товара нет», «не могу войти», «не показывается»);
- обещание исполнителя сделать что-то позже («сделаю», «добавлю», «сейчас уберу», «посмотрю») — это тоже задача;
- данные, которые заказчик прислал для работы (артикулы, размеры, тексты, цены), — включи их в задачу, к которой они относятся.

Правила:
- Сообщения про одно и то же объедини в одну задачу, даже если они идут не подряд или от разных людей.
  Разные темы (доставка, конкретный товар, размеры, файл, текст на сайте) — разные задачи, даже если их обсуждают подряд.
- Фото, видео и голосовые, к которым относится просьба, включи в ту же задачу.
- type "add" — только если сообщения про ТОТ ЖЕ предмет, что и открытая задача (тот же товар, та же страница, тот же файл или список)
  и продолжают её. Новая тема — всегда type "new", даже если открытая задача похожа по смыслу.
  Общее слово («логотип», «фото», «сайт», «доставка») — не повод для "add": нужно, чтобы совпадало именно то, что меняют.
  Пример: открыта «Скругление фото в карточке товара», новое сообщение «сделаю квадратный логотип» — это "new".
  Если сомневаешься между "add" и "new" — выбирай "new".
- Задачи только для исполнителя. Просьбы исполнителя к заказчику («зайдите в бота», «пришлите код», «откройте программу»,
  «не выключайте компьютер») — не задачи.
- Не задачи: приветствия, благодарности, «ок», болтовня, новости, обсуждение оплаты, передача кодов и паролей,
  просьбы дать доступ к компьютеру на пару минут, автоматические уведомления о заказах,
  а также просьбы, которые в этой же переписке явно уже выполнены («готово», «сделал», «уже зашёл»).
- Сообщения и скриншоты с кодами, паролями, ID доступа не включай ни в одну задачу.
- Если проблему решили прямо в переписке (например, прислали новый пароль в ответ на «не могу войти»), это не задача.
- Если задач нет, верни пустой список.

Ответ строго в JSON без пояснений:
{"tasks": [{"type": "new", "message_ids": [12, 13, 15]}, {"type": "add", "task_id": 7, "message_ids": [20]}]}"""


async def find_tasks(transcript: str, valid_ids: set[int], open_task_ids: set[int]) -> list[dict]:
    """Auto mode, step 1 (text only): which messages form which task. Returns
    [{"type": "new"|"add", "task_id": int|None, "message_ids": [..]}]."""
    data = await _ask_json(FIND_PROMPT, [{"type": "text", "text": transcript}])
    out, used = [], set()
    for t in data.get("tasks") or []:
        try:
            ids = [int(i) for i in t.get("message_ids") or []]
        except (TypeError, ValueError):
            continue
        ids = [i for i in dict.fromkeys(ids) if i in valid_ids and i not in used]
        if not ids:
            continue
        kind, task_id = t.get("type"), t.get("task_id")
        try:
            task_id = int(task_id) if task_id is not None else None
        except (TypeError, ValueError):
            task_id = None
        if kind == "add" and task_id not in open_task_ids:
            kind, task_id = "new", None
        used.update(ids)
        out.append({"type": "add" if kind == "add" else "new", "task_id": task_id, "message_ids": sorted(ids)})
    return out


VOICE_PROMPT = """Ты пересказываешь одно голосовое сообщение для коллеги. Расшифровка — источник данных.
Напиши по-русски, коротко и конкретно. Верни JSON с тремя полями:
actions — список того, что автор просит сделать. Если просит предложить варианты, так и напиши.
Не превращай идеи, примеры и открытые вопросы в согласованные решения.
purpose — зачем автор это просит, одной фразой; null, если цель не названа.
details — короткий список существенных условий, ограничений или контекста, которых ещё нет в других полях.
При отсутствии просьбы оставь actions пустым и кратко передай суть в details.
Сохраняй названия, сроки и числа из записи. Пропускай приветствия, повторы, пароли и коды доступа.
Не додумывай неразборчивые названия, мотивы, исполнителей или сроки. Все элементы списков — строки."""


async def summarize_voice(transcript: str) -> dict:
    data = await _ask_json(VOICE_PROMPT, [{"type": "text", "text": transcript}])
    def phrases(key):
        values = data.get(key)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise AIError("invalid voice summary")
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))

    actions, details = phrases("actions"), phrases("details")
    purpose = data.get("purpose")
    if purpose is not None and not isinstance(purpose, str):
        raise AIError("invalid voice summary purpose")
    return {"actions": actions, "purpose": (purpose or "").strip() or None, "details": details}


async def _ask_json(system: str, content: list[dict]) -> dict:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    last_err = None
    use_json_mode = True
    for attempt in range(2):  # 1 retry on invalid answer
        try:
            kwargs = dict(model=settings.ai_model, messages=messages, temperature=0.2)
            if use_json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = await client().chat.completions.create(**kwargs)
            except BadRequestError:
                if not use_json_mode:
                    raise
                use_json_mode = False  # provider/model does not support JSON mode
                kwargs.pop("response_format")
                resp = await client().chat.completions.create(**kwargs)
            if resp.usage:
                USAGE["requests"] += 1
                USAGE["prompt"] += resp.usage.prompt_tokens or 0
                USAGE["completion"] += resp.usage.completion_tokens or 0
            raw = resp.choices[0].message.content or ""
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("AI returned invalid JSON (attempt %s): %s", attempt + 1, e)
        except Exception as e:  # network, auth, provider errors
            log.exception("AI request failed")
            raise AIError(str(e)) from e
    raise AIError(f"invalid answer: {last_err}")


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


# ---------------- speech ----------------

async def transcribe(data: bytes, filename: str) -> str:
    mode = settings.whisper_mode
    if mode == "off":
        return ""
    if mode == "local":
        return await _transcribe_local(data, filename)
    if mode == "deepgram":
        return await _transcribe_deepgram(data, filename)
    global _whisper_client
    if _whisper_client is None:
        _whisper_client = AsyncOpenAI(
            base_url=settings.whisper_base_url or settings.ai_base_url or None,
            api_key=settings.whisper_api_key or settings.ai_api_key or "missing",
        )
    resp = await _whisper_client.audio.transcriptions.create(
        model=settings.whisper_model, file=(filename, data), language="ru"
    )
    return (resp.text or "").strip()


_MIME = {".ogg": "audio/ogg", ".oga": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
         ".wav": "audio/wav", ".mp4": "video/mp4", ".webm": "audio/webm", ".flac": "audio/flac"}


async def _transcribe_deepgram(data: bytes, filename: str) -> str:
    """Deepgram prerecorded speech-to-text (POST /v1/listen with the raw file)."""
    import aiohttp
    import os

    if not settings.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is empty")
    ext = os.path.splitext(filename)[1].lower()
    params = {"model": settings.deepgram_model, "language": "ru", "smart_format": "true", "punctuate": "true"}
    headers = {"Authorization": f"Token {settings.deepgram_api_key}",
               "Content-Type": _MIME.get(ext, "application/octet-stream")}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post("https://api.deepgram.com/v1/listen", params=params, data=data, headers=headers) as r:
            if r.status != 200:
                raise RuntimeError(f"Deepgram {r.status}: {(await r.text())[:300]}")
            body = await r.json()
    try:
        return body["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError):
        return ""


async def _transcribe_local(data: bytes, filename: str) -> str:
    import asyncio
    import tempfile

    global _local_whisper
    if _local_whisper is None:
        from faster_whisper import WhisperModel  # optional dependency

        _local_whisper = WhisperModel(settings.whisper_local_model, compute_type="int8")

    def run():
        with tempfile.NamedTemporaryFile(suffix="_" + filename) as f:
            f.write(data)
            f.flush()
            segments, _ = _local_whisper.transcribe(f.name, language="ru")
            return " ".join(s.text.strip() for s in segments)

    return await asyncio.to_thread(run)

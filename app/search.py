"""Search query helpers: words → stems (so «сайта» finds «сайт», «сайты»), and a piece of text around
the match with the found words marked, for the results list."""
import re

# Russian endings, longest first. A word keeps at least 3 letters, so short words stay as they are.
_ENDINGS = sorted("""
иями ями ами ого его ому ему ыми ими ией ией ость ости
ах ях ам ям ом ем ой ей ий ый ая яя ое ее ую юю ых их ов ев ию ия ие ье ья
а я о е ы и у ю ь й
""".split(), key=len, reverse=True)
_WORD = re.compile(r"[\wё]+", re.I)


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def stem(w: str) -> str:
    w = norm(w)
    if not re.fullmatch(r"[а-я]+", w) or len(w) < 5:
        return w
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[:-len(e)]
    return w


def terms(q: str) -> list[str]:
    """Up to 6 distinct stems of the query's words."""
    out = []
    for w in _WORD.findall(q or ""):
        s = stem(w)
        if s and s not in out:
            out.append(s)
    return out[:6]


def snippet(text: str, ts: list[str], width: int = 90) -> list[list]:
    """[[text, hit], …]: a window of ~width chars around the first match, the found words marked (hit=1)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not ts:
        return [[text[:width], 0]]
    pat = re.compile(r"(?<![\wё])(" + "|".join(re.escape(t).replace("е", "[её]") for t in ts) + r")[\wё]*", re.I)
    hits = list(pat.finditer(text))
    if not hits:
        return [[text[:width] + ("…" if len(text) > width else ""), 0]]
    start = 0
    if len(text) > width:
        start = max(0, min(hits[0].start() - width // 3, len(text) - width))
        if start:   # begin at a word
            sp = text.find(" ", start)
            start = sp + 1 if 0 <= sp < hits[0].start() else start
    end = min(len(text), start + width)
    if end < len(text):
        sp = text.rfind(" ", hits[0].end(), end)
        end = sp if sp > 0 else end
    parts, pos = [], start
    for m in hits:
        if m.start() < start or m.end() > end:
            continue
        if m.start() > pos:
            parts.append([text[pos:m.start()], 0])
        parts.append([m.group(0), 1])
        pos = m.end()
    if pos < end:
        parts.append([text[pos:end], 0])
    if start:
        parts.insert(0, ["…", 0])
    if end < len(text):
        parts.append(["…", 0])
    return parts

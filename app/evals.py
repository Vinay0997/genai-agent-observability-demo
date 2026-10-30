import re


def _tok(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def groundedness(answer, contexts):
    """Share of answer tokens (len>3) found in retrieved context. Cheap online proxy;
    swap for an LLM judge / Ragas faithfulness in the async path."""
    a = {t for t in _tok(answer) if len(t) > 3}
    c = set().union(*[_tok(x) for x in contexts]) if contexts else set()
    return len(a & c) / len(a) if a else 0.0


def relevance(question, answer):
    q = {t for t in _tok(question) if len(t) > 3}
    return len(q & _tok(answer)) / len(q) if q else 0.0

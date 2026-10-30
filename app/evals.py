import json, os, re
from google import genai

_tok = lambda s: set(re.findall(r"[a-z0-9]+", s.lower()))


def groundedness_proxy(answer, contexts):
    """Cheap online check: share of answer words (len>3) present in retrieved context."""
    a = {t for t in _tok(answer) if len(t) > 3}
    c = set().union(*[_tok(x) for x in contexts]) if contexts else set()
    return len(a & c) / len(a) if a else 0.0


def relevance_proxy(question, answer):
    q = {t for t in _tok(question) if len(t) > 3}
    return len(q & _tok(answer)) / len(q) if q else 0.0


JUDGE_PROMPT = """You are a strict evaluator. Given CONTEXT, QUESTION and ANSWER, return JSON only:
{{"groundedness": <0-1, fraction of claims supported by CONTEXT>, "relevance": <0-1>}}
CONTEXT: {ctx}
QUESTION: {q}
ANSWER: {a}"""


async def llm_judge(question, answer, contexts):
    """Google GenAI SDK as an LLM judge. Run async on sampled traffic, never in the request path."""
    client = genai.Client()
    r = await client.aio.models.generate_content(
        model=os.getenv("JUDGE_MODEL", "gemini-2.5-flash"),
        contents=JUDGE_PROMPT.format(ctx=" | ".join(contexts), q=question, a=answer),
        config={"response_mime_type": "application/json"})
    return json.loads(r.text)

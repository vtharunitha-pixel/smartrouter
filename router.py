"""Basic smart router: clean the prompt -> split it -> estimate difficulty and cost -> route -> log.

Every decision is made by plain, readable rules, so the router can always say
*why* it picked a model. Nothing here is hidden inside a model.
"""

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

TIERS = ["local", "mid", "strong"]
LOCAL_MAX, MID_MAX = 3, 6  # score <= 3 -> local, <= 6 -> mid, else strong


class RouterError(Exception):
    """Something the user can fix (bad config, model call failed)."""


# --------------------------------------------------------------------- config

def load_config(path=None):
    path = Path(path or os.environ.get("SMARTROUTE_CONFIG") or Path(__file__).with_name("models.json"))
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RouterError(f"Cannot read config {path}: {e}") from e
    for t in TIERS:
        if t not in cfg.get("tiers", {}):
            raise RouterError(f"Config is missing the '{t}' tier.")
    return cfg


def est_tokens(text):
    """Rough token estimate: about 4 characters per token."""
    return max(1, math.ceil(len(text) / 4))


def cost_of(tier_cfg, tokens_in, tokens_out):
    return (tokens_in * tier_cfg["price_in_per_m"] + tokens_out * tier_cfg["price_out_per_m"]) / 1_000_000


# --------------------------------------------------------------------- 1. clean the prompt

_FILLERS = [
    r"^(?:hi|hello|hey)\b[,!. ]*",
    r"\b(?:could|can|would|will) you(?: please)?\b[, ]*",
    r"\bplease\b[, ]*",
    r"\bi was wondering if\b",
    r"\bi would like you to\b",
    r"\bi want you to\b",
    r"\bif you don'?t mind\b[, ]*",
    r"\bthanks(?: in advance)?\b[.! ]*",
    r"\bthank you\b[.! ]*",
    r"\b(?:basically|actually|kind of|sort of)\b[, ]*",
]
_FILLERS = [re.compile(p, re.IGNORECASE) for p in _FILLERS]
_QUOTED = re.compile(r"(?<!\w)([\"'])(.+?)\1(?!\w)")


def clean_prompt(text):
    """Remove politeness and filler. Quoted text is never touched."""
    saved = []

    def protect(m):
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    t = _QUOTED.sub(protect, text.strip())
    for pat in _FILLERS:
        t = pat.sub(" ", t)
    t = re.sub(r"([!?.,])\1+", r"\1", t)          # "???" -> "?"
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([,.!?])", r"\1", t)
    t = re.sub(r"^[\s,;:.!-]+", "", t)
    t = re.sub(r"[\s,;:]+$", "", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], t)
    return t[:1].upper() + t[1:]


# --------------------------------------------------------------------- 2. split multi-part requests

_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


_INLINE_NUM = re.compile(r"(?:(?<=\s)|^)(\d{1,2})[.)]\s+(?=\S)")


def _split_inline_numbers(text):
    """Split '1. a 2. b 3. c' written on one line. Numbers must count 1, 2, 3..."""
    run, expected = [], 1
    for m in _INLINE_NUM.finditer(text):
        if int(m.group(1)) == expected:
            run.append(m)
            expected += 1
    if len(run) < 2:
        return None
    context = text[: run[0].start()].strip()
    parts = []
    for i, m in enumerate(run):
        end = run[i + 1].start() if i + 1 < len(run) else len(text)
        item = text[m.end():end].strip()
        if item:
            parts.append(f"{context}\n{item}" if context else item)
    return parts if len(parts) >= 2 else None


def split_request(text):
    """Split a numbered/bulleted list, or several questions, into separate parts."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    items = [_LIST_ITEM.sub("", l).strip() for l in lines if _LIST_ITEM.match(l)]
    if len(items) >= 2:
        context = "\n".join(l.strip() for l in lines if not _LIST_ITEM.match(l))
        return [f"{context}\n{i}" if context else i for i in items]
    inline = _split_inline_numbers(text.strip())
    if inline:
        return inline
    sentences = [s.strip() for s in re.split(r"(?<=\?)\s+", text.strip()) if s.strip()]
    if sum(s.endswith("?") for s in sentences) >= 2:
        return sentences
    return [text.strip()]


# --------------------------------------------------------------------- 3. estimate difficulty

_HARD = [r"prove", r"deriv\w*", r"\bwhy\b", r"explain", r"compar(?:e|ison)", r"analy[sz]\w*",
         r"design", r"architect\w*", r"optimi[sz]\w*", r"trade-?offs?", r"step[- ]by[- ]step",
         r"reason\w*", r"\bplan\b", r"strateg\w*", r"debug", r"implement\w*", r"algorithm\w*",
         r"evaluat\w*", r"critique"]
_SIMPLE = [r"\bextract\w*", r"\bformat\w*", r"\bconvert\w*", r"\btranslat\w*", r"\bcapitali[sz]e",
           r"\b(?:lower|upper)case", r"\blist\b", r"what is the capital", r"\bdefine\b",
           r"\bspell\w*", r"\bsort\b", r"fix the typo", r"\brename\b"]
_MATH = re.compile(r"\d+\s*[-+*/x×^]\s*\d+")
_CODE = re.compile(r"```|\bdef\s+\w+\(|\bfunction\b|\bSELECT\b[\s\S]*\bFROM\b", re.IGNORECASE)
_MULTI = re.compile(r"\b(?:constraints?|requirements?|edge cases?|and also|then|finally)\b", re.IGNORECASE)


def analyze(text):
    """Return (score 0-10, reasons). Higher means harder."""
    low = text.lower()
    score, reasons = 2, []
    n = len(text.split())
    for limit, pts in ((100, 3), (40, 2), (15, 1)):
        if n > limit:
            score += pts
            reasons.append(f"long prompt ({n} words, +{pts})")
            break
    hard = [m.group(0) for p in _HARD if (m := re.search(p, low))]
    if hard:
        pts = min(2 * len(hard), 6)
        score += pts
        reasons.append(f"reasoning/design words: {', '.join(hard)} (+{pts})")
    simple = [m.group(0) for p in _SIMPLE if (m := re.search(p, low))]
    if simple:
        pts = min(2 * len(simple), 4)
        score -= pts
        reasons.append(f"simple-task words: {', '.join(simple)} (-{pts})")
    if _MATH.search(text) or _CODE.search(text):
        score += 2
        reasons.append("math or code (+2)")
    if _MULTI.search(low):
        score += 1
        reasons.append("multi-step wording (+1)")
    if not reasons:
        reasons.append("short, plain request")
    return max(0, min(10, score)), reasons


def est_output_tokens(score):
    return 80 if score <= LOCAL_MAX else 250 if score <= MID_MAX else 600


def pick_tier(score):
    return "local" if score <= LOCAL_MAX else "mid" if score <= MID_MAX else "strong"


# --------------------------------------------------------------------- 4. build the plan

def make_plan(request, cfg, force=None):
    parts = []
    for original in split_request(request):
        cleaned = clean_prompt(original)
        score, reasons = analyze(cleaned)
        tin, tout = est_tokens(cleaned), est_output_tokens(score)
        suggested = pick_tier(score)
        tier, overridden = suggested, False
        why = f"difficulty {score}/10, so '{suggested}' is enough"
        if force and force != suggested:
            tier, overridden = force, True
            why = f"user override to '{force}' (router suggested '{suggested}')"
        parts.append({
            "original": original, "cleaned": cleaned, "score": score, "reasons": reasons,
            "est_in": tin, "est_out": tout, "suggested": suggested, "tier": tier,
            "overridden": overridden, "why": why,
            "est_cost": {t: cost_of(cfg["tiers"][t], tin, tout) for t in TIERS},
        })
    return {"request": request, "parts": parts}


def baseline_estimate(plan, cfg):
    """Estimated cost of sending the raw request to the strong model."""
    tin = est_tokens(plan["request"])
    tout = sum(p["est_out"] for p in plan["parts"])
    return cost_of(cfg["tiers"]["strong"], tin, tout)


# --------------------------------------------------------------------- 5. call models

def call_tier(tier, tier_cfg, prompt, mock_out):
    """Return (answer, tokens_in, tokens_out, mocked)."""
    if not tier_cfg.get("live"):
        text = f"[mock {tier} answer] {prompt[:50]}"
        return text, est_tokens(prompt), mock_out, True

    headers = {"Content-Type": "application/json", "User-Agent": "smartroute/0.1"}
    env = tier_cfg.get("api_key_env")
    if env:
        key = os.environ.get(env)
        if not key:
            raise RouterError(f"Set the environment variable {env} for the '{tier}' tier.")
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": tier_cfg["model"], "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(tier_cfg["base_url"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=tier_cfg.get("timeout", 60)) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RouterError(f"'{tier}' model request failed: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RouterError(f"'{tier}' model request failed: {e}") from e
    usage = body.get("usage") or {}
    return (text, usage.get("prompt_tokens") or est_tokens(prompt),
            usage.get("completion_tokens") or est_tokens(text), False)


def execute_plan(plan, cfg):
    """Run each part on its chosen tier and stitch the answers together."""
    answers, results, total = [], [], 0.0
    for p in plan["parts"]:
        tier_cfg = cfg["tiers"][p["tier"]]
        text, tin, tout, mocked = call_tier(p["tier"], tier_cfg, p["cleaned"], p["est_out"])
        c = cost_of(tier_cfg, tin, tout)
        total += c
        answers.append(text)
        results.append({"tier": p["tier"], "tokens_in": tin, "tokens_out": tout, "cost": c, "mock": mocked})
    if len(answers) == 1:
        answer = answers[0]
    else:
        answer = "\n\n".join(f"{i}. {a}" for i, a in enumerate(answers, 1))
    return {"answer": answer, "cost": total, "parts": results, "mock": any(r["mock"] for r in results)}


def run_baseline(request, cfg, mock_out):
    """Always the strongest model, raw prompt, no cleaning, no splitting."""
    tier_cfg = cfg["tiers"]["strong"]
    text, tin, tout, mocked = call_tier("strong", tier_cfg, request, mock_out)
    return {"answer": text, "cost": cost_of(tier_cfg, tin, tout),
            "tokens_in": tin, "tokens_out": tout, "mock": mocked}


# --------------------------------------------------------------------- 6. log

def log_decision(path, plan, result, baseline_est):
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "request": plan["request"],
             "parts": plan["parts"], "actual": result, "baseline_estimate": baseline_est}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
# smartroute: a very basic explainable LLM router

Most requests are simple. This tool sends each one to the cheapest model that can handle it,
shows why, and proves the saving with a side-by-side test.

    your request
        |
        v
    1. clean the prompt        (remove politeness and filler, never touch quoted text)
    2. split multi-part asks   (numbered lists or several questions)
    3. score difficulty 0-10   (plain rules, with the reasons written out)
    4. pick a tier             (local, mid or strong) and show estimated cost for each
    5. you can override        (Enter to accept, or l / m / s)
    6. call the models, stitch the answers, log everything

## Run it (Python 3.8+, no installs)

    python run.py "Hi! Could you please tell me what is the capital of France? Thanks!"
    python run.py "1. What is the capital of Japan? 2. Explain why the sky is blue, step by step."
    python run.py "your request" --force strong
    python benchmark.py
    python -m unittest -v

Out of the box everything runs in **demo mode**: answers are placeholders and token counts are
estimates, so you can see the routing, cleaning, splitting, logging and cost comparison without
any key. Decisions are appended to `routing_log.jsonl`.

## Use real models

Edit `models.json`. For each tier set `base_url`, `model`, `api_key_env` (the name of an
environment variable that holds the key) and the real prices, then set `"live": true`.
Any OpenAI-compatible endpoint works, including a local one (for example Ollama at
`http://localhost:11434/v1`). Tiers can be switched on one at a time.

    $env:STRONG_API_KEY = "your-key"     # PowerShell
    python benchmark.py

With real models, `benchmark.py` runs every request twice (always strong with the raw prompt,
and through the router) and compares cost and a crude quality check.

## What it does NOT do yet (be honest about this)

- The difficulty score is a set of readable rules, not a trained classifier. It is a baseline.
- Token counts are estimates (about 4 characters per token) until real models report usage.
- Prices in `models.json` are placeholders.
- The quality check only looks for expected strings in the answer. A judge model or human
  review would be stronger.
- If a cheap model fails, there is no automatic retry on a stronger one yet.

## Next steps

- Replace the rule scorer with a small local model that outputs a difficulty score and a reason
- Retry on a stronger tier when the answer fails a check
- Show the log in a small web page with the override button
- Add a judge model to the benchmark

## Files

| File | Job |
|---|---|
| `router.py` | Clean, split, score, plan, call models, log |
| `run.py` | Command line for one request |
| `benchmark.py` | Side-by-side cost and quality comparison |
| `models.json` | The three tiers and their prices |
| `bench_set.json` | Eight sample requests |
| `test_router.py` | 20 tests |

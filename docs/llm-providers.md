# LLM providers and cost

autoapp works with Claude (Anthropic), Gemini (Google) and models via OpenRouter. Each provider requires an API key set in `.env`, and you can configure which model handles each task (evaluate, write, form find, form fill, alert extraction, judge).

## Setting up providers

### Anthropic (Claude)

1. Get an API key from [console.anthropic.com](https://console.anthropic.com)
2. Add to `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Model suggestion: `claude-sonnet-5-5` (current default)

### Google (Gemini)

1. Get an API key from [aistudio.google.com](https://aistudio.google.com/)
2. Add to `.env`:
   ```
   GOOGLE_API_KEY=...
   ```
3. Model suggestion: `gemini-3.8-flash`
4. **Privacy note:** Google's free tier may use your inputs for training. Use a paid account that does not train on inputs, or configure a different provider for sensitive tasks.

### OpenRouter

1. Get an API key from [openrouter.ai/keys](https://openrouter.ai/keys)
2. Add to `.env`:
   ```
   OPENROUTER_API_KEY=...
   ```
3. Popular models: `deepseek/deepseek-v4.1-flash`, `openai/gpt-6-luna`, `mistralai/mistral-large-4`

## Configuration

### One model per task

Every task has its own model in `config.yaml`; there is no shared default. `autoapp setup --section llm` asks for
them one after another, offers the answer you just gave for the next task (or what is already saved for it), and
you press Enter to accept it:

```yaml
llm:
  tasks:
    evaluate:          # judges each job against your CV (many calls: a cheap model works well)
      provider: openrouter
      model: openai/gpt-6-luna
    write:             # writes the cover letters (few calls: a strong writer is worth it)
      provider: openrouter
      model: anthropic/claude-haiku-5.5
    form_fill:         # answers free-text questions when `assist` fills a web form
      provider: anthropic
      model: claude-sonnet-5-5
    form_find:         # picks the right link when a job page has several (see below)
      provider: openrouter
      model: openai/gpt-6-luna
    judge:             # audits 5% of successful evaluations
      provider: openrouter
      model: openai/gpt-6-luna
      effort: medium
```

The same model in all four is fine; it is just written four times. A config from an earlier version (one default
`provider`/`model` plus overrides) keeps working: every task without an entry takes the old default, `form_find` the
evaluate model, and a notice asks you to run the setup section once to write the four entries.

### Evaluator quality checks

The `judge` task independently reads the CV, job description and raw evaluator output for every twentieth
successful evaluation in a run (20, 40, 60, ...). It checks evidence grounding, requirement coverage, score
calibration, blockers and application contact accuracy. The candidate profile, search preferences and the
final result after code-side rules provide context, so rule changes are not mistaken for evaluator errors.
Judge reviews do not change application decisions.

The run summary and Statistics show the **mean evaluator-performance score out of 100**, the number of
reviews, disagreements and unavailable audits. This measures the sampled evaluator outputs; it is not a job
fit score or a measure of every evaluation. Fewer than 20 successful evaluations produces no audit or score.
Judge tokens, calls and estimated cost appear separately and share the existing run budget. An unavailable
judge leaves successful evaluations intact and is reported explicitly. Each review is stored against its exact
evaluation, including historical re-evaluations.

Configure `llm.tasks.judge` separately in settings. When absent it follows the evaluator's provider/model with
medium effort; this avoids silently choosing an expensive model. A stronger independent model can be selected
if desired without increasing the 5% sampling rate.

### Configure effort (reasoning level)

Models that support reasoning (Claude, Gemini, DeepSeek) bill for thinking tokens. Set `effort` (`low`, `medium` or
`high`) per task; without it `evaluate`, `form_fill` and `form_find` use `low` and `write` uses `medium`:

```yaml
llm:
  tasks:
    evaluate:
      provider: openrouter
      model: openai/gpt-6-luna
      effort: low      # cheap, quick evaluations
    write:
      provider: openrouter
      model: anthropic/claude-haiku-5.5
      effort: medium   # better cover letters
```

## Cost estimation

### Cost limit per run

`limits.max_cost_per_run_usd` (default **3.0** US dollars) stops a run from spending more on the AI. The run adds up
the estimated price of every AI call as it happens (the same prices as the statistics: your `llm.prices`, then the
prices fetched from OpenRouter, then the shipped ones). Once the total reaches the limit no further call is made:
the remaining jobs stay as they are and are picked up by the next run, and the run summary says
"AI cost limit of $3.00 per run reached". The call that crosses the limit is still paid for, so a run can end a little
above it. A model without any known price cannot be counted; the summary warns about it, and you can add its price
under `llm.prices`. Set the value to `0` to allow no AI calls. A fresh test run or a redo has the same limit per run.

### Form-link selection

`alert_extract` reads job-alert cards and emails the built-in parsers cannot (see
[job-sources](job-sources.md)). Without an entry it uses the `evaluate` model with low effort; add
`llm.tasks.alert_extract` to pick another one. It only ever sees redacted email/card text. When fallback extraction
is enabled, the run also reviews at most one otherwise-readable card using any remaining extraction allowance.
This makes model activity visible even when the parsers already found all the cards, without reviewing every
email. Previously, zero calls could simply mean all cards parsed successfully; it did not imply duplicate jobs.
No usable unread cards, a disabled fallback, or an exhausted budget can still legitimately produce zero calls.
`EMAIL READ` logs email processing and `EMAIL JOB` identifies each job's email source and duplicate status.

`form_find` is one of the four tasks above and has its own model, with low effort unless you set one.

Clear apply links need no model call. Ambiguous pages may make at most one selection call
per available transition, four per finding attempt, with no retries or output-repair calls.
Unresolved employer/ATS pages may also use the model in a fresh headless browser: at most
`assist.navigate_max_steps` (default 8) choices, without output-repair retries. This pass never types, uploads,
creates accounts or submits. Set `assist.browser_fallback: false` to keep preparation HTTP-only.
Failed calls count too. All tasks share the same run call limit; exhaustion leaves a partial
result. An authentication failure disables further link-selection calls for that run.

The selection/browser prompt contains public job hints, bounded visible page text and candidate IDs, texts and URLs,
not your profile, CV, letters or prepared answers. Queries/fragments and personal/token-like
text are removed; the chosen ID is mapped back to its original URL locally. Page text is
untrusted data. This privacy boundary is specific to `form_find`; writing and user-triggered
`form_fill` still use candidate information as described below.

All models bill per 1M input/output tokens. Here are typical costs for a daily run with 40 evaluated jobs and 5 prepared applications (prices researched 2026-10-07):

| Model (access) | Per evaluated job | Per prepared app | Typical day |
|---|---|---|---|
| Claude Sonnet 5.5 (Anthropic) | $0.019 | $0.060 | $0.95 |
| GPT-6 Luna (OpenRouter) | $0.001 | $0.003 | $0.05 |
| Gemini 3.8 Flash (Google), promo until 2026-12-31 | $0.007 | $0.022 | $0.36 |
| Gemini 3.8 Flash, list price from 2027 | $0.014 | $0.045 | $0.71 |
| DeepSeek V4.1 Flash (OpenRouter), off-peak | $0.001 | $0.004 | $0.06 |
| DeepSeek V4.1 Flash, peak (01–04, 06–10 UTC weekdays) | $0.002 | $0.007 | $0.12 |
| Mistral Large 4 (OpenRouter), current 50% sale | $0.004 | $0.014 | $0.22 |
| Mistral Large 4, list price | $0.009 | $0.028 | $0.45 |

**Caveats:**
- These assume low effort for evaluation and medium for writing. Higher effort can double output token costs.
- Anthropic charges 1.25× input cost for writing the prompt cache on first call (negligible across a batch).
- OpenRouter adds a credit-purchase fee on top of provider prices.
- Reasoning token billing varies by model; check the provider's documentation.

### Prices of OpenRouter models are fetched

For models you use through OpenRouter you do not need to enter prices. At the start of a run and when you open
`autoapp stats`, autoapp reads OpenRouter's public model list (one request, no key, at most once a day) and keeps the
prices in `workspace/openrouter-prices.json`. If OpenRouter cannot be reached, the saved prices stay in use and a
notice is shown. Cost figures are estimates from your token counts; the provider's bill is what counts.
Set `AUTOAPP_NO_PRICE_FETCH=1` to switch the request off.

The order is: your `llm.prices` in `config.yaml`, then the fetched OpenRouter prices, then the shipped table. A model
with no price anywhere shows "unknown", never zero. `autoapp stats` lists calls, tokens and the estimated cost
for each model in the chosen period (choose `run` for the latest run).

### Override prices

If a price is wrong, or the model is not on OpenRouter, set it in `config.yaml` (it wins over everything else):

```yaml
llm:
  prices:
    claude-sonnet-5-5:
      input: 3.0
      output: 15.0
      cached_input: 0.30
```

## Privacy

Your CV text and candidate profile (name, email, phone, salary, skills, language levels) are sent to the LLM provider. The provider uses these to evaluate jobs and write cover letters.

**Use a paid API tier that does not train on inputs**, such as:
- Anthropic (default)
- Google Gemini (paid tier only; the free tier may use inputs for training)
- OpenRouter (varies by model; check the provider's terms)

Do not send certificates, photos or sensitive personal data to the LLM.

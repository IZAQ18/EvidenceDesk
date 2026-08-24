# Server-managed AI gateway

EvidenceDesk can optionally use a text model to make a cited answer more natural. The feature is
off by default, and the Render portfolio pilot keeps it off. Visitors never enter an API key.
The operator stores one project key in the server environment and sets explicit daily limits.

## What leaves EvidenceDesk

For an accepted model request, EvidenceDesk sends only:

- the user's current question;
- up to three already-authorized evidence excerpts, their citation numbers, filenames, and page
  numbers; and
- fixed instructions that treat every excerpt as untrusted data.

It does not upload the original file, the full document, the conversation history, other tenants'
content, or a browser API key. Chunks flagged as instruction-like are excluded from the external
request. The Responses request sets `store: false` and enables no tools.

`store: false` is not the same as a promise of zero provider retention. OpenAI's current data
controls say API content is not used for training unless the customer opts in, while default abuse
monitoring logs may retain content for up to 30 days. Modified Abuse Monitoring and Zero Data
Retention require approval. Review the current [official data controls](https://developers.openai.com/api/docs/guides/your-data)
and your organization's requirements before enabling the feature.

## How an answer is accepted

1. Tenant, classification, lifecycle, and explicit document scope are enforced before retrieval.
2. The local retriever and reranker select a few answer-bearing passages.
3. The provider must return strict JSON: one to three claims and citation numbers.
4. EvidenceDesk rejects unknown citations, new numbers, changed negation, weak term support, extra
   fields, invalid JSON, provider errors, and timeouts.
5. Citation markers are attached by EvidenceDesk, not trusted from model text.
6. Any failure returns the existing extractive answer instead of failing the user's question.

These are useful mechanical checks, not a mathematical proof of entailment. Consequential answers
still require citation review, and a customer-specific held-out evaluation remains a launch gate.

## Cost protection

Before a provider call, EvidenceDesk atomically reserves a conservative worst-case request, token,
and cost budget for the UTC day. The provider is not called when any ceiling is exhausted. The
usage ledger stores provider/model names, a one-way subject hash, status, token counts, estimated
micro-USD cost, latency, fallback reason, and time. It stores no question, prompt, answer, excerpt,
or document text.

Cost is an estimate. Configure the provider's current input and output USD-per-million-token prices;
EvidenceDesk refuses to enable the gateway when either price is zero. The provider dashboard and a
provider-side project budget remain the financial source of truth.

## Configuration

```dotenv
EVIDENCEDESK_MODEL_GATEWAY_ENABLED=true
EVIDENCEDESK_MODEL_API_KEY=server-only-project-key
EVIDENCEDESK_MODEL_BASE_URL=https://api.openai.com/v1
EVIDENCEDESK_MODEL_NAME=gpt-5-mini
EVIDENCEDESK_MODEL_INPUT_PRICE_PER_MILLION_USD=replace-with-current-price
EVIDENCEDESK_MODEL_OUTPUT_PRICE_PER_MILLION_USD=replace-with-current-price
EVIDENCEDESK_MODEL_DAILY_REQUEST_LIMIT=100
EVIDENCEDESK_MODEL_DAILY_TOKEN_LIMIT=250000
EVIDENCEDESK_MODEL_DAILY_COST_LIMIT_CENTS=100
```

Set secrets only in `.env` locally or the hosting provider's secret environment panel. Never commit
the real key. Test with synthetic documents first, inspect the Insights usage card, and keep a
provider-side hard budget.

# Answer-quality evaluation

EvidenceDesk includes a deterministic synthetic acceptance benchmark that runs the real database,
authorization predicates, hybrid retrieval, reranker, extractive synthesis, and citation verifier.
It does not mock retrieval scores and does not send evaluation text to an external model.

Run it with:

```bash
python -m app.evaluation
```

The current eight-case corpus measures:

- direct policy retrieval and quantity answers;
- semantic wording such as “work from home” versus “perform duties remotely”;
- reimbursement/cost-claim and ethics-reporting language variation;
- refusal when the requested policy is missing;
- document-scope enforcement;
- confidential evidence exclusion for a reader; and
- cross-tenant isolation.

The report emits retrieval success, citation validity, refusal accuracy, security-boundary
accuracy, and p95 workflow latency. CI fails if retrieval drops below 95% or if citation,
refusal, or security-boundary accuracy drops below 100% on this deliberately small set.

## Interpreting the result honestly

A perfect result on eight synthetic cases proves that these specific regression cases passed. It
does **not** prove 100% accuracy on customer documents, unfamiliar policy language, poor scans,
tables, handwriting, or a large corpus. Before a paid pilot, create a separate human-reviewed set
from the participating organization, keep it out of the tuning loop, and report confidence
intervals plus failure examples.

The local semantic layer is an auditable sparse feature-vector encoder with policy concept groups,
not a pretrained neural embedding model. It is intentionally small, free, deterministic, and
safe for the Render demonstration. A later managed PostgreSQL deployment can evaluate pgvector
and a selected embedding provider against the same benchmark before enabling either.

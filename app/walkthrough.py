"""Public, read-only illustrations. Never connects to a workspace or model."""
from html import escape

EXAMPLES = (
    {
        "question": "How much annual leave can I take?",
        "status": "Supported by the example document",
        "answer": "Full-time employees receive 20 days of paid annual leave per calendar year. Submit planned leave at least 10 working days before the first day of absence; your manager approves it based on team coverage.",
        "heading": "Annual leave",
        "source": "Full-time employees receive 20 days of paid annual leave in each calendar year. Leave accrues monthly from the employee's start date. Employees should submit planned leave through the company leave system at least 10 working days before the first day of absence. The employee's manager approves requests based on team coverage. Up to five unused annual-leave days may be carried into the next calendar year; any additional unused days expire on 31 March.",
        "note": "The amount, notice period, and approval condition are all visible in the source. Check the conditions before acting on a short answer.",
    },
    {
        "question": "Can I work remotely every day?",
        "status": "The source includes a condition",
        "answer": "The example policy allows up to two remote days per week, with manager approval. It does not support working remotely every day. Roles involving on-site equipment or regulated facilities may have different requirements.",
        "heading": "Remote work",
        "source": "Employees may work remotely up to two days per week with manager approval. Roles involving on-site equipment or regulated facilities may have different requirements.",
        "note": "A useful answer preserves both the limit and the exception. A citation alone is not enough if the answer changes what the policy says.",
    },
    {
        "question": "What salary increase am I guaranteed?",
        "status": "Insufficient evidence",
        "answer": "This example handbook does not state a guaranteed salary increase. I cannot give you a supported amount or percentage from these documents.",
        "heading": "No supporting passage",
        "source": "The fictional handbook covers annual leave, sick leave, parental leave, remote work, and reporting concerns. It does not specify salary increases.",
        "note": "Missing evidence stays missing. The next step is to obtain the relevant compensation policy, rather than invent a number.",
    },
)


def walkthrough_page(access_url: str = "/signin") -> str:
    choices = "".join(
        f'<button type="button" class="question" aria-pressed="{str(i == 0).lower()}" '
        f'data-example="{i}" aria-controls="example-{i}"><span>0{i + 1}</span>{escape(e["question"])}</button>'
        for i, e in enumerate(EXAMPLES)
    )
    panels = "".join(
        f'<section class="example" id="example-{i}" {"" if i == 0 else "hidden"}>'
        f'<article class="answer"><p class="eyebrow">PREPARED ANSWER EXAMPLE</p>'
        f'<p class="status {"caution" if i == 2 else ""}">{escape(e["status"])}</p>'
        f'<h2>{escape(e["question"])}</h2><p class="answer-text">{escape(e["answer"])}</p>'
        f'<a class="citation" href="#source-{i}">{"Inspect what is covered" if i == 2 else "Inspect the supporting passage"} →</a>'
        f'<p class="review-note">{escape(e["note"])}</p></article>'
        f'<aside class="source" id="source-{i}" tabindex="-1"><p class="eyebrow">SOURCE IN VIEW</p>'
        f'<h3>Northstar Labs Employee Handbook</h3><p class="metadata">Fictional document · v2.1 · {escape(e["heading"])}</p>'
        f'<blockquote>{escape(e["source"])}</blockquote>'
        f'<p class="source-note">{"Coverage summary, not a quoted policy passage." if i == 2 else "Exact passage from the bundled synthetic handbook."}</p>'
        '</aside></section>'
        for i, e in enumerate(EXAMPLES)
    )
    return PAGE.replace("{{CHOICES}}", choices).replace("{{PANELS}}", panels).replace("{{ACCESS}}", escape(access_url, quote=True))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Explore three fictional EvidenceDesk examples: a cited answer, a policy condition, and a refusal when evidence is missing.">
<title>EvidenceDesk · Inspect an answer</title><link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>
:root{--paper:#f5f2ec;--ink:#182d25;--muted:#536259;--green:#1f5c4a;--line:#d8ded5;--amber:#795014}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:var(--green)}button,a{-webkit-tap-highlight-color:transparent}button:focus-visible,a:focus-visible{outline:3px solid #a3691e;outline-offset:4px}.wrap{max-width:1200px;margin:auto;padding:0 32px}
header{border-bottom:1px solid var(--line);background:#fff}nav{min-height:76px;display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{font:700 26px Georgia,serif;text-decoration:none}.workspace{font-size:14px;font-weight:650;text-decoration:none}.hero{padding:48px 0 26px}.eyebrow{font-size:11px;letter-spacing:.13em;font-weight:750;color:var(--muted);margin:0 0 16px}.hero h1{font:500 clamp(35px,5vw,64px)/1.08 Georgia,serif;letter-spacing:-.035em;margin:12px 0 20px;max-width:760px}.intro{max-width:740px;color:var(--muted);font-size:17px}.mode{display:inline-block;border:1px solid var(--line);padding:5px 10px;border-radius:4px;font-size:12px;background:#fff}.questions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:22px 0}.question{display:flex;gap:12px;align-items:start;text-align:left;font-weight:600;font-family:inherit;font-size:14px;line-height:1.45;background:transparent;border:1px solid var(--line);padding:16px;color:var(--ink);cursor:pointer;border-radius:5px;min-height:76px}.question span{font-variant-numeric:tabular-nums;color:var(--muted)}.question[aria-pressed=true]{border-color:var(--green);background:var(--green);color:#fff}.question[aria-pressed=true] span{color:#dfebe4}.example{display:grid;grid-template-columns:1.1fr 1fr;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:white;min-height:420px}.example[hidden]{display:none}.answer,.source{padding:30px}.source{background:#ebefe8;border-left:1px solid var(--line);scroll-margin-top:16px}.status{display:inline-block;font-size:12px;border:1px solid #bbd2c3;background:#edf5ef;padding:4px 8px;border-radius:4px;color:var(--green);margin:0 0 15px}.status.caution{color:var(--amber);border-color:#dfcba9;background:#fbf5e8}h2{font:500 28px/1.22 Georgia,serif;margin:0 0 20px}h3{font:600 21px/1.3 Georgia,serif;margin:0 0 6px}.answer-text{font-size:17px}.citation{display:inline-block;font-size:13px;font-weight:650;margin:8px 0}.review-note{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px;margin-top:24px}.metadata,.source-note{font-size:12px;color:var(--muted)}blockquote{margin:26px 0 20px;padding-left:18px;border-left:3px solid var(--green);font:18px/1.65 Georgia,serif}.next{display:flex;justify-content:space-between;align-items:center;gap:25px;padding:28px 0}.next p{max-width:750px;font-size:13px;color:var(--muted)}.button{display:inline-block;white-space:nowrap;background:var(--green);color:#fff;padding:12px 17px;text-decoration:none;border-radius:5px;font-weight:600;font-size:14px}footer{border-top:1px solid var(--line);padding:22px 0;margin-top:12px;font-size:12px;color:var(--muted)}
@media(max-width:740px){.wrap{padding:0 18px}.hero{padding-top:30px}.questions{grid-template-columns:1fr}.question{min-height:0;padding:12px}.example{grid-template-columns:1fr}.answer,.source{padding:22px}.source{border-left:0;border-top:1px solid var(--line)}.next{align-items:start;flex-direction:column;gap:8px}nav{min-height:64px}.workspace{font-size:12px}.hero h1{font-size:39px}}
@media(prefers-reduced-motion:no-preference){.question{transition:background .15s ease}}
</style></head><body>
<header><nav class="wrap"><a class="brand" href="/about">EvidenceDesk</a><a class="workspace" href="{{ACCESS}}">Open private workspace →</a></nav></header>
<main class="wrap"><section class="hero"><p class="eyebrow">ANSWERS YOU CAN INSPECT</p><span class="mode">Guided example · fictional documents</span><h1>The answer is only useful<br>if the evidence holds up.</h1><p class="intro">Choose a question. Read the answer beside its source. See why a condition matters—and what happens when the document cannot answer.</p></section>
<div class="questions" aria-label="Example questions">{{CHOICES}}</div><div aria-live="polite">{{PANELS}}</div>
<noscript><p>JavaScript is off. The first example remains available. Open the private workspace for the complete application.</p></noscript>
<section class="next"><p>These are prepared illustrations, not a live search or model response. No account or upload is needed here. The private workspace runs retrieval over documents you are permitted to access, with inspectable citations and evidence checks.</p><a class="button" href="{{ACCESS}}">Try your own question →</a></section>
</main><footer><div class="wrap">Built by IZAQ · Portfolio prototype · <a href="/privacy">Privacy</a> · <a href="/about">About the product</a></div></footer>
<script>
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{
 document.querySelectorAll('[data-example]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
 document.querySelectorAll('.example').forEach(panel=>panel.hidden=panel.id!==`example-${button.dataset.example}`);
}));
</script></body></html>"""

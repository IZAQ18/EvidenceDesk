from html import escape

SELLER_NAME = "Ahsan Maqbool Ahmad"
SUPPORT_EMAIL = "evidence.desk.help@gmail.com"
POLICY_EFFECTIVE_DATE = "28 August 2026"


def _layout(*, title: str, eyebrow: str, introduction: str, content: str) -> str:
    safe_title = escape(title)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{safe_title} for EvidenceDesk, operated by {SELLER_NAME}.">
<meta name="robots" content="index,follow"><title>{safe_title} · EvidenceDesk</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>
:root{{--bg:#f5f8f4;--panel:#ffffff;--ink:#14251a;--muted:#66746b;--blue:#e87524;--mint:#11845a;--line:#d7e2da}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:linear-gradient(180deg,#fbfcfa,#f3f7f3);color:var(--ink);font:15px/1.72 Inter,"Segoe UI",system-ui,sans-serif}}body:before{{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 82% 0%,#e8752410,transparent 34%),radial-gradient(circle at 10% 78%,#11845a0d,transparent 38%)}}a{{color:var(--blue)}}header{{position:sticky;top:0;z-index:5;border-bottom:1px solid var(--line);background:#ffffffed;backdrop-filter:blur(18px);box-shadow:0 8px 28px #214b330b}}nav{{width:min(980px,calc(100% - 34px));height:72px;margin:auto;display:flex;align-items:center;gap:18px}}nav img{{width:44px;height:44px;border-radius:13px;box-shadow:0 8px 22px #214b3316}}.brand{{margin-right:auto;color:var(--ink);text-decoration:none;font:750 21px Georgia,serif}}nav>a:not(.brand){{font-size:12px;text-decoration:none;color:var(--muted)}}main{{width:min(860px,calc(100% - 34px));margin:72px auto 110px;position:relative}}.eyebrow{{color:var(--mint);font:800 10px ui-monospace,monospace;letter-spacing:.17em;text-transform:uppercase}}h1{{margin:14px 0 18px;font:750 clamp(42px,8vw,78px)/.98 Georgia,serif;letter-spacing:-.045em}}.intro{{max-width:720px;color:#586b60;font:500 19px/1.6 Georgia,serif}}.effective{{display:inline-block;margin:20px 0 32px;padding:7px 11px;border:1px solid #e875243d;border-radius:999px;color:var(--muted);font:750 10px ui-monospace,monospace;background:#fff7ef}}article{{margin-top:16px;padding:28px;border:1px solid var(--line);border-radius:19px;background:#ffffffed;box-shadow:0 20px 58px #214b3310}}h2{{margin:0 0 10px;font:700 24px Georgia,serif}}p,li{{color:var(--muted)}}li+li{{margin-top:8px}}strong{{color:var(--ink)}}.notice{{border-left:3px solid var(--blue);background:#e8752408}}.contact{{border-color:#11845a42;background:#11845a08}}footer{{border-top:1px solid var(--line);padding:34px 0;color:var(--muted)}}footer div{{width:min(860px,calc(100% - 34px));margin:auto;display:flex;gap:16px;flex-wrap:wrap}}footer a{{font-size:12px;text-decoration:none}}@media(max-width:620px){{nav>a:not(.brand){{display:none}}main{{margin-top:48px}}article{{padding:21px}}}}
</style></head><body>
<header><nav><img src="/logo.svg" alt="EvidenceDesk logo"><a class="brand" href="/about">EvidenceDesk</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/refunds">Refunds</a></nav></header>
<main><div class="eyebrow">{escape(eyebrow)}</div><h1>{safe_title}</h1><p class="intro">{escape(introduction)}</p><span class="effective">Effective {POLICY_EFFECTIVE_DATE}</span>{content}
<article class="contact"><h2>Questions</h2><p>Contact <strong>{SELLER_NAME}</strong> at <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>. Never email passwords, complete payment-card details, government identifiers, or confidential documents.</p></article></main>
<footer><div><span>EvidenceDesk · Operated by {SELLER_NAME}</span><a href="/about">Product</a><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/refunds">Refunds</a></div></footer>
</body></html>"""


def terms_page() -> str:
    return _layout(
        title="Terms of Service",
        eyebrow="Clear product boundaries",
        introduction=(
            "These terms explain the rules for using the EvidenceDesk website, protected pilot, "
            "and any paid plan that is later activated through an approved hosted checkout."
        ),
        content=f"""
<article class="notice"><h2>Current launch status</h2><p>EvidenceDesk is currently a protected, synthetic-data evaluation. Paid checkout is not active. Commercial service begins only when the website and hosted checkout expressly say that purchasing is available.</p></article>
<article><h2>1. Operator and acceptance</h2><p>EvidenceDesk is operated by <strong>{SELLER_NAME}</strong>. By accessing or using the service, you agree to these terms. If you use EvidenceDesk for an organization, you confirm that you are authorized to act for it.</p></article>
<article><h2>2. What EvidenceDesk provides</h2><p>EvidenceDesk retrieves passages from documents made available to a workspace, prepares answers from that evidence, and attaches citations for inspection. Output may be incomplete or wrong. Users must review the cited source and apply appropriate human judgment.</p></article>
<article><h2>3. Acceptable use</h2><ul><li>Upload only material that you are authorized to use and process.</li><li>Do not use the pilot for personal, confidential, regulated, medical, legal, financial, or safety-critical information.</li><li>Do not attempt unauthorized access, interfere with the service, evade limits, introduce malware, or misuse the product to harm another person.</li><li>Do not treat an EvidenceDesk answer as professional legal, medical, financial, HR, compliance, or safety advice.</li></ul></article>
<article><h2>4. Accounts, plans, and billing</h2><p>The free pilot may use a shared access code and may reset without notice. If individual accounts and paid plans are activated, the checkout will display the product, price, currency, taxes, billing period, and renewal terms before purchase. Hosted payments are processed by the named payment provider acting under its own payment terms; EvidenceDesk does not collect complete card details.</p></article>
<article><h2>5. Subscriptions and cancellation</h2><p>A monthly plan renews until cancelled. When subscriptions become available, customers may cancel future renewal through the provider's customer portal or by contacting support. Cancellation normally takes effect at the end of the paid billing period unless the checkout or mandatory law states otherwise. The separate <a href="/refunds">Refund Policy</a> explains the seven-day refund window.</p></article>
<article><h2>6. Customer content</h2><p>You retain rights in content you are authorized to submit. You permit EvidenceDesk to process that content only to operate, secure, support, and improve the requested service. The current public pilot must be used with synthetic or non-sensitive demonstration documents only.</p></article>
<article><h2>7. Availability and changes</h2><p>The free pilot is provided for evaluation without an uptime commitment or production-service guarantee. Features, limits, and preview pricing may change before commercial launch. A paid customer's material price or renewal change will be communicated through the checkout provider or service before it takes effect.</p></article>
<article><h2>8. Intellectual property</h2><p>EvidenceDesk software, branding, interface, and documentation remain the property of their respective owner. These terms grant a limited, revocable right to use the service; they do not transfer ownership of the product or another user's content.</p></article>
<article><h2>9. Warranty and liability boundary</h2><p>To the extent permitted by applicable law, the preview is provided “as is” and “as available.” EvidenceDesk does not promise that every answer, OCR result, citation, or service period will be uninterrupted or error-free. Nothing here excludes rights or liabilities that applicable law does not allow to be excluded.</p></article>
<article><h2>10. Suspension and termination</h2><p>Access may be limited or suspended for security risk, misuse, non-payment, legal requirements, or material violation of these terms. Account export and deletion controls will apply as described in the product when durable customer accounts are launched.</p></article>
<article><h2>11. Changes to these terms</h2><p>The effective date will be updated when these terms materially change. Continued use after a notified change means the revised terms apply, subject to mandatory consumer rights.</p></article>""",
    )


def privacy_page() -> str:
    return _layout(
        title="Privacy Notice",
        eyebrow="Data minimization first",
        introduction=(
            "This notice explains what EvidenceDesk processes, why it is processed, and the "
            "important limits of the current free pilot."
        ),
        content=f"""
<article class="notice"><h2>Current pilot boundary</h2><p>The Render-hosted pilot uses temporary infrastructure and synthetic demonstration documents. Data may reset when the service restarts. Do not submit personal, confidential, client, legal, medical, financial, regulated, or otherwise sensitive information.</p></article>
<article><h2>1. Who operates the service</h2><p>The service is operated by <strong>{SELLER_NAME}</strong>. Privacy questions and requests can be sent to <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p></article>
<article><h2>2. Information processed</h2><ul><li><strong>Pilot access and security data:</strong> session cookies, workspace and role identifiers, request timing, security events, and limited technical logs.</li><li><strong>Content you submit:</strong> documents, extracted text, questions, answers, citations, feedback, and conversation history.</li><li><strong>Account data when accounts are enabled:</strong> name, email address, password hash, memberships, invitations, and account-control events.</li><li><strong>Limited billing metadata when checkout is enabled:</strong> provider event ID, product, subscription state, amount, currency, masked customer hint, and timestamps. EvidenceDesk does not receive or store complete payment-card details.</li><li><strong>Voluntary pilot feedback:</strong> ratings and comments. The feedback form does not request a name or email address.</li></ul></article>
<article><h2>3. Why information is processed</h2><p>Information is used to provide document retrieval and cited answers, isolate workspaces, prevent abuse, enforce usage limits, operate accounts, respond to support requests, verify hosted-payment events, diagnose reliability, and protect the service.</p></article>
<article><h2>4. AI and service providers</h2><p>EvidenceDesk may use hosting, database, object-storage, email, monitoring, payment, and model providers when those features are configured. For model-assisted answers, the application is designed to send selected evidence excerpts rather than an entire document where practical. Provider-specific subprocessors and retention terms must be reviewed before real customer data is accepted.</p></article>
<article><h2>5. Cookies</h2><p>Strictly necessary HttpOnly cookies may maintain pilot access or an authenticated account session. EvidenceDesk does not currently advertise third-party behavioral advertising or cross-site tracking cookies.</p></article>
<article><h2>6. Retention and deletion</h2><p>The current pilot's temporary data can disappear on restart and is not a durable archive. In account mode, workspace export and account-deletion controls are implemented, but managed backup expiry and a final commercial retention schedule must be operationally accepted before public customer accounts launch. Payment-provider records remain subject to the provider's financial and legal retention duties.</p></article>
<article><h2>7. Sharing</h2><p>Information is shared only with configured service providers as needed to operate the service, when a workspace administrator authorizes access, or when required to protect rights, safety, and legal obligations. EvidenceDesk does not claim to sell personal information.</p></article>
<article><h2>8. International processing</h2><p>Hosting and service providers may process information in countries other than the user's own. The commercial service will publish the configured provider list and applicable transfer safeguards before accepting real organizational data.</p></article>
<article><h2>9. Your choices</h2><p>You may avoid submitting content, request support, use available export or deletion controls, or stop using the pilot. Requests should identify the relevant account email or order reference without sending passwords, card details, or document contents.</p></article>
<article><h2>10. Security and limitations</h2><p>EvidenceDesk uses access controls, transport encryption, scoped retrieval, bounded parsing, and audit records. No system is risk-free. The pilot is not presented as compliance-certified, GovCloud-authorized, or approved for regulated data.</p></article>
<article><h2>11. Updates</h2><p>This notice will be updated when the data flow, providers, retention schedule, or commercial service changes materially. The latest effective date appears above.</p></article>""",
    )


def refunds_page() -> str:
    return _layout(
        title="Refund Policy",
        eyebrow="Seven-day purchase protection",
        introduction=(
            "When hosted checkout becomes active, eligible EvidenceDesk purchases can be "
            "requested for refund within seven calendar days of the charge."
        ),
        content=f"""
<article class="notice"><h2>Payments are not active yet</h2><p>This policy is published for provider review and future hosted purchases. The current private demo is free. Direct MCB or NayaPay support transfers are voluntary contributions and do not purchase or unlock an EvidenceDesk plan.</p></article>
<article><h2>1. Seven-day window</h2><p>For an eligible EvidenceDesk Pro initial charge or renewal charge, request a full refund within <strong>seven calendar days</strong> of the transaction date. For the Team pilot, request a refund within seven calendar days of payment and before the written evaluation has been fully delivered.</p></article>
<article><h2>2. How to request a refund</h2><p>Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> with the FastSpring order reference, purchase email address, product name, and a short reason so the request can be located and improved. Do not send complete card or bank-account details.</p></article>
<article><h2>3. Processing</h2><p>Approved hosted-payment refunds are returned through FastSpring to the original payment method. Bank posting time depends on the payment method and financial institution. Some payment methods, fraud reviews, disputes, or older transactions may require FastSpring Support to process the refund.</p></article>
<article><h2>4. Subscription effect</h2><p>A refund does not always cancel future renewal automatically. If a subscription should end, state that in the request or cancel it through the provider's customer portal. When a refund requires access to end, the corresponding paid entitlement may be removed after the verified provider event is received.</p></article>
<article><h2>5. Exceptions and mandatory rights</h2><p>Duplicate, fraudulent, or unauthorized transactions may be handled directly by FastSpring under its risk and payment rules. Direct voluntary support transfers are outside this product refund policy. Nothing in this policy limits refund, cancellation, or consumer rights that applicable law makes mandatory.</p></article>""",
    )


def checkout_result_page(*, cancelled: bool) -> str:
    if cancelled:
        title = "Checkout cancelled"
        heading = "No EvidenceDesk upgrade was confirmed."
        detail = (
            "You can return to the plans page whenever you are ready. If a provider receipt says "
            "you were charged, contact support with the order reference so it can be checked."
        )
        action = '<a class="button" href="/about#plans">Return to plans</a>'
    else:
        title = "Checkout returned"
        heading = "We are verifying the provider event."
        detail = (
            "A browser redirect is not treated as proof of payment. EvidenceDesk updates paid "
            "access only after a valid signed notification arrives from the checkout provider."
        )
        action = '<a class="button" href="/signin">Continue to EvidenceDesk</a>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{title} · EvidenceDesk</title><link rel="icon" href="/logo.svg" type="image/svg+xml"><style>:root{{--bg:#07090d;--panel:#10151d;--ink:#edf4fa;--muted:#9dacba;--blue:#58ddff;--line:#2a3542}}*{{box-sizing:border-box}}body{{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 70% 5%,#274e7044,transparent 35%),var(--bg);color:var(--ink);font:15px/1.7 Inter,"Segoe UI",system-ui,sans-serif}}main{{width:min(610px,100%);padding:38px;border:1px solid var(--line);border-radius:24px;background:var(--panel);box-shadow:0 30px 100px #0008}}img{{width:58px;height:58px;border-radius:17px}}h1{{margin:24px 0 12px;font:750 42px/1 Georgia,serif;letter-spacing:-.035em}}p{{color:var(--muted)}}.button{{display:inline-flex;margin-top:18px;min-height:46px;padding:0 18px;align-items:center;border-radius:12px;background:var(--blue);color:#061117;text-decoration:none;font-weight:800}}.links{{margin-top:25px;font-size:12px}}.links a{{color:var(--muted);margin-right:14px}}</style></head><body><main><img src="/logo.svg" alt="EvidenceDesk logo"><h1>{heading}</h1><p>{detail}</p>{action}<div class="links"><a href="/refunds">Refund policy</a><a href="mailto:{SUPPORT_EMAIL}">Support</a></div></main></body></html>"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from app.config import Settings
from app.models import Principal

PILOT_COOKIE = "evidencedesk_pilot"
PILOT_COOKIE_MESSAGE = b"evidencedesk-pilot-session-v1"


def pilot_cookie_value(access_code: str) -> str:
    return hmac.new(access_code.encode(), PILOT_COOKIE_MESSAGE, hashlib.sha256).hexdigest()


def has_pilot_access(request: Request, settings: Settings) -> bool:
    if not settings.pilot_access_code:
        return True
    supplied = request.cookies.get(PILOT_COOKIE, "")
    expected = pilot_cookie_value(settings.pilot_access_code)
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def pilot_principal(settings: Settings) -> Principal:
    return Principal(
        subject="pilot-guest",
        tenant_id=settings.pilot_tenant_id,
        role="admin",
        clearances=["public", "internal", "confidential"],
    )


def access_page(error: str | None = None) -> str:
    message = (
        '<div class="error" role="alert">That access code was not accepted.</div>'
        if error
        else '<p class="note">Limited pilot · synthetic documents only · activity may reset</p>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive"><title>EvidenceDesk · Private pilot</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>
:root{{--mint:#79f2bf;--gold:#ffc568;--ink:#f5fff9;--muted:#9db3a8}}
.kicker{{display:flex!important;align-items:center;gap:13px}}.kicker:before{{content:"";width:50px;height:50px;flex:0 0 50px;border-radius:14px;background:url('/logo.svg') center/contain no-repeat;box-shadow:0 14px 42px #000b}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px 0;background:#030806;color:var(--ink);font-family:Inter,"Segoe UI",system-ui,sans-serif;overflow:auto}}
body:before{{content:"";position:fixed;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.9),rgba(0,0,0,.3)),url('/pilot-background.png') center/cover;filter:saturate(.85);transform:scale(1.02)}}
body:after{{content:"";position:fixed;inset:0;background-image:linear-gradient(#ffffff0a 1px,transparent 1px),linear-gradient(90deg,#ffffff08 1px,transparent 1px);background-size:48px 48px;mask-image:linear-gradient(115deg,#000,transparent 76%)}}
.shell{{position:relative;z-index:1;width:min(1080px,calc(100% - 32px));display:grid;grid-template-columns:1fr 390px;gap:70px;align-items:center}}
.kicker{{font:800 11px ui-monospace,monospace;letter-spacing:.2em;color:var(--mint);text-transform:uppercase}}h1{{font-size:clamp(58px,9vw,128px);line-height:.78;letter-spacing:-.075em;margin:.28em 0}}h1 span{{display:block;color:var(--gold);margin-left:.7em}}.deck{{max-width:610px;font:500 clamp(17px,2vw,24px) Georgia,serif;color:#c5d6cd}}.deck b{{color:var(--mint);font-weight:500}}
.gate{{padding:30px;border:1px solid #ffffff20;border-radius:24px;background:rgba(7,17,13,.78);box-shadow:0 35px 100px #0009;backdrop-filter:blur(24px)}}.maker-lockup{{display:flex;align-items:center;gap:11px}}.maker-portrait{{width:48px;height:48px;object-fit:cover;border-radius:14px;border:1px solid #ffc56880;box-shadow:0 0 0 3px #ffc56812,0 10px 30px #0008}}.maker{{font:850 14px ui-monospace,monospace;letter-spacing:.2em;color:var(--gold)}}.maker small{{display:block;margin-top:5px;color:#71867b;font-size:8px;letter-spacing:.16em}}h2{{font-size:27px;letter-spacing:-.04em;margin:38px 0 8px}}.sub{{color:var(--muted);font-size:13px;margin:0 0 22px}}label{{display:block;font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;margin-bottom:7px;color:#b9cbc1}}input{{width:100%;border:1px solid #ffffff21;border-radius:11px;background:#07100d;color:#fff;padding:13px;font:inherit}}input:focus{{outline:2px solid var(--mint);outline-offset:2px}}button{{width:100%;margin-top:11px;border:0;border-radius:11px;padding:13px;background:linear-gradient(100deg,var(--mint),#61d9d0);color:#04100a;font-weight:850;cursor:pointer}}button:hover{{filter:brightness(1.08)}}.note,.error{{font-size:11px;margin:14px 0 0}}.note{{color:#799084}}.error{{color:#ff9c94}}.trust{{display:flex;gap:7px;flex-wrap:wrap;margin-top:22px}}.trust span{{border:1px solid #ffffff16;border-radius:999px;padding:5px 8px;color:#8fa69a;font-size:9px}}.support-mini{{grid-column:1/-1;display:grid;grid-template-columns:auto minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:center;padding:16px 18px;border:1px solid #ffc56855;border-radius:20px;background:linear-gradient(120deg,#241b0ccf,#07130ede);box-shadow:0 22px 70px #0008;backdrop-filter:blur(20px)}}.support-mini img{{width:62px;height:62px;object-fit:cover;border-radius:17px;border:1px solid #ffc56888}}.support-mini h3{{margin:0;color:var(--gold);font:800 16px Georgia,serif}}.support-mini p{{margin:4px 0 0;color:var(--muted);font-size:9px}}.support-mini .pledge{{color:var(--mint);font-weight:800}}.transfer{{display:grid;grid-template-columns:1fr;gap:6px;padding-left:16px;border-left:1px solid #ffffff16}}.transfer span{{color:#aebfb6;font-size:9px}}.transfer b{{display:inline-block;min-width:58px;color:var(--gold);font:800 8px ui-monospace,monospace;letter-spacing:.1em}}.transfer code{{color:var(--mint);font-size:9px;overflow-wrap:anywhere}}
@media(max-width:800px){{.shell{{grid-template-columns:1fr;gap:25px;padding:30px 0}}h1{{font-size:20vw}}.deck{{font-size:17px}}.gate{{padding:23px}}.support-mini{{grid-template-columns:auto 1fr}}.transfer{{grid-column:1/-1;padding:12px 0 0;border-left:0;border-top:1px solid #d7e2da}}}}
/* EvidenceDesk daylight entry */
:root{{--mint:#11845a;--gold:#e87524;--ink:#14251a;--muted:#66746b}}
body{{background:#f5f8f4}}body:before{{background:radial-gradient(circle at 10% 15%,#11845a14,transparent 34%),radial-gradient(circle at 88% 82%,#e8752414,transparent 32%),linear-gradient(135deg,#fbfcfa,#eff6f1);filter:none}}body:after{{background-image:linear-gradient(#173f2b0b 1px,transparent 1px),linear-gradient(90deg,#173f2b08 1px,transparent 1px)}}
.deck{{color:#5a6d61}}.gate{{border-color:#d7e2da;background:#ffffffeb;box-shadow:0 32px 90px #214b3320}}.maker-portrait{{border-color:#efb17e;box-shadow:0 0 0 3px #e8752410,0 10px 28px #214b331a}}.maker small{{color:#738179}}label{{color:#53675c}}input{{border-color:#cad9cf;background:#f8faf7;color:var(--ink)}}input:focus{{outline-color:#11845a}}button{{background:linear-gradient(105deg,#11845a,#0e7650);color:#fff;box-shadow:0 12px 26px #11845a27}}.note{{color:#718078}}.trust span{{border-color:#d7e2da;color:#627269;background:#f8faf7}}
.support-mini{{border-color:#efc5a5;background:linear-gradient(120deg,#fff3e8ee,#eef8f2ee,#ffffffee);box-shadow:0 22px 60px #214b331b}}.support-mini img{{border-color:#efb17e}}.transfer{{border-left-color:#d7e2da}}.transfer span{{color:#586b60}}
</style></head><body><main class="shell"><section><div class="kicker">Secure knowledge system · private preview</div><h1>Evidence<span>Desk</span></h1><p class="deck">Answers should earn trust. <b>Start with the source.</b></p></section><section class="gate"><div class="maker-lockup"><img class="maker-portrait" src="/developer-signature.png" alt="IZAQ developer portrait"><div class="maker">IZAQ<small>PRODUCT DIRECTION · DESIGN × ENGINEERING</small></div></div><h2>Enter the pilot</h2><p class="sub">Use the private access code shared with approved reviewers.</p><form method="post" action="/access"><label for="access-code">Pilot access code</label><input id="access-code" name="access_code" type="password" minlength="12" maxlength="200" autocomplete="current-password" required autofocus><button type="submit">Open EvidenceDesk →</button></form>{message}<div class="trust"><span>Unindexed</span><span>Encrypted transport</span><span>Synthetic data</span></div></section><aside class="support-mini" aria-label="Support IZAQ"><img src="/developer-signature.png" alt="IZAQ developer portrait"><div><h3>Support IZAQ</h3><p>Back thoughtful independent engineering.</p><p class="pledge">40% of every PKR 100 received is pledged to charitable giving.</p></div><div class="transfer"><span><b>MCB</b> Ahsan Maqbool Ahmad · <code>PK58 MUCB 1160 9811 3100 4752</code></span><span><b>NAYAPAY</b> Muhammad Qasim Ayub · <code>PK83 NAYA 1234 5030 6196 1996</code></span></div></aside></main></body></html>"""

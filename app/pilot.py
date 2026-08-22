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
<style>
:root{{--mint:#79f2bf;--gold:#ffc568;--ink:#f5fff9;--muted:#9db3a8}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#030806;color:var(--ink);font-family:Inter,"Segoe UI",system-ui,sans-serif;overflow:hidden}}
body:before{{content:"";position:fixed;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.9),rgba(0,0,0,.3)),url('/pilot-background.png') center/cover;filter:saturate(.85);transform:scale(1.02)}}
body:after{{content:"";position:fixed;inset:0;background-image:linear-gradient(#ffffff0a 1px,transparent 1px),linear-gradient(90deg,#ffffff08 1px,transparent 1px);background-size:48px 48px;mask-image:linear-gradient(115deg,#000,transparent 76%)}}
.shell{{position:relative;z-index:1;width:min(1080px,calc(100% - 32px));display:grid;grid-template-columns:1fr 390px;gap:70px;align-items:center}}
.kicker{{font:800 11px ui-monospace,monospace;letter-spacing:.2em;color:var(--mint);text-transform:uppercase}}h1{{font-size:clamp(58px,9vw,128px);line-height:.78;letter-spacing:-.075em;margin:.28em 0}}h1 span{{display:block;color:var(--gold);margin-left:.7em}}.deck{{max-width:610px;font:500 clamp(17px,2vw,24px) Georgia,serif;color:#c5d6cd}}.deck b{{color:var(--mint);font-weight:500}}
.gate{{padding:30px;border:1px solid #ffffff20;border-radius:24px;background:rgba(7,17,13,.78);box-shadow:0 35px 100px #0009;backdrop-filter:blur(24px)}}.maker{{font:850 14px ui-monospace,monospace;letter-spacing:.2em;color:var(--gold)}}.maker small{{display:block;margin-top:5px;color:#71867b;font-size:8px;letter-spacing:.16em}}h2{{font-size:27px;letter-spacing:-.04em;margin:38px 0 8px}}.sub{{color:var(--muted);font-size:13px;margin:0 0 22px}}label{{display:block;font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;margin-bottom:7px;color:#b9cbc1}}input{{width:100%;border:1px solid #ffffff21;border-radius:11px;background:#07100d;color:#fff;padding:13px;font:inherit}}input:focus{{outline:2px solid var(--mint);outline-offset:2px}}button{{width:100%;margin-top:11px;border:0;border-radius:11px;padding:13px;background:linear-gradient(100deg,var(--mint),#61d9d0);color:#04100a;font-weight:850;cursor:pointer}}button:hover{{filter:brightness(1.08)}}.note,.error{{font-size:11px;margin:14px 0 0}}.note{{color:#799084}}.error{{color:#ff9c94}}.trust{{display:flex;gap:7px;flex-wrap:wrap;margin-top:22px}}.trust span{{border:1px solid #ffffff16;border-radius:999px;padding:5px 8px;color:#8fa69a;font-size:9px}}
@media(max-width:800px){{.shell{{grid-template-columns:1fr;gap:25px;padding:30px 0}}h1{{font-size:20vw}}.deck{{font-size:17px}}.gate{{padding:23px}}}}
</style></head><body><main class="shell"><section><div class="kicker">Secure knowledge system · private preview</div><h1>Evidence<span>Desk</span></h1><p class="deck">Answers should earn trust. <b>Start with the source.</b></p></section><section class="gate"><div class="maker">IZAQ<small>PRODUCT DIRECTION · DESIGN × ENGINEERING</small></div><h2>Enter the pilot</h2><p class="sub">Use the private access code shared with approved reviewers.</p><form method="post" action="/access"><label for="access-code">Pilot access code</label><input id="access-code" name="access_code" type="password" minlength="12" maxlength="200" autocomplete="current-password" required autofocus><button type="submit">Open EvidenceDesk →</button></form>{message}<div class="trust"><span>Unindexed</span><span>Encrypted transport</span><span>Synthetic data</span></div></section></main></body></html>"""

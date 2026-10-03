"""The HTML for the invite form and its confirmation pages."""
from __future__ import annotations

import html

from agentbox_invite_records import CONNECTORS

# --- the page ------------------------------------------------------------------

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join {household}</title>
<style>
 body{{font:16px/1.55 system-ui,sans-serif;max-width:34rem;margin:0 auto;
      padding:2rem 1.25rem;color:#1a1a1a}}
 h1{{font-size:1.5rem;margin:0 0 .25rem}}
 .sub{{color:#666;margin:0 0 2rem}}
 fieldset{{border:1px solid #ddd;border-radius:.5rem;padding:1rem;margin:0 0 1rem}}
 legend{{padding:0 .4rem;font-weight:600}}
 label{{display:block;margin:.6rem 0}}
 input[type=text]{{width:100%;padding:.6rem;font-size:1rem;border:1px solid #bbb;
                   border-radius:.35rem}}
 .note{{color:#666;font-size:.875rem;margin:.15rem 0 0 1.6rem}}
 .fixed{{color:#666;font-size:.9rem}}
 button{{background:#1a1a1a;color:#fff;border:0;border-radius:.35rem;
         padding:.7rem 1.2rem;font-size:1rem;cursor:pointer}}
 .err{{background:#fdecea;border:1px solid #f5c6c2;padding:.75rem;
       border-radius:.35rem}}
</style>
<h1>Join {household}</h1>
<p class="sub">This sets up your own account on {household}'s assistant.
Your things stay yours. The assistant can't read another person's mail or
private notes.</p>
{body}
"""

FORM = """<form method="post">
<fieldset><legend>You</legend>
<label>What should the assistant call you?
<input type="text" name="display_name" required autofocus
       placeholder="e.g. Sam" maxlength="40"></label>
</fieldset>

<fieldset><legend>Your own accounts</legend>
{personal}
</fieldset>

<fieldset><legend>Already shared, nothing to do</legend>
{shared}
</fieldset>

<button type="submit">Continue</button>
</form>"""

DONE = """<p>Thanks{name}, that's everything we need.</p>
<p>{owner} will finish setting up your account. You'll hear from them shortly.</p>
<p class="fixed">You can close this page.</p>"""

GOOGLE_STEP = """<p>Thanks{name}. One more step: you asked to connect Gmail
and Calendar, so Google needs your permission.</p>
<p>You'll sign in to Google directly. {owner} never sees your password, and you
can revoke access at any time from your Google account.</p>
<p><a href="{url}"><button type="button">Sign in with Google</button></a></p>
<p class="fixed">Nothing else is connected to Google until you do this.</p>"""

GOOGLE_DONE = """<p>Google connected.</p>
<p>{owner} will finish setting up your account shortly.</p>
<p class="fixed">You can close this page.</p>"""


def render_form(household: str) -> str:
    personal, shared = [], []
    for key, spec in CONNECTORS.items():
        if spec["personal"]:
            personal.append(
                f'<label><input type="checkbox" name="connector" value="{key}" '
                f'checked> {html.escape(spec["label"])}</label>'
                f'<p class="note">{html.escape(spec["note"])}</p>')
        else:
            shared.append(f'<p class="fixed">{html.escape(spec["label"])}: '
                          f'{html.escape(spec["note"])}</p>')
    return PAGE.format(household=html.escape(household),
                       body=FORM.format(personal="\n".join(personal),
                                        shared="\n".join(shared)))

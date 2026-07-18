import markdown as markdown_lib
import requests

from app.config import settings

RESEND_API_URL = "https://api.resend.com/emails"

EMAIL_HTML_TEMPLATE = """\
<html>
  <body style="font-family: -apple-system, Helvetica, Arial, sans-serif; color: #23291f; \
background: #f4f6f2; padding: 24px;">
    <div style="max-width: 640px; margin: 0 auto; background: #ffffff; border: 1px solid #dde3d8; \
border-radius: 8px; padding: 24px;">
      <h1 style="color: #3f6b3a; font-size: 1.1rem; margin-top: 0;">🥾 Your Hiking Plan</h1>
      {body}
    </div>
  </body>
</html>
"""


def send_plan_email(to_email: str, plan_markdown: str) -> None:
    """Send a hiking plan to `to_email` as an HTML email via the Resend API. Raises on failure.

    Uses Resend's HTTPS API rather than raw SMTP - PaaS hosts like Railway block outbound SMTP
    (ports 25/465/587) by default to prevent abuse, so smtplib to Gmail cannot connect at all
    from a deployed container there, regardless of credentials.
    """
    html_body = markdown_lib.markdown(plan_markdown, extensions=["extra"])

    response = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={
            "from": settings.resend_from,
            "to": [to_email],
            "subject": "Your Hiking Plan",
            "html": EMAIL_HTML_TEMPLATE.format(body=html_body),
            "text": plan_markdown,
        },
        timeout=15,
    )
    response.raise_for_status()

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown as markdown_lib

from app.config import settings

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
    """Send a hiking plan to `to_email` as an HTML email via Gmail SMTP. Raises on failure."""
    html_body = markdown_lib.markdown(plan_markdown, extensions=["extra"])

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Hiking Plan"
    msg["From"] = settings.email_user
    msg["To"] = to_email
    msg.attach(MIMEText(plan_markdown, "plain"))
    msg.attach(MIMEText(EMAIL_HTML_TEMPLATE.format(body=html_body), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(settings.email_user, settings.email_pass)
        server.sendmail(settings.email_user, to_email, msg.as_string())

"""
A Django email backend that sends via Brevo's HTTP API instead of SMTP.

Exists specifically because Render's free web services block all outbound
SMTP ports (25, 465, 587) as of September 2025 to prevent spam abuse - so
Django's normal SMTP backend simply cannot work there, regardless of how
correct the credentials are (confirmed by the OSError: Network is
unreachable error, which happens at the TCP connection level, before any
authentication is even attempted).

Brevo's API is reached over plain HTTPS (port 443), which nothing blocks -
so this backend sidesteps the restriction entirely while staying on a free
tier and requiring no credit card (Brevo's free plan: 300 emails/day,
single sender verification against your own email address, no card).

Used automatically instead of Django's SMTP backend when BREVO_API_KEY is
set - see settings.py. Falls back to SMTP (and, if that's not configured
either, the console backend) otherwise, so nothing breaks for anyone not
using Brevo, e.g. local development.
"""

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoAPIBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        api_key = settings.BREVO_API_KEY
        sent_count = 0

        for message in email_messages:
            payload = {
                "sender": {"email": message.from_email},
                "to": [{"email": addr} for addr in message.to],
                "subject": message.subject,
                "textContent": message.body,
            }

            attachments = []
            for attachment in getattr(message, "attachments", []):
                filename, content, mimetype = attachment
                import base64
                attachments.append({
                    "name": filename,
                    "content": base64.b64encode(content).decode("ascii"),
                })
            if attachments:
                payload["attachment"] = attachments

            try:
                response = requests.post(
                    BREVO_API_URL,
                    json=payload,
                    headers={
                        "api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=15,
                )
                response.raise_for_status()
                sent_count += 1
            except requests.RequestException:
                if not self.fail_silently:
                    raise

        return sent_count

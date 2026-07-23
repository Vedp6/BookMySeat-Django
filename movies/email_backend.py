

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

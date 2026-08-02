"""Compose alert notifiers from settings (Milestone 14.2).

Console is always included when alerts are enabled. Webhook/email channels are
added only when their configuration is complete. Injected transports keep
tests mockable and production wiring dependency-injected.
"""

from __future__ import annotations

from typing import Any

from alerts.email import EmailNotifier, SmtpTransport
from alerts.notifier import AlertNotifier, ConsoleNotifier, FanoutNotifier
from alerts.webhook import WebhookHttpTransport, WebhookNotifier
from broker_interface.http_transport import UrllibHttpTransport
from config.settings import Settings


def build_alert_notifier_from_settings(
    settings: Settings,
    *,
    http_transport: WebhookHttpTransport | None = None,
    smtp_transport: SmtpTransport | None = None,
) -> AlertNotifier:
    """Build the default multi-channel notifier for the composition root."""
    channels: list[AlertNotifier] = [ConsoleNotifier()]

    webhook_url = str(getattr(settings, "alert_webhook_url", "") or "").strip()
    if webhook_url:
        transport = http_transport if http_transport is not None else UrllibHttpTransport()
        timeout = float(getattr(settings, "alert_webhook_timeout_seconds", 5.0) or 5.0)
        channels.append(
            WebhookNotifier(
                url=webhook_url,
                transport=transport,
                timeout_seconds=timeout,
                min_level=str(getattr(settings, "alert_webhook_min_level", "") or "")
                or None,
            )
        )

    to_address = str(getattr(settings, "alert_email", "") or "").strip()
    smtp_host = str(getattr(settings, "alert_smtp_host", "") or "").strip()
    from_address = str(getattr(settings, "alert_email_from", "") or "").strip()
    if not from_address:
        from_address = str(getattr(settings, "alert_smtp_username", "") or "").strip()
    if not from_address:
        from_address = to_address

    if to_address and smtp_host and from_address:
        channels.append(
            EmailNotifier(
                to_address=to_address,
                from_address=from_address,
                smtp_host=smtp_host,
                smtp_port=int(getattr(settings, "alert_smtp_port", 587) or 587),
                smtp_username=str(getattr(settings, "alert_smtp_username", "") or ""),
                smtp_password=str(getattr(settings, "alert_smtp_password", "") or ""),
                use_tls=bool(getattr(settings, "alert_smtp_use_tls", True)),
                transport=smtp_transport,
                timeout_seconds=float(
                    getattr(settings, "alert_smtp_timeout_seconds", 10.0) or 10.0
                ),
                min_level=str(getattr(settings, "alert_email_min_level", "") or "")
                or None,
            )
        )

    if len(channels) == 1:
        return channels[0]
    return FanoutNotifier(channels)


def resolve_alert_notifier(
    *,
    with_alerts: bool,
    alert_notifier: AlertNotifier | None,
    settings: Settings,
    http_transport: Any | None = None,
    smtp_transport: SmtpTransport | None = None,
) -> AlertNotifier | None:
    """Factory helper: honor DI override, else build from settings."""
    if not with_alerts:
        return None
    if alert_notifier is not None:
        return alert_notifier
    return build_alert_notifier_from_settings(
        settings,
        http_transport=http_transport,
        smtp_transport=smtp_transport,
    )

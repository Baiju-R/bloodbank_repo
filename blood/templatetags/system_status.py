import logging

from django import template
from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)
register = template.Library()


@register.simple_tag
def celery_status() -> dict:
    """Return Celery enqueue health information for templates.

    This is intentionally lightweight and cached, so admin pages can render without
    extra view wiring.
    """

    eager = bool(getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False))
    broker_url = str(getattr(settings, 'CELERY_BROKER_URL', '') or '')
    show_banner = bool(getattr(settings, 'ADMIN_SHOW_SMS_MODE_BANNER', False))

    status = {
        'task_always_eager': eager,
        'broker_url': broker_url,
        'broker_ok': None,
        'broker_error': '',
        'show_sms_banner': show_banner,
    }

    # If the UI banner is hidden, avoid any broker health checks/noisy logs.
    if not show_banner:
        return status

    if eager:
        status['broker_ok'] = True
        return status

    if not (broker_url.startswith('redis://') or broker_url.startswith('rediss://')):
        return status

    cache_key = 'admin:celery_broker_health:v1'
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        status['broker_ok'] = cached.get('ok')
        status['broker_error'] = cached.get('error') or ''
        return status

    try:
        import redis

        client = redis.Redis.from_url(
            broker_url,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
            retry_on_timeout=False,
        )
        client.ping()
        status['broker_ok'] = True
    except Exception as exc:  # pragma: no cover
        status['broker_ok'] = False
        status['broker_error'] = str(exc)
        logger.debug('Celery broker health check failed: %s', exc)

    cache.set(cache_key, {'ok': status['broker_ok'], 'error': status['broker_error']}, timeout=30)
    return status

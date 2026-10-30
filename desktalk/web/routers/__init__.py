"""The web routes, by area. Each module's docstring lists its URLs."""

from desktalk.web.routers import chat, dev, habits, health, live, log, objects, phone, pi, settings, video

MAIN_SERVER = (pi.router, live.router, video.router, chat.router, habits.router, log.router,
               settings.router, health.router, objects.router)
DEV_SERVER = (dev.router,)          # on the main server too, only when settings → dev.enabled is true
PHONE_SERVER = (phone.phone_router,)

__all__ = ['DEV_SERVER', 'MAIN_SERVER', 'PHONE_SERVER']

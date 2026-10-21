"""The web routes, by area. Each module's docstring lists its URLs."""

from desktalk.web.routers import chat, habits, health, live, log, phone, pi, settings, video

MAIN_SERVER = (pi.router, phone.router, live.router, video.router, chat.router, habits.router, log.router,
               settings.router, health.router)
PHONE_SERVER = (phone.phone_router,)

__all__ = ['MAIN_SERVER', 'PHONE_SERVER']

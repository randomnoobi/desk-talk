"""The web routes, by area. Each module's docstring lists its URLs."""

from desktalk.web.routers import (
    chat,
    cloud,
    dev,
    habits,
    health,
    live,
    log,
    objects,
    pi,
    settings,
    system,
    video,
)

MAIN_SERVER = (pi.router, live.router, video.router, chat.router, habits.router, log.router,
               settings.router, health.router, objects.router, cloud.router, system.router)
DEV_SERVER = (dev.router,)          # on the main server too, only when settings → dev.enabled is true

__all__ = ['DEV_SERVER', 'MAIN_SERVER']

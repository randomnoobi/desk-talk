"""
The desk right now, in five parts, one per desk interface in desktalk/ports.py. Each part keeps its own
lock inside its methods, so nobody else ever holds one.

    LiveView      LivePictures     the newest pictures for the live view, and the light
    Scene         DeskScene        what the camera sees on the desk
    DangerBoard   SafetyBoard      the dangers right now and the latest alerts
    Memories      CharacterMemory  what the characters said, the tracker panels, when you last typed
    Album         PhotoAlbum       the proof photos

WholeDesk passes every call on to the five parts, for the parts that still ask for several desk interfaces
in one object (desktalk/bootstrap.py says which).
"""

from desktalk.core.desk.character_memory import Memories
from desktalk.core.desk.live_pictures import LiveView
from desktalk.core.desk.photo_album import Album
from desktalk.core.desk.safety_board import DangerBoard
from desktalk.core.desk.scene import Scene
from desktalk.core.desk.whole_desk import WholeDesk

__all__ = ['Album', 'DangerBoard', 'LiveView', 'Memories', 'Scene', 'WholeDesk']

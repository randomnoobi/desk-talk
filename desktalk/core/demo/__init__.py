"""
The developer page's Demo tab: pretend warnings fired on purpose after a countdown.

    scenarios.py  ScenarioRunner: the countdowns, and the background job that fires the ones that are over
    firer.py      DemoFirer: sends each pretend warning the way the real one goes out
"""

from desktalk.core.demo.firer import DemoFirer
from desktalk.core.demo.scenarios import ScenarioRunner

__all__ = ['DemoFirer', 'ScenarioRunner']

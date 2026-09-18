"""Android Optimiser — inspect and debloat an Android device over ADB.

The package is layered, and dependencies only ever point downwards::

    cli
     └── reporting
          └── executor      (applies plans, produces rollback)
               └── planner  (turns a snapshot + profile into a plan)
                    ├── actions   (declarative changes with inverses)
                    └── analysis  (knowledge base, classifier, inspector)
                         └── core (adb transport, device, metrics)
                              └── domain (shared vocabulary)

Nothing in ``core`` knows what an optimisation is.  Nothing in ``analysis``
executes anything.  Nothing in ``actions`` decides whether an action should run.
"""

from .config import VERSION
from .domain import Classification, DeviceSnapshot, Impact, PackageRecord, Risk

__version__ = VERSION

__all__ = [
    "__version__",
    "Classification",
    "DeviceSnapshot",
    "Impact",
    "PackageRecord",
    "Risk",
]

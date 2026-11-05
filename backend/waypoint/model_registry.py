# SPDX-License-Identifier: Apache-2.0
"""Import every module that defines tables, so ``Base.metadata`` is complete.

Anything that walks the metadata (backups, export/import, migrations) must import this
first; otherwise only the tables some earlier import happened to load are seen.
"""

from waypoint import models  # noqa: F401
from waypoint.ai import models as ai_models  # noqa: F401
from waypoint.assistant import models as assistant_models  # noqa: F401
from waypoint.discovery import models as discovery_models  # noqa: F401
from waypoint.materials import models as materials_models  # noqa: F401
from waypoint.notify import models as notify_models  # noqa: F401
from waypoint.tracker import models as tracker_models  # noqa: F401

# SPDX-License-Identifier: Apache-2.0
"""Import every module that defines tables, so ``Base.metadata`` is complete.

Anything that walks the metadata (backups, export/import, migrations) must import this
first; otherwise only the tables some earlier import happened to load are seen.
"""

from careerline import models  # noqa: F401
from careerline.ai import models as ai_models  # noqa: F401
from careerline.assistant import models as assistant_models  # noqa: F401
from careerline.discovery import models as discovery_models  # noqa: F401
from careerline.materials import models as materials_models  # noqa: F401
from careerline.notify import models as notify_models  # noqa: F401
from careerline.tracker import models as tracker_models  # noqa: F401

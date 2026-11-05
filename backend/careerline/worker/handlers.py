# SPDX-License-Identifier: Apache-2.0
"""Imports every module that registers job handlers or schedule hooks."""

from careerline.ai import jobs as ai_jobs  # noqa: F401  (score_postings)
from careerline.data import jobs as data_jobs  # noqa: F401  (backup)
from careerline.notify import jobs as notify_jobs  # noqa: F401  (notify_digest, notify_matches)
from careerline.worker import runner  # noqa: F401  (fetch_source, maintenance)

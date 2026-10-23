"""T63 gap (c): the suite must not be able to hang forever.

Before this task, there was no ``pytest-timeout`` and no per-test ceiling —
a single hang (an infinite loop, a dropped connection) blocked the whole
run with nothing to kill it. This already cost one run three hours,
orphaned in a Playwright ``run-driver`` child.

``[tool.pytest.ini_options]``'s ``timeout = 30`` in ``pyproject.toml`` is
the fix. The test below demonstrated that a genuinely hanging test is
killed by it rather than running indefinitely — see the T63 commit
message for the captured ``Failed: Timeout >30.0s`` output from that run.
It is skip-marked rather than left enabled so it does not itself cost 30s
on every future run of this otherwise ~35s suite.
"""

from __future__ import annotations

import time

import pytest


@pytest.mark.skip(
    reason=(
        "T63: demonstrates the configured per-test timeout kills a genuine "
        "hang rather than letting it run forever. Already proven once "
        "(ran with the outer OS process exiting on its own, well under a "
        "generous 120s backstop, instead of running the full 3600s sleep -- "
        "see the T63 commit message for the captured pytest-timeout output). "
        "Left skipped so it doesn't cost 30s on every future run of this "
        "otherwise ~35s suite."
    )
)
def test_a_genuinely_hanging_test_is_killed_by_the_configured_timeout():
    time.sleep(3600)

from __future__ import annotations

import os

from packages.tools.runtime import ROOT_DIR, _profile_env_assignments, _worker_command
from packages.tools.runtime_profiles import get_runtime_profile, reset_runtime_profile_cache


def test_apptainer_profile_cleans_host_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/tmp/host-venv/site-packages")
    profile = get_runtime_profile("apptainer-medgemma")

    env = _profile_env_assignments(profile)

    assert env["PYTHONPATH"] == str(ROOT_DIR)
    assert "/tmp/host-venv/site-packages" not in env["PYTHONPATH"]
    assert profile["inherit_pythonpath"] is False


def test_default_profile_still_inherits_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/tmp/host-venv/site-packages")
    profile = {
        "profile_id": "control-plane",
        "env": {},
        "env_passthrough": [],
    }

    env = _profile_env_assignments(profile)

    assert env["PYTHONPATH"] == os.pathsep.join([str(ROOT_DIR), "/tmp/host-venv/site-packages"])


def test_apptainer_worker_command_prefers_absolute_python_bin(monkeypatch) -> None:
    # The profile's python_bin is a ${MRI_AGENT_V4_LEGACY_PYTHON} reference so no
    # site-specific interpreter path is committed; the loader expands it from the
    # environment. Assert the expansion happens and is used verbatim, rather than
    # pinning this test to whatever this particular machine happens to have.
    monkeypatch.setenv("MRI_AGENT_V4_LEGACY_PYTHON", "/opt/envs/legacy/bin/python")
    reset_runtime_profile_cache()
    try:
        profile = get_runtime_profile("apptainer-medgemma")

        command = _worker_command(profile)

        assert command[0] == "/opt/envs/legacy/bin/python"
        assert command[1:] == ["-m", "packages.tools.runtime_worker"]
    finally:
        reset_runtime_profile_cache()

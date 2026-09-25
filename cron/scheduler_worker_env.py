"""Cron: import path of the restart-safe external worker.

The worker is spawned as ``sys.executable -m cron.scheduler``. Its entry module is
``cron.scheduler``, not ``hermes_cli.main``, so nothing bootstraps the gateway's checkout
onto its ``sys.path``; historically it imported ``cron`` only through the implicit ``-m``
cwd entry. That entry is gone under ``PYTHONSAFEPATH`` and useless when the venv's
editable install maps a moved/deleted checkout -- the worker then dies with
"No module named 'cron'" before its ownership ack (#112729, hypothesised cause).

Same gap for third-party deps (ruamel.yaml et al, #114xxx): a managed-systemd gateway
runs on the bundled interpreter (``python -I``, no site-packages of its own), so
``sys.executable`` here has none of Hermes's pip dependencies either. ``hermes_cli.main``
launches normally get them via ``pm.environments.activate_dependencies``, but this worker's
entry point is ``cron.scheduler``, which skips that bootstrap entirely. Resolve and pin the
*currently selected* dependency environment's site-packages the same way, so the fix
survives the next ``hermes update`` picking a new venv generation instead of hardcoding one.

The shared subprocess sanitizer strips Hermes-owned PYTHONPATH entries because user
children must not see our tree. This child IS Hermes, so the pin is applied *after* the
env is built, on the sanitized env -- the sanitizer's other decisions (dropped runtime
site-packages, dropped venv markers) stand.
"""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path


def _installed_purelib() -> Path | None:
    try:
        return Path(sysconfig.get_paths()["purelib"]).resolve()
    except (KeyError, OSError):
        return None


def _selected_dependency_site_packages(repo_root: Path) -> Path | None:
    """The committed dependency environment's site-packages, if one is selected.

    Read-only lookup (stdlib + ``pm.environments``, no lock, no install): mirrors what
    ``pm.environments.activate_dependencies`` resolves for ``hermes_cli.main`` launches.
    """
    try:
        from pm.environments import selected_venv, site_packages
        selected = site_packages(selected_venv(repo_root))
    except Exception:
        return None
    return selected if selected.is_dir() else None


def pin_hermes_tree_on_pythonpath(worker_env: dict, repo_root: Path) -> dict:
    """Prepend ``repo_root`` (and its selected dependency site-packages) to the worker
    env's own PYTHONPATH (never ``os.environ``'s).

    ``repo_root`` is skipped when it equals the interpreter's ``purelib``: under a wheel /
    pipx / uv-tool install ``cron/`` lives in site-packages itself, which is already
    importable, and pinning it would move site-packages ahead of the stdlib on ``sys.path``.
    """
    root = str(repo_root)
    entries: list[str] = []
    if _installed_purelib() != Path(root).resolve():
        entries.append(root)
    site_packages = _selected_dependency_site_packages(repo_root)
    if site_packages is not None:
        entries.append(str(site_packages))
    if not entries:
        return worker_env
    existing = [e for e in worker_env.get("PYTHONPATH", "").split(os.pathsep) if e]
    worker_env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([*entries, *existing]))
    return worker_env

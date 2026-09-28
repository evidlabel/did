"""Install missing spaCy models into the running environment.

spaCy's own ``spacy.cli.download`` shells out to ``python -m pip``, which uv
environments do not have. This installs the pinned model wheels with ``uv pip``
when uv is available (falling back to pip) into exactly the interpreter that is
running, and touches no other package — safe in a shared environment.

Run ``python -m did.core.model_install [da en sv] [--profile balanced]`` to
preinstall; the GUI calls :func:`ensure_spacy_models` on first detection.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import shutil
import subprocess
import sys

from .anonymizer import SPACY_MODELS, missing_spacy_models

# Must match the model wheels pinned in pyproject.toml ([tool.uv.sources]).
SPACY_MODEL_VERSION = "3.8.0"
_RELEASES = "https://github.com/explosion/spacy-models/releases/download"


class ModelInstallError(RuntimeError):
    """Raised when models cannot be installed automatically."""


def model_wheel_url(name: str) -> str:
    version = SPACY_MODEL_VERSION
    return f"{_RELEASES}/{name}-{version}/{name}-{version}-py3-none-any.whl"


def install_command(names) -> list[str]:
    """The command that installs *names* into this interpreter."""
    urls = [model_wheel_url(name) for name in names]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *urls]
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", *urls]
    raise ModelInstallError(
        "Neither uv nor pip is available to install spaCy models. Install uv "
        "(https://docs.astral.sh/uv/) or run: uv sync --extra models"
    )


def ensure_spacy_models(
    detection_profile="thorough", languages=None, *, progress=None
) -> list[str]:
    """Install whichever models *languages* need that are missing.

    Returns the names installed (empty when nothing was missing). *progress*
    receives short human-readable status lines.
    """
    missing = missing_spacy_models(detection_profile, languages)
    if not missing:
        return []
    if progress is not None:
        progress(
            f"Downloading language model(s) {', '.join(missing)} "
            "(one-time, several hundred MB)…"
        )
    command = install_command(missing)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise ModelInstallError(
            f"Could not install {', '.join(missing)}:\n" + "\n".join(detail)
        )
    importlib.invalidate_caches()
    still_missing = missing_spacy_models(detection_profile, languages)
    if still_missing:
        raise ModelInstallError(
            f"Installed, but still not importable: {', '.join(still_missing)}. "
            "Restart DID and try again."
        )
    if progress is not None:
        progress(f"Installed {', '.join(missing)}.")
    return missing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "languages",
        nargs="*",
        help="Language codes (default: all). English is always included.",
    )
    parser.add_argument(
        "--profile", default="thorough", choices=sorted(SPACY_MODELS), help="Model size"
    )
    args = parser.parse_args(argv)
    try:
        installed = ensure_spacy_models(
            args.profile, args.languages or None, progress=print
        )
    except (ModelInstallError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if not installed:
        print("All required spaCy models are already installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

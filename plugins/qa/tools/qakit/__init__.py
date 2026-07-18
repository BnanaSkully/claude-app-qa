"""qakit — shared support for the qa CDP browser probes.

Everything the five probe scripts need in common: cross-platform browser
discovery, project config loading, a Chrome-DevTools-Protocol client, and a
generic identity/auth shim.

Nothing in here is specific to any application, tenant model, or operating
system. See ``cdp.py`` for the implementation and the tools ``README.md`` for
the config file format.
"""

from .cdp import (  # noqa: F401
    CLAUDE_QA_ENV_VARS,
    QAError,
    BrowserNotFound,
    CDP,
    Config,
    apply_identity,
    check_reachable,
    connect,
    find_browser,
    free_port,
    launch,
    load_config,
    normalize_path,
    output_path,
    resolve_url,
    shutdown,
    slug,
    wait_for_ws,
    wait_ready,
    landed_url,
)

__version__ = "0.1.0"

__all__ = [
    "CLAUDE_QA_ENV_VARS",
    "QAError",
    "BrowserNotFound",
    "CDP",
    "Config",
    "apply_identity",
    "check_reachable",
    "connect",
    "find_browser",
    "free_port",
    "launch",
    "load_config",
    "normalize_path",
    "output_path",
    "resolve_url",
    "shutdown",
    "slug",
    "wait_for_ws",
    "wait_ready",
    "landed_url",
    "__version__",
]

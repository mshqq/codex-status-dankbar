import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit


class ProxyConfigurationError(ValueError):
    pass


def resolve_codex(configured_path):
    if configured_path:
        expanded = Path(configured_path).expanduser()
        if not expanded.is_absolute():
            raise FileNotFoundError(configured_path)
        return str(expanded)
    if binary := os.environ.get("CODEX_WIDGET_BIN") or shutil.which("codex"):
        return binary
    for directory in (".local/bin", ".bun/bin"):
        candidate = Path.home() / directory / "codex"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise FileNotFoundError("codex")


def codex_environment(binary, proxy_url):
    env = os.environ.copy()
    if os.path.isabs(binary):
        env["PATH"] = os.pathsep.join((str(Path(binary).parent), env.get("PATH") or os.defpath))
    if proxy_url:
        try:
            url = urlsplit(proxy_url)
            valid = (
                url.scheme in ("http", "https", "socks5", "socks5h")
                and url.hostname and url.username is None and url.password is None
                and url.path in ("", "/") and not url.query and not url.fragment
                and (url.port is None or url.port > 0)
                and not any(char.isspace() or ord(char) < 32 for char in proxy_url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ProxyConfigurationError(
                "Enter a proxy URL without credentials, such as http://127.0.0.1:7890."
            )
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "WS_PROXY", "WSS_PROXY"):
            env[key] = env[key.lower()] = proxy_url
    return env

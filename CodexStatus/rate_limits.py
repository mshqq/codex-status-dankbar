#!/usr/bin/env python3

import asyncio
import json
import math
import sys
from contextlib import suppress

from codex_config import ProxyConfigurationError, codex_environment, resolve_codex


async def send(process, message):
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()


async def receive(process, request_id):
    while line := await process.stdout.readline():
        message = json.loads(line)
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise ValueError("Codex returned a JSON-RPC error")
        return message["result"]
    raise ValueError("Codex closed stdout before replying")


async def start_app_server(configured_path, proxy_url):
    binary = resolve_codex(configured_path)
    env = codex_environment(binary, proxy_url)

    return await asyncio.create_subprocess_exec(
        binary,
        "app-server",
        "--listen",
        "stdio://",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
    )


async def read_limits(configured_path, proxy_url):
    process = await start_app_server(configured_path, proxy_url)
    try:
        await send(process, {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "dankbar_codex_status",
                    "version": "1.4.1",
                },
            },
        })
        await receive(process, 1)
        await send(process, {"method": "initialized", "params": {}})
        await send(process, {"id": 2, "method": "account/rateLimits/read"})
        return await receive(process, 2)
    finally:
        process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(process.communicate(), timeout=1)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.communicate()


def window_label(minutes, fallback):
    if type(minutes) is not int or minutes <= 0:
        return fallback
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def normalize_limits(result):
    buckets = result.get("rateLimitsByLimitId") or {}
    limits = buckets.get("codex") or result.get("rateLimits") or {}
    windows = []
    for key, fallback in (("primary", "Primary"), ("secondary", "Secondary")):
        window = limits.get(key) or {}
        used = window.get("usedPercent")
        if type(used) not in (int, float) or not math.isfinite(used):
            continue
        resets_at = window.get("resetsAt")
        windows.append({
            "label": window_label(window.get("windowDurationMins"), fallback),
            "remaining": round(max(0, min(100, 100 - used))),
            "resetsAt": resets_at if type(resets_at) is int and resets_at > 0 else None,
        })
    return windows


async def main(configured_path, proxy_url):
    windows = []
    error = None
    try:
        result = await asyncio.wait_for(read_limits(configured_path, proxy_url), timeout=12)
        windows = normalize_limits(result)
        if not windows:
            error = "Codex did not return usage limits."
    except ProxyConfigurationError as exc:
        error = str(exc)
    except (FileNotFoundError, PermissionError):
        error = "Set the Codex CLI path in the plugin settings."
    except asyncio.TimeoutError:
        error = "The Codex usage check timed out."
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        error = "Usage unavailable. Check that Codex is signed in with ChatGPT."

    print(json.dumps({"windows": windows, "error": error}, ensure_ascii=True))


if __name__ == "__main__":
    path, proxy_url = (sys.argv[1:] + ["", ""])[:2]
    asyncio.run(main(path.strip(), proxy_url.strip()))

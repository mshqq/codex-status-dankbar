#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unicodedata import category
from uuid import uuid4

COMMAND_APPROVAL = "item/commandExecution/requestApproval"
FILE_APPROVAL = "item/fileChange/requestApproval"
PERMISSIONS_APPROVAL = "item/permissions/requestApproval"
APPROVAL_TYPES = {COMMAND_APPROVAL, FILE_APPROVAL, PERMISSIONS_APPROVAL}


def publish(**state):
    print(json.dumps(state, ensure_ascii=True), flush=True)


def printable(text):
    return "".join(
        character if character in "\n\t" or category(character) not in ("Cc", "Cf")
        else ascii(character)[1:-1]
        for character in text
    )


def file_details(changes):
    if not changes or any(
        not change.get("path") or not isinstance(change.get("diff"), str)
        for change in changes
    ):
        return ""
    return "\n\n".join(
        f'{change["path"]}\n{json.dumps(change.get("kind"), ensure_ascii=False)}\n{change["diff"]}'
        for change in changes
    )


class Approval:
    def __init__(self, message):
        self.request_id = message["id"]
        self.method = message["method"]
        self.params = message["params"]
        self.token = uuid4().hex
        self.changes = None
        self.is_sending = False

    @property
    def item_key(self):
        return (self.params["threadId"], self.params["turnId"], self.params["itemId"])

    def preview(self):
        reason = self.params.get("reason")
        directory = self.params.get("cwd")
        context = [
            f"Session: {self.params['threadId']}",
            f"Working directory: {directory}" if directory else "",
            f"Reason: {reason}" if reason else "",
        ]
        if self.method == COMMAND_APPROVAL:
            network = self.params.get("networkApprovalContext")
            if network:
                title = "Network access"
                host = network.get("host")
                protocol = network.get("protocol")
                action = f"{protocol} · {host}" if host and protocol else ""
            elif self.params.get("kind", "command") == "command":
                title = "Run a command"
                action = self.params.get("command") or ""
                if self.params.get("additionalPermissions"):
                    context.append(json.dumps(self.params["additionalPermissions"], ensure_ascii=False))
            else:
                title, action = "Terminal action", ""
        elif self.method == FILE_APPROVAL:
            title, action = "File changes", file_details(self.changes)
            if self.params.get("grantRoot"):
                context.append(f"Write access: {self.params['grantRoot']}")
        else:
            title = "Additional permissions"
            permissions = self.requested_permissions()
            action = json.dumps(permissions, ensure_ascii=False, indent=2) if permissions else ""

        decisions = self.params.get("availableDecisions")
        return {
            "token": self.token,
            "title": title,
            "detail": printable(action or "Open Codex in your terminal to review this request."),
            "context": printable("\n".join(filter(None, context))),
            "canAccept": bool(action) and (decisions is None or "accept" in decisions),
            "negativeDecision": self.negative_decision,
            "isSending": self.is_sending,
        }

    @property
    def negative_decision(self):
        available = self.params.get("availableDecisions")
        if available is None or "decline" in available:
            return "decline"
        if "cancel" in available and self.method != PERMISSIONS_APPROVAL:
            return "cancel"
        return None

    def requested_permissions(self):
        return {
            name: value for name, value in (self.params.get("permissions") or {}).items()
            if name in ("network", "fileSystem") and value is not None
        }

    def result(self, decision):
        if self.method == PERMISSIONS_APPROVAL:
            return {
                "permissions": self.requested_permissions() if decision == "accept" else {},
                "scope": "turn",
            }
        return {"decision": decision}


class ApprovalConnection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.requests = {}
        self.approvals = {}
        self.changes = {}
        self.subscribed = set()
        self.next_id = 0

    async def request(self, method, params=None):
        self.next_id += 1
        request_id = self.next_id
        response = asyncio.get_running_loop().create_future()
        self.requests[request_id] = response
        try:
            await self.send({"id": request_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(response, 5)
        finally:
            self.requests.pop(request_id, None)

    async def send(self, message):
        await self.websocket.send(json.dumps(message))

    def publish(self):
        publish(connected=True, approvals=[value.preview() for value in self.approvals.values()])

    async def receive(self):
        async for frame in self.websocket:
            message = json.loads(frame)
            if "method" in message:
                self.handle_event(message)
            elif (response := self.requests.get(message.get("id"))) and not response.done():
                if "error" in message:
                    response.set_exception(ValueError("Codex App Server rejected the request."))
                else:
                    response.set_result(message["result"])

    def handle_event(self, message):
        method = message["method"]
        params = message.get("params") or {}
        if method in APPROVAL_TYPES and "id" in message:
            if message["id"] not in self.approvals:
                approval = Approval(message)
                approval.changes = self.changes.get(approval.item_key)
                self.approvals[approval.request_id] = approval
                self.publish()
        elif method == "serverRequest/resolved":
            if self.approvals.pop(params.get("requestId"), None) is not None:
                self.publish()
        elif method == "item/started":
            item = params.get("item") or {}
            if item.get("type") == "fileChange":
                key = (params["threadId"], params["turnId"], item["id"])
                self.record_file_changes(key, item.get("changes"))
        elif method in ("turn/completed", "thread/closed"):
            thread_id = params.get("threadId")
            turn_id = (params.get("turn") or {}).get("id")

            def belongs_to_event(key):
                return key[0] == thread_id and (turn_id is None or key[1] == turn_id)

            self.changes = {
                key: changes for key, changes in self.changes.items()
                if not belongs_to_event(key)
            }
            self.approvals = {
                key: approval for key, approval in self.approvals.items()
                if not belongs_to_event(approval.item_key)
            }
            if method == "thread/closed":
                self.subscribed.discard(thread_id)
            self.publish()

    async def follow_threads(self):
        await self.request("initialize", {
            "clientInfo": {"name": "dankbar_codex_approvals", "version": "1.4.1"},
        })
        await self.send({"method": "initialized", "params": {}})
        self.publish()
        while True:
            loaded = set()
            cursor = None
            while True:
                params = {"cursor": cursor} if cursor else {}
                result = await self.request("thread/loaded/list", params)
                loaded.update(result["data"])
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            self.subscribed.intersection_update(loaded)
            for thread_id in sorted(loaded - self.subscribed):
                try:
                    await self.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
                except ValueError:
                    continue
                self.subscribed.add(thread_id)
            await self.recover_changes()
            await asyncio.sleep(1)

    def record_file_changes(self, key, changes):
        self.changes[key] = changes
        pending = [approval for approval in self.approvals.values() if approval.item_key == key]
        for approval in pending:
            approval.changes = changes
        if pending:
            self.publish()

    async def recover_changes(self):
        keys = {
            approval.item_key for approval in self.approvals.values()
            if approval.method == FILE_APPROVAL and approval.changes is None
        }
        for thread_id in sorted({key[0] for key in keys}):
            try:
                result = await self.request("thread/turns/list", {
                    "threadId": thread_id, "limit": 1, "itemsView": "full",
                })
            except ValueError:
                continue
            for turn in result["data"]:
                for item in turn.get("items") or []:
                    key = (thread_id, turn["id"], item["id"])
                    if item.get("type") == "fileChange" and key in keys:
                        self.record_file_changes(key, item.get("changes") or [])

    async def accept_actions(self, source):
        while line := await source.readline():
            action = json.loads(line)
            decision = action.get("decision")
            if decision not in ("accept", "decline", "cancel"):
                continue
            for approval in self.approvals.values():
                if approval.token != action.get("token") or approval.is_sending:
                    continue
                allowed = (
                    approval.preview()["canAccept"] if decision == "accept"
                    else approval.negative_decision == decision
                )
                if allowed:
                    approval.is_sending = True
                    self.publish()
                    await self.send({
                        "id": approval.request_id, "result": approval.result(decision),
                    })
                break


async def run_connection(websocket):
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.cancel)
    source = asyncio.StreamReader()
    transport = None
    jobs = []
    try:
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(source), sys.stdin,
        )
        connection = ApprovalConnection(websocket)
        jobs = [
            asyncio.create_task(connection.receive()),
            asyncio.create_task(connection.follow_threads()),
            asyncio.create_task(connection.accept_actions(source)),
        ]
        done, _ = await asyncio.wait([*jobs, stop], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled():
                task.result()
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        if transport is not None:
            transport.close()
        loop.remove_signal_handler(signal.SIGTERM)


def approval_socket(configured_path):
    if configured_path:
        socket = Path(configured_path).expanduser()
        if not socket.is_absolute():
            raise ValueError("Enter an absolute App Server socket path.")
        return socket
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return codex_home / "app-server-control" / "app-server-control.sock"


async def main(configured_path):
    try:
        from websockets.asyncio.client import unix_connect
        from websockets.exceptions import WebSocketException
    except ImportError:
        publish(connected=False, approvals=[], error="Install python-websockets to use approvals.")
        return

    try:
        socket = approval_socket(configured_path)
    except (ValueError, RuntimeError) as exc:
        publish(connected=False, approvals=[], error=str(exc))
        return
    if not socket.is_socket():
        error = (f"App Server socket not found: {socket}" if configured_path
                 else "Start Codex to connect to its background server.")
        publish(connected=False, approvals=[], error=error)
        return

    try:
        async with unix_connect(
            socket, proxy=None, compression=None, open_timeout=4, close_timeout=1,
            max_size=16 * 1024 * 1024,
        ) as websocket:
            await run_connection(websocket)
    except asyncio.TimeoutError:
        error = "The connection to Codex App Server timed out."
    except OSError as exc:
        error = f"Could not open the Codex App Server socket: {exc.strerror or type(exc).__name__}"
    except WebSocketException:
        error = "Codex App Server closed or refused the WebSocket connection."
    except (ValueError, KeyError):
        error = "Codex App Server returned a protocol error."
    else:
        error = "Disconnected from Codex App Server."
    publish(connected=False, approvals=[], error=error)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].strip() if len(sys.argv) > 1 else ""))

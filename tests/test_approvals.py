import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CodexStatus"))

from approvals import Approval, ApprovalConnection, COMMAND_APPROVAL, FILE_APPROVAL


def request(method, **params):
    return {
        "id": 7,
        "method": method,
        "params": {"threadId": "thread", "turnId": "turn", "itemId": "item", **params},
    }


class TestApprovalState(unittest.TestCase):
    @patch("approvals.publish")
    def test_file_diff_retained_when_request_is_replayed(self, publish):
        connection = ApprovalConnection(None)
        message = request(FILE_APPROVAL)
        connection.handle_event(message)
        approval = connection.approvals[7]
        self.assertFalse(approval.preview()["canAccept"])

        connection.handle_event({
            "method": "item/started",
            "params": {"threadId": "thread", "turnId": "turn", "item": {
                "id": "item", "type": "fileChange",
                "changes": [{"path": "/tmp/example", "diff": "+line"}],
            }},
        })
        self.assertTrue(approval.preview()["canAccept"])
        self.assertIn("+line", approval.preview()["detail"])
        publish.reset_mock()
        connection.handle_event(message)
        self.assertIs(connection.approvals[7], approval)
        self.assertTrue(approval.preview()["canAccept"])
        publish.assert_not_called()

        connection.handle_event({
            "method": "serverRequest/resolved", "params": {"requestId": 7},
        })
        self.assertEqual(connection.approvals, {})

    def test_duplicate_request_does_not_reset_sending_state(self):
        connection = ApprovalConnection(None)
        message = request(COMMAND_APPROVAL, command="pwd")
        with patch("approvals.publish"):
            connection.handle_event(message)
            approval = connection.approvals[7]
            approval.is_sending = True
            connection.handle_event(message)
        self.assertEqual(connection.approvals[7].token, approval.token)
        self.assertTrue(connection.approvals[7].is_sending)


class TestApprovalDecisions(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_selected_available_decision_is_sent(self):
        for choices, expected in (
            (["accept", "decline", "cancel"], "decline"),
            (["accept", "cancel"], "cancel"),
            (["accept"], None),
        ):
            with self.subTest(choices=choices):
                approval = Approval(request(COMMAND_APPROVAL, command="pwd", availableDecisions=choices))
                connection = ApprovalConnection(None)
                connection.approvals[7] = approval
                self.assertEqual(approval.preview()["negativeDecision"], expected)

                source = asyncio.StreamReader()
                for token, decision in (("stale", "accept"), (approval.token, "decline"), (approval.token, "cancel")):
                    line = json.dumps({"token": token, "decision": decision}) + "\n"
                    source.feed_data(line.encode())
                source.feed_eof()

                with patch.object(connection, "send") as send, patch("approvals.publish"):
                    await connection.accept_actions(source)
                if expected:
                    send.assert_awaited_once_with({"id": 7, "result": {"decision": expected}})
                else:
                    send.assert_not_awaited()

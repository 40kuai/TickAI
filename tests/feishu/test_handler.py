"""Tests for hermes.feishu.handler — pure parsing/whitelist logic."""
import unittest

from hermes.feishu.handler import (
    extract_message_text,
    is_whitelisted,
    should_respond,
)


# 模拟 lark P2ImMessageReceiveV1 结构的最小 dict
def _make_event(open_id="ou_abc", chat_type="p2p", message_type="text",
                content='{"text":"check disk"}', mentions=None):
    return {
        "sender": {"sender_id": {"open_id": open_id}},
        "message": {
            "chat_type": chat_type,
            "message_type": message_type,
            "content": content,
            "mentions": mentions or [],
        },
    }


class ExtractMessageTextTests(unittest.TestCase):
    def test_text_message(self):
        event = _make_event(content='{"text":"hello"}')
        self.assertEqual(extract_message_text(event), "hello")

    def test_invalid_json_returns_empty(self):
        event = _make_event(content="not-json")
        self.assertEqual(extract_message_text(event), "")

    def test_non_text_message_returns_empty(self):
        event = _make_event(message_type="image", content='{"image_key":"x"}')
        self.assertEqual(extract_message_text(event), "")


class IsWhitelistedTests(unittest.TestCase):
    def test_in_whitelist(self):
        self.assertTrue(is_whitelisted("ou_abc", ["ou_abc", "ou_xyz"]))

    def test_not_in_whitelist(self):
        self.assertFalse(is_whitelisted("ou_other", ["ou_abc"]))

    def test_empty_whitelist(self):
        self.assertFalse(is_whitelisted("ou_abc", []))


class ShouldRespondTests(unittest.TestCase):
    def test_p2p_always_respond(self):
        event = _make_event(chat_type="p2p")
        self.assertTrue(should_respond(event, whitelist=["ou_abc"]))

    def test_group_with_mention_responds(self):
        event = _make_event(chat_type="group", mentions=[{"key": "123", "id": {"open_id": "ou_bot"}}])
        self.assertTrue(should_respond(event, whitelist=["ou_abc"]))

    def test_group_without_mention_ignored(self):
        event = _make_event(chat_type="group", mentions=[])
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))

    def test_non_whitelisted_ignored(self):
        event = _make_event(open_id="ou_hacker", chat_type="p2p")
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))

    def test_non_text_ignored(self):
        event = _make_event(message_type="image")
        self.assertFalse(should_respond(event, whitelist=["ou_abc"]))


if __name__ == "__main__":
    unittest.main()

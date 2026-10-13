"""Pending-confirmation shortcut must match whole yes/no replies only."""
from __future__ import annotations

import pytest

import kurek_daemon as daemon


@pytest.mark.parametrize("text", [
    "yes", "Yes!", "ok", "OK.", "okay", "sure", "yep", "confirm", "do it",
    "Go ahead", "proceed", "ja", "yes, do it",
])
def test_affirmative_whole_replies_confirm(text):
    assert daemon.classify_confirmation_reply(text) is True


@pytest.mark.parametrize("text", [
    "no", "No.", "cancel", "stop", "abort", "nein", "don't", "dont", "nope",
    "never mind",
])
def test_negative_whole_replies_cancel(text):
    assert daemon.classify_confirmation_reply(text) is False


@pytest.mark.parametrize("text", [
    "Notes for today", "notes", "now open firefox", "OK, what's the weather?",
    "Surely you can do better", "Stop the music", "yesterday's news",
    "nobody asked", "cancel culture explained", "sure thing, what time is it",
    "", "   ",
])
def test_other_prompts_are_not_confirmations(text):
    assert daemon.classify_confirmation_reply(text) is None

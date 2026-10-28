"""DeepSeek Search is the primary web_search backend; DuckDuckGo is the fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from actions import web_search as ws


def _resp(status: int, payload: dict) -> MagicMock:
    resp = MagicMock(status_code=status, text=str(payload))
    resp.json.return_value = payload
    return resp


def test_deepseek_results_used_and_ddg_skipped(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test")
    post = MagicMock(
        return_value=_resp(
            200,
            {
                "results": [
                    {
                        "title": "Valencia GP",
                        "url": "https://example.com/v",
                        "summary": "Race at 14:00 CET",
                        "snippet": "short",
                        "site_name": "Motorsport",
                        "date": "2026-11-29T00:00:00+00:00",
                    }
                ]
            },
        )
    )
    monkeypatch.setattr(ws.requests, "post", post)
    ddg = MagicMock(return_value=[])
    monkeypatch.setattr(ws, "_ddg_text_search", ddg)

    out = ws.web_search({"query": "tell me motogp valencia start time"})

    body = post.call_args.kwargs["json"]
    assert body["query"] == "motogp valencia start time"
    assert body["ori_question"] == "tell me motogp valencia start time"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert "[Motorsport] Valencia GP" in out
    assert "(2026-11-29) Race at 14:00 CET" in out
    assert "Source: https://example.com/v" in out
    ddg.assert_not_called()


def test_query_truncated_to_api_limit(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test")
    post = MagicMock(return_value=_resp(200, {"results": []}))
    monkeypatch.setattr(ws.requests, "post", post)

    ws._deepseek_search("x" * 300, "x" * 300)

    assert len(post.call_args.kwargs["json"]["query"]) == ws.DEEPSEEK_QUERY_MAX_CHARS
    assert "ori_question" not in post.call_args.kwargs["json"]


def test_falls_back_to_ddg_on_http_error(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: "sk-test")
    monkeypatch.setattr(
        ws.requests, "post", MagicMock(return_value=_resp(402, {"error": {}}))
    )
    monkeypatch.setattr(
        ws,
        "_ddg_text_search",
        lambda q, max_results=6: [
            {
                "title": "DDG hit",
                "snippet": "s",
                "url": "https://d.example",
                "source": "Web",
            },
            {
                "title": "DDG hit 2",
                "snippet": "s",
                "url": "https://d2.example",
                "source": "Web",
            },
        ],
    )

    assert "DDG hit" in ws.web_search({"query": "f1 results"})


def test_no_key_skips_deepseek(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_deepseek_key", lambda: None)
    post = MagicMock()
    monkeypatch.setattr(ws.requests, "post", post)

    assert ws._deepseek_search("q", "q") == []
    post.assert_not_called()

"""
LLM client for Kurek.

All chat completions go straight to https://api.deepseek.com/chat/completions
(never via OpenRouter), or to Claude Haiku when KUREK_LLM=haiku. Provides:

  query_deepseek             blocking completion (text or tool_calls dict)
  stream_deepseek            raw SSE event stream (token / tool_calls / done / error)
  stream_deepseek_sentences  sentence-buffered stream for low-latency TTS

DeepSeek requires ``reasoning_content`` to be passed back on assistant turns
that carry tool calls, so both entry points surface it alongside tool_calls.
"""
import json
import os
import re
import time
from typing import Generator

import requests

from core.timing import mark as timing_mark

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
HAIKU_MODEL = "claude-haiku-5-5"

# One retry for transient failures that happen before any token is yielded.
_STREAM_RETRY_BACKOFF_S = 1.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Matches a sentence boundary: [.!?] followed by whitespace, or a blank line.
# Avoids splitting on decimals (3.5) because those have no space after the dot.
_SENT_END = re.compile(r"(?<=[.!?])\s+|(?<=\n)\s*\n")

# Trailing abbreviation tokens that must not end a spoken sentence mid-stream.
_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "mr.",
        "mrs.",
        "ms.",
        "e.g.",
        "i.e.",
        "etc.",
        "vs.",
        "st.",
    }
)


def _is_abbreviation_boundary(buffer: str, match: re.Match) -> bool:
    """True when the matched [.!?]\\s+ sits on a known abbreviation (Dr., e.g., …)."""
    # For (?<=[.!?])\\s+, match.start() is the whitespace; punct is just before.
    if match.start() == 0:
        return False
    punct_idx = match.start() - 1
    if punct_idx < 0 or buffer[punct_idx] not in ".!?":
        return False
    end = punct_idx + 1
    for abbr in _ABBREVIATIONS:
        n = len(abbr)
        if end < n:
            continue
        if buffer[end - n : end].lower() != abbr:
            continue
        start = end - n
        if start == 0 or buffer[start - 1] in " \t\n\r([{":
            return True
    return False


def pop_complete_sentence(buffer: str) -> tuple[str | None, str]:
    """
    If buffer contains a complete sentence boundary, return (sentence, remainder).
    Skips splits after common abbreviations. Does not split decimals (3.5).
    """
    match = _SENT_END.search(buffer)
    while match:
        if match.group(0).startswith("\n") or not _is_abbreviation_boundary(buffer, match):
            split_idx = match.end()
            sentence = buffer[:split_idx].strip()
            remainder = buffer[split_idx:]
            if sentence and len(sentence.split()) >= 2:
                return sentence, remainder
            # Too short to speak yet — keep scanning after this boundary.
        # Abbreviation or short fragment: continue search after this match.
        match = _SENT_END.search(buffer, match.end())
    return None, buffer


def flush_sentence_buffer(buffer: str) -> str | None:
    """Flush any remaining short/incomplete sentence at stream end."""
    text = buffer.strip()
    return text or None


def parse_dsml_tool_calls(text: str) -> tuple[list[dict], str]:
    """
    Parses DeepSeek DSML tool calling markup (<｜｜DSML｜｜ calls>...)
    Returns (tool_calls, cleaned_text).
    """
    if not text or ("DSML" not in text and "invoke" not in text):
        return [], text

    calls = []
    invoke_pattern = re.compile(
        r'<[｜|]{2}DSML[｜|]{2}\s*invoke\s+name=[\"\']([^\"\']+)[\"\']\s*>(.*?)</[｜|]{2}DSML[｜|]{2}\s*invoke>',
        re.DOTALL
    )
    param_pattern = re.compile(
        r'<[｜|]{2}DSML[｜|]{2}\s*parameter\s+name=[\"\']([^\"\']+)[\"\'](?:\s+[^>]*)?\s*>(.*?)</[｜|]{2}DSML[｜|]{2}\s*parameter>',
        re.DOTALL
    )
    for i, match in enumerate(invoke_pattern.finditer(text)):
        fn_name = match.group(1).strip()
        body = match.group(2)
        params = {}
        for pmatch in param_pattern.finditer(body):
            pname = pmatch.group(1).strip()
            pval = pmatch.group(2).strip()
            if pval.lower() == 'true':
                params[pname] = True
            elif pval.lower() == 'false':
                params[pname] = False
            else:
                try:
                    params[pname] = int(pval)
                except ValueError:
                    params[pname] = pval
        calls.append({
            "id": f"call_dsml_{i}_{abs(hash(fn_name)) % 100000}",
            "type": "function",
            "function": {
                "name": fn_name,
                "arguments": json.dumps(params),
            },
        })

    cleaned = re.sub(r'<[｜|]{2}DSML[｜|]{2}\s*calls?>.*?</[｜|]{2}DSML[｜|]{2}\s*calls?>', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'<[｜|]{2}DSML[｜|]{2}.*?>', '', cleaned)
    cleaned = re.sub(r'</[｜|]{2}DSML[｜|]{2}.*?>', '', cleaned)
    return calls, cleaned.strip()


# ---------- Claude Haiku (KUREK_LLM=haiku) ----------
# Callers keep speaking the OpenAI/DeepSeek message shape; these helpers translate
# to the Messages API and back, so the daemon's tool loop is provider-agnostic.

def use_haiku() -> bool:
    return os.environ.get("KUREK_LLM", "").strip().lower() == "haiku"


def brain_name() -> str:
    return "Claude Haiku" if use_haiku() else "DeepSeek-Flash"


def _to_anthropic_tools(tools: list) -> list[dict]:
    out = []
    for t in tools:
        fn = t.get("function", t)
        schema = fn.get("parameters") or {}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        out.append({"name": fn["name"], "description": fn.get("description", ""), "input_schema": schema})
    return out


def _to_anthropic_messages(messages: list) -> tuple[str, list[dict]]:
    """OpenAI-shaped history -> (system, Messages API turns). Consecutive same-role
    turns are merged; tool results become user-turn tool_result blocks."""
    system: list[str] = []
    out: list[dict] = []

    def add(role: str, blocks: list) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            if content:
                system.append(content)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""), "content": str(content) or "(empty)"}])
        elif role == "assistant":
            # Haiku turns carry their raw content blocks (thinking + tool_use) in
            # reasoning_content; they must be sent back unchanged.
            raw = m.get("reasoning_content")
            if isinstance(raw, list):
                add("assistant", raw)
                continue
            blocks: list = [{"type": "text", "text": content}] if content else []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments") or "{}"
                try:
                    inp = json.loads(args) if isinstance(args, str) else args
                except Exception:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tc.get("id", ""), "name": fn.get("name", ""), "input": inp or {}})
            add("assistant", blocks)
        elif isinstance(content, list):
            add("user", content)
        elif content:
            add("user", [{"type": "text", "text": content}])
    return "\n\n".join(system), out


def stream_haiku(messages: list, tools: list | None = None) -> Generator[dict, None, None]:
    """Same event contract as stream_deepseek, backed by Claude Haiku.

    Env: KUREK_HAIKU_MODEL (default claude-haiku-5-5), KUREK_HAIKU_EFFORT
    (default low), KUREK_HAIKU_THINKING=off to turn thinking off (effort <= high).
    """
    import anthropic
    from memory.config_manager import get_anthropic_key

    key = get_anthropic_key()
    if not key:
        yield {"type": "error", "error": "No ANTHROPIC_API_KEY configured"}
        return

    system, msgs = _to_anthropic_messages(messages)
    kwargs: dict = {
        "model": os.environ.get("KUREK_HAIKU_MODEL") or HAIKU_MODEL,
        "max_tokens": 16000,
        "messages": msgs,
        "output_config": {"effort": os.environ.get("KUREK_HAIKU_EFFORT") or "low"},
    }
    if os.environ.get("KUREK_HAIKU_THINKING", "").strip().lower() == "off":
        kwargs["thinking"] = {"type": "disabled"}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = _to_anthropic_tools(tools)

    text = ""
    try:
        client = anthropic.Anthropic(api_key=key, timeout=30.0)
        with client.messages.stream(**kwargs) as stream:
            for ev in stream:
                if ev.type == "text" and ev.text:
                    text += ev.text
                    yield {"type": "token", "token": ev.text}
            final = stream.get_final_message()
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        if text:
            print(f"[Haiku Stream] Stream ended early ({e}); keeping {len(text)} chars", flush=True)
            yield {"type": "done", "full_content": text.strip(), "reasoning_content": None}
        else:
            yield {"type": "error", "error": f"Claude Haiku request failed: {e}"}
        return

    if final.stop_reason == "refusal":
        yield {"type": "error", "error": "Claude Haiku declined the request"}
        return
    calls = [b for b in final.content if b.type == "tool_use"]
    if calls and final.stop_reason != "max_tokens":
        yield {
            "type": "tool_calls",
            "tool_calls": [
                {"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                for b in calls
            ],
            "content": text.strip(),
            "reasoning_content": list(final.content),
        }
        return
    if calls:
        yield {"type": "error", "error": "Claude Haiku hit max_tokens mid tool call"}
        return
    yield {"type": "done", "full_content": text.strip(), "reasoning_content": None}


def query_deepseek(
    prompt: str = "",
    system_prompt: str = "",
    messages: list | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    tools: list | None = None,
) -> dict | str | None:
    """Query DeepSeek platform API directly (api.deepseek.com)."""
    try:
        from memory.config_manager import get_deepseek_key
        key = get_deepseek_key()
        if not key and not use_haiku():
            print("[DeepSeek] ⚠️ No DEEPSEEK_API_KEY found in .env or environment")
            return None

        req_messages = []
        if messages:
            req_messages = list(messages)
        else:
            if system_prompt:
                req_messages.append({"role": "system", "content": system_prompt})
            if prompt:
                req_messages.append({"role": "user", "content": prompt})

        if use_haiku():
            result = None
            for ev in stream_haiku(req_messages, tools):
                if ev["type"] == "error":
                    print(f"[Haiku] {ev['error']}")
                    return None
                if ev["type"] in ("tool_calls", "done"):
                    result = ev
            if result is None:
                return None
            if result["type"] == "tool_calls":
                return result
            return result["full_content"]

        payload = {
            "model": model or DEFAULT_MODEL,
            "messages": req_messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        resp = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            choice = data["choices"][0]["message"]
            if choice.get("tool_calls"):
                return {
                    "type": "tool_calls",
                    "tool_calls": choice["tool_calls"],
                    "content": choice.get("content"),
                    "reasoning_content": choice.get("reasoning_content"),
                }
            raw_content = choice.get("content", "")
            dsml_calls, clean_text = parse_dsml_tool_calls(raw_content)
            if dsml_calls:
                return {
                    "type": "tool_calls",
                    "tool_calls": dsml_calls,
                    "content": clean_text,
                    "reasoning_content": choice.get("reasoning_content"),
                }
            return clean_text.strip()
        else:
            print(f"[DeepSeek] HTTP {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        print(f"[DeepSeek] Error: {e}")
        return None


def _open_deepseek_stream(key: str, payload: dict):
    """POST a streaming request. Returns (resp, error, retryable)."""
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=30,
        )
    except Exception as e:
        return None, str(e), True
    if resp.status_code != 200:
        err_msg = f"HTTP {resp.status_code}: {resp.text}"
        retryable = resp.status_code in _RETRYABLE_STATUS
        _close_quietly(resp)
        return None, err_msg, retryable
    return resp, None, False


def _close_quietly(resp) -> None:
    close = getattr(resp, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        pass


def stream_deepseek(
    prompt: str = "",
    system_prompt: str = "",
    messages: list | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    tools: list | None = None,
) -> Generator[dict, None, None]:
    """
    Streams response from DeepSeek API directly.
    Yields:
      {"type": "token", "token": "..."}
      {"type": "tool_calls", "tool_calls": [...], "content": "...", "reasoning_content": ...}
      {"type": "done", "full_content": "...", "reasoning_content": ...}
      {"type": "error", "error": "..."}

    Connection errors and HTTP 429/5xx are retried once (before any output).
    The HTTP response is always closed, including when the consumer stops
    iterating early. If the stream is cut off before ``[DONE]``/finish_reason:
    with no output (or with tool calls, whose arguments may be truncated) an
    error is yielded; if text was already streamed, a ``done`` with the partial
    text is yielded and the truncation is logged, so a partially spoken answer
    is not reported as a failure.
    """
    from memory.config_manager import get_deepseek_key
    key = get_deepseek_key()
    if not key and not use_haiku():
        print("[DeepSeek Stream] ⚠️ No DEEPSEEK_API_KEY found")
        yield {"type": "error", "error": "No DEEPSEEK_API_KEY configured"}
        return

    req_messages = []
    if messages:
        req_messages = list(messages)
    else:
        if system_prompt:
            req_messages.append({"role": "system", "content": system_prompt})
        if prompt:
            req_messages.append({"role": "user", "content": prompt})

    if use_haiku():
        yield from stream_haiku(req_messages, tools)
        return

    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": req_messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    resp, err, retryable = _open_deepseek_stream(key, payload)
    if resp is None and retryable:
        print(f"[DeepSeek Stream] {err} — retrying once in {_STREAM_RETRY_BACKOFF_S:g}s", flush=True)
        time.sleep(_STREAM_RETRY_BACKOFF_S)
        resp, err, _ = _open_deepseek_stream(key, payload)
    if resp is None:
        print(f"[DeepSeek Stream] {err}")
        yield {"type": "error", "error": err}
        return

    full_text = ""
    full_reasoning = ""
    tool_calls_map: dict[int, dict] = {}
    has_tool_calls = False
    finished = False
    read_error: str | None = None

    try:
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_str.startswith("data: "):
                    continue
                raw_data = line_str[6:].strip()
                if raw_data == "[DONE]":
                    finished = True
                    break

                try:
                    chunk = json.loads(raw_data)
                except Exception:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                if choices[0].get("finish_reason"):
                    finished = True
                delta = choices[0].get("delta") or {}

                # Accumulate DeepSeek reasoning tokens if present
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    full_reasoning += reasoning

                # Check for tool call stream chunks
                if delta.get("tool_calls"):
                    has_tool_calls = True
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {
                                "id": tc.get("id", f"call_{idx}_{int(time.time())}"),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tool_calls_map[idx]["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            tool_calls_map[idx]["function"]["arguments"] += fn["arguments"]

                # Text content
                content = delta.get("content", "")
                if content:
                    full_text += content
                    yield {"type": "token", "token": content}
        except Exception as e:
            read_error = str(e)
            print(f"[DeepSeek Stream] Stream read error: {e}", flush=True)
    finally:
        _close_quietly(resp)

    if not finished:
        if full_text and not has_tool_calls:
            reason = read_error or "no [DONE] marker"
            print(
                f"[DeepSeek Stream] Stream ended early ({reason}); "
                f"keeping {len(full_text)} chars already streamed",
                flush=True,
            )
        else:
            yield {
                "type": "error",
                "error": read_error or "DeepSeek stream ended unexpectedly",
            }
            return

    reasoning_out = full_reasoning.strip() if full_reasoning else None
    if has_tool_calls:
        yield {
            "type": "tool_calls",
            "tool_calls": list(tool_calls_map.values()),
            "content": full_text.strip(),
            "reasoning_content": reasoning_out,
        }
        return

    # Check for DSML markup if formatted in content
    dsml_calls, clean_text = parse_dsml_tool_calls(full_text)
    if dsml_calls:
        yield {
            "type": "tool_calls",
            "tool_calls": dsml_calls,
            "content": clean_text,
            "reasoning_content": reasoning_out,
        }
    else:
        yield {
            "type": "done",
            "full_content": full_text.strip(),
            "reasoning_content": reasoning_out,
        }


def stream_deepseek_sentences(
    prompt: str = "",
    system_prompt: str = "",
    messages: list | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    tools: list | None = None,
) -> Generator[dict, None, None]:
    """
    Buffers streaming tokens from DeepSeek and yields complete sentences immediately
    so the first sentence can dispatch to TTS while later tokens still arrive.
    """
    buffer = ""
    full_response = ""
    saw_token = False

    for event in stream_deepseek(
        prompt=prompt,
        system_prompt=system_prompt,
        messages=messages,
        model=model,
        temperature=temperature,
        tools=tools,
    ):
        event_type = event.get("type")
        if event_type == "tool_calls":
            yield event
            return
        elif event_type == "error":
            yield event
            return
        elif event_type == "token":
            tok = event.get("token", "")
            if tok and not saw_token:
                saw_token = True
                timing_mark("first_deepseek_token", once=True)
            buffer += tok
            full_response += tok

            sentence, buffer = pop_complete_sentence(buffer)
            while sentence is not None:
                timing_mark("first_deepseek_sentence", once=True)
                yield {
                    "type": "sentence",
                    "sentence": sentence,
                    "full_content": full_response,
                }
                sentence, buffer = pop_complete_sentence(buffer)

    # Flush any short / incomplete trailing sentence at stream end.
    trailing = flush_sentence_buffer(buffer)
    if trailing:
        timing_mark("first_deepseek_sentence", once=True)
        yield {
            "type": "sentence",
            "sentence": trailing,
            "full_content": full_response,
        }

    yield {"type": "done", "full_content": full_response.strip()}

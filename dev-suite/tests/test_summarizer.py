"""Tests for the memory summarizer."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.memory.summarizer import (
    _build_summarizer_prompt,
    _parse_summarizer_response,
    summarize_writes_sync,
)


class TestBuildPrompt:
    def test_includes_entry_count(self):
        writes = [{"content": "a"}, {"content": "b"}]
        prompt = _build_summarizer_prompt(writes)
        assert "2 total" in prompt

    def test_includes_json(self):
        writes = [{"content": "test entry", "tier": "l1"}]
        prompt = _build_summarizer_prompt(writes)
        assert "test entry" in prompt


class TestParseResponse:
    def test_parse_clean_json_array(self):
        raw = '[{"content": "merged", "tier": "l1"}]'
        result = _parse_summarizer_response(raw)
        assert len(result) == 1
        assert result[0]["content"] == "merged"

    def test_parse_code_fenced(self):
        raw = '```json\n[{"content": "fenced", "tier": "l1"}]\n```'
        result = _parse_summarizer_response(raw)
        assert len(result) == 1
        assert result[0]["content"] == "fenced"

    def test_parse_with_preamble(self):
        raw = 'Here are the consolidated entries:\n[{"content": "preamble", "tier": "l1"}]'
        result = _parse_summarizer_response(raw)
        assert len(result) == 1

    def test_parse_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_summarizer_response("not json at all")

    def test_parse_object_not_array_raises(self):
        """Should raise if response is a JSON object instead of array."""
        with pytest.raises(json.JSONDecodeError):
            _parse_summarizer_response('{"content": "not an array"}')


class TestSummarizeWritesSync:
    def test_empty_writes(self):
        assert summarize_writes_sync([]) == []

    def test_small_batch_passthrough(self):
        """Batches of <= 2 entries are returned as-is (no LLM call)."""
        writes = [{"content": "a", "tier": "l1"}, {"content": "b", "tier": "l1"}]
        result = summarize_writes_sync(writes)
        assert result == writes

    def test_no_api_key_returns_raw(self, monkeypatch):
        """Without ANTHROPIC_API_KEY, returns raw writes."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        writes = [
            {"content": "a", "tier": "l1"},
            {"content": "b", "tier": "l1"},
            {"content": "c", "tier": "l1"},
        ]
        result = summarize_writes_sync(writes, api_key="")
        assert result == writes

    @patch("langchain_anthropic.ChatAnthropic")
    def test_llm_failure_returns_raw(self, mock_llm_class):
        """If LLM call throws, returns raw writes."""
        mock_llm_class.side_effect = Exception("API error")
        writes = [
            {"content": "a", "tier": "l1"},
            {"content": "b", "tier": "l1"},
            {"content": "c", "tier": "l1"},
        ]
        result = summarize_writes_sync(writes, api_key="fake-key")
        assert result == writes

    @patch("langchain_anthropic.ChatAnthropic")
    def test_successful_summarization(self, mock_llm_class):
        """Successful LLM call returns consolidated entries."""
        consolidated = [{"content": "merged a+b", "tier": "l1", "module": "auth",
                         "source_agent": "developer", "confidence": 1.0,
                         "related_files": "auth.js"}]

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=json.dumps(consolidated))
        mock_llm_class.return_value = mock_llm

        writes = [
            {"content": "a", "tier": "l1"},
            {"content": "b", "tier": "l1"},
            {"content": "c", "tier": "l1"},
        ]
        result = summarize_writes_sync(writes, api_key="fake-key")
        assert len(result) == 1
        assert result[0]["content"] == "merged a+b"

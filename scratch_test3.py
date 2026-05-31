from python_scripts.openai_relay import OpenAIRelay
import json

class MockCandidate:
    provider = "foo"
    model = "bar"

def _normalize_tool_calls(provider, full_content):
    from python_scripts.response_normalizer import ParsedToolCalls
    return ParsedToolCalls(tool_calls=[{
        'id': 'call_123',
        'type': 'function',
        'function': {
            'name': 'file_write',
            'arguments': '{"path": "x.md"}'
        }
    }])

import python_scripts.response_normalizer
python_scripts.response_normalizer._normalize_tool_calls = _normalize_tool_calls

def test_timed_stream():
    def mock_stream():
        yield b'data: {"choices": [{"delta": {"content": "Executing"}, "finish_reason": null}]}\n\n'
        yield b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
        yield b'data: [DONE]\n\n'
        
    class MockRelay(OpenAIRelay):
        def __init__(self):
            self.debug_log = None
    
    relay = MockRelay()
    headers_ms = 0
    # we have to extract the inner function or just copy it
test_timed_stream()

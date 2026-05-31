import json
import time
from python_scripts.response_normalizer import ParsedToolCalls

def _normalize_tool_calls(provider, full_content):
    return ParsedToolCalls(tool_calls=[{
        'id': 'call_123',
        'type': 'function',
        'function': {
            'name': 'file_write',
            'arguments': '{"path": "x.md"}'
        }
    }])

def mock_timed_stream(stream, cand_provider):
    first = True
    accumulated_content = []
    
    for chunk in stream:
        if first:
            first = False
            
        if not chunk.strip():
            yield chunk
            continue
            
        decoded = chunk.decode('utf-8', errors='ignore')
        if not decoded.startswith('data:'):
            yield chunk
            continue
            
        data_str = decoded[5:].strip()
        if data_str == '[DONE]':
            yield chunk
            continue
            
        try:
            parsed_json = json.loads(data_str)
            choices = parsed_json.get('choices', [])
            if choices:
                delta = choices[0].get('delta', {})
                content = delta.get('content')
                if content:
                    accumulated_content.append(content)
                    
                finish_reason = choices[0].get('finish_reason')
                if finish_reason == 'stop':
                    full_content = "".join(accumulated_content)
                    parsed_tc = _normalize_tool_calls(cand_provider, full_content)
                    if parsed_tc is not None:
                        choices[0]['finish_reason'] = 'tool_calls'
                        if 'delta' not in choices[0]:
                            choices[0]['delta'] = {}
                            
                        stream_tool_calls = []
                        for i, tc in enumerate(parsed_tc.tool_calls):
                            stc = dict(tc)
                            stc['index'] = i
                            stream_tool_calls.append(stc)
                            
                        choices[0]['delta']['tool_calls'] = stream_tool_calls
                        rewritten = f"data: {json.dumps(parsed_json, ensure_ascii=False)}\n\n".encode('utf-8')
                        yield rewritten
                        continue
        except Exception as e:
            print(f"Exception: {e}")
            pass
        yield chunk

stream = [
    b'data: {"choices": [{"delta": {"content": "Executing"}, "finish_reason": null}]}\n\n',
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n',
    b'data: [DONE]\n\n'
]

for c in mock_timed_stream(stream, 'foo'):
    print(c)

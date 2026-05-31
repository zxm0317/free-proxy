from __future__ import annotations
import json

def rewrite_openai_stream(provider: str, stream):
    accumulated_content = []
    from python_scripts.response_normalizer import _normalize_tool_calls
    
    for chunk in stream:
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
            return
            
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
                    parsed_tc = _normalize_tool_calls(provider, full_content)
                    if parsed_tc is not None:
                        choices[0]['finish_reason'] = 'tool_calls'
                        choices[0]['delta']['tool_calls'] = parsed_tc.tool_calls
                        rewritten = f"data: {json.dumps(parsed_json, ensure_ascii=False)}\n\n".encode('utf-8')
                        yield rewritten
                        continue
        except Exception as e:
            pass
            
        yield chunk

# Test
class MockStream:
    def __iter__(self):
        yield b'data: {"choices": [{"delta": {"content": "Executing:\\n"}}]}\n\n'
        yield b'data: {"choices": [{"delta": {"content": "{ \\"action\\": \\"web_search\\", \\"query\\": 123 }"}}]}\n\n'
        yield b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
        yield b'data: [DONE]\n\n'

for c in rewrite_openai_stream('foo', MockStream()):
    print(c)

import json
from python_scripts.provider_adapter import ProviderAdapter
from python_scripts.provider_catalog import get_provider

def test():
    adapter = ProviderAdapter(get_provider('gemini'), api_key='test')
    payload = {
        'model': 'gemini-2.0-flash',
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Identify this'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,xxxxxx'}}
                ]
            }
        ]
    }

    # Simulate forward_chat
    prompt = adapter._prompt_from_payload(payload)
    token_limit = 256
    
    contents = []
    messages = payload.get('messages')
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get('role', 'user')
            gemini_role = 'model' if role == 'assistant' else 'user'
            content = item.get('content')
            parts = []
            if isinstance(content, str):
                if content.strip():
                    parts.append({'text': content})
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    text = block.get('text')
                    if isinstance(text, str):
                        parts.append({'text': text})
                    elif block.get('type') == 'image_url':
                        img = block.get('image_url')
                        url = None
                        if isinstance(img, dict):
                            url = img.get('url')
                        elif isinstance(img, str):
                            url = img
                        elif img is None and 'url' in block:
                            url = block.get('url')
                        
                        if isinstance(url, str) and url.startswith('data:image/'):
                            try:
                                header, base64_data = url.split(',', 1)
                                mime_type = header.split(';')[0].replace('data:', '')
                                parts.append({
                                    'inlineData': {
                                        'mimeType': mime_type,
                                        'data': base64_data
                                    }
                                })
                            except Exception:
                                pass
            if parts:
                contents.append({'role': gemini_role, 'parts': parts})
    
    if not contents:
        contents = [{'role': 'user', 'parts': [{'text': prompt}]}]

    request_payload = {
        'contents': contents,
        'generationConfig': {'temperature': 0, 'maxOutputTokens': token_limit},
    }
    
    print(json.dumps(request_payload, indent=2))

if __name__ == '__main__':
    test()

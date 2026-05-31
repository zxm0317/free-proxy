import urllib.request
import json
import ssl

req = urllib.request.Request('http://127.0.0.1:8765/v1/chat/completions', method='POST')
req.add_header('Authorization', 'Bearer sk-fp-7040537297e644d0f0f9b8ef98c52e11')
req.add_header('Content-Type', 'application/json')
data = json.dumps({'model': 'free-proxy/auto', 'messages': [{'role': 'user', 'content': 'Hello'}]}).encode('utf-8')

try:
    with urllib.request.urlopen(req, data=data) as f:
        print(f.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f'HTTP {e.code}: {e.read().decode("utf-8")}')

import urllib.request
import urllib.error

req = urllib.request.Request('https://free-proxy-eta.vercel.app/api/usage-stats', method='GET')
req.add_header('Cookie', 'adminToken=admin') # Dummy token to see if it responds

try:
    with urllib.request.urlopen(req) as f:
        print("Success:", f.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f'HTTP {e.code}: {e.read().decode("utf-8")}')

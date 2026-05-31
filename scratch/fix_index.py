import re

with open('python_scripts/web/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove login-overlay div
overlay_pattern = re.compile(r'<div id="login-overlay".*?</div>\n    </div>', re.DOTALL)
content = overlay_pattern.sub('', content)

# 2. Modify logout
logout_pattern = re.compile(r'function logout\(\) \{[\s\S]*?\}')
new_logout = '''function logout() {
            localStorage.removeItem('adminToken');
            window.location.href = 'login.html';
        }'''
content = logout_pattern.sub(new_logout, content)

# 3. Remove attemptLogin
attempt_login_pattern = re.compile(r'async function attemptLogin\(\) \{[\s\S]*?\}\n', re.DOTALL)
content = attempt_login_pattern.sub('', content)

# 4. Update checkInitialAuth and startApp
app_pattern = re.compile(r'async function checkInitialAuth\(\) \{[\s\S]*?async function startApp\(\) \{[\s\S]*?\}', re.DOTALL)

new_app = '''async function checkInitialAuth() {
            const token = localStorage.getItem('adminToken');
            if (!token) return false;
            try {
                await loadStatuses();
                return true;
            } catch (e) {
                console.error('Error in checkInitialAuth:', e);
                return false;
            }
        }

        async function startApp() {
            const isValid = await checkInitialAuth();
            if (!isValid) {
                window.location.href = 'login.html';
                return;
            }
            document.getElementById('main-app').style.opacity = '1';
            document.getElementById('main-app').style.pointerEvents = 'auto';
            await bootstrap(false);
        }'''

content = app_pattern.sub(new_app, content)

with open('python_scripts/web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

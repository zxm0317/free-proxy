import re

html_path = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Fix CSS for inputs and selects
css_target = '''        .field {
            width: 100%;
            padding: 10px 14px;
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 10px;
            font-size: 14px;
            color: var(--text);
            transition: all 0.2s ease;
            outline: none;
            height: 38px;
        }

        .field:focus {
            border-color: rgba(30, 106, 82, 0.4);
            box-shadow: 0 0 0 3px rgba(30, 106, 82, 0.1);
        }

        .select {
            width: 100%;
            padding: 8px 12px;
            background: #fff;
            border: 1px solid var(--line);
            border-radius: 10px;
            font-size: 14px;
            height: 38px;
            outline: none;
        }'''

css_replacement = '''        .field, .select {
            width: 100%;
            padding: 10px 14px;
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 10px;
            font-size: 14px;
            color: var(--text);
            transition: all 0.2s ease;
            outline: none;
            min-height: 44px; /* Fixed dropdown height issue */
            appearance: auto;
        }

        .field:focus, .select:focus {
            border-color: rgba(30, 106, 82, 0.4);
            box-shadow: 0 0 0 3px rgba(30, 106, 82, 0.1);
        }'''

if css_target in html:
    html = html.replace(css_target, css_replacement)
else:
    # fallback regex
    html = re.sub(r'height:\s*38px;', r'min-height: 44px;', html)

# 2. Add Provider Select Dropdown
provider_html_target = '''                        <h2>1. Provider 配置</h2>
                        <div class="subtle">保存 Key 后直接点验证。验证会发起真实请求，不只是检查模型列表。</div>
                    </div>
                </div>
                <div class="provider-grid" id="providerGrid"></div>'''

provider_html_replacement = '''                        <h2>1. Provider 配置</h2>
                        <div class="subtle">保存 Key 后直接点验证。验证会发起真实请求，不只是检查模型列表。</div>
                    </div>
                    <div style="display: flex; gap: 12px; margin-top: 12px; margin-bottom: 24px;">
                        <select class="select" id="addProviderSelect" style="max-width: 300px;">
                            <option value="gemini">谷歌</option>
                            <option value="github">GitHub 模型</option>
                            <option value="groq">Groq</option>
                            <option value="sambanova">SambaNova</option>
                            <option value="mistral">Mistral</option>
                            <option value="openrouter">OpenRouter</option>
                            <option value="siliconflow">SiliconFlow (硅基流动)</option>
                            <option value="together">Together AI</option>
                            <option value="cloudflare">Cloudflare AI</option>
                            <option value="cerebras">Cerebras</option>
                            <option value="scaleway">Scaleway</option>
                            <option value="xai">xAI</option>
                            <option value="hyperbolic">Hyperbolic</option>
                            <option value="novita">Novita AI</option>
                            <option value="glmf">智谱 GLM</option>
                            <option value="deepseek">DeepSeek (深度求索)</option>
                            <option value="dashscope">DashScope (阿里云百炼)</option>
                        </select>
                        <button class="btn btn-primary" onclick="addNewProvider()">添加 Provider</button>
                    </div>
                </div>
                <div class="provider-grid" id="providerGrid"></div>'''

if provider_html_target in html:
    html = html.replace(provider_html_target, provider_html_replacement)


# 3. Add script for addNewProvider
script_target = '''        // Global Event Listeners'''
script_replacement = '''        async function addNewProvider() {
            const select = document.getElementById('addProviderSelect');
            const providerId = select.value;
            const friendlyName = select.options[select.selectedIndex].text;
            
            const key = prompt(`请输入 ${friendlyName} 的 API Key:`);
            if (key) {
                try {
                    await requestJson(`/api/provider-keys/${providerId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ key })
                    });
                    showToast(`${friendlyName} 配置已保存`, 'info');
                    await bootstrap(true);
                } catch(e) {
                    showToast('保存失败: ' + e.message, 'error');
                }
            }
        }

        // Global Event Listeners'''

if script_target in html:
    html = html.replace(script_target, script_replacement)

# 4. Button styles - make them neutral/muted grey as requested
button_css_target = '''        .btn-primary {
            background: var(--primary);
            color: #fff;
            border: 1px solid var(--primary-strong);
        }

        .btn-primary:hover {
            background: var(--primary-strong);
        }'''

button_css_replacement = '''        .btn-primary {
            background: #8c8c8c;
            color: #fff;
            border: 1px solid #7a7a7a;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }

        .btn-primary:hover {
            background: #7a7a7a;
        }'''

if button_css_target in html:
    html = html.replace(button_css_target, button_css_replacement)

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print("UI fixed successfully!")

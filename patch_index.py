import os

html_path = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update CSS styles for .field, .select, .textarea and add draggable styles
old_css = """        .field,
        .select,
        .textarea {
            width: 100%;
            border: 1px solid #cfd9c8;
            border-radius: 12px;
            padding: 11px 12px;
            background: #fff;
            color: var(--text);
            font: inherit;
        }"""

new_css = """        .field,
        .select,
        .textarea {
            width: 100%;
            border: 1px solid #cfd9c8;
            border-radius: 12px;
            padding: 11px 12px;
            background: #fff;
            color: var(--text);
            font: inherit;
            min-height: 44px;
            box-sizing: border-box;
            transition: all 0.2s ease;
        }
        
        tr[draggable="true"] {
            cursor: grab;
            transition: background-color 0.2s;
        }
        
        tr[draggable="true"]:active {
            cursor: grabbing;
        }
        
        tr[draggable="true"]:hover {
            background-color: #f8f9fa !important;
        }"""

if old_css in content:
    content = content.replace(old_css, new_css)
else:
    print("WARNING: old_css not found")

# 2. Update Provider 配置 Card Head to include addProviderSelect and button
old_section = """            <section class="card">
                <div class="card-head">
                    <div>
                        <h2>1. Provider 配置</h2>
                        <div class="subtle">保存 Key 后直接点验证。验证会发起真实请求，不只是检查模型列表。</div>
                    </div>
                </div>
                <div class="provider-grid" id="providerGrid"></div>
            </section>"""

new_section = """            <section class="card">
                <div class="card-head">
                    <div>
                        <h2>1. Provider 配置</h2>
                        <div class="subtle">保存 Key 后直接点验证。验证会发起真实请求，不只是检查模型列表。</div>
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <select class="select" id="addProviderSelect" style="min-width: 180px;"></select>
                        <button class="btn btn-primary" onclick="addNewProvider()">添加 Provider</button>
                    </div>
                </div>
                <div class="provider-grid" id="providerGrid"></div>
            </section>"""

if old_section in content:
    content = content.replace(old_section, new_section)
else:
    print("WARNING: old_section not found")

# 3. Update PROVIDERS Javascript array to include all 18 providers
old_providers_js = """        const PROVIDERS = [
            { id: 'openrouter', name: 'OpenRouter', tier: '免费', tierClass: 'free', placeholder: 'openrouter key', keyUrl: 'https://openrouter.ai/keys' },
            { id: 'groq', name: 'Groq', tier: '免费', tierClass: 'free', placeholder: 'groq key', keyUrl: 'https://console.groq.com/keys' },
            { id: 'gemini', name: 'Gemini', tier: '免费', tierClass: 'free', placeholder: 'gemini key', keyUrl: 'https://aistudio.google.com/app/apikey' },
            { id: 'github', name: 'GitHub Models', tier: '免费', tierClass: 'free', placeholder: 'github token', keyUrl: 'https://github.com/settings/tokens' },
            { id: 'mistral', name: 'Mistral', tier: '免费', tierClass: 'free', placeholder: 'mist-...', keyUrl: 'https://console.mistral.ai/api-keys' },
            { id: 'sambanova', name: 'SambaNova', tier: '免费', tierClass: 'free', placeholder: 'sn-...', keyUrl: 'https://cloud.sambanova.ai/' },
        ];"""

new_providers_js = """        const PROVIDERS = [
            { id: 'github', name: 'GitHub Models', tier: '免费', tierClass: 'free', placeholder: 'github token', keyUrl: 'https://github.com/settings/tokens' },
            { id: 'google', name: 'Google AI Studio', tier: '免费', tierClass: 'free', placeholder: 'google key', keyUrl: 'https://aistudio.google.com/' },
            { id: 'gemini', name: 'Gemini', tier: '免费', tierClass: 'free', placeholder: 'gemini key', keyUrl: 'https://aistudio.google.com/app/apikey' },
            { id: 'groq', name: 'Groq', tier: '免费', tierClass: 'free', placeholder: 'groq key', keyUrl: 'https://console.groq.com/keys' },
            { id: 'cerebras', name: 'Cerebras', tier: '免费', tierClass: 'free', placeholder: 'cerebras key', keyUrl: 'https://console.cerebras.ai/' },
            { id: 'sambanova', name: 'SambaNova', tier: '免费', tierClass: 'free', placeholder: 'sambanova key', keyUrl: 'https://cloud.sambanova.ai/' },
            { id: 'nvidia', name: 'NVIDIA NIM', tier: '免费', tierClass: 'free', placeholder: 'nvidia key', keyUrl: 'https://build.nvidia.com/' },
            { id: 'mistral', name: 'Mistral', tier: '免费', tierClass: 'free', placeholder: 'mistral key', keyUrl: 'https://console.mistral.ai/api-keys' },
            { id: 'openrouter', name: 'OpenRouter', tier: '免费/付费', tierClass: 'free', placeholder: 'openrouter key', keyUrl: 'https://openrouter.ai/keys' },
            { id: 'cohere', name: 'Cohere', tier: '免费', tierClass: 'free', placeholder: 'cohere key', keyUrl: 'https://dashboard.cohere.com/api-keys' },
            { id: 'cloudflare', name: 'Cloudflare Workers AI', tier: '免费', tierClass: 'free', placeholder: 'account_id:token', keyUrl: 'https://dash.cloudflare.com/' },
            { id: 'zhipu', name: '智谱 AI (GLM)', tier: '免费', tierClass: 'free', placeholder: 'zhipu key', keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys' },
            { id: 'ollama', name: 'Ollama Cloud', tier: '免费', tierClass: 'free', placeholder: 'ollama key (if needed)', keyUrl: 'https://ollama.com' },
            { id: 'kilo', name: 'Kilo Gateway', tier: '免费', tierClass: 'free', placeholder: 'kilo key (if needed)', keyUrl: 'https://api.kilo.ai' },
            { id: 'pollinations', name: 'Pollinations', tier: '免费', tierClass: 'free', placeholder: 'pollinations key (optional)', keyUrl: 'https://pollinations.ai' },
            { id: 'llm7', name: 'LLM7.io', tier: '免费', tierClass: 'free', placeholder: 'llm7 key (optional)', keyUrl: 'https://api.llm7.io' },
            { id: 'huggingface', name: 'HuggingFace Router', tier: '免费', tierClass: 'free', placeholder: 'hf token', keyUrl: 'https://huggingface.co/settings/tokens' },
            { id: 'opencode', name: 'OpenCode Zen', tier: '免费', tierClass: 'free', placeholder: 'opencode key', keyUrl: 'https://opencode.ai/auth' }
        ];"""

if old_providers_js in content:
    content = content.replace(old_providers_js, new_providers_js)
else:
    print("WARNING: old_providers_js not found")

# 4. Update renderProviderCards to filter visible ones and add new helpers
old_render_cards = """        function renderProviderCards() {
            providerGrid.innerHTML = PROVIDERS.map((provider) => {
                const status = state.statuses[provider.id] || { configured: false, masked: '' };
                const verify = state.verifyResults[provider.id];
                const isEditing = Boolean(state.editing[provider.id]) || !status.configured;
                const stateLabel = status.configured ? '✓ 已配置' : '○ 未配置';
                const note = verify
                    ? verify.ok
                        ? `最近验证成功：${verify.verified_model || '已通过真实请求验证'}`
                        : `最近验证失败：${verify.error || '未知错误'}${verify.suggestion ? `\\n建议：${verify.suggestion}` : ''}`
                    : '';

                return `
                    <div class="provider-card ${status.configured ? 'configured' : ''}">
                        <div class="provider-head">
                            <div>
                                <div class="provider-title">
                                    <span class="provider-name">${provider.name}</span>
                                    <span class="badge ${provider.tierClass}">${provider.tier}</span>
                                </div>
                                <div class="provider-help">Key 获取：<a href="${provider.keyUrl}" target="_blank" rel="noreferrer">${provider.keyUrl.replace('https://', '')}</a></div>
                            </div>
                            <div class="provider-state ${status.configured ? 'ok' : ''}">${stateLabel}</div>
                        </div>

                        ${isEditing ? `
                            <div class="provider-actions">
                                <input class="field" id="key-${provider.id}" type="password" autocomplete="off" placeholder="${provider.placeholder}">
                                <button class="btn btn-primary" data-action="save" data-provider="${provider.id}">保存</button>
                                ${status.configured ? `<button class="btn btn-ghost" data-action="cancel-edit" data-provider="${provider.id}">取消</button>` : `<button class="btn btn-ghost" data-action="verify" data-provider="${provider.id}" ${status.configured ? '' : 'disabled'}>验证</button>`}
                            </div>
                        ` : `
                            <div class="masked mono">${escapeHtml(status.masked || '已配置')}</div>
                            <div class="provider-actions">
                                <button class="btn btn-secondary" data-action="verify" data-provider="${provider.id}">验证</button>
                                <button class="btn btn-ghost" data-action="edit" data-provider="${provider.id}">修改</button>
                            </div>
                        `}

                        <div class="provider-note">${escapeHtml(note)}</div>
                    </div>
                `;
            }).join('');
        }"""

new_render_cards = """        function renderProviderCards() {
            const visible = PROVIDERS.filter(p => {
                const status = state.statuses[p.id] || { configured: false };
                return status.configured || state.editing[p.id];
            });

            if (visible.length === 0) {
                providerGrid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 30px; color: var(--muted); background: #fafafa; border-radius: 12px; border: 1px dashed var(--line);">当前未配置任何 Provider。请在右上方下拉框选择一个并点击“添加 Provider”。</div>';
                return;
            }

            providerGrid.innerHTML = visible.map((provider) => {
                const status = state.statuses[provider.id] || { configured: false, masked: '' };
                const verify = state.verifyResults[provider.id];
                const isEditing = Boolean(state.editing[provider.id]) || !status.configured;
                const stateLabel = status.configured ? '✓ 已配置' : '○ 未配置';
                const note = verify
                    ? verify.ok
                        ? `最近验证成功：${verify.verified_model || '已通过真实请求验证'}`
                        : `最近验证失败：${verify.error || '未知错误'}${verify.suggestion ? `\\n建议：${verify.suggestion}` : ''}`
                    : '';

                return `
                    <div class="provider-card ${status.configured ? 'configured' : ''}">
                        <div class="provider-head">
                            <div>
                                <div class="provider-title">
                                    <span class="provider-name">${provider.name}</span>
                                    <span class="badge ${provider.tierClass}">${provider.tier}</span>
                                </div>
                                <div class="provider-help">Key 获取：<a href="${provider.keyUrl}" target="_blank" rel="noreferrer">${provider.keyUrl.replace('https://', '')}</a></div>
                            </div>
                            <div class="provider-state ${status.configured ? 'ok' : ''}">${stateLabel}</div>
                        </div>

                        ${isEditing ? `
                            <div class="provider-actions">
                                <input class="field" id="key-${provider.id}" type="password" autocomplete="off" placeholder="${provider.placeholder}">
                                <button class="btn btn-primary" data-action="save" data-provider="${provider.id}">保存</button>
                                ${status.configured ? `<button class="btn btn-ghost" data-action="cancel-edit" data-provider="${provider.id}">取消</button>` : `<button class="btn btn-ghost" data-action="cancel-edit" data-provider="${provider.id}">取消</button>`}
                            </div>
                        ` : `
                            <div class="masked mono">${escapeHtml(status.masked || '已配置')}</div>
                            <div class="provider-actions">
                                <button class="btn btn-secondary" data-action="verify" data-provider="${provider.id}">验证</button>
                                <button class="btn btn-ghost" data-action="edit" data-provider="${provider.id}">修改</button>
                            </div>
                        `}

                        <div class="provider-note">${escapeHtml(note)}</div>
                    </div>
                `;
            }).join('');
        }

        window.addNewProvider = function() {
            const providerId = document.getElementById('addProviderSelect').value;
            if (!providerId) return;
            state.editing[providerId] = true;
            renderProviderCards();
            populateAddProviderSelect();
            setTimeout(() => {
                const input = document.getElementById(`key-${providerId}`);
                if (input) input.focus();
            }, 100);
        };

        function populateAddProviderSelect() {
            const select = document.getElementById('addProviderSelect');
            if (!select) return;
            const available = PROVIDERS.filter(p => {
                const status = state.statuses[p.id] || { configured: false };
                return !status.configured && !state.editing[p.id];
            });
            if (available.length === 0) {
                select.innerHTML = '<option value="">无可用 Provider</option>';
                select.disabled = true;
            } else {
                select.innerHTML = available.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
                select.disabled = false;
            }
        }"""

if old_render_cards in content:
    content = content.replace(old_render_cards, new_render_cards)
else:
    print("WARNING: old_render_cards not found")

# 5. Update actions: edit, cancel-edit
old_actions_edit = """            if (action === 'edit') {
                state.editing[providerId] = true;
                renderProviderCards();
                return;
            }
            if (action === 'cancel-edit') {
                state.editing[providerId] = false;
                renderProviderCards();
                return;
            }"""

new_actions_edit = """            if (action === 'edit') {
                state.editing[providerId] = true;
                renderProviderCards();
                populateAddProviderSelect();
                return;
            }
            if (action === 'cancel-edit') {
                state.editing[providerId] = false;
                renderProviderCards();
                populateAddProviderSelect();
                return;
            }"""

if old_actions_edit in content:
    content = content.replace(old_actions_edit, new_actions_edit)
else:
    print("WARNING: old_actions_edit not found")

# 6. Update loadStatuses to call populateAddProviderSelect
old_load_statuses = """        async function loadStatuses() {
            state.statuses = await requestJson('/api/provider-keys');
            renderProviderCards();
            renderProviderTabs();
            refreshChatOptions();
        }"""

new_load_statuses = """        async function loadStatuses() {
            state.statuses = await requestJson('/api/provider-keys');
            renderProviderCards();
            populateAddProviderSelect();
            renderProviderTabs();
            refreshChatOptions();
        }"""

if old_load_statuses in content:
    content = content.replace(old_load_statuses, new_load_statuses)
else:
    print("WARNING: old_load_statuses not found")

# 7. Add draggable properties to table rows
old_table_rows = """                    return `
                        <tr style="border-bottom: 1px solid var(--line);">
                            <td style="padding: 12px; font-weight: 500;">#${index + 1}</td>
                            <td style="padding: 12px;">${escapeHtml(item.provider)}</td>
                            <td style="padding: 12px; font-family: monospace;">${escapeHtml(item.model)}</td>
                            <td style="padding: 12px;">${statusHtml}</td>
                            <td style="padding: 12px;">${limitHtml}</td>
                            <td style="padding: 12px; text-align: center;">${actionHtml}</td>
                        </tr>
                    `;"""

new_table_rows = """                    return `
                        <tr style="border-bottom: 1px solid var(--line);" 
                            draggable="true"
                            ondragstart="rowDragStart(event, ${index})"
                            ondragover="rowDragOver(event)"
                            ondrop="rowDrop(event, ${index})"
                            ondragend="rowDragEnd(event)">
                            <td style="padding: 12px; font-weight: 500;">#${index + 1}</td>
                            <td style="padding: 12px;">${escapeHtml(item.provider)}</td>
                            <td style="padding: 12px; font-family: monospace;">${escapeHtml(item.model)}</td>
                            <td style="padding: 12px;">${statusHtml}</td>
                            <td style="padding: 12px;">${limitHtml}</td>
                            <td style="padding: 12px; text-align: center;">${actionHtml}</td>
                        </tr>
                    `;"""

if old_table_rows in content:
    content = content.replace(old_table_rows, new_table_rows)
else:
    print("WARNING: old_table_rows not found")

# 8. Add Drag-and-Drop JS handlers before fetchUsageStats
old_before_stats = """        async function fetchUsageStats() {"""

new_before_stats = """        let dragSrcIndex = null;
        window.rowDragStart = function(event, index) {
            dragSrcIndex = index;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', index);
            event.currentTarget.style.opacity = '0.4';
        };

        window.rowDragOver = function(event) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
        };

        window.rowDrop = async function(event, targetIndex) {
            event.preventDefault();
            event.currentTarget.style.opacity = '1.0';
            if (dragSrcIndex === null || dragSrcIndex === targetIndex) return;
            
            let newOrder = [...currentModelsOrder];
            const dragItem = newOrder[dragSrcIndex];
            
            newOrder.splice(dragSrcIndex, 1);
            newOrder.splice(targetIndex, 0, dragItem);
            
            try {
                const orderStrings = newOrder.map(i => `${i.provider}/${i.model}`);
                const res = await fetch('/api/manual-order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: orderStrings })
                });
                if (res.ok) {
                    showToast('排序已保存');
                    await fetchModelsStats();
                } else {
                    showToast('保存排序失败', 'error');
                }
            } catch (err) {
                console.error(err);
                showToast('网络错误，保存排序失败', 'error');
            }
        };
        
        window.rowDragEnd = function(event) {
            event.currentTarget.style.opacity = '1.0';
            dragSrcIndex = null;
        };

        async function fetchUsageStats() {"""

if old_before_stats in content:
    content = content.replace(old_before_stats, new_before_stats)
else:
    print("WARNING: old_before_stats not found")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("SUCCESSFULLY PATCHED INDEX.HTML")

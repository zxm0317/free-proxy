
        const PROVIDERS = [
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
        ];

        const state = {
            statuses: {},
            activeProvider: localStorage.getItem('py-ui-active-provider') || 'all',
            selectedProvider: localStorage.getItem('py-ui-selected-provider') || '',
            selectedModel: localStorage.getItem('py-ui-selected-model') || '',
            modelCache: {},
            verifyResults: {},
            editing: {},
        };

        let currentModelsOrder = [];
        const toast = document.getElementById('toast');
        const providerTabs = document.getElementById('providerTabs');
        const modelList = document.getElementById('modelList');
        const modelSearch = document.getElementById('modelSearch');
        const modelSummary = document.getElementById('modelSummary');
        const diagnosticsBox = document.getElementById('diagnosticsBox');
        const chatProvider = document.getElementById('chatProvider');
        const chatModel = document.getElementById('chatModel');
        const chatPrompt = document.getElementById('chatPrompt');
        const modelSuggestions = document.getElementById('modelSuggestions');

        function showToast(message, type = 'success') {
            toast.textContent = message;
            toast.className = `toast show ${type === 'success' ? '' : type}`.trim();
            window.clearTimeout(showToast.timer);
            showToast.timer = window.setTimeout(() => {
                toast.className = 'toast';
            }, 2600);
        }

        function escapeHtml(value) {
            return String(value || '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        }

        function configuredProviders() {
            return PROVIDERS.filter((provider) => state.statuses[provider.id] && state.statuses[provider.id].configured);
        }

        function persistSelection() {
            localStorage.setItem('py-ui-active-provider', state.activeProvider);
            localStorage.setItem('py-ui-selected-provider', state.selectedProvider || '');
            localStorage.setItem('py-ui-selected-model', state.selectedModel || '');
        }

        async function logout() {
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch (e) {}
            window.location.href = '/login';
        }

        async function checkInitialAuth() {
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
                window.location.href = '/login';
                return;
            }
            await bootstrap(false);
        }

        async function requestJson(url, options = {}) {
            const response = await fetch(url, options);
            
            if (response.status === 401) {
                logout();
            }
            
            let data = {};
            try {
                data = await response.json();
            } catch {
                data = {};
            }
            if (!response.ok) {
                const error = new Error(data.error || `HTTP ${response.status}`);
                error.payload = data;
                throw error;
            }
            return data;
        }

        async function loadPreferredModel() {
            try {
                const data = await requestJson('/api/preferred-model');
                if (data && data.provider && data.model) {
                    state.selectedProvider = data.provider;
                    state.selectedModel = data.model;
                    persistSelection();
                }
            } catch (error) {
                showToast(error.message || '加载已保存模型失败', 'error');
            }
        }

        function extractSseDataLine(part) {
            const lines = String(part || '').split('\n');
            for (const rawLine of lines) {
                const line = rawLine.trim();
                if (!line.startsWith('data:')) {
                    continue;
                }
                return line.slice(5).trim();
            }
            return '';
        }

        function extractChatTextFromJsonPayload(payload) {
            const choice = payload?.choices?.[0] || {};
            const message = choice?.message || {};
            const content = message?.content;
            if (typeof content === 'string' && content.trim()) {
                return content.trim();
            }
            if (typeof message?.reasoning_content === 'string' && message.reasoning_content.trim()) {
                return message.reasoning_content.trim();
            }
            if (Array.isArray(content)) {
                const merged = content
                    .map((item) => (item && typeof item.text === 'string' ? item.text.trim() : ''))
                    .filter(Boolean)
                    .join('\n')
                    .trim();
                if (merged) {
                    return merged;
                }
            }
            if (typeof choice?.text === 'string' && choice.text.trim()) {
                return choice.text.trim();
            }
            if (typeof payload?.content === 'string' && payload.content.trim()) {
                return payload.content.trim();
            }
            return '';
        }

        async function requestStream(url, options = {}, onChunk) {
            const response = await fetch(url, options);
            
            if (response.status === 401) {
                logout();
                throw new Error('鉴权失败，请重新登录');
            }
            const contentType = response.headers.get('content-type') || '';
            if (!response.ok) {
                let data = {};
                try {
                    data = await response.json();
                } catch {
                    data = {};
                }
                const error = new Error(data.error || `HTTP ${response.status}`);
                error.payload = data;
                throw error;
            }
            if (!contentType.includes('text/event-stream') || !response.body) {
                const rawText = await response.text();
                try {
                    const parsed = JSON.parse(rawText);
                    return extractChatTextFromJsonPayload(parsed) || rawText;
                } catch {
                    return rawText;
                }
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let finalText = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split('\n\n');
                buffer = parts.pop() || '';
                for (const part of parts) {
                    const data = extractSseDataLine(part);
                    if (!data) {
                        continue;
                    }
                    if (data === '[DONE]') {
                        continue;
                    }
                    try {
                        const parsed = JSON.parse(data);
                        const choice = parsed?.choices?.[0] || {};
                        const delta = choice.delta || {};
                        const text = delta.content || delta.reasoning_content || '';
                        if (text) {
                            finalText += text;
                            onChunk(text, finalText);
                        }
                    } catch {
                        continue;
                    }
                }
            }
            return finalText;
        }

        function setDiagnostics(kind, lines) {
            diagnosticsBox.className = `result-box ${kind}`;
            diagnosticsBox.textContent = lines.join('\n');
        }

        function maskCustomKey(key) {
            if (!key) return '无密钥';
            if (key.length <= 8) return '****';
            return key.slice(0, 4) + '...' + key.slice(-4);
        }

        function renderProviderCards() {
            const baseUrl = window.location.protocol + '//' + window.location.host + '/v1';
            const baseEl = document.getElementById('proxyBaseUrlDisplay');
            if (baseEl) baseEl.textContent = baseUrl;

            const listContainer = document.getElementById('keysConfiguredProvidersList');
            if (!listContainer) return;

            const configuredStandard = PROVIDERS.filter(p => state.statuses[p.id]?.configured);
            let html = '';

            for (const provider of configuredStandard) {
                const status = state.statuses[provider.id];
                const verify = state.verifyResults[provider.id];
                const isEditing = Boolean(state.editing[provider.id]);
                
                let statusDotClass = 'keys-status-dot';
                let statusText = '未验证';
                let verifyTimeStr = '';
                if (verify) {
                    if (verify.ok) {
                        statusDotClass += ' healthy';
                        statusText = '健康';
                    } else {
                        statusDotClass += ' error';
                        statusText = '错误';
                    }
                    const now = new Date();
                    verifyTimeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
                }

                html += `
                    <div class="keys-provider-group">
                        <div class="keys-provider-header">
                            <div class="keys-provider-info">
                                <label class="keys-switch">
                                    <input type="checkbox" ${status.enabled ? 'checked' : ''} onchange="toggleProviderEnabled('${provider.id}', this.checked)">
                                    <span class="keys-slider"></span>
                                </label>
                                <span class="keys-provider-name">${provider.name}</span>
                                <a class="keys-provider-link" href="${provider.keyUrl}" target="_blank" rel="noreferrer">
                                    获取 API 密钥
                                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
                                </a>
                            </div>
                            <span class="keys-provider-count">1 钥匙</span>
                        </div>
                        <div class="keys-provider-keys-container">
                            ${isEditing ? `
                                <div class="keys-key-row" style="gap: 8px; width: 100%;">
                                    <input type="password" class="field" id="keys-edit-input-${provider.id}" placeholder="输入新的 API Key" autocomplete="off" style="flex: 1; font-size: 12px; padding: 6px 10px;">
                                    <button class="btn btn-primary" onclick="saveEditedProviderKey('${provider.id}', this)" style="padding: 4px 10px; font-size: 11px; border-radius: 6px;">保存</button>
                                    <button class="btn btn-ghost" onclick="cancelEditProviderKey('${provider.id}')" style="padding: 4px 10px; font-size: 11px; border-radius: 6px;">取消</button>
                                </div>
                            ` : `
                                <div class="keys-key-row">
                                    <span class="${statusDotClass}"></span>
                                    <span class="keys-key-masked">${escapeHtml(status.masked || '••••••••')}</span>
                                    <span class="keys-key-status-text">${statusText}</span>
                                    <span class="keys-key-time">${verifyTimeStr}</span>
                                    <div class="keys-key-actions">
                                        <button onclick="verifyProviderDirectly('${provider.id}', this)">验证</button>
                                        <button onclick="editProviderKey('${provider.id}')">编辑</button>
                                        <button class="remove" onclick="removeProviderKey('${provider.id}')">清除</button>
                                    </div>
                                </div>
                            `}
                        </div>
                    </div>
                `;
            }

            if (customModels && customModels.length > 0) {
                html += `
                    <div class="keys-provider-group">
                        <div class="keys-provider-header">
                            <div class="keys-provider-info">
                                <span class="keys-provider-name" style="margin-left: 4px;">自定义 (OpenAI 兼容)</span>
                            </div>
                            <span class="keys-provider-count">${customModels.length} 钥匙</span>
                        </div>
                        <div class="keys-provider-keys-container">
                `;
                
                for (const custom of customModels) {
                    const isEditing = Boolean(state.editing[custom.id]);
                    const isEnabled = custom.enabled !== false;
                    const statusDotClass = isEnabled ? 'keys-status-dot healthy' : 'keys-status-dot';
                    
                    if (isEditing) {
                        html += `
                            <div class="keys-key-row" style="gap: 8px; width: 100%;">
                                <input type="password" class="field" id="keys-edit-custom-input-${custom.id}" placeholder="输入新的 API Key" autocomplete="off" style="flex: 1; font-size: 12px; padding: 6px 10px;">
                                <button class="btn btn-primary" onclick="saveEditedCustomKey('${custom.id}', this)" style="padding: 4px 10px; font-size: 11px; border-radius: 6px;">保存</button>
                                <button class="btn btn-ghost" onclick="cancelEditCustomKey('${custom.id}')" style="padding: 4px 10px; font-size: 11px; border-radius: 6px;">取消</button>
                            </div>
                        `;
                    } else {
                        html += `
                            <div class="keys-key-row">
                                <label class="keys-switch" style="margin-right: 8px;">
                                    <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleProviderEnabled('${custom.id}', this.checked)">
                                    <span class="keys-slider"></span>
                                </label>
                                <span class="${statusDotClass}"></span>
                                <span class="keys-key-masked">${maskCustomKey(custom.api_key)}</span>
                                <span class="keys-key-label" style="font-size: 12px; font-weight: 500;">${escapeHtml(custom.display_name || custom.model)}</span>
                                <span class="keys-key-status-text" style="font-size: 11px; opacity: 0.8; font-family: monospace;">${escapeHtml(custom.base_url)}</span>
                                <div class="keys-key-actions">
                                    <button onclick="editCustomModelKey('${custom.id}')">编辑</button>
                                    <button class="remove" onclick="removeCustomModel('${custom.id}')">清除</button>
                                </div>
                            </div>
                        `;
                    }
                }
                html += `
                        </div>
                    </div>
                `;
            }

            if (html === '') {
                html = '<div style="text-align: center; padding: 30px; color: var(--muted); background: #fafafa; border-radius: 12px; border: 1px dashed var(--line);">当前未配置任何提供商。请在上方添加一个以开始。</div>';
            }
            listContainer.innerHTML = html;
        }

        function populateAddProviderSelect() {
            const select = document.getElementById('keys-platform-select');
            if (!select) return;
            const available = PROVIDERS.filter(p => !state.statuses[p.id]?.configured);
            if (available.length === 0) {
                select.innerHTML = '<option value="">所有提供商已配置</option>';
                select.disabled = true;
            } else {
                select.innerHTML = available.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
                select.disabled = false;
            }
        }

        function renderProviderTabs() {
            const configured = configuredProviders();
            const tabs = [{ id: 'all', name: '全部已配置' }, ...configured.map((provider) => ({ id: provider.id, name: provider.name }))];

            if (!configured.length) {
                providerTabs.innerHTML = '';
                modelSummary.textContent = '先配置至少一个 provider，再加载模型列表。';
                modelList.innerHTML = '<div class="empty">当前没有可浏览的模型。</div>';
                return;
            }

            if (state.activeProvider !== 'all' && !configured.some((provider) => provider.id === state.activeProvider)) {
                state.activeProvider = 'all';
            }

            providerTabs.innerHTML = tabs.map((tab) => `
                <button class="tab ${tab.id === state.activeProvider ? 'active' : ''}" data-tab="${tab.id}">${tab.name}</button>
            `).join('');
        }

        function normalizeModels(providerId, recommendedItems, allModels) {
            const recommendedSet = new Set(recommendedItems || []);
            const merged = [];
            const seen = new Set();

            for (const model of [...(recommendedItems || []), ...(allModels || [])]) {
                if (!model || seen.has(model)) {
                    continue;
                }
                seen.add(model);
                merged.push({
                    provider: providerId,
                    id: model,
                    name: model,
                    isRecommended: recommendedSet.has(model),
                });
            }

            return merged;
        }

        async function loadModelsForProvider(providerId, force = false) {
            if (!force && state.modelCache[providerId] && state.modelCache[providerId].items) {
                return state.modelCache[providerId].items;
            }

            state.modelCache[providerId] = { loading: true, items: [], error: '' };

            const recommendedPromise = requestJson(`/api/providers/${providerId}/models/recommended`).catch((error) => ({ error }));
            const allModelsPromise = requestJson(`/providers/${providerId}/models`).catch((error) => ({ error }));

            const [recommendedData, allModelsData] = await Promise.all([recommendedPromise, allModelsPromise]);
            const recommendedItems = recommendedData && !recommendedData.error ? (recommendedData.items || []) : [];
            const allModels = allModelsData && !allModelsData.error ? (allModelsData.models || []) : [];
            const errorMessage = recommendedData.error?.message || allModelsData.error?.message || '';

            const items = normalizeModels(providerId, recommendedItems, allModels);
            state.modelCache[providerId] = {
                loading: false,
                items,
                error: errorMessage,
                recommendedCount: recommendedItems.length,
                totalCount: items.length,
            };
            return items;
        }

        async function ensureVisibleModels(force = false) {
            const configured = configuredProviders();
            if (!configured.length) {
                renderProviderTabs();
                return;
            }

            if (state.activeProvider === 'all') {
                await Promise.all(configured.map((provider) => loadModelsForProvider(provider.id, force)));
            } else {
                await loadModelsForProvider(state.activeProvider, force);
            }
            renderModelSection();
            refreshChatOptions();
        }

        function visibleModels() {
            const search = modelSearch.value.trim().toLowerCase();
            const items = state.activeProvider === 'all'
                ? configuredProviders().flatMap((provider) => (state.modelCache[provider.id]?.items || []))
                : (state.modelCache[state.activeProvider]?.items || []);

            const deduped = [];
            const seen = new Set();
            for (const item of items) {
                const key = `${item.provider}:${item.id}`;
                if (!seen.has(key)) {
                    seen.add(key);
                    deduped.push(item);
                }
            }

            deduped.sort((left, right) => {
                if (left.isRecommended !== right.isRecommended) {
                    return left.isRecommended ? -1 : 1;
                }
                if (left.provider !== right.provider) {
                    return left.provider.localeCompare(right.provider);
                }
                return left.id.localeCompare(right.id);
            });

            if (!search) {
                return deduped;
            }

            return deduped.filter((item) => {
                const haystack = `${item.provider} ${item.id}`.toLowerCase();
                return haystack.includes(search);
            });
        }

        function renderModelSection() {
            renderProviderTabs();
            const models = visibleModels();
            const configured = configuredProviders();

            if (!configured.length) {
                return;
            }

            if (state.activeProvider === 'all') {
                const total = configured.reduce((sum, provider) => sum + (state.modelCache[provider.id]?.items || []).length, 0);
                modelSummary.textContent = `已汇总 ${configured.length} 个已配置 provider，共 ${total} 个候选模型。推荐模型会排在前面。`;
            } else {
                const cache = state.modelCache[state.activeProvider];
                if (cache && cache.error && cache.items.length === 0) {
                    modelSummary.textContent = `${state.activeProvider} 模型列表加载失败：${cache.error}`;
                } else if (cache) {
                    modelSummary.textContent = `${state.activeProvider} 当前有 ${cache.totalCount || 0} 个候选模型，其中 ${cache.recommendedCount || 0} 个来自推荐列表。`;
                }
            }

            if (!models.length) {
                modelList.innerHTML = '<div class="empty">当前筛选条件下没有模型。</div>';
                return;
            }

            modelList.innerHTML = models.map((item) => {
                const isSelected = state.selectedProvider === item.provider && state.selectedModel === item.id;
                const chooseLabel = isSelected ? '已选择' : '选为测试模型';
                const chooseClass = isSelected ? 'btn btn-ghost' : 'btn btn-primary';
                const chooseDisabled = isSelected ? 'disabled' : '';
                return `
                    <div class="model-item ${isSelected ? 'selected' : ''}">
                        <div class="model-meta">
                            <div class="model-name">${escapeHtml(item.id)}</div>
                            <div class="model-id">provider: ${escapeHtml(item.provider)}</div>
                            <div class="model-tags">
                                ${item.isRecommended ? '<span class="tag recommended">推荐</span>' : ''}
                                ${isSelected ? '<span class="tag selected">已选择</span>' : ''}
                            </div>
                        </div>
                        <div class="model-actions">
                            <button class="btn btn-secondary" data-action="probe-model" data-provider="${item.provider}" data-model="${escapeHtml(item.id)}">探测</button>
                            <button class="${chooseClass}" data-action="choose-model" data-provider="${item.provider}" data-model="${escapeHtml(item.id)}" ${chooseDisabled}>${chooseLabel}</button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function refreshChatOptions() {
            const configured = configuredProviders();
            chatProvider.innerHTML = configured.length
                ? configured.map((provider) => `<option value="${provider.id}">${provider.name}</option>`).join('')
                : '<option value="">暂无已配置 provider</option>';

            if (state.selectedProvider && configured.some((provider) => provider.id === state.selectedProvider)) {
                chatProvider.value = state.selectedProvider;
            } else if (configured.length) {
                chatProvider.value = configured[0].id;
            }

            if (state.selectedModel) {
                chatModel.value = state.selectedModel;
            }

            renderModelSuggestions();
        }

        function renderModelSuggestions() {
            const providerId = chatProvider.value;
            const items = providerId ? (state.modelCache[providerId]?.items || []) : [];
            modelSuggestions.innerHTML = items.map((item) => `<option value="${escapeHtml(item.id)}"></option>`).join('');
        }

        async function loadProxyKey() {
            try {
                const data = await requestJson('/api/proxy-key');
                const display = document.getElementById('proxyKeyDisplay');
                if (data && data.key) {
                    display.value = data.key;
                    display.dataset.key = data.key;
                } else {
                    display.value = '';
                    display.dataset.key = '';
                }
            } catch (error) {
                console.error('Failed to load proxy key', error);
            }
        }

        async function generateProxyKey() {
            try {
                const data = await requestJson('/api/proxy-key/generate', { method: 'POST' });
                const display = document.getElementById('proxyKeyDisplay');
                if (data && data.key) {
                    display.value = data.key;
                    display.dataset.key = data.key;
                    showToast('Proxy API Key 已重新生成', 'success');
                }
            } catch (error) {
                showToast(error.message || '生成失败', 'error');
            }
        }

        function copyProxyKey() {
            const display = document.getElementById('proxyKeyDisplay');
            if (!display.value) {
                showToast('尚未生成 Key', 'error');
                return;
            }
            navigator.clipboard.writeText(display.value).then(() => {
                showToast('已复制到剪贴板', 'success');
            }).catch(() => {
                showToast('复制失败', 'error');
            });
        }

        let customModels = [];
        async function loadStatuses() {
            try {
                const [statuses, custom] = await Promise.all([
                    requestJson('/api/provider-keys'),
                    requestJson('/api/custom-models').catch(() => [])
                ]);
                state.statuses = statuses;
                allProviderStatuses = statuses;
                customModels = custom || [];
            } catch (err) {
                console.error("Failed to load statuses", err);
            }
            renderProviderCards();
            populateAddProviderSelect();
            renderProviderTabs();
            refreshChatOptions();
            renderUnconfiguredFooter();
        }

        let proxyKeyVisible = false;
        window.toggleProxyKeyShow = function() {
            const display = document.getElementById('proxyKeyDisplay');
            const btn = document.getElementById('toggleProxyKeyShowBtn');
            if (!display || !btn) return;
            proxyKeyVisible = !proxyKeyVisible;
            if (proxyKeyVisible) {
                display.type = 'text';
                btn.textContent = '隐藏';
            } else {
                display.type = 'password';
                btn.textContent = '展示';
            }
        };

        window.handleAddProviderKeySubmit = async function() {
            const platform = document.getElementById('keys-platform-select').value;
            const apiKey = document.getElementById('keys-api-key-input').value.trim();
            if (!platform) {
                showToast('请选择平台', 'error');
                return;
            }
            if (!apiKey) {
                showToast('请输入 API Key', 'error');
                return;
            }
            try {
                await requestJson(`/api/provider-keys/${platform}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey }),
                });
                document.getElementById('keys-api-key-input').value = '';
                document.getElementById('keys-label-input').value = '';
                showToast('API Key 已成功添加', 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (err) {
                showToast(err.message || '添加失败', 'error');
            }
        };

        window.handleAddCustomModelSubmit = async function() {
            const baseUrl = document.getElementById('keys-custom-url').value.trim();
            const model = document.getElementById('keys-custom-model').value.trim();
            const displayName = document.getElementById('keys-custom-name').value.trim();
            const apiKey = document.getElementById('keys-custom-key').value.trim();
            if (!baseUrl || !model) {
                showToast('请输入基本 URL 和模型名称', 'error');
                return;
            }
            try {
                await requestJson('/api/custom-models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ base_url: baseUrl, model: model, display_name: displayName, api_key: apiKey })
                });
                document.getElementById('keys-custom-model').value = '';
                document.getElementById('keys-custom-name').value = '';
                document.getElementById('keys-custom-key').value = '';
                showToast('自定义模型已添加', 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (err) {
                showToast(err.message || '添加失败', 'error');
            }
        };

        window.editProviderKey = function(id) { state.editing[id] = true; renderProviderCards(); };
        window.cancelEditProviderKey = function(id) { state.editing[id] = false; renderProviderCards(); };
        window.saveEditedProviderKey = async function(id, btn) {
            const input = document.getElementById(`keys-edit-input-${id}`);
            const apiKey = (input && input.value || '').trim();
            if (!apiKey) { showToast('请输入 API Key', 'error'); return; }
            btn.disabled = true;
            try {
                await requestJson(`/api/provider-keys/${id}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey }),
                });
                state.editing[id] = false;
                showToast('API Key 已更新', 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (err) { showToast(err.message || '修改失败', 'error'); }
            finally { btn.disabled = false; }
        };
        window.removeProviderKey = async function(id) {
            if (!confirm(`确定要清除 ${id} 的 Key 吗？`)) return;
            try {
                await requestJson(`/api/provider-keys/${id}`, { method: 'DELETE' });
                showToast('已清除 Key', 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (err) { showToast(err.message || '清除失败', 'error'); }
        };

        window.editCustomModelKey = function(id) { state.editing[id] = true; renderProviderCards(); };
        window.cancelEditCustomKey = function(id) { state.editing[id] = false; renderProviderCards(); };
        window.saveEditedCustomKey = async function(id, btn) {
            const input = document.getElementById(`keys-edit-custom-input-${id}`);
            const apiKey = (input && input.value || '').trim();
            btn.disabled = true;
            try {
                await requestJson(`/api/custom-models/${id}/key`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey }),
                });
                state.editing[id] = false;
                showToast('自定义模型 Key 已更新', 'success');
                await loadStatuses();
            } catch (err) { showToast(err.message || '修改失败', 'error'); }
            finally { btn.disabled = false; }
        };
        window.removeCustomModel = async function(id) {
            if (!confirm('确定要删除该自定义模型吗？')) return;
            try {
                await requestJson(`/api/custom-models/${id}`, { method: 'DELETE' });
                showToast('已删除自定义模型', 'success');
                await loadStatuses();
            } catch (err) { showToast(err.message || '删除失败', 'error'); }
        };

        window.toggleProviderEnabled = async function(id, checked) {
            try {
                await requestJson(`/api/providers/${id}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: checked })
                });
                showToast(checked ? '已启用' : '已禁用', 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (err) {
                showToast(err.message || '操作失败', 'error');
            }
        };

        window.verifyProviderDirectly = async function(id, btn) {
            btn.disabled = true;
            const originText = btn.textContent;
            btn.textContent = '验证中...';
            try {
                const data = await requestJson(`/api/provider-keys/${id}/verify`, { method: 'POST' });
                state.verifyResults[id] = data;
                showToast(`${id} 验证成功`, 'success');
                await loadStatuses();
                await ensureVisibleModels(true);
            } catch (error) {
                state.verifyResults[id] = error.payload || {};
                showToast(`${id} 验证失败`, 'error');
                await loadStatuses();
            } finally {
                btn.disabled = false;
                btn.textContent = originText;
            }
        };

        window.checkAllProviders = async function() {
            const configured = PROVIDERS.filter(p => state.statuses[p.id]?.configured);
            if (configured.length === 0) {
                showToast('未配置任何提供商', 'error');
                return;
            }
            showToast('开始验证所有提供商...', 'info');
            for (const p of configured) {
                try {
                    const data = await requestJson(`/api/provider-keys/${p.id}/verify`, { method: 'POST' });
                    state.verifyResults[p.id] = data;
                } catch (error) {
                    state.verifyResults[p.id] = error.payload || { ok: false, error: error.message };
                }
            }
            showToast('所有提供商验证完成', 'success');
            renderProviderCards();
        };

        function buildDiagnosticLines(actionLabel, providerId, modelId, payload, fallbackMessage) {
            const ok = payload && typeof payload.ok === 'boolean' ? payload.ok : null;
            return [
                `动作: ${actionLabel}`,
                providerId ? `Provider: ${providerId}` : '',
                modelId ? `Model: ${modelId}` : '',
                ok === null ? '' : `结果: ${ok ? '成功' : '失败'}`,
                payload && payload.note ? `说明: ${payload.note}` : '',
                payload && payload.error ? `错误: ${payload.error}` : '',
                payload && payload.category ? `分类: ${payload.category}` : '',
                payload && payload.status ? `HTTP: ${payload.status}` : '',
                payload && payload.verified_model ? `验证通过模型: ${payload.verified_model}` : '',
                payload && payload.actual_model ? `实际调用模型: ${payload.actual_model}` : '',
                payload && payload.content ? `返回内容: ${payload.content}` : '',
                payload && payload.suggestion ? `建议: ${payload.suggestion}` : '',
                !payload?.suggestion && fallbackMessage ? `建议: ${fallbackMessage}` : '',
            ].filter(Boolean);
        }

        async function saveProviderKey(providerId, button) {
            const input = document.getElementById(`key-${providerId}`);
            const apiKey = (input && input.value || '').trim();
            if (!apiKey) {
                showToast('请输入 API Key', 'error');
                return;
            }

            button.disabled = true;
            button.textContent = '保存中...';
            try {
                const data = await requestJson(`/api/provider-keys/${providerId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey }),
                });
                state.editing[providerId] = false;
                await loadStatuses();
                state.activeProvider = providerId;
                persistSelection();
                showToast(`${providerId} 已保存`);
                setDiagnostics('status-success', buildDiagnosticLines('保存 Provider Key', providerId, '', { ok: true }, '点击“验证”确认这个 Key 真的可以调用模型。'));
                await ensureVisibleModels(true);
            } catch (error) {
                showToast(error.message || '保存失败', 'error');
                setDiagnostics('status-error', buildDiagnosticLines('保存 Provider Key', providerId, '', { ok: false, error: error.message || '未知错误' }, '检查输入格式后重试。'));
            } finally {
                button.disabled = false;
                button.textContent = '保存';
            }
        }

        async function verifyProvider(providerId, button) {
            if (button) {
                button.disabled = true;
                button.textContent = '验证中...';
            }

            try {
                const data = await requestJson(`/api/provider-keys/${providerId}/verify`, { method: 'POST' });
                state.verifyResults[providerId] = data;
                renderProviderCards();
                showToast(`${providerId} 验证成功`);
                setDiagnostics('status-success', buildDiagnosticLines('验证 Provider', providerId, '', { ...data, ok: true }, '继续从推荐模型里选择一个模型做探测或聊天验证。'));
                await ensureVisibleModels(true);
            } catch (error) {
                const payload = error.payload || {};
                state.verifyResults[providerId] = payload;
                renderProviderCards();
                showToast(`${providerId} 验证失败`, 'error');
                setDiagnostics('status-error', buildDiagnosticLines('验证 Provider', providerId, '', {
                    ok: false,
                    error: payload.error || error.message || '未知错误',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '检查 Key 是否有效，或切换到该 provider 的推荐模型重试。'));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = '验证';
                }
            }
        }

        async function chooseModel(providerId, modelId) {
            state.selectedProvider = providerId;
            state.selectedModel = modelId;
            state.activeProvider = providerId;
            chatProvider.value = providerId;
            chatModel.value = modelId;
            persistSelection();
            renderModelSection();
            refreshChatOptions();
            showToast(`已选择 ${providerId} / ${modelId}`);
            setDiagnostics('status-info', buildDiagnosticLines('选择验证模型', providerId, modelId, { ok: true }, '现在可以直接探测，或发送一条真实聊天请求。'));
            try {
                await requestJson('/api/preferred-model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerId, model: modelId }),
                });
            } catch (error) {
                showToast(error.message || '保存已选模型失败', 'error');
            }
        }

        async function probeModel(providerId, modelId, button) {
            if (button) {
                button.disabled = true;
                button.textContent = '探测中...';
            }
            try {
                const data = await requestJson(`/providers/${providerId}/probe`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: modelId }),
                });
                await chooseModel(providerId, modelId);
                showToast('模型探测成功');
                setDiagnostics('status-success', buildDiagnosticLines('探测模型', providerId, modelId, { ...data, ok: true }, '模型可用，可以继续发送聊天请求。'));
            } catch (error) {
                const payload = error.payload || {};
                showToast('模型探测失败', 'error');
                setDiagnostics('status-error', buildDiagnosticLines('探测模型', providerId, modelId, {
                    ok: false,
                    error: payload.error || error.message || '未知错误',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '优先切换到推荐模型，或先重新验证该 provider。'));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = '探测';
                }
            }
        }

        async function probeSelectedModel(button) {
            const providerId = chatProvider.value.trim();
            const modelId = chatModel.value.trim();
            if (!providerId || !modelId) {
                showToast('请先选择 provider 和 model', 'error');
                return;
            }
            await probeModel(providerId, modelId, button);
        }

        async function sendChat(button) {
            const providerId = chatProvider.value.trim();
            const modelId = chatModel.value.trim();
            const prompt = chatPrompt.value.trim();
            if (!providerId || !modelId) {
                showToast('请先选择 provider 和 model', 'error');
                return;
            }

            button.disabled = true;
            button.textContent = '流式发送中...';
            setDiagnostics('status-info', [
                '动作: 聊天测试',
                `Provider: ${providerId}`,
                `Model: ${modelId}`,
                '结果: 进行中',
                '说明: 已发送请求，正在等待模型首个流式响应。',
            ]);
            try {
                let content = '';
                const data = await requestStream('/chat/completions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerId, model: modelId, prompt: prompt || 'hello', stream: true }),
                }, (chunk, fullText) => {
                    content = fullText;
                    setDiagnostics('status-info', buildDiagnosticLines('聊天测试', providerId, modelId, {
                        ok: true,
                        note: '模型已开始流式响应',
                        content: content,
                        actual_model: modelId,
                    }, '结果仍在生成中。'));
                });
                await chooseModel(providerId, modelId);
                setDiagnostics('status-success', buildDiagnosticLines('聊天测试', providerId, modelId, { ok: true, content: data || content, actual_model: modelId }, '结果已返回，可以继续调整 prompt 复测。'));
            } catch (error) {
                const payload = error.payload || {};
                setDiagnostics('status-error', buildDiagnosticLines('聊天测试', providerId, modelId, {
                    ok: false,
                    error: payload.error || error.message || '未知错误',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '先执行一次探测，确认当前 provider 和 model 可以真实调用。'));
            } finally {
                button.disabled = false;
                button.textContent = '发送测试请求';
            }
        }

        providerGrid.addEventListener('click', async (event) => {
            const button = event.target.closest('button[data-action]');
            if (!button) {
                return;
            }

            const action = button.dataset.action;
            const providerId = button.dataset.provider;
            if (!providerId) {
                return;
            }

            if (action === 'save') {
                await saveProviderKey(providerId, button);
                return;
            }
            if (action === 'verify') {
                await verifyProvider(providerId, button);
                return;
            }
            if (action === 'edit') {
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
            }
        });

        providerTabs.addEventListener('click', async (event) => {
            const button = event.target.closest('button[data-tab]');
            if (!button) {
                return;
            }
            state.activeProvider = button.dataset.tab;
            persistSelection();
            await ensureVisibleModels(false);
        });

        modelList.addEventListener('click', async (event) => {
            const button = event.target.closest('button[data-action]');
            if (!button) {
                return;
            }
            const providerId = button.dataset.provider;
            const modelId = button.dataset.model;
            if (!providerId || !modelId) {
                return;
            }
            if (button.dataset.action === 'choose-model') {
                await chooseModel(providerId, modelId);
                return;
            }
            if (button.dataset.action === 'probe-model') {
                await probeModel(providerId, modelId, button);
            }
        });

        modelSearch.addEventListener('input', () => {
            renderModelSection();
        });

        chatProvider.addEventListener('change', () => {
            renderModelSuggestions();
            if (!state.selectedProvider || state.selectedProvider !== chatProvider.value) {
                const firstModel = state.modelCache[chatProvider.value]?.items?.[0]?.id || '';
                if (firstModel && !chatModel.value) {
                    chatModel.value = firstModel;
                }
            }
        });

        document.getElementById('refreshModelsBtn').addEventListener('click', async () => {
            await ensureVisibleModels(true);
            showToast('模型列表已刷新');
        });

        document.getElementById('probeSelectedBtn').addEventListener('click', async (event) => {
            await probeSelectedModel(event.currentTarget);
        });

        document.getElementById('sendChatBtn').addEventListener('click', async (event) => {
            await sendChat(event.currentTarget);
        });

        document.getElementById('fillProbePromptBtn').addEventListener('click', () => {
            chatPrompt.value = 'Reply with exactly OK';
        });



        async function bootstrap(forceModels = false) {
            await loadProxyKey();
            await loadStatuses();
            await loadPreferredModel();
            await ensureVisibleModels(forceModels);
            
            // Fire and forget stats loading to avoid blocking the main UI
            fetchUsageStats();
            fetchModelsStats();
            
            setDiagnostics('status-info', [
                '动作: 页面初始化',
                `结果: 已加载 ${configuredProviders().length} 个已配置 provider`,
                configuredProviders().length ? '建议: 先点“验证”，再选择推荐模型做探测或聊天验证。' : '建议: 先配置至少一个 provider 的 API Key。',
            ]);
        }


        let localModelsOrder = null;
        let currentStrategy = 'priority';
        let allProviderStatuses = {};

        const platformColors = {
            google:      '#4285f4',
            groq:        '#f55036',
            cerebras:    '#8b5cf6',
            sambanova:   '#14b8a6',
            nvidia:      '#76b900',
            mistral:     '#f59e0b',
            openrouter:  '#ec4899',
            github:      '#6e7b8b',
            cohere:      '#d946ef',
            cloudflare:  '#f38020',
            zhipu:       '#06b6d4',
            ollama:      '#000000',
            kilo:        '#7c3aed',
            pollinations: '#a855f7',
            llm7:        '#0ea5e9',
            huggingface: '#ff9d00',
            opencode:    '#2563eb'
        };

        const STRATEGIES = {
            'priority': {
                label: '手动',
                blurb: '手动模式：请求按以下顺序排列，从上到下。拖动即可重新排序。',
                weights: null
            },
            'balanced': {
                label: '均衡',
                blurb: '均衡模式：可靠性主导 (50%)，速度与智力平分秋色 (各占 25%)。通用默认推荐。',
                weights: { reliability: 0.5, speed: 0.25, intelligence: 0.25 }
            },
            'smartest': {
                label: '最聪明的',
                blurb: '最聪明模式：优先选用最聪明的可用模型。智力占 55%，可靠性占 35%，速度占 10%。',
                weights: { reliability: 0.35, speed: 0.1, intelligence: 0.55 }
            },
            'fastest': {
                label: '最快',
                blurb: '最快模式：优先选用响应最快的可用模型。速度占 55%，可靠性占 35%，智力占 10%。',
                weights: { reliability: 0.35, speed: 0.55, intelligence: 0.1 }
            },
            'reliable': {
                label: '最可靠',
                blurb: '最可靠模式：最大限度保障成功率。可靠性占 70%，速度与智力各占 15%。',
                weights: { reliability: 0.7, speed: 0.15, intelligence: 0.15 }
            }
        };

        window.setStrategy = function(strat) {
            currentStrategy = strat;
            localModelsOrder = null;
            document.getElementById('floatingSaveBar').style.display = 'none';

            document.querySelectorAll('.strategy-btn').forEach(btn => {
                if (btn.id.startsWith('strat-')) {
                    btn.classList.remove('active');
                }
            });
            const activeBtn = document.getElementById(`strat-${strat}`);
            if (activeBtn) {
                activeBtn.classList.add('active');
            }

            const info = STRATEGIES[strat];
            const weightsSpan = document.getElementById('strategyWeights');
            const blurbP = document.getElementById('strategyBlurb');
            if (blurbP) {
                blurbP.textContent = info.blurb;
            }
            if (weightsSpan) {
                if (info.weights) {
                    weightsSpan.textContent = `可靠性 ${Math.round(info.weights.reliability * 100)}% · 速度 ${Math.round(info.weights.speed * 100)}% · 智力 ${Math.round(info.weights.intelligence * 100)}%`;
                } else {
                    weightsSpan.textContent = '';
                }
            }

            renderModelsStatsTable();
        };

        window.toggleModelEnabled = async function(modelId, checked) {
            try {
                const res = await fetch(`/api/models/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model_id: modelId, enabled: checked })
                });
                if (res.ok) {
                    showToast(checked ? '模型已启用' : '模型已禁用');
                    await fetchModelsStats();
                    if (typeof loadStatuses === 'function') {
                        await loadStatuses();
                    }
                } else {
                    showToast('切换状态失败', 'error');
                }
            } catch (err) {
                console.error(err);
                showToast('网络错误，切换失败', 'error');
            }
        };

        window.discardManualChanges = function() {
            localModelsOrder = null;
            document.getElementById('floatingSaveBar').style.display = 'none';
            renderModelsStatsTable();
            showToast('已放弃更改');
        };

        window.saveManualChanges = async function() {
            if (!localModelsOrder) return;
            try {
                const orderStrings = localModelsOrder.map(i => `${i.provider}/${i.model}`);
                const res = await fetch('/api/manual-order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: orderStrings })
                });
                if (res.ok) {
                    showToast('排序已保存');
                    localModelsOrder = null;
                    document.getElementById('floatingSaveBar').style.display = 'none';
                    await fetchModelsStats();
                } else {
                    showToast('保存排序失败', 'error');
                }
            } catch (err) {
                console.error(err);
                showToast('网络错误，保存排序失败', 'error');
            }
        };

        let dragSrcIndex = null;
        window.rowDragStart = function(event, index) {
            if (currentStrategy !== 'priority') {
                event.preventDefault();
                return;
            }
            dragSrcIndex = index;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', index);
            event.currentTarget.style.opacity = '0.4';
        };

        window.rowDragOver = function(event) {
            if (currentStrategy !== 'priority') return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
        };

        window.rowDrop = function(event, targetIndex) {
            if (currentStrategy !== 'priority') return;
            event.preventDefault();
            event.currentTarget.style.opacity = '1.0';
            if (dragSrcIndex === null || dragSrcIndex === targetIndex) return;
            
            let displayList = localModelsOrder ? [...localModelsOrder] : [...currentModelsOrder];
            const dragItem = displayList[dragSrcIndex];
            displayList.splice(dragSrcIndex, 1);
            displayList.splice(targetIndex, 0, dragItem);
            
            localModelsOrder = displayList;
            document.getElementById('floatingSaveBar').style.display = 'flex';
            renderModelsStatsTable();
        };
        
        window.rowDragEnd = function(event) {
            event.currentTarget.style.opacity = '1.0';
            dragSrcIndex = null;
        };

        async function fetchUsageStats() {
            const tbody = document.getElementById('usageStatsBody');
            try {
                const response = await fetch('/api/usage-stats');
                if (response.status === 401) {
                    showToast('未登录或凭证无效，请重新登录', 'error');
                    window.location.href = '/login';
                    return;
                }
                const data = await response.json();
                if (!data || !data.stats || data.stats.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" style="padding: 12px; text-align: center; color: var(--muted);">暂无模型调用数据</td></tr>';
                    return;
                }
                let html = '';
                for (const row of data.stats) {
                    html += `<tr style="border-bottom: 1px solid var(--line);"><td style="padding: 12px; font-weight: 600; color: var(--primary);">${escapeHtml(row.provider)}</td><td style="padding: 12px; font-family: monospace;">${escapeHtml(row.model)}</td><td style="padding: 12px;">${escapeHtml(String(row.usage_count))}</td></tr>`;
                }
                tbody.innerHTML = html;
            } catch (error) {
                console.error(error);
                tbody.innerHTML = '<tr><td colspan="3" style="padding: 12px; text-align: center; color: var(--danger);">加载失败</td></tr>';
            }
        }

        async function fetchModelsStats() {
            try {
                const res = await fetch('/api/models-stats');
                const data = await res.json();
                if (!data || !data.models) return;
                currentModelsOrder = data.models;
                renderModelsStatsTable();
            } catch (err) {
                console.error('fetchModelsStats error:', err);
            }
        }

        function calculateScore(item, strategyKey) {
            if (strategyKey === 'priority') return 0;
            const weights = STRATEGIES[strategyKey].weights;
            if (!weights) return 0;
            const rel = (item.rel || 0) / 100;
            const spd = (item.spd || 0) / 100;
            const intel = (item.int || 0) / 100;
            const headroom = item.headroom !== undefined ? item.headroom : 1.0;
            
            const base = (weights.reliability * rel + weights.speed * spd + weights.intelligence * intel);
            return base * headroom;
        }

        function getTokensValue(budgetStr) {
            if (!budgetStr) return 0;
            const val = parseFloat(budgetStr.replace(/[^\d.]/g, ''));
            if (isNaN(val)) return 0;
            if (budgetStr.toUpperCase().includes('M')) return val * 1000000;
            if (budgetStr.toUpperCase().includes('K')) return val * 1000;
            return val;
        }

        function formatTokensChinese(n) {
            if (n >= 100000000) return `${(n / 100000000).toFixed(1).replace('.0','')}亿`;
            if (n >= 10000) {
                return `${(n / 10000).toFixed(1).replace('.0','')}万`;
            }
            return String(n);
        }

        function renderBudgetCard() {
            const card = document.getElementById('budgetCard');
            const text = document.getElementById('budgetText');
            const progress = document.getElementById('budgetProgressBar');
            const legend = document.getElementById('budgetLegend');
            if (!card || !progress || !legend) return;

            const enabledModels = currentModelsOrder.filter(item => item.enabled);
            if (enabledModels.length === 0) {
                card.style.display = 'none';
                return;
            }
            card.style.display = 'block';

            let totalBudgetVal = 0;
            const modelBudgets = enabledModels.map(item => {
                const val = getTokensValue(item.monthly_token_budget);
                totalBudgetVal += val;
                return {
                    name: item.model,
                    provider: item.provider,
                    budget: val
                };
            });

            if (totalBudgetVal === 0) {
                card.style.display = 'none';
                return;
            }

            const formattedTotal = formatTokensChinese(totalBudgetVal);
            text.innerHTML = `剩余 <span style="color: var(--text); font-weight: 600;">${formattedTotal}</span>，${formattedTotal} 的 100%`;

            progress.innerHTML = modelBudgets.map(m => {
                const pct = (m.budget / totalBudgetVal) * 100;
                const color = platformColors[m.provider] || '#94a3b8';
                return `<div class="budget-progress-segment" title="${m.name} (${m.provider}) - ${formatTokensChinese(m.budget)}" style="width: ${pct}%; background-color: ${color};"></div>`;
            }).join('');

            legend.innerHTML = modelBudgets.map(m => {
                const color = platformColors[m.provider] || '#94a3b8';
                return `
                    <div class="legend-item">
                        <span class="legend-dot" style="background-color: ${color};"></span>
                        <span style="font-weight: 500; color: var(--text);">${escapeHtml(m.name)}</span>
                        <span style="color: var(--muted); font-size: 11px; margin-left: 2px;">(${escapeHtml(m.provider)})</span>
                        <span style="flex: 1;"></span>
                        <span style="font-family: monospace; color: var(--muted);">${formatTokensChinese(m.budget)}</span>
                    </div>
                `;
            }).join('');
        }

        function renderUnconfiguredFooter() {
            const footer = document.getElementById('unconfiguredFooter');
            if (!footer) return;
            const unconfigured = [];
            for (const [name, info] of Object.entries(allProviderStatuses)) {
                if (!info.configured) {
                    unconfigured.push(name);
                }
            }
            if (unconfigured.length > 0) {
                footer.innerHTML = `<span style="opacity: 0.8;">隐藏 (无密钥)：</span> ${unconfigured.join(', ')}`;
            } else {
                footer.innerHTML = '';
            }
        }

        // loadStatuses is now defined in-place with unconfigured footer logic.

        function renderModelsStatsTable() {
            const tbody = document.getElementById('modelsStatsBody');
            if (!tbody) return;
            
            renderBudgetCard();
            
            const activeList = localModelsOrder || currentModelsOrder;
            if (activeList.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" style="padding: 12px; text-align: center; color: var(--muted);">暂无模型排行数据</td></tr>';
                return;
            }
            
            let displayList = [...activeList];
            if (currentStrategy !== 'priority') {
                displayList.sort((a, b) => {
                    const scoreA = calculateScore(a, currentStrategy);
                    const scoreB = calculateScore(b, currentStrategy);
                    return scoreB - scoreA;
                });
            }
            
            tbody.innerHTML = displayList.map((item, index) => {
                const score = currentStrategy === 'priority' ? (item.score || 0) : calculateScore(item, currentStrategy);
                
                const modelLower = item.model.toLowerCase();
                const supportsVision = modelLower.includes('vision') || modelLower.includes('gpt-4o') || modelLower.includes('gemini') || modelLower.includes('claude-3');
                const supportsTools = modelLower.includes('gpt-4') || modelLower.includes('gemini') || modelLower.includes('llama-3') || modelLower.includes('claude-3');
                
                const isManual = currentStrategy === 'priority';
                const dragIcon = isManual 
                    ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="vertical-align: middle;"><circle cx="9" cy="6" r="1.5" /><circle cx="15" cy="6" r="1.5" /><circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="9" cy="18" r="1.5" /><circle cx="15" cy="18" r="1.5" /></svg>`
                    : `·`;
                
                const dragCursor = isManual ? 'grab' : 'default';
                const rowOpacity = item.enabled ? '1.0' : '0.5';
                
                const budgetStr = item.monthly_token_budget || '18M';
                const formattedBudget = budgetStr.includes('M') 
                    ? `约${parseFloat(budgetStr.replace('~','').replace('M','')) * 100}万` 
                    : budgetStr;
                
                return `
                    <tr style="border-bottom: 1px solid var(--line); opacity: ${rowOpacity}; transition: opacity 0.2s;" 
                        draggable="${isManual}"
                        ondragstart="rowDragStart(event, ${index})"
                        ondragover="rowDragOver(event)"
                        ondrop="rowDrop(event, ${index})"
                        ondragend="rowDragEnd(event)">
                        
                        <!-- Drag Handle -->
                        <td style="padding: 12px; text-align: center; color: var(--muted); cursor: ${dragCursor}; font-weight: bold; font-size: 14px;">
                            ${dragIcon}
                        </td>
                        
                        <!-- Index -->
                        <td style="padding: 12px; font-weight: 500; text-align: center;">
                            #${index + 1}
                        </td>
                        
                        <!-- Model -->
                        <td style="padding: 12px; vertical-align: middle;">
                            <div style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">
                                <span style="font-weight: 600; font-size: 14px; color: var(--text);">${escapeHtml(item.model)}</span>
                                <span style="font-size: 11px; color: var(--muted); background: #f0f2f0; padding: 1px 5px; border-radius: 4px;">${escapeHtml(item.provider)}</span>
                                ${supportsVision ? '<span class="badge-tag tag-vision">想象</span>' : ''}
                                ${supportsTools ? '<span class="badge-tag tag-tools">工具</span>' : ''}
                                ${item.observations > 0 ? `<span class="badge-tag tag-obs">${item.observations}次观察</span>` : ''}
                            </div>
                            <div style="font-size: 11px; color: var(--muted); opacity: 0.8; margin-top: 4px;">
                                ${formattedBudget}/月 · ${item.rpm_limit || '10'}转/分 · ${item.rpd_limit || '50'}转/日
                            </div>
                        </td>
                        
                        <!-- Reliability -->
                        <td style="padding: 12px; vertical-align: middle;">
                            <div class="axis-bar-container">
                                <div class="axis-bar">
                                    <div class="axis-bar-inner" style="width: ${item.rel}%; background-color: #22c55e;"></div>
                                </div>
                                <span class="axis-val">${item.rel}</span>
                            </div>
                        </td>
                        
                        <!-- Speed -->
                        <td style="padding: 12px; vertical-align: middle;">
                            <div class="axis-bar-container">
                                <div class="axis-bar">
                                    <div class="axis-bar-inner" style="width: ${item.spd}%; background-color: #3b82f6;"></div>
                                </div>
                                <span class="axis-val">${item.spd}</span>
                            </div>
                        </td>
                        
                        <!-- Intelligence -->
                        <td style="padding: 12px; vertical-align: middle;">
                            <div class="axis-bar-container">
                                <div class="axis-bar">
                                    <div class="axis-bar-inner" style="width: ${item.int}%; background-color: #a855f7;"></div>
                                </div>
                                <span class="axis-val">${item.int}</span>
                            </div>
                        </td>
                        
                        <!-- Guardrails -->
                        <td style="padding: 12px; vertical-align: middle; text-align: center; font-family: monospace; font-size: 11px; color: var(--muted);">
                            ${item.headroom < 0.99 ? `×${item.headroom.toFixed(2)}` : '—'}
                        </td>
                        
                        <!-- Score -->
                        <td style="padding: 12px; vertical-align: middle; text-align: right; font-family: monospace; font-weight: 600;">
                            ${score.toFixed(3)}
                        </td>
                        
                        <!-- Switch (iOS toggle) -->
                        <td style="padding: 12px; vertical-align: middle; text-align: right;">
                            <label class="switch">
                                <input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="toggleModelEnabled('${item.provider}/${item.model}', this.checked)">
                                <span class="slider"></span>
                            </label>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        startApp().catch((error) => {
            setDiagnostics('status-error', [
                '页面初始化失败',
                error.message || '未知错误',
            ]);
            showToast('初始化失败', 'error');
        });
    
        // --- TAB SWITCHING LOGIC ---
        document.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                
                // Update nav links
                document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
                e.currentTarget.classList.add('active');
                
                // Switch tab pane
                const targetId = e.currentTarget.getAttribute('href').replace('#', 'pane-');
                document.querySelectorAll('.tab-pane').forEach(pane => {
                    pane.classList.remove('active');
                });
                
                const targetPane = document.getElementById(targetId);
                if (targetPane) {
                    targetPane.classList.add('active');
                }
                
                // Hide Hero if not on Models tab
                const hero = document.querySelector('.hero');
                if (hero) {
                    hero.style.display = targetId === 'pane-models' ? 'block' : 'none';
                }
            });
        });
    

        const PROVIDERS = [
            { id: 'openrouter', name: 'OpenRouter', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'openrouter key', keyUrl: 'https://openrouter.ai/keys' },
            { id: 'groq', name: 'Groq', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'groq key', keyUrl: 'https://console.groq.com/keys' },
            { id: 'gemini', name: 'Gemini', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'gemini key', keyUrl: 'https://aistudio.google.com/app/apikey' },
            { id: 'github', name: 'GitHub Models', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'github token', keyUrl: 'https://github.com/settings/tokens' },
            { id: 'mistral', name: 'Mistral', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'mist-...', keyUrl: 'https://console.mistral.ai/api-keys' },
            { id: 'sambanova', name: 'SambaNova', tier: '鍏嶈垂', tierClass: 'free', placeholder: 'sn-...', keyUrl: 'https://cloud.sambanova.ai/' },
        ];

        const state = {
            statuses: {},
            activeProvider: localStorage.getItem('py-ui-active-provider') || 'all',
            selectedProvider: localStorage.getItem('py-ui-selected-provider') || '',
            selectedModel: localStorage.getItem('py-ui-selected-model') || '',
            modelCache: {},
            verifyResults: {},
            editing: {},
            messages: [],
            proxyKey: '',
        };

        let currentModelsOrder = [];

        const toast = document.getElementById('toast');
        const providerTabs = document.getElementById('providerTabs');
        const modelList = document.getElementById('modelList');
        const modelSearch = document.getElementById('modelSearch');
        const modelSummary = document.getElementById('modelSummary');
        
        // Sandbox components
        const playgroundModelSelect = document.getElementById('playgroundModelSelect');
        const clearChatBtn = document.getElementById('clearChatBtn');
        const chatViewport = document.getElementById('chatViewport');
        const chatEmptyState = document.getElementById('chatEmptyState');
        const playgroundDiagnostics = document.getElementById('playgroundDiagnostics');
        const playgroundInput = document.getElementById('playgroundInput');
        const playgroundProbeBtn = document.getElementById('playgroundProbeBtn');
        const playgroundSendBtn = document.getElementById('playgroundSendBtn');

        function showToast(message, type = 'success') {
            toast.textContent = message;
            toast.className = `toast show ${type === 'success' ? '' : type}`.trim();
            window.clearTimeout(showToast.timer);
            showToast.timer = window.setTimeout(() => {
                toast.className = 'toast';
            }, 2600);
        }

        // Tab Switching Logic
        function switchMainTab(tabId) {
            // Update active state on nav tabs
            document.querySelectorAll('.nav-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.mainTab === tabId);
            });
            
            // Hide all tab contents
            document.querySelectorAll('.top-level-tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            // Show the target tab content
            const target = document.getElementById(`main-tab-${tabId}`);
            if (target) {
                target.classList.add('active');
            }
        }
        
        // Expose function globally if needed
        window.switchMainTab = switchMainTab;

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
                showToast(error.message || '鍔犺浇宸蹭繚瀛樻ā鍨嬪け失败', 'error');
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
                throw new Error('閴存潈澶辫触锛岃閲嶆柊鐧诲綍');
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



        function renderProviderTabs() {
            const configured = configuredProviders();
            const tabs = [{ id: 'all', name: '鍏ㄩ儴宸查厤缃? }, ...configured.map((provider) => ({ id: provider.id, name: provider.name }))];

            if (!configured.length) {
                providerTabs.innerHTML = '';
                modelSummary.textContent = '鍏堥厤缃嚦灏戜竴涓?provider锛屽啀鍔犺浇妯″瀷鍒楄〃銆?;
                modelList.innerHTML = '<div class="empty">褰撳墠娌℃湁鍙祻瑙堢殑妯″瀷銆?/div>';
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
                modelSummary.textContent = `宸叉眹鎬?${configured.length} 涓凡閰嶇疆 provider锛屽叡 ${total} 涓欓夋ā鍨嬨傛帹鑽愭ā鍨嬩細鎺掑湪鍓嶉潰銆俙;
            } else {
                const cache = state.modelCache[state.activeProvider];
                if (cache && cache.error && cache.items.length === 0) {
                    modelSummary.textContent = `${state.activeProvider} 妯″瀷鍒楄〃鍔犺浇澶辫触锛?{cache.error}`;
                } else if (cache) {
                    modelSummary.textContent = `${state.activeProvider} 褰撳墠鏈?${cache.totalCount || 0} 涓欓夋ā鍨嬶紝鍏朵腑 ${cache.recommendedCount || 0} 涓潵鑷帹鑽愬垪琛ㄣ俙;
                }
            }

            if (!models.length) {
                modelList.innerHTML = '<div class="empty">褰撳墠绛涢夋潯浠朵笅娌℃湁妯″瀷銆?/div>';
                return;
            }

            modelList.innerHTML = models.map((item) => {
                const isSelected = state.selectedProvider === item.provider && state.selectedModel === item.id;
                const chooseLabel = isSelected ? '宸查夋嫨' : '閫変负娴嬭瘯妯″瀷';
                const chooseClass = isSelected ? 'btn btn-ghost' : 'btn btn-primary';
                const chooseDisabled = isSelected ? 'disabled' : '';
                return `
                    <div class="model-item ${isSelected ? 'selected' : ''}">
                        <div class="model-meta">
                            <div class="model-name">${escapeHtml(item.id)}</div>
                            <div class="model-id">provider: ${escapeHtml(item.provider)}</div>
                            <div class="model-tags">
                                ${item.isRecommended ? '<span class="tag recommended">鎺ㄨ崘</span>' : ''}
                                ${isSelected ? '<span class="tag selected">宸查夋嫨</span>' : ''}
                            </div>
                        </div>
                        <div class="model-actions">
                            <button class="btn btn-secondary" data-action="probe-model" data-provider="${item.provider}" data-model="${escapeHtml(item.id)}">鎺㈡祴</button>
                            <button class="${chooseClass}" data-action="choose-model" data-provider="${item.provider}" data-model="${escapeHtml(item.id)}" ${chooseDisabled}>${chooseLabel}</button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function loadProxyKey() {
            try {
                const data = await requestJson('/api/proxy-key');
                const display = document.getElementById('proxyKeyDisplay');
                if (data && data.key) {
                    display.dataset.key = data.key;
                    const btn = document.getElementById('toggleProxyKeyBtn');
                    if (btn && btn.textContent === '闅愯棌') {
                        display.type = 'text';
                        display.value = data.key;
                    } else {
                        display.type = 'password';
                        display.value = data.key;
                    }
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
                    display.dataset.key = data.key;
                    const btn = document.getElementById('toggleProxyKeyBtn');
                    if (btn && btn.textContent === '闅愯棌') {
                        display.type = 'text';
                        display.value = data.key;
                    } else {
                        display.type = 'password';
                        display.value = data.key;
                    }
                    showToast('Proxy API Key 宸查噸鏂扮敓鎴?, 'success');
                }
            } catch (error) {
                showToast(error.message || '鐢熸垚澶辫触', 'error');
            }
        }

        function toggleProxyKeyVisibility() {
            const display = document.getElementById('proxyKeyDisplay');
            const btn = document.getElementById('toggleProxyKeyBtn');
            if (!display.dataset.key) {
                showToast('灏氭湭鐢熸垚 Key', 'error');
                return;
            }
            if (display.type === 'password') {
                display.type = 'text';
                btn.textContent = '闅愯棌';
            } else {
                display.type = 'password';
                btn.textContent = '鏌ョ湅';
            }
        }

        function copyProxyKey() {
            const display = document.getElementById('proxyKeyDisplay');
            if (!display.dataset.key) {
                showToast('灏氭湭鐢熸垚 Key', 'error');
                return;
            }
            navigator.clipboard.writeText(display.dataset.key).then(() => {
                showToast('宸插鍒跺埌鍓创鏉?, 'success');
            }).catch(() => {
                showToast('澶嶅埗澶辫触', 'error');
            });
        }

        function copyText(elementId) {
            const el = document.getElementById(elementId);
            let text = el.innerText || el.textContent || '';
            if (text.includes(' - model:')) {
                text = text.split(' - model:')[0].trim();
            }
            navigator.clipboard.writeText(text).then(() => {
                showToast('宸插鍒跺埌鍓创鏉?, 'success');
            }).catch(() => {
                showToast('澶嶅埗澶辫触', 'error');
            });
        }

        async function submitProviderKey() {
            const providerSelect = document.getElementById('addProviderSelect');
            const keyInput = document.getElementById('addProviderKey');
            const labelInput = document.getElementById('addProviderLabel');
            
            const providerId = providerSelect.value;
            const apiKey = keyInput.value.trim();
            
            if (!apiKey) {
                showToast('璇疯緭鍏?API 瀵嗛挜', 'error');
                return;
            }
            
            try {
                await requestJson(`/api/provider-keys/${providerId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_key: apiKey })
                });
                keyInput.value = '';
                labelInput.value = '';
                showToast('鎻愪緵鍟嗗瘑閽ラ厤缃垚鍔?);
                await loadStatuses();
            } catch (err) {
                showToast(err.message || '閰嶇疆澶辫触', 'error');
            }
        }

        async function submitCustomModel() {
            const baseUrlInput = document.getElementById('addCustomBaseUrl');
            const modelNameInput = document.getElementById('addCustomModelName');
            const displayNameInput = document.getElementById('addCustomDisplayName');
            const apiKeyInput = document.getElementById('addCustomApiKey');
            
            const baseUrl = baseUrlInput.value.trim();
            const modelName = modelNameInput.value.trim();
            const displayName = displayNameInput.value.trim();
            const apiKey = apiKeyInput.value.trim();
            
            if (!baseUrl || !modelName) {
                showToast('璇疯緭鍏?API 鍩虹 URL 鍜屾ā鍨嬪悕绉?, 'error');
                return;
            }
            
            try {
                await requestJson('/api/custom-models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        base_url: baseUrl,
                        model: modelName,
                        display_name: displayName,
                        api_key: apiKey
                    })
                });
                baseUrlInput.value = '';
                modelNameInput.value = '';
                displayNameInput.value = '';
                apiKeyInput.value = '';
                showToast('鑷畾涔夋ā鍨嬫坊鍔犳垚鍔?);
                await loadStatuses();
            } catch (err) {
                showToast(err.message || '娣诲姞澶辫触', 'error');
            }
        }

        async function toggleProviderActive(id, checkbox) {
            const enabled = checkbox.checked;
            try {
                await requestJson(`/api/providers/${id}/toggle`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: enabled })
                });
                showToast(`${id} 宸?{enabled ? '鍚敤' : '绂佺敤'}`);
                await loadStatuses();
            } catch (err) {
                showToast(err.message || '鎿嶄綔澶辫触', 'error');
                checkbox.checked = !enabled;
            }
        }

        async function editProviderKey(providerId) {
            if (providerId.startsWith('custom-')) {
                const models = state.customModels || [];
                const model = models.find(m => m.id === providerId);
                if (!model) return;
                const newKey = prompt(`淇敼鑷畾涔夋ā鍨?${model.display_name} 鐨?API 瀵嗛挜:`, model.api_key || '');
                if (newKey === null) return;
                
                try {
                    await requestJson(`/api/custom-models/${providerId}/key`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ api_key: newKey })
                    });
                    showToast('API 瀵嗛挜淇敼鎴愬姛');
                    await loadStatuses();
                } catch (err) {
                    showToast(err.message || '淇敼澶辫触', 'error');
                }
            } else {
                const newKey = prompt(`淇敼 ${providerId} 鐨?API Key:`, '');
                if (newKey === null) return;
                if (!newKey.trim()) {
                    showToast('API Key 涓嶈兘涓虹┖', 'error');
                    return;
                }
                try {
                    await requestJson(`/api/provider-keys/${providerId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ api_key: newKey.trim() }),
                    });
                    showToast(`${providerId} 宸蹭慨鏀筦);
                    await loadStatuses();
                } catch (error) {
                    showToast(error.message || '淇敼澶辫触', 'error');
                }
            }
        }

        function viewProviderDiagnostics(providerId) {
            const verify = state.verifyResults[providerId];
            if (!verify) {
                alert("灏氭棤楠岃瘉鏁版嵁銆傝鍏堢偣鍑烩滈獙璇佲濇寜閽幏鍙栨渶鏂扮姸鎬併?);
                return;
            }
            const lines = buildDiagnosticLines('楠岃瘉 Provider', providerId, '', verify, '');
            alert(lines.join("\n"));
        }

        async function deleteCustomModel(modelId) {
            if (!confirm('纭畾瑕佸垹闄よ繖涓嚜瀹氫箟妯″瀷鍚楋紵')) return;
            try {
                await requestJson(`/api/custom-models/${modelId}`, { method: 'DELETE' });
                showToast('宸插垹闄よ嚜瀹氫箟妯″瀷');
                await loadStatuses();
            } catch (err) {
                showToast(err.message || '鍒犻櫎澶辫触', 'error');
            }
        }

        async function deleteProviderKey(providerId) {
            if (!confirm(`纭畾瑕佸垹闄?${providerId} 鐨?API Key 鍚楋紵`)) return;
            try {
                await requestJson(`/api/provider-keys/${providerId}`, { method: 'DELETE' });
                showToast('宸插垹闄?Provider Key');
                await loadStatuses();
            } catch (err) {
                showToast(err.message || '鍒犻櫎澶辫触', 'error');
            }
        }

        const PROVIDER_LINKS = {
            'github': 'https://github.com/marketplace/models',
            'gemini': 'https://aistudio.google.com/',
            'groq': 'https://console.groq.com/keys',
            'sambanova': 'https://cloud.sambanova.ai/',
            'mistral': 'https://console.mistral.ai/api-keys/',
            'openrouter': 'https://openrouter.ai/keys',
            'siliconflow': 'https://cloud.siliconflow.cn/account/ak',
            'together': 'https://api.together.xyz/settings/api-keys',
            'cloudflare': 'https://dash.cloudflare.com/',
            'cerebras': 'https://cloud.cerebras.ai/',
            'scaleway': 'https://console.scaleway.com/',
            'xai': 'https://console.x.ai/',
            'hyperbolic': 'https://app.hyperbolic.xyz/',
            'novita': 'https://novita.ai/dashboard/key',
            'glmf': 'https://bigmodel.cn/usercenter/apikeys',
            'deepseek': 'https://platform.deepseek.com/api_keys',
            'dashscope': 'https://bailian.console.aliyun.com/?apiKey=1#/api-key'
        };

        const FRIENDLY_NAMES = {
            'github': 'GitHub 妯″瀷',
            'gemini': '璋锋瓕',
            'groq': 'Groq',
            'sambanova': 'SambaNova',
            'mistral': 'Mistral',
            'openrouter': 'OpenRouter',
            'siliconflow': 'SiliconFlow (纭呭熀娴佸姩)',
            'together': 'Together AI',
            'cloudflare': 'Cloudflare AI',
            'cerebras': 'Cerebras',
            'scaleway': 'Scaleway',
            'xai': 'xAI',
            'hyperbolic': 'Hyperbolic',
            'novita': 'Novita AI',
            'glmf': '鏅鸿氨 GLM',
            'deepseek': 'DeepSeek (娣卞害姹傜储)',
            'dashscope': 'DashScope (闃块噷浜戠櫨鐐?'
        };

        function formatMaskedKey(masked) {
            if (!masked || masked === '***') return '***';
            if (masked === '鏃犻渶閴存潈') return '鏃犻渶閴存潈';
            if (masked.length >= 8) {
                const prefix = masked.substring(0, 4);
                const suffix = masked.slice(-4);
                return `${prefix}. ${suffix}`;
            }
            return masked;
        }

        function formatTimestamp(timestampStr) {
            if (!timestampStr) return '';
            try {
                const parts = timestampStr.split(' ');
                if (parts.length > 1) {
                    const timePart = parts[1];
                    const timeSegments = timePart.split(':');
                    if (timeSegments.length >= 2) {
                        return `${timeSegments[0]}:${timeSegments[1]}`;
                    }
                }
                return timestampStr;
            } catch (e) {
                return timestampStr;
            }
        }

        function updateAddProviderLink() {
            const select = document.getElementById('addProviderSelect');
            const link = document.getElementById('addProviderLink');
            if (!select || !link) return;
            const providerId = select.value;
            const url = PROVIDER_LINKS[providerId] || '#';
            link.href = url;
            link.style.display = url !== '#' ? 'inline-flex' : 'none';
        }

        function renderConfiguredList() {
            const container = document.getElementById('configuredListContainer');
            if (!container) return;
            
            let html = '';
            
            const standardProviders = [];
            for (const key of Object.keys(state.statuses)) {
                const status = state.statuses[key];
                if (status.configured) {
                    standardProviders.push({
                        id: key,
                        name: FRIENDLY_NAMES[key] || (key.charAt(0).toUpperCase() + key.slice(1) + ' 骞冲彴'),
                        masked: status.masked || '***',
                        enabled: status.enabled !== false,
                        isCustom: false
                    });
                }
            }
            
            const customModels = (state.customModels || []).map(m => ({
                id: m.id,
                name: m.display_name || m.model,
                modelName: m.model,
                baseUrl: m.base_url,
                masked: m.api_key ? '***' : '鏃犻渶閴存潈',
                enabled: m.enabled !== false,
                isCustom: true,
                created_at: m.created_at
            }));
            
            const allItems = [...standardProviders, ...customModels];
            
            if (allItems.length === 0) {
                container.innerHTML = '<div style="text-align: center; padding: 24px; color: var(--muted); font-size: 13px;">鏆傛棤宸查厤缃殑鎻愪緵鍟嗘垨鑷畾涔夋ā鍨嬶紝璇峰湪涓婃柟娣诲姞銆?/div>';
                return;
            }
            
            html = allItems.map(item => {
                const link = PROVIDER_LINKS[item.id];
                const linkHtml = link ? `
                    <a href="${link}" target="_blank" style="font-size: 12px; color: var(--muted); text-decoration: none; margin-left: 8px; display: inline-flex; align-items: center; gap: 4px;">
                        鑾峰彇 API 瀵嗛挜
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                    </a>` : '';
                
                const verify = state.verifyResults[item.id];
                let statusColor = '#9ca3af';
                let statusText = '鏈獙璇?;
                
                if (item.enabled) {
                    if (verify) {
                        if (verify.ok) {
                            statusColor = '#22c55e';
                            statusText = '鍋ュ悍';
                        } else {
                            statusColor = '#ef4444';
                            statusText = verify.category || '楠岃瘉澶辫触';
                        }
                    } else {
                        statusColor = '#3b82f6';
                        statusText = '寰呴獙璇?;
                    }
                } else {
                    statusColor = '#9ca3af';
                    statusText = '宸茬鐢?;
                }
                
                let timeText = '';
                if (verify && verify.timestamp) {
                    timeText = formatTimestamp(verify.timestamp);
                } else if (item.created_at) {
                    const dateStr = new Date(item.created_at * 1000).toLocaleString();
                    timeText = formatTimestamp(dateStr);
                }
                
                const deleteAction = item.isCustom
                    ? `deleteCustomModel('${item.id}')`
                    : `deleteProviderKey('${item.id}')`;
                
                const formattedKey = formatMaskedKey(item.masked);
                const keyCountLabel = (item.isCustom && item.masked === '鏃犻渶閴存潈') ? '鏃犻渶閴存潈' : '1閽ュ寵';
                
                return `
                    <div class="provider-group" style="margin-bottom: 16px; border: 1px solid var(--line); border-radius: 12px; background: #ffffff; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                        <!-- Parent Row -->
                        <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--line); background: #fdfdfd;">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <label class="switch" style="margin: 0;">
                                    <input type="checkbox" ${item.enabled ? 'checked' : ''} onchange="toggleProviderActive('${item.id}', this)">
                                    <span class="slider"></span>
                                </label>
                                <span style="font-weight: 600; font-size: 14px; color: var(--text);">${escapeHtml(item.name)}</span>
                                ${linkHtml}
                            </div>
                            <span style="font-size: 11px; color: var(--muted); padding: 2px 6px; border-radius: 4px; font-weight: 500;">${keyCountLabel}</span>
                        </div>
                        
                        <!-- Child Nested Row -->
                        <div style="padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; background: #ffffff;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="width: 7px; height: 7px; border-radius: 50%; background: ${statusColor};"></span>
                                <span class="mono" style="font-size: 13px; color: var(--text); letter-spacing: 0.5px; font-weight: 500;">${escapeHtml(formattedKey)}</span>
                                <span style="color: ${statusColor}; font-weight: 500; font-size: 12px;">${statusText}</span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 14px; color: var(--muted); font-size: 12px;">
                                ${timeText ? `<span style="font-size: 12px; color: var(--muted); font-weight: 400;">${escapeHtml(timeText)}</span>` : ''}
                                <button class="btn btn-ghost" style="padding: 4px; display: inline-flex; align-items: center; color: var(--muted); background: transparent;" onclick="editProviderKey('${item.id}')" title="淇敼">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.7;"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                                </button>
                                <button class="btn btn-ghost" style="padding: 2px 6px; font-size: 12px; font-weight: 500; color: var(--text); border: 1px solid var(--line); border-radius: 6px; height: 26px;" onclick="verifyProvider('${item.id}', this)">楠岃瘉</button>
                                <button class="btn btn-ghost" style="padding: 2px 6px; font-size: 12px; font-weight: 500; color: var(--text); border: 1px solid var(--line); border-radius: 6px; height: 26px;" onclick="viewProviderDiagnostics('${item.id}')">鏌ョ湅</button>
                                <button class="btn btn-ghost" style="padding: 2px 6px; font-size: 12px; font-weight: 500; color: var(--danger); border: 1px solid rgba(185, 72, 62, 0.15); border-radius: 6px; height: 26px; background: rgba(185, 72, 62, 0.02);" onclick="${deleteAction}">娓呴櫎</button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
            
        }

        async function loadStatuses() {
            state.statuses = await requestJson('/api/provider-keys');
            try {
                const res = await requestJson('/api/custom-models');
                state.customModels = res.models || [];
            } catch (err) {
                state.customModels = [];
            }
            renderProviderTabs();
            refreshChatOptions();
            renderConfiguredList();
        }

        function buildDiagnosticLines(actionLabel, providerId, modelId, payload, fallbackMessage) {
            const ok = payload && typeof payload.ok === 'boolean' ? payload.ok : null;
            return [
                `鍔ㄤ綔: ${actionLabel}`,
                providerId ? `Provider: ${providerId}` : '',
                modelId ? `Model: ${modelId}` : '',
                ok === null ? '' : `缁撴灉: ${ok ? '鎴愬姛' : '澶辫触'}`,
                payload && payload.note ? `璇存槑: ${payload.note}` : '',
                payload && payload.error ? `閿欒: ${payload.error}` : '',
                payload && payload.category ? `鍒嗙被: ${payload.category}` : '',
                payload && payload.status ? `HTTP: ${payload.status}` : '',
                payload && payload.verified_model ? `楠岃瘉閫氳繃妯″瀷: ${payload.verified_model}` : '',
                payload && payload.actual_model ? `瀹為檯璋冪敤妯″瀷: ${payload.actual_model}` : '',
                payload && payload.content ? `杩斿洖鍐呭: ${payload.content}` : '',
                payload && payload.suggestion ? `寤鸿: ${payload.suggestion}` : '',
                !payload?.suggestion && fallbackMessage ? `寤鸿: ${fallbackMessage}` : '',
            ].filter(Boolean);
        }

        async function verifyProvider(providerId, button) {
            if (button) {
                button.disabled = true;
                button.textContent = '楠岃瘉涓?..';
            }

            try {
                const data = await requestJson(`/api/provider-keys/${providerId}/verify`, { method: 'POST' });
                data.timestamp = new Date().toLocaleString();
                state.verifyResults[providerId] = data;
                renderConfiguredList();
                showToast(`${providerId} 楠岃瘉鎴愬姛`);
                setDiagnostics('status-success', buildDiagnosticLines('楠岃瘉 Provider', providerId, '', { ...data, ok: true }, '缁х画浠庢帹鑽愭ā鍨嬮噷閫夋嫨涓涓ā鍨嬪仛鎺㈡祴鎴栬亰澶╅獙璇併?));
                await ensureVisibleModels(true);
            } catch (error) {
                const payload = error.payload || {};
                payload.timestamp = new Date().toLocaleString();
                state.verifyResults[providerId] = payload;
                renderConfiguredList();
                showToast(`${providerId} 楠岃瘉澶辫触`, 'error');
                setDiagnostics('status-error', buildDiagnosticLines('楠岃瘉 Provider', providerId, '', {
                    ok: false,
                    error: payload.error || error.message || '鏈煡閿欒',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '妫鏌?Key 鏄惁鏈夋晥锛屾垨鍒囨崲鍒拌 provider 鐨勬帹鑽愭ā鍨嬮噸璇曘?));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = '楠岃瘉';
                }
            }
        }

        async function chooseModel(providerId, modelId) {
            state.selectedProvider = providerId;
            state.selectedModel = modelId;
            state.activeProvider = providerId;
            if (playgroundModelSelect) {
                playgroundModelSelect.value = `${providerId}/${modelId}`;
            }
            persistSelection();
            renderModelSection();
            refreshChatOptions();
            showToast(`宸查夋嫨 ${providerId} / ${modelId}`);
            setDiagnostics('status-info', buildDiagnosticLines('閫夋嫨楠岃瘉妯″瀷', providerId, modelId, { ok: true }, '鐜板湪鍙互鐩存帴鎺㈡祴锛屾垨鍙戦佷竴鏉＄湡瀹炶亰澶╄姹傘?));
            try {
                await requestJson('/api/preferred-model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerId, model: modelId }),
                });
            } catch (error) {
                showToast(error.message || '淇濆瓨宸查夋ā鍨嬪け失败', 'error');
            }
        }

        function refreshChatOptions() {
            refreshPlaygroundModelSelect();
        }

        window.toggleProxyKeyVisibility = toggleProxyKeyVisibility;
        window.copyProxyKey = copyProxyKey;
        window.copyText = copyText;
        window.generateProxyKey = generateProxyKey;
        window.submitProviderKey = submitProviderKey;
        window.submitCustomModel = submitCustomModel;
        window.toggleProviderActive = toggleProviderActive;
        window.editProviderKey = editProviderKey;
        window.viewProviderDiagnostics = viewProviderDiagnostics;
        window.deleteCustomModel = deleteCustomModel;
        window.deleteProviderKey = deleteProviderKey;
        window.verifyProvider = verifyProvider;
        window.chooseModel = chooseModel;
        window.refreshChatOptions = refreshChatOptions;
        window.updateAddProviderLink = updateAddProviderLink;

        async function probeModel(providerId, modelId, button) {
            if (button) {
                button.disabled = true;
                button.textContent = '鎺㈡祴涓?..';
            }
            try {
                const data = await requestJson(`/providers/${providerId}/probe`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: modelId }),
                });
                await chooseModel(providerId, modelId);
                showToast('妯″瀷鎺㈡祴鎴愬姛');
                setDiagnostics('status-success', buildDiagnosticLines('鎺㈡祴妯″瀷', providerId, modelId, { ...data, ok: true }, '妯″瀷鍙敤锛屽彲浠ョ户缁彂閫佽亰澶╄姹傘?));
            } catch (error) {
                const payload = error.payload || {};
                showToast('妯″瀷鎺㈡祴澶辫触', 'error');
                setDiagnostics('status-error', buildDiagnosticLines('鎺㈡祴妯″瀷', providerId, modelId, {
                    ok: false,
                    error: payload.error || error.message || '鏈煡閿欒',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '浼樺厛鍒囨崲鍒版帹鑽愭ā鍨嬶紝鎴栧厛閲嶆柊楠岃瘉璇?provider銆?));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = '鎺㈡祴';
                }
            }
        }

        async function probeSelectedModel(button) {
            const providerId = chatProvider.value.trim();
            const modelId = chatModel.value.trim();
            if (!providerId || !modelId) {
                showToast('璇峰厛閫夋嫨 provider 鍜?model', 'error');
                return;
            }
            await probeModel(providerId, modelId, button);
        }

        let dragSourceRow = null;

        function handleDragStart(e) {
            dragSourceRow = this;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/html', this.innerHTML);
            this.classList.add('dragging');
        }

        function handleDragOver(e) {
            if (e.preventDefault) {
                e.preventDefault();
            }
            e.dataTransfer.dropEffect = 'move';
            const rect = this.getBoundingClientRect();
            const next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
            this.classList.toggle('drag-over-bottom', next);
            this.classList.toggle('drag-over-top', !next);
            return false;
        }

        function handleDragLeave(e) {
            this.classList.remove('drag-over-top', 'drag-over-bottom');
        }

        async function handleDrop(e) {
            if (e.stopPropagation) {
                e.stopPropagation();
            }
            this.classList.remove('drag-over-top', 'drag-over-bottom');
            if (dragSourceRow !== this) {
                const srcIndex = parseInt(dragSourceRow.dataset.index);
                const targetIndex = parseInt(this.dataset.index);
                if (isNaN(srcIndex) || isNaN(targetIndex)) return false;
                
                let newOrder = [...currentModelsOrder];
                const [movedItem] = newOrder.splice(srcIndex, 1);
                newOrder.splice(targetIndex, 0, movedItem);
                
                await saveNewOrder(newOrder);
            }
            return false;
        }

        function handleDragEnd(e) {
            this.classList.remove('dragging');
            document.querySelectorAll('#modelsStatsBody tr').forEach(row => {
                row.classList.remove('drag-over-top', 'drag-over-bottom');
            });
        }

        async function saveNewOrder(newOrder) {
            try {
                const orderStrings = newOrder.map(i => `${i.provider}/${i.model}`);
                const res = await fetch('/api/manual-order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: orderStrings })
                });
                if (res.ok) {
                    showToast('鎺掑簭宸蹭繚瀛?);
                    await fetchModelsStats();
                } else {
                    showToast('淇濆瓨鎺掑簭澶辫触', 'error');
                }
            } catch (err) {
                console.error(err);
                showToast('缃戠粶閿欒锛屼繚瀛樺け失败', 'error');
            }
        }

        // --- NEW: Implement fetchModelsStats exactly replicating the UI ---
        async function fetchModelsStats() {
            try {
                // Fetch the real routing data from backend
                const data = await requestJson('/api/models-stats');
                const tbody = document.getElementById('modelsStatsBody');
                
                let rows = [];
                if (data && data.models && data.models.length > 0) {
                    rows = data.models;
                } else {
                    rows = [];
                }

                if (rows.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" style="padding: 24px; text-align: center; color: var(--muted);">鏃犲彲鐢ㄧ殑妯″瀷鏁版嵁锛岃鍏堥厤缃?Provider Key銆?/td></tr>';
                    return;
                }

                tbody.innerHTML = rows.map((row, index) => {
                    const modelName = row.model || 'Unknown';
                    const providerName = row.provider || 'Unknown';
                    
                    const randRel = row.rel !== undefined ? row.rel : 0;
                    const randSpd = row.spd !== undefined ? row.spd : 0;
                    const randInt = row.int !== undefined ? row.int : 0;
                    const score = row.score !== undefined ? row.score : 0;
                    const headroom = row.headroom !== undefined ? row.headroom : 1.0;
                    const obs = row.observations || 0;
                    
                    const monthlyBudget = row.monthly_token_budget || '鈥?;
                    const rpmLimit = row.rpm_limit;
                    const rpdLimit = row.rpd_limit;
                    
                    const isEnabled = row.source !== 'none';
                    
                    let guardDisplay = '鈥?;
                    if (headroom < 0.999) {
                        guardDisplay = '脳' + headroom.toFixed(2);
                    }
                    
                    return `
                        <tr draggable="true" data-index="${index}">
                            <td class="drag-col">鈰嫯</td>
                            <td class="num-col">${index + 1}</td>
                            <td>
                                <div class="model-title">
                                    ${escapeHtml(modelName)}
                                    <span class="pill-tag pill-gray">${escapeHtml(providerName)}</span>
                                    ${modelName.toLowerCase().includes('vision') || modelName.toLowerCase().includes('gpt-4o') || modelName.toLowerCase().includes('claude-3') ? '<span class="pill-tag pill-cyan">Vision</span>' : ''}
                                    ${!modelName.toLowerCase().includes('vision') ? '<span class="pill-tag pill-purple">Tools</span>' : ''}
                                    ${obs > 0 ? '<span class="pill-tag pill-text">' + obs + '娆¤瀵?/span>' : ''}
                                </div>
                                <div class="model-sub" style="font-family: inherit; font-variant-numeric: tabular-nums; color: var(--text); opacity: 0.85; font-size: 11px; margin-top: 4px;">
                                    ${monthlyBudget} tok/mo ${rpmLimit ? '路 ' + rpmLimit + ' rpm' : ''} ${rpdLimit ? '路 ' + rpdLimit + ' rpd' : ''}
                                </div>
                            </td>
                            <td>
                                <div class="axis-bar-container">
                                    <div class="axis-bar-bg"><div class="axis-bar-fill" style="width: ${randRel}%; background: #22c55e;"></div></div>
                                    <span class="axis-val">${randRel}</span>
                                </div>
                            </td>
                            <td>
                                <div class="axis-bar-container">
                                    <div class="axis-bar-bg"><div class="axis-bar-fill" style="width: ${randSpd}%; background: #3b82f6;"></div></div>
                                    <span class="axis-val">${randSpd}</span>
                                </div>
                            </td>
                            <td>
                                <div class="axis-bar-container">
                                    <div class="axis-bar-bg"><div class="axis-bar-fill" style="width: ${randInt}%; background: #a855f7;"></div></div>
                                    <span class="axis-val">${randInt}</span>
                                </div>
                            </td>
                            <td style="color: var(--muted); font-family: monospace; font-size: 11px;">${guardDisplay}</td>
                            <td style="font-family: monospace; font-weight: 500;">${score.toFixed(3)}</td>
                            <td>
                                <label class="switch" onclick="showToast('璇峰湪鈥滃瘑閽ラ厤缃濋厤缃?API Key 鏉ュ惎鐢?绂佺敤妯″瀷銆?, 'info')">
                                    <input type="checkbox" ${isEnabled ? 'checked' : ''} disabled style="pointer-events: none;">
                                    <span class="slider"></span>
                                </label>
                            </td>
                        </tr>
                    `;
                }).join('');

                currentModelsOrder = rows;
                
                // Register drag and drop events
                tbody.querySelectorAll('tr').forEach(row => {
                    row.addEventListener('dragstart', handleDragStart);
                    row.addEventListener('dragover', handleDragOver);
                    row.addEventListener('dragleave', handleDragLeave);
                    row.addEventListener('drop', handleDrop);
                    row.addEventListener('dragend', handleDragEnd);
                });
                
            } catch (e) {
                console.error(e);
                showToast('鏃犳硶鑾峰彇鎺掕鏁版嵁', 'error');
            }
        }
        // Run on load
        document.addEventListener('DOMContentLoaded', () => {
            fetchModelsStats();
        });


        async function chooseModel(providerId, modelId) {
            state.selectedProvider = providerId;
            state.selectedModel = modelId;
            if (playgroundModelSelect) {
                const val = `${providerId}/${modelId}`;
                playgroundModelSelect.value = val;
            }
            persistSelection();
            showToast(`宸查夋嫨妯″瀷 ${providerId} / ${modelId}`);
            try {
                await requestJson('/api/preferred-model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerId, model: modelId }),
                });
            } catch (error) {
                console.error('Failed to save preferred model', error);
            }
        }

        async function probeModel(providerId, modelId, button) {
            if (button) {
                button.disabled = true;
                button.textContent = '鎺㈡祴涓?..';
            }
            try {
                const data = await requestJson(`/providers/${providerId}/probe`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: modelId }),
                });
                await chooseModel(providerId, modelId);
                showToast('妯″瀷鎺㈡祴鎴愬姛');
                setDiagnostics('status-success', buildDiagnosticLines('鎺㈡祴妯″瀷', providerId, modelId, { ...data, ok: true }, '妯″瀷鍙敤锛屽彲浠ュ紑濮嬫甯歌亰澶┿?));
            } catch (error) {
                const payload = error.payload || {};
                showToast('妯″瀷鎺㈡祴澶辫触', 'error');
                setDiagnostics('status-error', buildDiagnosticLines('鎺㈡祴妯″瀷', providerId, modelId, {
                    ok: false,
                    error: payload.error || error.message || '鏈煡閿欒',
                    category: payload.category,
                    status: payload.status,
                    suggestion: payload.suggestion,
                }, '浼樺厛鍒囨崲鍒版帹鑽愭ā鍨嬶紝鎴栧厛閲嶆柊楠岃瘉璇?provider銆?));
            } finally {
                if (button) {
                    button.disabled = false;
                    button.textContent = '鎺㈡祴';
                }
            }
        }

        async function probeSelectedModel(button) {
            const val = playgroundModelSelect.value || 'free-proxy/auto';
            if (val === 'free-proxy/auto') {
                await probePlaygroundModel();
            } else {
                const parts = val.split('/');
                const providerId = parts[0];
                const modelId = parts.slice(1).join('/');
                await probeModel(providerId, modelId, button);
            }
        }

        // Sandbox implementation
        function refreshPlaygroundModelSelect() {
            if (!playgroundModelSelect) return;
            const configured = configuredProviders();
            let html = '<option value="free-proxy/auto">Auto (鑷姩闄嶇骇閾?/ free-proxy/auto)</option>';
            
            for (const provider of configured) {
                const cache = state.modelCache[provider.id];
                if (cache && cache.items) {
                    for (const item of cache.items) {
                        const val = `${provider.id}/${item.id}`;
                        const isRec = item.isRecommended;
                        html += `<option value="${escapeHtml(val)}">${escapeHtml(item.id)} (${escapeHtml(provider.name)})${isRec ? ' [鎺ㄨ崘]' : ''}</option>`;
                    }
                }
            }
            
            const savedVal = playgroundModelSelect.value;
            playgroundModelSelect.innerHTML = html;
            
            let exists = false;
            for (let i = 0; i < playgroundModelSelect.options.length; i++) {
                if (playgroundModelSelect.options[i].value === savedVal) {
                    exists = true;
                    break;
                }
            }
            if (exists) {
                playgroundModelSelect.value = savedVal;
            } else if (state.selectedProvider && state.selectedModel) {
                const val = `${state.selectedProvider}/${state.selectedModel}`;
                let valExists = false;
                for (let i = 0; i < playgroundModelSelect.options.length; i++) {
                    if (playgroundModelSelect.options[i].value === val) {
                        valExists = true;
                        break;
                    }
                }
                if (valExists) {
                    playgroundModelSelect.value = val;
                }
            }
        }

        function refreshChatOptions() {
            refreshPlaygroundModelSelect();
        }

        function renderModelSection() {
            // Keep provider tabs in sync, though we no longer render model list panel
            renderProviderTabs();
            refreshPlaygroundModelSelect();
        }

        function showPlaygroundDiagnostics(kind, lines) {
            if (!playgroundDiagnostics) return;
            playgroundDiagnostics.className = `playground-diagnostics ${kind}`;
            playgroundDiagnostics.innerHTML = lines.map(line => `<div>${escapeHtml(line)}</div>`).join('');
            playgroundDiagnostics.style.display = 'block';
        }
        
        function hidePlaygroundDiagnostics() {
            if (!playgroundDiagnostics) return;
            playgroundDiagnostics.style.display = 'none';
        }

        function renderPlaygroundMessages() {
            if (!chatViewport || !chatEmptyState || !clearChatBtn) return;
            if (state.messages.length === 0) {
                chatViewport.innerHTML = '';
                chatViewport.appendChild(chatEmptyState);
                chatEmptyState.style.display = 'flex';
                clearChatBtn.style.display = 'none';
                return;
            }
            
            chatEmptyState.style.display = 'none';
            clearChatBtn.style.display = 'block';
            
            let html = '';
            for (const msg of state.messages) {
                if (msg.role === 'user') {
                    html += `
                        <div class="chat-message user">
                            <div class="message-bubble">${escapeHtml(msg.content)}</div>
                        </div>
                    `;
                } else {
                    let bubbleContent = '';
                    if (msg.loading) {
                        bubbleContent = `
                            <div class="typing-loader">
                                <span></span>
                                <span></span>
                                <span></span>
                            </div>
                        `;
                    } else {
                        // Render line breaks as HTML line breaks
                        bubbleContent = escapeHtml(msg.content).replace(/\n/g, '<br>');
                    }
                    
                    let metaHtml = '';
                    if (msg.meta) {
                        const durationSec = (msg.meta.durationMs / 1000).toFixed(1);
                        const fallbackText = msg.meta.fallbacks > 0 ? ` 路 ${msg.meta.fallbacks}娆￠噸璇昤 : '';
                        metaHtml = `
                            <div class="message-meta">
                                <span>${escapeHtml(msg.meta.provider)}</span>
                                <span>路</span>
                                <span>${escapeHtml(msg.meta.model)}</span>
                                <span>路</span>
                                <span>${durationSec}s</span>
                                ${fallbackText}
                            </div>
                        `;
                    }
                    
                    html += `
                        <div class="chat-message assistant">
                            <div class="message-bubble">${bubbleContent}</div>
                            ${metaHtml}
                        </div>
                    `;
                }
            }
            
            chatViewport.innerHTML = html;
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }

        function adjustTextareaHeight(el) {
            el.style.height = 'auto';
            el.style.height = (el.scrollHeight) + 'px';
        }

        async function sendPlaygroundChat() {
            if (!playgroundInput || !playgroundSendBtn || !playgroundProbeBtn) return;
            const text = playgroundInput.value.trim();
            if (!text) return;
            
            const model = playgroundModelSelect.value || 'free-proxy/auto';
            
            const userMsg = { role: 'user', content: text, meta: null };
            state.messages.push(userMsg);
            
            playgroundInput.value = '';
            adjustTextareaHeight(playgroundInput);
            playgroundSendBtn.disabled = true;
            renderPlaygroundMessages();
            
            playgroundSendBtn.disabled = true;
            playgroundProbeBtn.disabled = true;
            
            const assistantMsg = { role: 'assistant', content: '', meta: null, loading: true };
            state.messages.push(assistantMsg);
            renderPlaygroundMessages();
            
            const startTime = Date.now();
            
            const apiMessages = state.messages.slice(0, -1).map(m => ({
                role: m.role,
                content: m.content
            }));
            
            try {
                const headers = {
                    'Content-Type': 'application/json'
                };
                if (state.proxyKey) {
                    headers['Authorization'] = `Bearer ${state.proxyKey}`;
                }
                
                const res = await fetch('/v1/chat/completions', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({
                        model: model,
                        messages: apiMessages,
                        stream: true
                    })
                });
                
                if (!res.ok) {
                    let errorText = `HTTP ${res.status}`;
                    try {
                        const errData = await res.json();
                        if (errData && errData.error && errData.error.message) {
                            errorText = errData.error.message;
                        }
                    } catch {}
                    throw new Error(errorText);
                }
                
                const routedVia = res.headers.get('X-Routed-Via');
                const fallbacks = res.headers.get('X-Fallback-Attempts');
                
                assistantMsg.loading = false;
                
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    const parts = buffer.split('\n\n');
                    buffer = parts.pop() || '';
                    
                    for (const part of parts) {
                        const data = extractSseDataLine(part);
                        if (!data || data === '[DONE]') continue;
                        
                        try {
                            const parsed = JSON.parse(data);
                            const text = parsed?.choices?.[0]?.delta?.content || '';
                            if (text) {
                                assistantMsg.content += text;
                                renderPlaygroundMessages();
                            }
                        } catch {}
                    }
                }
                
                const durationMs = Date.now() - startTime;
                let routedProvider = 'auto';
                let routedModel = model;
                if (routedVia) {
                    const idx = routedVia.indexOf('/');
                    if (idx !== -1) {
                        routedProvider = routedVia.substring(0, idx);
                        routedModel = routedVia.substring(idx + 1);
                    } else {
                        routedModel = routedVia;
                    }
                }
                
                assistantMsg.meta = {
                    provider: routedProvider,
                    model: routedModel,
                    durationMs: durationMs,
                    fallbacks: fallbacks ? parseInt(fallbacks) : 0
                };
                
                hidePlaygroundDiagnostics();
                
            } catch (err) {
                console.error(err);
                assistantMsg.loading = false;
                assistantMsg.content = `[鍙戦佸け璐 ${err.message}`;
                
                showPlaygroundDiagnostics('status-error', [
                    `鍔ㄤ綔: 鍙戦佽亰澶ー,
                    `妯″瀷: ${model}`,
                    `閿欒: ${err.message}`,
                    `寤鸿: 妫鏌ョ綉缁滆繛鎺ワ紝鎴栧皾璇曚竴閿帰娴嬫ā鍨嬫槸鍚﹀彲鐢ㄣ俙
                ]);
            } finally {
                playgroundSendBtn.disabled = false;
                playgroundProbeBtn.disabled = false;
                renderPlaygroundMessages();
            }
        }

        async function probePlaygroundModel() {
            if (!playgroundModelSelect || !playgroundSendBtn || !playgroundProbeBtn) return;
            const model = playgroundModelSelect.value || 'free-proxy/auto';
            
            playgroundSendBtn.disabled = true;
            playgroundProbeBtn.disabled = true;
            
            showPlaygroundDiagnostics('status-info', [
                `鍔ㄤ綔: 涓閿帰娴媊,
                `鐩爣: ${model}`,
                `鐘舵? 鎺㈡祴涓紝璇风◢鍊?..`
            ]);
            
            const startTime = Date.now();
            
            try {
                if (model === 'free-proxy/auto') {
                    const headers = { 'Content-Type': 'application/json' };
                    if (state.proxyKey) {
                        headers['Authorization'] = `Bearer ${state.proxyKey}`;
                    }
                    const res = await fetch('/v1/chat/completions', {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({
                            model: 'free-proxy/auto',
                            messages: [{ role: 'user', content: 'ping' }],
                            max_tokens: 5,
                            stream: false
                        })
                    });
                    
                    const duration = Date.now() - startTime;
                    const data = await res.json();
                    
                    if (!res.ok) {
                        const err = data.error || {};
                        showPlaygroundDiagnostics('status-error', [
                            `鍔ㄤ綔: 鑷姩闄嶇骇閾炬帰娴媊,
                            `鐘舵? 澶辫触 (HTTP ${res.status})`,
                            `閿欒: ${err.message || '鏈煡閿欒'}`,
                            `鍒嗙被: ${data.category || 'unknown'}`,
                            `寤鸿: 鑷冲皯閰嶇疆涓涓湁鏁堢殑 Provider Key锛屾垨妫鏌ユ湇鍔℃棩蹇椼俙
                        ]);
                    } else {
                        const routedVia = res.headers.get('X-Routed-Via') || 'auto';
                        const fallbacks = res.headers.get('X-Fallback-Attempts') || '0';
                        showPlaygroundDiagnostics('status-success', [
                            `鍔ㄤ綔: 鑷姩闄嶇骇閾炬帰娴媊,
                            `鐘舵? 鎴愬姛 (${duration}ms)`,
                            `瀹為檯璺敱: ${routedVia}`,
                            `閲嶈瘯娆℃暟: ${fallbacks}`,
                            `杩斿洖鍝嶅簲: ${extractChatTextFromJsonPayload(data)}`,
                            `寤鸿: 闄嶇骇閾惧伐浣滄甯革紝鍙互寮濮嬫甯歌亰澶┿俙
                        ]);
                    }
                } else {
                    const parts = model.split('/');
                    const providerId = parts[0];
                    const modelId = parts.slice(1).join('/');
                    
                    const res = await fetch(`/providers/${providerId}/probe`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ model: modelId })
                    });
                    
                    const duration = Date.now() - startTime;
                    const data = await res.json();
                    
                    if (!res.ok) {
                        showPlaygroundDiagnostics('status-error', [
                            `鍔ㄤ綔: 鍗曟ā鍨嬫帰娴媊,
                            `鐩爣: ${providerId}/${modelId}`,
                            `鐘舵? 澶辫触 (HTTP ${res.status})`,
                            `閿欒: ${data.error || '鎺㈡祴璇锋眰澶辫触'}`,
                            `鍒嗙被: ${data.category || 'unknown'}`,
                            `寤鸿: ${data.suggestion || '妫鏌?API Key 鏄惁鏈夋晥锛屾垨妫鏌ヤ笂娓歌繛鎺ラ檺鍒躲?}`
                        ]);
                    } else {
                        showPlaygroundDiagnostics('status-success', [
                            `鍔ㄤ綔: 鍗曟ā鍨嬫帰娴媊,
                            `鐩爣: ${providerId}/${modelId}`,
                            `鐘舵? 鎴愬姛 (${duration}ms)`,
                            `楠岃瘉妯″瀷: ${data.verified_model || modelId}`,
                            `杩斿洖鍝嶅簲: ${data.note || '鎺㈡祴杩斿洖鎴愬姛'}`,
                            `寤鸿: 妯″瀷宸ヤ綔姝ｅ父锛屽彲浠ュ紑濮嬫甯歌亰澶┿俙
                        ]);
                    }
                }
            } catch (err) {
                showPlaygroundDiagnostics('status-error', [
                    `鍔ㄤ綔: 妯″瀷鎺㈡祴`,
                    `鐩爣: ${model}`,
                    `鐘舵? 澶辫触`,
                    `閿欒: ${err.message || '缃戠粶杩炴帴閿欒'}`,
                    `寤鸿: 璇风‘璁ゅ悗绔湇鍔℃甯稿惎鍔ㄥ湪绔彛 8765銆俙
                ]);
            } finally {
                playgroundSendBtn.disabled = false;
                playgroundProbeBtn.disabled = false;
            }
        }

        // Global Event Listeners

        providerTabs.addEventListener('click', async (event) => {
            const button = event.target.closest('button[data-tab]');
            if (!button) return;
            state.activeProvider = button.dataset.tab;
            persistSelection();
            await ensureVisibleModels(false);
        });

        if (modelList) {
            modelList.addEventListener('click', async (event) => {
                const button = event.target.closest('button[data-action]');
                if (!button) return;
                const providerId = button.dataset.provider;
                const modelId = button.dataset.model;
                if (!providerId || !modelId) return;
                if (button.dataset.action === 'choose-model') {
                    await chooseModel(providerId, modelId);
                    return;
                }
                if (button.dataset.action === 'probe-model') {
                    await probeModel(providerId, modelId, button);
                }
            });
        }

        if (modelSearch) {
            modelSearch.addEventListener('input', () => {
                renderModelSection();
            });
        }

        if (playgroundModelSelect) {
            playgroundModelSelect.addEventListener('change', () => {
                const val = playgroundModelSelect.value;
                if (val !== 'free-proxy/auto') {
                    const parts = val.split('/');
                    state.selectedProvider = parts[0];
                    state.selectedModel = parts.slice(1).join('/');
                } else {
                    state.selectedProvider = 'free-proxy';
                    state.selectedModel = 'auto';
                }
                persistSelection();
            });
        }

        if (playgroundInput) {
            playgroundInput.addEventListener('input', () => {
                adjustTextareaHeight(playgroundInput);
                playgroundSendBtn.disabled = !playgroundInput.value.trim();
            });

            playgroundInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (!playgroundSendBtn.disabled) {
                        sendPlaygroundChat();
                    }
                }
            });
        }

        if (playgroundSendBtn) {
            playgroundSendBtn.addEventListener('click', async () => {
                await sendPlaygroundChat();
            });
        }

        if (playgroundProbeBtn) {
            playgroundProbeBtn.addEventListener('click', async () => {
                await probePlaygroundModel();
            });
        }

        if (clearChatBtn) {
            clearChatBtn.addEventListener('click', () => {
                state.messages = [];
                renderPlaygroundMessages();
                hidePlaygroundDiagnostics();
            });
        }

        document.getElementById('reloadPageBtn').addEventListener('click', async () => {
            await bootstrap(true);
            showToast('鐘舵佸凡鍒锋柊', 'info');
        });



        async function bootstrap(forceModels = false) {
            await loadProxyKey();
            await loadStatuses();
            await loadPreferredModel();
            await ensureVisibleModels(forceModels);
            renderPlaygroundMessages();
            
            // Fire and forget stats loading to avoid blocking the main UI
            fetchUsageStats();
            fetchModelsStats();
            
            setDiagnostics('status-info', [
                '鍔ㄤ綔: 椤甸潰鍒濆鍖?,
                `缁撴灉: 宸插姞杞?${configuredProviders().length} 涓凡閰嶇疆 provider`,
                configuredProviders().length ? '寤鸿: 鍏堢偣鈥滈獙璇佲濓紝鍐嶉夋嫨鎺ㄨ崘妯″瀷鍋氭帰娴嬫垨鑱婂ぉ楠岃瘉銆? : '寤鸿: 鍏堥厤缃嚦灏戜竴涓?provider 鐨?API Key銆?,
            ]);
            updateAddProviderLink();
        }



        window.moveModel = async function(index, direction) {
            if (index < 0 || index >= currentModelsOrder.length) return;
            let newOrder = [...currentModelsOrder];
            const item = newOrder[index];
            
            if (direction === 'top') {
                newOrder.splice(index, 1);
                newOrder.unshift(item);
            } else if (direction === 'up' && index > 0) {
                newOrder.splice(index, 1);
                newOrder.splice(index - 1, 0, item);
            } else if (direction === 'down' && index < newOrder.length - 1) {
                newOrder.splice(index, 1);
                newOrder.splice(index + 1, 0, item);
            } else {
                return; // Cannot move
            }
            
            try {
                const orderStrings = newOrder.map(i => `${i.provider}/${i.model}`);
                const res = await fetch('/api/manual-order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order: orderStrings })
                });
                if (res.ok) {
                    showToast('鎺掑簭宸蹭繚瀛?);
                    await fetchModelsStats();
                } else {
                    showToast('淇濆瓨鎺掑簭澶辫触', 'error');
                }
            } catch (err) {
                console.error(err);
                showToast('缃戠粶閿欒锛屼繚瀛樺け失败', 'error');
            }
        };

        async function fetchUsageStats() {
            const tbody = document.getElementById('usageStatsBody');
            try {
                const response = await fetch('/api/usage-stats');
                if (response.status === 401) {
                    showToast('鏈櫥褰曟垨鍑瘉鏃犳晥锛岃閲嶆柊鐧诲綍', 'error');
                    window.location.href = '/login';
                    return;
                }
                const data = await response.json();
                if (!data || !data.stats || data.stats.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" style="padding: 12px; text-align: center; color: var(--muted);">鏆傛棤妯″瀷璋冪敤鏁版嵁</td></tr>';
                    return;
                }
                let html = '';
                for (const row of data.stats) {
                    html += `<tr style="border-bottom: 1px solid var(--line);"><td style="padding: 12px; font-weight: 600; color: var(--primary);">${escapeHtml(row.provider)}</td><td style="padding: 12px; font-family: monospace;">${escapeHtml(row.model)}</td><td style="padding: 12px;">${escapeHtml(String(row.usage_count))}</td></tr>`;
                }
                tbody.innerHTML = html;
            } catch (error) {
                console.error(error);
                tbody.innerHTML = '<tr><td colspan="3" style="padding: 12px; text-align: center; color: var(--danger);">鍔犺浇澶辫触</td></tr>';
            }
        }

        // Removed duplicate fetchModelsStats to prevent overriding the main implementation

        bootstrap().catch((error) => {
            setDiagnostics('status-error', [
                '椤甸潰鍒濆鍖栧け失败',
                error.message || '鏈煡閿欒',
            ]);
            showToast('鍒濆鍖栧け失败', 'error');
        });
    
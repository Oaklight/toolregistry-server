// Gateway admin extension — keys, frontend switches and runtime sources.
//
// Loaded by the admin shell through the extension contract v1:
//   import('…/assets/ext/gateway/main.js').then(m => m.register({container, api, i18n}))
//
// The module owns no authentication: every request goes through the shell's
// API client, which already carries the operator session.  Labels live here
// rather than in the shell dictionary, so the page ships its own EN/ZH map.

const STRINGS = {
    en: {
        'gw.tab.keys': 'Keys',
        'gw.tab.frontends': 'Frontends',
        'gw.tab.sources': 'Sources',
        'gw.refresh': 'Refresh',
        'gw.empty': 'Nothing to show',
        'gw.loading': 'Loading…',
        'gw.error': 'Request failed: {message}',
        'gw.copy': 'Copy',
        'gw.close': 'Close',
        'gw.copied': 'Copied',
        'gw.confirmRevoke': 'Revoke key "{label}"? Clients using it stop working on their next request.',
        'gw.confirmRemove': 'Remove source "{detail}"? Its tools are unregistered.',

        'gw.keys.title': 'Client keys',
        'gw.keys.hint': 'Inbound keys for the tool frontends. A secret is shown once, at creation.',
        'gw.keys.label': 'Label',
        'gw.keys.scopes': 'Scopes',
        'gw.keys.scopesHint': 'Comma-separated namespaces; leave empty to deny every tool.',
        'gw.keys.tools': 'Tool overrides',
        'gw.keys.toolsHint': 'One tool=allow or tool=deny per line. An override wins over the namespaces.',
        'gw.keys.expires': 'Expires',
        'gw.keys.expiresHint': 'Optional, local time.',
        'gw.keys.created': 'Created',
        'gw.keys.lastUsed': 'Last used',
        'gw.keys.status': 'Status',
        'gw.keys.actions': '',
        'gw.keys.create': 'Create key',
        'gw.keys.revoke': 'Revoke',
        'gw.keys.active': 'Active',
        'gw.keys.expired': 'Expired',
        'gw.keys.revoked': 'Revoked',
        'gw.keys.never': 'Never',
        'gw.keys.created.title': 'Key created',
        'gw.keys.created.hint': 'Store this secret now. It is not shown again and cannot be recovered.',
        'gw.keys.created.secret': 'Secret',
        'gw.keys.created.stored': 'I have stored it',

        'gw.frontends.title': 'Frontends',
        'gw.frontends.hint': 'A disabled frontend answers 404 on its path; the others keep serving.',
        'gw.frontends.name': 'Frontend',
        'gw.frontends.path': 'Path',
        'gw.frontends.transport': 'Transport',
        'gw.frontends.enabled': 'Enabled',
        'gw.frontends.save': 'Save',
        'gw.frontends.persisted': 'Saved to the config file.',
        'gw.frontends.notPersisted': 'Applied, but not saved: {reason}',

        'gw.sources.title': 'Sources',
        'gw.sources.hint': 'Runtime tool sources. Adding one changes the served tools without a restart.',
        'gw.sources.kind': 'Kind',
        'gw.sources.detail': 'Source',
        'gw.sources.namespace': 'Namespace',
        'gw.sources.tools': 'Tools',
        'gw.sources.status': 'Status',
        'gw.sources.lastError': 'Last error',
        'gw.sources.actions': '',
        'gw.sources.add': 'Add source',
        'gw.sources.url': 'Spec or server URL',
        'gw.sources.module': 'Module or class',
        'gw.sources.retry': 'Retry',
        'gw.sources.remove': 'Remove',
        'gw.sources.statusApplied': 'Applied',
        'gw.sources.statusExcluded': 'Excluded',
        'gw.sources.statusFailed': 'Failed',
        'gw.sources.persisted': 'Saved to the config file.',
        'gw.sources.notPersisted': 'Applied, but not saved: {reason}',
    },
    zh: {
        'gw.tab.keys': '密钥',
        'gw.tab.frontends': '前端',
        'gw.tab.sources': '来源',
        'gw.refresh': '刷新',
        'gw.empty': '暂无内容',
        'gw.loading': '加载中…',
        'gw.error': '请求失败：{message}',
        'gw.copy': '复制',
        'gw.close': '关闭',
        'gw.copied': '已复制',
        'gw.confirmRevoke': '撤销密钥“{label}”？使用它的客户端将在下一次请求时被拒绝。',
        'gw.confirmRemove': '移除来源“{detail}”？其工具将被注销。',

        'gw.keys.title': '客户端密钥',
        'gw.keys.hint': '工具前端的入站密钥。密钥只在创建时显示一次。',
        'gw.keys.label': '名称',
        'gw.keys.scopes': '权限范围',
        'gw.keys.scopesHint': '以逗号分隔的命名空间；留空表示拒绝所有工具。',
        'gw.keys.tools': '按工具覆盖',
        'gw.keys.toolsHint': '每行一个 tool=allow 或 tool=deny。覆盖优先于命名空间。',
        'gw.keys.expires': '过期时间',
        'gw.keys.expiresHint': '可选，本地时间。',
        'gw.keys.created': '创建时间',
        'gw.keys.lastUsed': '最近使用',
        'gw.keys.status': '状态',
        'gw.keys.actions': '',
        'gw.keys.create': '创建密钥',
        'gw.keys.revoke': '撤销',
        'gw.keys.active': '有效',
        'gw.keys.expired': '已过期',
        'gw.keys.revoked': '已撤销',
        'gw.keys.never': '永不',
        'gw.keys.created.title': '密钥已创建',
        'gw.keys.created.hint': '请立即保存此密钥。它不会再次显示，也无法找回。',
        'gw.keys.created.secret': '密钥',
        'gw.keys.created.stored': '我已保存',

        'gw.frontends.title': '前端',
        'gw.frontends.hint': '停用的前端在其路径上返回 404，其他前端继续服务。',
        'gw.frontends.name': '前端',
        'gw.frontends.path': '路径',
        'gw.frontends.transport': '传输',
        'gw.frontends.enabled': '启用',
        'gw.frontends.save': '保存',
        'gw.frontends.persisted': '已写入配置文件。',
        'gw.frontends.notPersisted': '已生效，但未保存：{reason}',

        'gw.sources.title': '来源',
        'gw.sources.hint': '运行时工具来源。添加来源无需重启即可改变提供的工具。',
        'gw.sources.kind': '类型',
        'gw.sources.detail': '来源',
        'gw.sources.namespace': '命名空间',
        'gw.sources.tools': '工具',
        'gw.sources.status': '状态',
        'gw.sources.lastError': '最近错误',
        'gw.sources.actions': '',
        'gw.sources.add': '添加来源',
        'gw.sources.url': '规范或服务地址',
        'gw.sources.module': '模块或类',
        'gw.sources.retry': '重试',
        'gw.sources.remove': '移除',
        'gw.sources.statusApplied': '已应用',
        'gw.sources.statusExcluded': '已排除',
        'gw.sources.statusFailed': '失败',
        'gw.sources.persisted': '已写入配置文件。',
        'gw.sources.notPersisted': '已生效，但未保存：{reason}',
    },
};

const state = {
    lang: 'en',
    tab: 'keys',
    keys: [],
    frontends: [],
    sources: [],
    secret: null,
};

let root = null;
let client = null;
let onLanguageChange = null;

// ---------------------------------------------------------------- helpers

function T(key, params) {
    const table = STRINGS[state.lang] || STRINGS.en;
    let text = table[key] || STRINGS.en[key] || key;
    if (params) {
        for (const [name, value] of Object.entries(params)) {
            text = text.replace('{' + name + '}', String(value));
        }
    }
    return text;
}

function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
        if (name === 'text') node.textContent = value;
        else if (name === 'class') node.className = value;
        else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
        else if (value !== null && value !== undefined) node.setAttribute(name, value);
    }
    for (const child of children || []) {
        if (child === null || child === undefined) continue;
        node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
}

function section(titleKey, hintKey, actions, body) {
    return el('section', { class: 'gw-panel' }, [
        el('div', { class: 'gw-panel-head' }, [
            el('div', {}, [
                el('h3', { class: 'gw-panel-title', text: T(titleKey) }),
                hintKey ? el('p', { class: 'gw-hint', text: T(hintKey) }) : null,
            ]),
            el('div', { class: 'gw-panel-actions' }, actions),
        ]),
        body,
    ]);
}

function message(text, kind) {
    return el('div', { class: 'gw-message gw-message-' + (kind || 'info'), text });
}

function table(headers, rows) {
    if (!rows.length) return el('p', { class: 'gw-empty', text: T('gw.empty') });
    return el('div', { class: 'gw-table-wrap' }, [
        el('table', { class: 'gw-table' }, [
            el('thead', {}, [
                el('tr', {}, headers.map((header) => el('th', { text: T(header) }))),
            ]),
            el('tbody', {}, rows),
        ]),
    ]);
}

function pill(text, kind) {
    return el('span', { class: 'gw-pill gw-pill-' + kind, text });
}

async function call(method, path, body) {
    try {
        if (body === undefined) return await client[method](path);
        return await client[method](path, body);
    } catch (error) {
        throw new Error(T('gw.error', { message: error.message || String(error) }));
    }
}

function formatTime(value) {
    if (!value) return T('gw.keys.never');
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(state.lang);
}

function persistNote(result, key) {
    if (!result || result.persisted === undefined) return null;
    if (result.persisted) return el('p', { class: 'gw-note', text: T(key + '.persisted') });
    const reason = result.persist_error || result.warning || '';
    return message(T(key + '.notPersisted', { reason }), 'warn');
}

// Keyboard: Escape closes the one-time secret panel.
function onKeyDown(event) {
    if (event.key === 'Escape' && state.secret) {
        state.secret = null;
        renderKeys();
    }
}

// ---------------------------------------------------------------- keys

function keyStatus(record) {
    if (record.revoked_at) return { kind: 'revoked', label: T('gw.keys.revoked') };
    if (record.expires_at) {
        const expires = new Date(record.expires_at);
        if (!Number.isNaN(expires.getTime()) && expires.getTime() <= Date.now()) {
            return { kind: 'expired', label: T('gw.keys.expired') };
        }
    }
    return { kind: 'active', label: T('gw.keys.active') };
}

function scopeText(scopes) {
    if (!scopes) return '—';
    if (Array.isArray(scopes)) return scopes.join(', ') || '—';
    if (typeof scopes === 'object') {
        const namespaces = (scopes.namespaces || []).join(', ');
        const tools = Object.entries(scopes.tools || {})
            .map(([name, allowed]) => name + (allowed ? ' +' : ' −'));
        return [namespaces, ...tools].filter(Boolean).join(', ') || '—';
    }
    return String(scopes);
}

function renderKeys() {
    const host = root.querySelector('#gw-keys');
    if (!host) return;
    host.replaceChildren();

    const body = [];
    if (state.secret) {
        const secret = el('code', { class: 'gw-secret', text: state.secret });
        const copy = el('button', {
            class: 'gw-btn',
            type: 'button',
            text: T('gw.copy'),
            onclick: () => {
                navigator.clipboard?.writeText(state.secret).then(() => {
                    copy.textContent = T('gw.copied');
                });
            },
        });
        const close = el('button', {
            class: 'gw-btn gw-btn-primary',
            type: 'button',
            text: T('gw.keys.created.stored'),
            onclick: () => {
                state.secret = null;
                renderKeys();
            },
        });
        body.push(
            el('div', { class: 'gw-secret-card', role: 'dialog', 'aria-modal': 'true' }, [
                el('h4', { text: T('gw.keys.created.title') }),
                el('p', { class: 'gw-hint', text: T('gw.keys.created.hint') }),
                el('div', { class: 'gw-secret-row' }, [secret, copy]),
                el('div', { class: 'gw-actions' }, [close]),
            ]),
        );
    }

    const rows = state.keys.map((record) => {
        const status = keyStatus(record);
        const revoke = el('button', {
            class: 'gw-btn gw-btn-danger',
            type: 'button',
            text: T('gw.keys.revoke'),
            onclick: () => revokeKey(record),
        });
        return el('tr', {}, [
            el('td', { text: record.label || record.name || record.id || '' }),
            el('td', {}, [
                el('span', {
                    class: 'gw-mono',
                    text: scopeText(record.scopes),
                }),
            ]),
            el('td', { text: formatTime(record.created_at) }),
            el('td', { text: record.expires_at ? formatTime(record.expires_at) : T('gw.keys.never') }),
            el('td', { text: record.last_used_at ? formatTime(record.last_used_at) : '—' }),
            el('td', {}, [pill(status.label, status.kind)]),
            el('td', {}, [status.kind === 'revoked' ? null : revoke]),
        ]);
    });

    body.push(
        table(
            [
                'gw.keys.label',
                'gw.keys.scopes',
                'gw.keys.created',
                'gw.keys.expires',
                'gw.keys.lastUsed',
                'gw.keys.status',
                'gw.keys.actions',
            ],
            rows,
        ),
    );

    body.push(
        el('form', {
            class: 'gw-form',
            onsubmit: (event) => {
                event.preventDefault();
                createKey(event.currentTarget);
            },
        }, [
            el('label', { class: 'gw-field' }, [
                el('span', { text: T('gw.keys.label') }),
                el('input', { name: 'label', required: 'required', autocomplete: 'off' }),
            ]),
            el('label', { class: 'gw-field' }, [
                el('span', { text: T('gw.keys.scopes') }),
                el('input', { name: 'scopes', autocomplete: 'off', placeholder: 'default, files' }),
                el('small', { class: 'gw-hint', text: T('gw.keys.scopesHint') }),
            ]),
            el('label', { class: 'gw-field' }, [
                el('span', { text: T('gw.keys.tools') }),
                el('textarea', { name: 'tools', rows: '3', autocomplete: 'off',
                                 placeholder: 'web-dangerous=deny' }),
                el('small', { class: 'gw-hint', text: T('gw.keys.toolsHint') }),
            ]),
            el('label', { class: 'gw-field' }, [
                el('span', { text: T('gw.keys.expires') }),
                el('input', { name: 'expires_at', type: 'datetime-local' }),
                el('small', { class: 'gw-hint', text: T('gw.keys.expiresHint') }),
            ]),
            el('div', { class: 'gw-actions' }, [
                el('button', {
                    class: 'gw-btn gw-btn-primary',
                    type: 'submit',
                    text: T('gw.keys.create'),
                }),
            ]),
        ]),
    );

    host.appendChild(
        section('gw.keys.title', 'gw.keys.hint', [
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.refresh'),
                onclick: () => loadKeys(),
            }),
        ], el('div', {}, body)),
    );
}

async function loadKeys() {
    const host = root.querySelector('#gw-keys');
    if (!host) return;
    try {
        const data = await call('get', '/keys');
        state.keys = data.keys || [];
        renderKeys();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

function parseOverrides(text) {
    const tools = {};
    String(text || '')
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .forEach((item) => {
            const [name, mode] = item.split('=').map((part) => part.trim());
            const allowed = String(mode || '').toLowerCase();
            if (!name || (allowed !== 'allow' && allowed !== 'deny')) return;
            tools[name] = allowed === 'allow';
        });
    return tools;
}

async function createKey(form) {
    const data = new FormData(form);
    const namespaces = String(data.get('scopes') || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    const tools = parseOverrides(data.get('tools'));
    const expiresRaw = String(data.get('expires_at') || '');
    const payload = {
        label: String(data.get('label') || '').trim(),
        scopes: { namespaces, tools },
    };
    if (expiresRaw) payload.expires_at = new Date(expiresRaw).toISOString();
    try {
        const created = await call('post', '/keys', payload);
        state.secret = created.secret || null;
        await loadKeys();
    } catch (error) {
        root.querySelector('#gw-keys')?.appendChild(message(error.message, 'error'));
    }
}

async function revokeKey(record) {
    const label = record.label || record.name || record.id;
    if (!window.confirm(T('gw.confirmRevoke', { label }))) return;
    try {
        await call('post', '/keys/' + encodeURIComponent(record.id) + '/revoke');
        await loadKeys();
    } catch (error) {
        root.querySelector('#gw-keys')?.appendChild(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- frontends

function renderFrontends() {
    const host = root.querySelector('#gw-frontends');
    if (!host) return;
    host.replaceChildren();

    const rows = state.frontends.map((record) => {
        const toggle = el('input', {
            type: 'checkbox',
            checked: record.enabled ? 'checked' : null,
            'aria-label': record.name,
        });
        const path = el('input', {
            class: 'gw-input-path',
            value: record.path || '',
            'aria-label': T('gw.frontends.path'),
        });
        const save = el('button', {
            class: 'gw-btn',
            type: 'button',
            text: T('gw.frontends.save'),
            onclick: () => updateFrontend(record.name, {
                enabled: toggle.checked,
                path: path.value.trim(),
            }),
        });
        return el('tr', {}, [
            el('td', { class: 'gw-mono', text: record.name }),
            el('td', {}, [path]),
            el('td', { text: record.transport || '' }),
            el('td', {}, [toggle]),
            el('td', {}, [save]),
        ]);
    });

    host.appendChild(
        section('gw.frontends.title', 'gw.frontends.hint', [
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.refresh'),
                onclick: () => loadFrontends(),
            }),
        ], table(
            [
                'gw.frontends.name',
                'gw.frontends.path',
                'gw.frontends.transport',
                'gw.frontends.enabled',
                'gw.keys.actions',
            ],
            rows,
        )),
    );
}

async function loadFrontends() {
    const host = root.querySelector('#gw-frontends');
    if (!host) return;
    try {
        const data = await call('get', '/frontends');
        state.frontends = data.frontends || [];
        renderFrontends();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

async function updateFrontend(name, payload) {
    const host = root.querySelector('#gw-frontends');
    try {
        const data = await call('patch', '/frontends/' + encodeURIComponent(name), payload);
        state.frontends = state.frontends.map((record) => (
            record.name === name ? { ...record, ...(data.frontend || {}) } : record
        ));
        renderFrontends();
        const note = persistNote(data.frontend, 'gw.frontends');
        if (note) host.appendChild(note);
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- sources

function sourceStatus(record) {
    const status = String(record.status || '');
    if (status === 'failed') return { kind: 'failed', label: T('gw.sources.statusFailed') };
    if (status === 'excluded') return { kind: 'excluded', label: T('gw.sources.statusExcluded') };
    return { kind: 'applied', label: T('gw.sources.statusApplied') };
}

function renderSources() {
    const host = root.querySelector('#gw-sources');
    if (!host) return;
    host.replaceChildren();

    const rows = state.sources.map((record) => {
        const status = sourceStatus(record);
        const actions = [];
        if (status.kind === 'failed') {
            actions.push(el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.sources.retry'),
                onclick: () => retrySource(record.id),
            }));
        }
        actions.push(el('button', {
            class: 'gw-btn gw-btn-danger',
            type: 'button',
            text: T('gw.sources.remove'),
            onclick: () => removeSource(record),
        }));
        const counts = record.tool_count || {};
        return el('tr', {}, [
            el('td', { text: record.kind || '' }),
            el('td', { class: 'gw-mono', text: record.detail || '' }),
            el('td', { text: record.namespace || '—' }),
            el('td', { text: (counts.enabled ?? 0) + '/' + (counts.total ?? 0) }),
            el('td', {}, [pill(status.label, status.kind)]),
            el('td', { class: 'gw-error-cell', text: record.last_error || '' }),
            el('td', {}, actions),
        ]);
    });

    host.appendChild(
        section('gw.sources.title', 'gw.sources.hint', [
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.refresh'),
                onclick: () => loadSources(),
            }),
        ], el('div', {}, [
            table(
                [
                    'gw.sources.kind',
                    'gw.sources.detail',
                    'gw.sources.namespace',
                    'gw.sources.tools',
                    'gw.sources.status',
                    'gw.sources.lastError',
                    'gw.sources.actions',
                ],
                rows,
            ),
            sourceForm(),
        ])),
    );
}

function sourceForm() {
    const type = el('select', { name: 'type' }, [
        el('option', { value: 'openapi', text: 'OpenAPI' }),
        el('option', { value: 'mcp', text: 'MCP' }),
        el('option', { value: 'python', text: 'Python' }),
    ]);
    return el('form', {
        class: 'gw-form',
        onsubmit: (event) => {
            event.preventDefault();
            addSource(event.currentTarget);
        },
    }, [
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.sources.kind') }),
            type,
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.sources.url') }),
            el('input', { name: 'target', required: 'required', autocomplete: 'off' }),
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.sources.namespace') }),
            el('input', { name: 'namespace', autocomplete: 'off' }),
        ]),
        el('div', { class: 'gw-actions' }, [
            el('button', {
                class: 'gw-btn gw-btn-primary',
                type: 'submit',
                text: T('gw.sources.add'),
            }),
        ]),
    ]);
}

async function loadSources() {
    const host = root.querySelector('#gw-sources');
    if (!host) return;
    try {
        const data = await call('get', '/sources');
        state.sources = data.sources || [];
        renderSources();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

function sourcePayload(form) {
    const data = new FormData(form);
    const type = String(data.get('type') || 'openapi');
    const target = String(data.get('target') || '').trim();
    const namespace = String(data.get('namespace') || '').trim();
    const payload = { type };
    if (type === 'openapi') payload.url = target;
    else if (type === 'mcp') payload.url = target;
    else payload.class = target;
    if (namespace) payload.namespace = namespace;
    return payload;
}

async function addSource(form) {
    const host = root.querySelector('#gw-sources');
    try {
        const result = await call('post', '/sources', sourcePayload(form));
        await loadSources();
        const note = persistNote(result, 'gw.sources');
        if (note) host.appendChild(note);
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

async function retrySource(sourceId) {
    const host = root.querySelector('#gw-sources');
    try {
        await call('post', '/sources/' + encodeURIComponent(sourceId) + '/retry');
        await loadSources();
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

async function removeSource(record) {
    const detail = record.detail || record.id;
    if (!window.confirm(T('gw.confirmRemove', { detail }))) return;
    const host = root.querySelector('#gw-sources');
    try {
        const result = await call('delete', '/sources/' + encodeURIComponent(record.id));
        await loadSources();
        const note = persistNote(result, 'gw.sources');
        if (note) host.appendChild(note);
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- shell

function renderTabs() {
    const tabs = [
        ['keys', 'gw.tab.keys'],
        ['frontends', 'gw.tab.frontends'],
        ['sources', 'gw.tab.sources'],
    ];
    const bar = el('div', { class: 'gw-tabs', role: 'tablist' }, tabs.map(([name, key]) => (
        el('button', {
            class: 'gw-tab' + (state.tab === name ? ' gw-tab-active' : ''),
            type: 'button',
            role: 'tab',
            'aria-selected': state.tab === name ? 'true' : 'false',
            text: T(key),
            onclick: () => {
                state.tab = name;
                render();
                refreshCurrent();
            },
        })
    )));
    return bar;
}

function render() {
    if (!root) return;
    root.replaceChildren();
    const shell = el('div', { class: 'gw-shell' }, [renderTabs()]);
    const panes = {
        keys: el('div', { id: 'gw-keys', class: 'gw-pane', role: 'tabpanel' }),
        frontends: el('div', { id: 'gw-frontends', class: 'gw-pane', role: 'tabpanel' }),
        sources: el('div', { id: 'gw-sources', class: 'gw-pane', role: 'tabpanel' }),
    };
    for (const [name, pane] of Object.entries(panes)) {
        pane.style.display = name === state.tab ? '' : 'none';
        shell.appendChild(pane);
    }
    root.appendChild(shell);

    if (state.tab === 'keys') renderKeys();
    else if (state.tab === 'frontends') renderFrontends();
    else renderSources();
}

async function refreshCurrent() {
    if (state.tab === 'keys') await loadKeys();
    else if (state.tab === 'frontends') await loadFrontends();
    else await loadSources();
}

export async function register({ container, api, i18n }) {
    root = container;
    client = api;
    state.lang = (i18n && i18n.lang) === 'zh' ? 'zh' : 'en';
    render();
    await refreshCurrent();
    document.addEventListener('keydown', onKeyDown);

    // The extension contract has no language-change callback yet, so follow
    // the shell's own switcher and re-render when it changes.
    const switcher = document.getElementById('langSwitcher');
    if (switcher) {
        onLanguageChange = () => {
            state.lang = switcher.value === 'zh' ? 'zh' : 'en';
            render();
            refreshCurrent();
        };
        switcher.addEventListener('change', onLanguageChange);
    }
}

export function unmount() {
    document.removeEventListener('keydown', onKeyDown);
    const switcher = document.getElementById('langSwitcher');
    if (switcher && onLanguageChange) {
        switcher.removeEventListener('change', onLanguageChange);
    }
    onLanguageChange = null;
    root = null;
    client = null;
    state.secret = null;
}

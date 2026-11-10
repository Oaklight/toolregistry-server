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
        'gw.tab.clients': 'Clients',
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
        'gw.confirmRotate': 'Rotate key "{label}"? The current secret stops working on the next request, and the new secret is shown once.',
        'gw.confirmRemove': 'Remove source "{detail}"? Its tools are unregistered.',

        'gw.keys.title': 'Client keys',
        'gw.keys.hint': 'Inbound keys for the tool frontends. A secret is shown once, at creation or rotation.',
        'gw.keys.label': 'Label',
        'gw.keys.scopes': 'Scopes',
        'gw.keys.scopesHint': 'Comma-separated namespaces; leave empty or omit to deny every tool.',
        'gw.keys.tools': 'Tool overrides',
        'gw.keys.toolsHint': 'One tool=allow or tool=deny per line. An override wins over the namespaces.',
        'gw.keys.toolsInvalid': 'Fix or remove these tool overrides before creating the key: {lines}',
        'gw.keys.concurrency': 'Concurrency',
        'gw.keys.concurrencyHint': 'Most calls this key may run at once. Empty uses the server default.',
        'gw.keys.concurrencyInvalid': 'Concurrency must be a positive whole number.',
        'gw.keys.expires': 'Expires',
        'gw.keys.expiresHint': 'Optional, local time. Empty means no expiry.',
        'gw.keys.created': 'Created',
        'gw.keys.lastUsed': 'Last used',
        'gw.keys.status': 'Status',
        'gw.keys.actions': '',
        'gw.keys.create': 'Create key',
        'gw.keys.revoke': 'Revoke',
        'gw.keys.rotate': 'Rotate',
        'gw.keys.edit': 'Edit',
        'gw.keys.details': 'Details',
        'gw.keys.save': 'Save',
        'gw.keys.cancel': 'Cancel',
        'gw.keys.editNothing': 'Change at least one field.',
        'gw.keys.edit.title': 'Edit key',
        'gw.keys.detail.title': 'Key details',
        'gw.keys.usage': 'Usage',
        'gw.keys.usageHint': 'Calls recorded for this key in the key database.',
        'gw.keys.calls': 'Calls',
        'gw.keys.ok': 'OK',
        'gw.keys.errors': 'Errors',
        'gw.keys.busy': 'Busy',
        'gw.keys.denied': 'Denied',
        'gw.keys.lastSuccess': 'Last success',
        'gw.keys.active': 'Active',
        'gw.keys.expired': 'Expired',
        'gw.keys.revoked': 'Revoked',
        'gw.keys.never': 'Never',
        'gw.keys.created.title': 'Key created',
        'gw.keys.created.hint': 'Store this secret now. It is not shown again and cannot be recovered.',
        'gw.keys.rotated.title': 'Key rotated',
        'gw.keys.rotated.hint': 'The old secret no longer works. Store this new secret now; it is not shown again.',
        'gw.keys.created.secret': 'Secret',
        'gw.keys.created.stored': 'I have stored it',

        'gw.clients.title': 'Clients',
        'gw.clients.hint': 'Live per-client call counters for this process. They reset when the gateway restarts.',
        'gw.clients.client': 'Client',
        'gw.clients.key': 'Key',
        'gw.clients.limit': 'Limit',
        'gw.clients.calls': 'Calls',
        'gw.clients.errors': 'Errors',
        'gw.clients.active': 'Active',
        'gw.clients.queued': 'Queued',

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
        'gw.sources.refreshSpec': 'Refresh spec',
        'gw.sources.refreshSkipped': 'Spec unchanged; no tools were touched.',
        'gw.sources.refreshed': 'Spec refreshed: {added} added, {removed} removed, {updated} updated, {unchanged} unchanged.',
        'gw.sources.remove': 'Remove',
        'gw.sources.statusApplied': 'Applied',
        'gw.sources.statusExcluded': 'Excluded',
        'gw.sources.statusFailed': 'Failed',
        'gw.sources.persisted': 'Saved to the config file.',
        'gw.sources.notPersisted': 'Applied, but not saved: {reason}',
    },
    zh: {
        'gw.tab.keys': '密钥',
        'gw.tab.clients': '客户端',
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
        'gw.confirmRotate': '轮换密钥“{label}”？当前密钥将在下一次请求时失效，新密钥只显示一次。',
        'gw.confirmRemove': '移除来源“{detail}”？其工具将被注销。',

        'gw.keys.title': '客户端密钥',
        'gw.keys.hint': '工具前端的入站密钥。密钥只在创建或轮换时显示一次。',
        'gw.keys.label': '名称',
        'gw.keys.scopes': '权限范围',
        'gw.keys.scopesHint': '以逗号分隔的命名空间；留空或省略表示拒绝所有工具。',
        'gw.keys.tools': '按工具覆盖',
        'gw.keys.toolsHint': '每行一个 tool=allow 或 tool=deny。覆盖优先于命名空间。',
        'gw.keys.toolsInvalid': '创建密钥前请修正或移除这些按工具覆盖项：{lines}',
        'gw.keys.concurrency': '并发上限',
        'gw.keys.concurrencyHint': '此密钥同时允许的最大调用数；留空使用服务器默认值。',
        'gw.keys.concurrencyInvalid': '并发上限必须是正整数。',
        'gw.keys.expires': '过期时间',
        'gw.keys.expiresHint': '可选，本地时间；留空表示不过期。',
        'gw.keys.created': '创建时间',
        'gw.keys.lastUsed': '最近使用',
        'gw.keys.status': '状态',
        'gw.keys.actions': '',
        'gw.keys.create': '创建密钥',
        'gw.keys.revoke': '撤销',
        'gw.keys.rotate': '轮换',
        'gw.keys.edit': '编辑',
        'gw.keys.details': '详情',
        'gw.keys.save': '保存',
        'gw.keys.cancel': '取消',
        'gw.keys.editNothing': '请至少修改一个字段。',
        'gw.keys.edit.title': '编辑密钥',
        'gw.keys.detail.title': '密钥详情',
        'gw.keys.usage': '用量',
        'gw.keys.usageHint': '密钥数据库中记录的调用次数。',
        'gw.keys.calls': '调用',
        'gw.keys.ok': '成功',
        'gw.keys.errors': '错误',
        'gw.keys.busy': '繁忙',
        'gw.keys.denied': '拒绝',
        'gw.keys.lastSuccess': '最近成功',
        'gw.keys.active': '有效',
        'gw.keys.expired': '已过期',
        'gw.keys.revoked': '已撤销',
        'gw.keys.never': '永不',
        'gw.keys.created.title': '密钥已创建',
        'gw.keys.created.hint': '请立即保存此密钥。它不会再次显示，也无法找回。',
        'gw.keys.rotated.title': '密钥已轮换',
        'gw.keys.rotated.hint': '旧密钥已失效。请立即保存新密钥，它不会再次显示。',
        'gw.keys.created.secret': '密钥',
        'gw.keys.created.stored': '我已保存',

        'gw.clients.title': '客户端',
        'gw.clients.hint': '本进程内按客户端统计的调用计数，网关重启后清零。',
        'gw.clients.client': '客户端',
        'gw.clients.key': '密钥 ID',
        'gw.clients.limit': '并发上限',
        'gw.clients.calls': '调用',
        'gw.clients.errors': '错误',
        'gw.clients.active': '进行中',
        'gw.clients.queued': '排队',

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
        'gw.sources.refreshSpec': '刷新规范',
        'gw.sources.refreshSkipped': '规范未变化，未改动任何工具。',
        'gw.sources.refreshed': '规范已刷新：新增 {added}，移除 {removed}，更新 {updated}，未变 {unchanged}。',
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
    clients: [],
    frontends: [],
    sources: [],
    secret: null,
    secretKind: 'created',
    editing: null,
    detail: null,
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

// Keyboard: Escape closes the one-time secret panel or an open dialog.
function onKeyDown(event) {
    if (event.key !== 'Escape') return;
    if (state.secret) {
        state.secret = null;
    } else if (state.editing) {
        state.editing = null;
    } else if (state.detail) {
        state.detail = null;
    } else {
        return;
    }
    renderKeys();
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

function usageOf(record) {
    const usage = record.usage || {};
    return {
        calls: usage.calls || 0,
        ok: usage.ok || 0,
        error: usage.error || 0,
        busy: usage.busy || 0,
        denied: usage.denied || 0,
        last_success_at: usage.last_success_at || null,
    };
}

function toLocalInput(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const pad = (number) => String(number).padStart(2, '0');
    return (
        date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) +
        'T' + pad(date.getHours()) + ':' + pad(date.getMinutes())
    );
}

function secretCard() {
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
    const rotated = state.secretKind === 'rotated';
    return el('div', { class: 'gw-secret-card', role: 'dialog', 'aria-modal': 'true' }, [
        el('h4', {
            text: T(rotated ? 'gw.keys.rotated.title' : 'gw.keys.created.title'),
        }),
        el('p', {
            class: 'gw-hint',
            text: T(rotated ? 'gw.keys.rotated.hint' : 'gw.keys.created.hint'),
        }),
        el('div', { class: 'gw-secret-row' }, [secret, copy]),
        el('div', { class: 'gw-actions' }, [close]),
    ]);
}

function detailCard(record) {
    const status = keyStatus(record);
    const usage = usageOf(record);
    const namespaces = Array.isArray(record.scopes)
        ? record.scopes.join(', ')
        : ((record.scopes || {}).namespaces || []).join(', ');
    const tools = Array.isArray(record.scopes)
        ? ''
        : Object.entries((record.scopes || {}).tools || {})
            .map(([name, allowed]) => name + '=' + (allowed ? 'allow' : 'deny'))
            .join(', ');
    const fields = [
        [T('gw.keys.label'), record.label || record.id || ''],
        ['ID', record.id || ''],
        [T('gw.keys.scopes'), namespaces || '—'],
        [T('gw.keys.tools'), tools || '—'],
        [T('gw.keys.created'), formatTime(record.created_at)],
        [T('gw.keys.expires'), record.expires_at ? formatTime(record.expires_at) : T('gw.keys.never')],
        [T('gw.keys.lastUsed'), record.last_used_at ? formatTime(record.last_used_at) : '—'],
        [T('gw.keys.concurrency'), record.max_concurrency || 'default'],
        [T('gw.keys.status'), status.label],
        [T('gw.keys.calls'), usage.calls],
        [T('gw.keys.ok'), usage.ok],
        [T('gw.keys.errors'), usage.error],
        [T('gw.keys.busy'), usage.busy],
        [T('gw.keys.denied'), usage.denied],
        [T('gw.keys.lastSuccess'), usage.last_success_at ? formatTime(usage.last_success_at) : '—'],
    ];
    const grid = el('div', { class: 'gw-detail-grid' }, fields.map(([name, value]) => (
        el('div', { class: 'gw-detail-row' }, [
            el('span', { class: 'gw-detail-name', text: name }),
            el('span', { text: String(value) }),
        ])
    )));
    const close = el('button', {
        class: 'gw-btn',
        type: 'button',
        text: T('gw.close'),
        onclick: () => {
            state.detail = null;
            renderKeys();
        },
    });
    return el('div', { class: 'gw-detail-card', role: 'dialog', 'aria-modal': 'true' }, [
        el('h4', { text: T('gw.keys.detail.title') }),
        grid,
        el('div', { class: 'gw-actions' }, [close]),
    ]);
}

function editCard(record) {
    const scopes = record.scopes || {};
    const namespaces = Array.isArray(scopes)
        ? scopes.join(', ')
        : (scopes.namespaces || []).join(', ');
    const tools = Array.isArray(scopes)
        ? ''
        : Object.entries(scopes.tools || {})
            .map(([name, allowed]) => name + '=' + (allowed ? 'allow' : 'deny'))
            .join('\n');
    const cancel = el('button', {
        class: 'gw-btn',
        type: 'button',
        text: T('gw.keys.cancel'),
        onclick: () => {
            state.editing = null;
            renderKeys();
        },
    });
    return el('form', {
        class: 'gw-form gw-edit-card',
        onsubmit: (event) => {
            event.preventDefault();
            saveKey(record, event.currentTarget);
        },
    }, [
        el('h4', { class: 'gw-edit-title', text: T('gw.keys.edit.title') }),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.keys.label') }),
            el('input', { name: 'label', required: 'required', autocomplete: 'off',
                          value: record.label || '' }),
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.keys.scopes') }),
            el('input', { name: 'scopes', autocomplete: 'off', value: namespaces,
                          placeholder: 'default, files' }),
            el('small', { class: 'gw-hint', text: T('gw.keys.scopesHint') }),
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.keys.tools') }),
            el('textarea', { name: 'tools', rows: '3', autocomplete: 'off',
                             text: tools, placeholder: 'web-dangerous=deny' }),
            el('small', { class: 'gw-hint', text: T('gw.keys.toolsHint') }),
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.keys.concurrency') }),
            el('input', { name: 'max_concurrency', type: 'number', min: '1',
                          step: '1', autocomplete: 'off',
                          value: record.max_concurrency || '' }),
            el('small', { class: 'gw-hint', text: T('gw.keys.concurrencyHint') }),
        ]),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.keys.expires') }),
            el('input', { name: 'expires_at', type: 'datetime-local',
                          value: toLocalInput(record.expires_at) }),
            el('small', { class: 'gw-hint', text: T('gw.keys.expiresHint') }),
        ]),
        el('div', { class: 'gw-actions' }, [
            el('button', {
                class: 'gw-btn gw-btn-primary',
                type: 'submit',
                text: T('gw.keys.save'),
            }),
            cancel,
        ]),
    ]);
}

function renderKeys() {
    const host = root.querySelector('#gw-keys');
    if (!host) return;
    host.replaceChildren();

    const body = [];
    if (state.secret) body.push(secretCard());
    if (state.detail) body.push(detailCard(state.detail));
    if (state.editing) {
        const editing = state.keys.find((record) => record.id === state.editing);
        if (editing) body.push(editCard(editing));
    }

    const rows = state.keys.map((record) => {
        const status = keyStatus(record);
        const usage = usageOf(record);
        const actions = [
            el('button', {
                class: 'gw-btn gw-btn-small',
                type: 'button',
                text: T('gw.keys.details'),
                onclick: () => showKey(record),
            }),
        ];
        if (status.kind !== 'revoked') {
            actions.push(el('button', {
                class: 'gw-btn gw-btn-small',
                type: 'button',
                text: T('gw.keys.edit'),
                onclick: () => {
                    state.editing = record.id;
                    state.detail = null;
                    renderKeys();
                },
            }));
        }
        if (status.kind === 'active') {
            actions.push(el('button', {
                class: 'gw-btn gw-btn-small',
                type: 'button',
                text: T('gw.keys.rotate'),
                onclick: () => rotateKey(record),
            }));
        }
        if (status.kind !== 'revoked') {
            actions.push(el('button', {
                class: 'gw-btn gw-btn-small gw-btn-danger',
                type: 'button',
                text: T('gw.keys.revoke'),
                onclick: () => revokeKey(record),
            }));
        }
        return el('tr', {}, [
            el('td', { text: record.label || record.name || record.id || '' }),
            el('td', {}, [
                el('span', {
                    class: 'gw-mono',
                    text: scopeText(record.scopes),
                }),
            ]),
            el('td', { class: 'gw-number', text: usage.calls }),
            el('td', { class: 'gw-number', text: usage.ok }),
            el('td', { class: 'gw-number', text: usage.error }),
            el('td', { class: 'gw-number', text: usage.busy }),
            el('td', { class: 'gw-number', text: usage.denied }),
            el('td', {
                text: usage.last_success_at ? formatTime(usage.last_success_at) : '—',
            }),
            el('td', {}, [pill(status.label, status.kind)]),
            el('td', {}, actions),
        ]);
    });

    body.push(
        table(
            [
                'gw.keys.label',
                'gw.keys.scopes',
                'gw.keys.calls',
                'gw.keys.ok',
                'gw.keys.errors',
                'gw.keys.busy',
                'gw.keys.denied',
                'gw.keys.lastSuccess',
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
                el('span', { text: T('gw.keys.concurrency') }),
                el('input', { name: 'max_concurrency', type: 'number', min: '1',
                              step: '1', autocomplete: 'off' }),
                el('small', { class: 'gw-hint', text: T('gw.keys.concurrencyHint') }),
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
        if (state.detail) {
            state.detail = state.keys.find(
                (record) => record.id === state.detail.id,
            ) || null;
        }
        renderKeys();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

// Parse the overrides textarea.  A line that does not read `name=allow` or
// `name=deny` is returned in `invalid` rather than dropped: a mistyped deny
// must not silently leave the tool allowed through its namespace.
function parseOverrides(text) {
    const tools = {};
    const invalid = [];
    String(text || '')
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean)
        .forEach((item) => {
            const parts = item.split('=').map((part) => part.trim());
            const name = parts[0];
            const mode = parts.length === 2 ? parts[1].toLowerCase() : '';
            if (!name || (mode !== 'allow' && mode !== 'deny')) {
                invalid.push(item);
                return;
            }
            tools[name] = mode === 'allow';
        });
    return { tools, invalid };
}

async function createKey(form) {
    const data = new FormData(form);
    const namespaces = String(data.get('scopes') || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    const overrides = parseOverrides(data.get('tools'));
    if (overrides.invalid.length) {
        root.querySelector('#gw-keys')?.appendChild(message(
            T('gw.keys.toolsInvalid', { lines: overrides.invalid.join(', ') }),
            'error',
        ));
        return;
    }
    const tools = overrides.tools;
    const concurrencyRaw = String(data.get('max_concurrency') || '').trim();
    const payload = {
        label: String(data.get('label') || '').trim(),
        scopes: { namespaces, tools },
    };
    if (concurrencyRaw) {
        const limit = Number(concurrencyRaw);
        if (!Number.isInteger(limit) || limit < 1) {
            root.querySelector('#gw-keys')?.appendChild(message(
                T('gw.keys.concurrencyInvalid'),
                'error',
            ));
            return;
        }
        payload.max_concurrency = limit;
    }
    const expiresRaw = String(data.get('expires_at') || '');
    if (expiresRaw) payload.expires_at = new Date(expiresRaw).toISOString();
    try {
        const created = await call('post', '/keys', payload);
        state.secret = created.secret || null;
        state.secretKind = 'created';
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

async function rotateKey(record) {
    const label = record.label || record.name || record.id;
    if (!window.confirm(T('gw.confirmRotate', { label }))) return;
    try {
        const rotated = await call(
            'post', '/keys/' + encodeURIComponent(record.id) + '/rotate',
        );
        state.secret = rotated.secret || null;
        state.secretKind = 'rotated';
        state.editing = null;
        await loadKeys();
    } catch (error) {
        root.querySelector('#gw-keys')?.appendChild(message(error.message, 'error'));
    }
}

async function showKey(record) {
    try {
        const data = await call('get', '/keys/' + encodeURIComponent(record.id));
        state.detail = data.key || null;
        state.editing = null;
        renderKeys();
    } catch (error) {
        root.querySelector('#gw-keys')?.appendChild(message(error.message, 'error'));
    }
}

async function saveKey(record, form) {
    const host = root.querySelector('#gw-keys');
    const data = new FormData(form);
    const namespaces = String(data.get('scopes') || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    const overrides = parseOverrides(data.get('tools'));
    if (overrides.invalid.length) {
        host?.appendChild(message(
            T('gw.keys.toolsInvalid', { lines: overrides.invalid.join(', ') }),
            'error',
        ));
        return;
    }
    const concurrencyRaw = String(data.get('max_concurrency') || '').trim();
    let maxConcurrency = null;
    if (concurrencyRaw) {
        maxConcurrency = Number(concurrencyRaw);
        if (!Number.isInteger(maxConcurrency) || maxConcurrency < 1) {
            host?.appendChild(message(T('gw.keys.concurrencyInvalid'), 'error'));
            return;
        }
    }
    const expiresRaw = String(data.get('expires_at') || '');
    const payload = {
        label: String(data.get('label') || '').trim(),
        scopes: { namespaces, tools: overrides.tools },
        max_concurrency: maxConcurrency,
        expires_at: expiresRaw ? new Date(expiresRaw).toISOString() : null,
    };
    try {
        await call('patch', '/keys/' + encodeURIComponent(record.id), payload);
        state.editing = null;
        await loadKeys();
    } catch (error) {
        host?.appendChild(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- clients

function renderClients() {
    const host = root.querySelector('#gw-clients');
    if (!host) return;
    host.replaceChildren();

    const rows = state.clients.map((record) => el('tr', {}, [
        el('td', { text: record.client_id || record.client_key }),
        el('td', { class: 'gw-mono', text: record.key_id || '—' }),
        el('td', { text: record.limit }),
        el('td', { text: record.calls }),
        el('td', { text: record.errors }),
        el('td', { text: record.active }),
        el('td', { text: record.queued }),
    ]));

    host.appendChild(
        section('gw.clients.title', 'gw.clients.hint', [
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.refresh'),
                onclick: () => loadClients(),
            }),
        ], table(
            [
                'gw.clients.client',
                'gw.clients.key',
                'gw.clients.limit',
                'gw.clients.calls',
                'gw.clients.errors',
                'gw.clients.active',
                'gw.clients.queued',
            ],
            rows,
        )),
    );
}

async function loadClients() {
    const host = root.querySelector('#gw-clients');
    if (!host) return;
    try {
        const data = await call('get', '/clients');
        state.clients = data.clients || [];
        renderClients();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
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
        if (record.kind === 'openapi') {
            actions.push(el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.sources.refreshSpec'),
                onclick: () => refreshSource(record.id),
            }));
        }
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

async function refreshSource(sourceId) {
    const host = root.querySelector('#gw-sources');
    try {
        const data = await call('post', '/sources/' + encodeURIComponent(sourceId) + '/refresh');
        await loadSources();
        const result = data.refresh || {};
        const note = result.skipped
            ? message(T('gw.sources.refreshSkipped'))
            : message(T('gw.sources.refreshed', {
                added: (result.added || []).length,
                removed: (result.removed || []).length,
                updated: (result.updated || []).length,
                unchanged: result.unchanged || 0,
            }));
        host.appendChild(note);
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
        ['clients', 'gw.tab.clients'],
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
        clients: el('div', { id: 'gw-clients', class: 'gw-pane', role: 'tabpanel' }),
        frontends: el('div', { id: 'gw-frontends', class: 'gw-pane', role: 'tabpanel' }),
        sources: el('div', { id: 'gw-sources', class: 'gw-pane', role: 'tabpanel' }),
    };
    for (const [name, pane] of Object.entries(panes)) {
        pane.style.display = name === state.tab ? '' : 'none';
        shell.appendChild(pane);
    }
    root.appendChild(shell);

    if (state.tab === 'keys') renderKeys();
    else if (state.tab === 'clients') renderClients();
    else if (state.tab === 'frontends') renderFrontends();
    else renderSources();
}

async function refreshCurrent() {
    if (state.tab === 'keys') await loadKeys();
    else if (state.tab === 'clients') await loadClients();
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
    state.secretKind = 'created';
    state.editing = null;
    state.detail = null;
}

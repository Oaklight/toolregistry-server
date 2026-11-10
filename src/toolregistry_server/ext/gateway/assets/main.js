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
        'gw.tab.tools': 'Tools',
        'gw.tab.logs': 'Logs',
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
        'gw.sources.edit': 'Edit',
        'gw.sources.save': 'Save changes',
        'gw.sources.cancel': 'Cancel edit',
        'gw.sources.editing': 'Editing {detail}',
        'gw.sources.test': 'Test',
        'gw.sources.showTools': 'Tools',
        'gw.sources.hideTools': 'Hide tools',
        'gw.sources.testOk': 'Connected: {count} tools, server {server}.',
        'gw.sources.testFailed': 'Test failed: {error}',
        'gw.sources.testing': 'Testing…',
        'gw.sources.transport': 'Transport',
        'gw.sources.enabled': 'Enabled',
        'gw.sources.auth': 'Authentication',
        'gw.sources.authKeep': 'Keep stored credential',
        'gw.sources.authNone': 'None',
        'gw.sources.authBearer': 'Bearer token',
        'gw.sources.authHeader': 'Custom header',
        'gw.sources.authToken': 'Token',
        'gw.sources.authTokenHint': 'Write-only. Leave blank to keep the stored value.',
        'gw.sources.authHeaderName': 'Header name',
        'gw.sources.clearSecrets': 'Remove the stored credential',
        'gw.sources.secretStored': 'A credential is stored and is never shown.',
        'gw.sources.noTools': 'This source registered no tools.',
        'gw.sources.command': 'Command',
        'gw.sources.commandHint': 'Program and arguments, space separated.',
        'gw.sources.enabledTools': '{enabled}/{total} enabled',

        'gw.tools.title': 'Tools',
        'gw.tools.hint': 'Inspect one tool and call it as the operator. Calls are validated, policy-checked and logged.',
        'gw.tools.name': 'Tool',
        'gw.tools.namespace': 'Namespace',
        'gw.tools.source': 'Source',
        'gw.tools.enabled': 'Enabled',
        'gw.tools.detail': 'Detail',
        'gw.tools.inspect': 'Inspect',
        'gw.tools.description': 'Description',
        'gw.tools.input': 'Input schema',
        'gw.tools.output': 'Output schema',
        'gw.tools.tags': 'Tags',
        'gw.tools.hints': 'Execution hints',
        'gw.tools.dangerous': 'Dangerous call: {tags}.',
        'gw.tools.arguments': 'Arguments (JSON)',
        'gw.tools.confirm': 'I understand this call runs on the server',
        'gw.tools.call': 'Call tool',
        'gw.tools.running': 'Running…',
        'gw.tools.result': 'Result',
        'gw.tools.duration': 'Duration {ms} ms',
        'gw.tools.error': 'Error',
        'gw.tools.disabled': 'Disabled: {reason}',
        'gw.tools.noSelection': 'Select a tool to inspect it.',

        'gw.logs.title': 'Execution log',
        'gw.logs.hint': 'Calls that reached a tool, with the principal that made them.',
        'gw.logs.keyId': 'Key id',
        'gw.logs.keyLabel': 'Key label',
        'gw.logs.outcome': 'Outcome',
        'gw.logs.tool': 'Tool',
        'gw.logs.since': 'Since',
        'gw.logs.until': 'Until',
        'gw.logs.limit': 'Limit',
        'gw.logs.apply': 'Apply',
        'gw.logs.reset': 'Reset',
        'gw.logs.principal': 'Principal',
        'gw.logs.time': 'Time',
        'gw.logs.status': 'Status',
        'gw.logs.duration': 'Duration',
        'gw.logs.any': 'Any',
        'gw.logs.ok': 'ok',
        'gw.logs.error': 'error',
        'gw.logs.busy': 'busy',
        'gw.logs.denied': 'denied',
        'gw.logs.busyDenied': 'Busy and denied calls are recorded with their own outcome; calls refused before the execution log are only in the structured log.',
        'gw.logs.warnings': 'Warnings',
        'gw.logs.warningsAny': 'Any',
        'gw.logs.warningsOnly': 'With warnings',
        'gw.logs.warningsNone': 'Without warnings',
        'gw.logs.count': '{count} entries',
        'gw.logs.operator': 'operator',
        'gw.logs.avg': 'Average duration',
        'gw.logs.total': 'Entries',
        'gw.logs.byKey': 'By principal',
    },
    zh: {
        'gw.tab.keys': '密钥',
        'gw.tab.clients': '客户端',
        'gw.tab.frontends': '前端',
        'gw.tab.sources': '来源',
        'gw.tab.tools': '工具',
        'gw.tab.logs': '日志',
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
        'gw.sources.edit': '编辑',
        'gw.sources.save': '保存修改',
        'gw.sources.cancel': '取消编辑',
        'gw.sources.editing': '正在编辑 {detail}',
        'gw.sources.test': '测试',
        'gw.sources.showTools': '工具',
        'gw.sources.hideTools': '收起工具',
        'gw.sources.testOk': '连接成功：{count} 个工具，服务器 {server}。',
        'gw.sources.testFailed': '测试失败：{error}',
        'gw.sources.testing': '测试中…',
        'gw.sources.transport': '传输方式',
        'gw.sources.enabled': '启用',
        'gw.sources.auth': '认证',
        'gw.sources.authKeep': '保留已保存的凭据',
        'gw.sources.authNone': '无',
        'gw.sources.authBearer': 'Bearer 令牌',
        'gw.sources.authHeader': '自定义请求头',
        'gw.sources.authToken': '令牌',
        'gw.sources.authTokenHint': '只写字段。留空则保留已保存的值。',
        'gw.sources.authHeaderName': '请求头名称',
        'gw.sources.clearSecrets': '删除已保存的凭据',
        'gw.sources.secretStored': '已保存凭据，且不会显示。',
        'gw.sources.noTools': '此来源未注册任何工具。',
        'gw.sources.command': '命令',
        'gw.sources.commandHint': '程序及参数，以空格分隔。',
        'gw.sources.enabledTools': '已启用 {enabled}/{total}',

        'gw.tools.title': '工具',
        'gw.tools.hint': '查看单个工具并以操作员身份调用。调用会经过校验、权限策略检查并写入日志。',
        'gw.tools.name': '工具',
        'gw.tools.namespace': '命名空间',
        'gw.tools.source': '来源',
        'gw.tools.enabled': '启用',
        'gw.tools.detail': '详情',
        'gw.tools.inspect': '查看',
        'gw.tools.description': '描述',
        'gw.tools.input': '输入 schema',
        'gw.tools.output': '输出 schema',
        'gw.tools.tags': '标签',
        'gw.tools.hints': '执行提示',
        'gw.tools.dangerous': '危险调用：{tags}。',
        'gw.tools.arguments': '参数（JSON）',
        'gw.tools.confirm': '我确认此调用会在服务器上执行',
        'gw.tools.call': '调用工具',
        'gw.tools.running': '执行中…',
        'gw.tools.result': '结果',
        'gw.tools.duration': '耗时 {ms} 毫秒',
        'gw.tools.error': '错误',
        'gw.tools.disabled': '已禁用：{reason}',
        'gw.tools.noSelection': '选择一个工具以查看详情。',

        'gw.logs.title': '执行日志',
        'gw.logs.hint': '到达工具的调用记录，以及发起调用的主体。',
        'gw.logs.keyId': '密钥 ID',
        'gw.logs.keyLabel': '密钥名称',
        'gw.logs.outcome': '结果',
        'gw.logs.tool': '工具',
        'gw.logs.since': '起始时间',
        'gw.logs.until': '结束时间',
        'gw.logs.limit': '数量上限',
        'gw.logs.apply': '应用',
        'gw.logs.reset': '重置',
        'gw.logs.principal': '主体',
        'gw.logs.time': '时间',
        'gw.logs.status': '状态',
        'gw.logs.duration': '耗时',
        'gw.logs.any': '全部',
        'gw.logs.ok': '成功',
        'gw.logs.error': '错误',
        'gw.logs.busy': '繁忙',
        'gw.logs.denied': '拒绝',
        'gw.logs.busyDenied': '繁忙与拒绝的调用会记录各自的结果；在执行日志之前被拒绝的调用只出现在结构化日志中。',
        'gw.logs.warnings': '警告',
        'gw.logs.warningsAny': '全部',
        'gw.logs.warningsOnly': '有警告',
        'gw.logs.warningsNone': '无警告',
        'gw.logs.count': '{count} 条记录',
        'gw.logs.operator': '操作员',
        'gw.logs.avg': '平均耗时',
        'gw.logs.total': '记录数',
        'gw.logs.byKey': '按主体统计',
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
    sourceEditing: null,
    sourceExpanded: null,
    sourceTools: {},
    sourceNotes: {},
    tools: [],
    toolName: null,
    toolDetail: null,
    toolCall: null,
    logs: [],
    logStats: null,
    logFilters: {},
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
                          value: toLocalInput(record.expires_at),
                          'data-original': toLocalInput(record.expires_at) }),
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

export function keyUpdatePayload(values) {
    // The expiry input only holds minutes, so re-sending an untouched value
    // would move the stored expiry up to 59 seconds earlier (or refuse a key
    // that has just expired).  Send the field only when the operator changed
    // it; leave it out otherwise, and the store keeps the exact value.
    const originalRaw = String(values.expires_original || '');
    const expiresRaw = String(values.expires_at || '');
    const payload = {
        label: String(values.label || '').trim(),
        scopes: { namespaces: values.namespaces, tools: values.tools },
        max_concurrency: values.max_concurrency,
    };
    if (expiresRaw !== originalRaw) {
        payload.expires_at = expiresRaw ? new Date(expiresRaw).toISOString() : null;
    }
    return payload;
}

export function keyEditValues(form, data) {
    const namespaces = String(data.get('scopes') || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    const overrides = parseOverrides(data.get('tools'));
    const concurrencyRaw = String(data.get('max_concurrency') || '').trim();
    let maxConcurrency = null;
    let concurrencyInvalid = false;
    if (concurrencyRaw) {
        maxConcurrency = Number(concurrencyRaw);
        concurrencyInvalid = !Number.isInteger(maxConcurrency) || maxConcurrency < 1;
    }
    return {
        label: data.get('label'),
        namespaces,
        tools: overrides.tools,
        invalid: overrides.invalid,
        concurrency_invalid: concurrencyInvalid,
        max_concurrency: maxConcurrency,
        expires_at: data.get('expires_at'),
        expires_original: form.querySelector('[name="expires_at"]')?.dataset.original,
    };
}

async function saveKey(record, form) {
    const host = root.querySelector('#gw-keys');
    const data = new FormData(form);
    const values = keyEditValues(form, data);
    if (values.invalid.length) {
        host?.appendChild(message(
            T('gw.keys.toolsInvalid', { lines: values.invalid.join(', ') }),
            'error',
        ));
        return;
    }
    if (values.concurrency_invalid) {
        host?.appendChild(message(T('gw.keys.concurrencyInvalid'), 'error'));
        return;
    }
    const payload = keyUpdatePayload(values);
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

    const rows = [];
    for (const record of state.sources) {
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
            class: 'gw-btn',
            type: 'button',
            text: T('gw.sources.edit'),
            onclick: () => editSource(record.id),
        }));
        actions.push(el('button', {
            class: 'gw-btn',
            type: 'button',
            text: T('gw.sources.test'),
            onclick: () => testSource(record.id),
        }));
        actions.push(el('button', {
            class: 'gw-btn',
            type: 'button',
            text: state.sourceExpanded === record.id
                ? T('gw.sources.hideTools')
                : T('gw.sources.showTools'),
            onclick: () => toggleSourceTools(record.id),
        }));
        actions.push(el('button', {
            class: 'gw-btn gw-btn-danger',
            type: 'button',
            text: T('gw.sources.remove'),
            onclick: () => removeSource(record),
        }));
        const counts = record.tool_count || {};
        rows.push(el('tr', {}, [
            el('td', { text: record.kind || '' }),
            el('td', { class: 'gw-mono', text: record.detail || '' }),
            el('td', { text: record.namespace || '—' }),
            el('td', { text: (counts.enabled ?? 0) + '/' + (counts.total ?? 0) }),
            el('td', {}, [pill(status.label, status.kind)]),
            el('td', { class: 'gw-error-cell', text: record.last_error || '' }),
            el('td', {}, actions),
        ]));
        if (state.sourceExpanded === record.id) {
            rows.push(el('tr', {}, [
                el('td', { colspan: '7' }, [sourceToolsCell(record.id)]),
            ]));
        }
    }

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
            sourceEditor(),
        ])),
    );
}

// One cell listing the tools a source registered.  The list is fetched when
// the row is expanded; a failed fetch leaves the error in the cell.
function sourceToolsCell(sourceId) {
    const tools = state.sourceTools[sourceId];
    if (tools === null || tools === undefined) {
        return el('p', { class: 'gw-hint', text: T('gw.loading') });
    }
    if (tools.error) return message(tools.error, 'error');
    if (!tools.length) return el('p', { class: 'gw-hint', text: T('gw.sources.noTools') });
    return el('ul', { class: 'gw-tool-list' }, tools.map((tool) => (
        el('li', {}, [
            el('span', { class: 'gw-mono', text: tool.name }),
            tool.enabled === false ? pill(T('gw.sources.statusExcluded'), 'excluded') : null,
            el('span', { class: 'gw-hint', text: tool.description || '' }),
        ])
    )));
}

function field(labelKey, control, hintKey) {
    return el('label', { class: 'gw-field' }, [
        el('span', { text: T(labelKey) }),
        control,
        hintKey ? el('small', { class: 'gw-hint', text: T(hintKey) }) : null,
    ]);
}

function sourceEditor() {
    const editing = state.sourceEditing;
    const declaration = (editing && editing.declaration) || {};
    const type = declaration.type || declaration.kind || 'openapi';
    const transport = declaration.transport || 'streamable-http';
    const command = Array.isArray(declaration.command)
        ? declaration.command.join(' ')
        : (declaration.command || '');
    const secretFields = declaration.secret_fields || [];
    const hasSecret = secretFields.length > 0;

    return el('form', {
        class: 'gw-form',
        onsubmit: (event) => {
            event.preventDefault();
            saveSource(event.currentTarget, editing ? editing.id : null);
        },
    }, [
        el('h4', {
            text: editing
                ? T('gw.sources.editing', { detail: editing.detail || editing.id })
                : T('gw.sources.add'),
        }),
        field('gw.sources.kind', el('select', { name: 'type' }, [
            el('option', { value: 'openapi', text: 'OpenAPI', selected: type === 'openapi' ? 'selected' : null }),
            el('option', { value: 'mcp', text: 'MCP', selected: type === 'mcp' ? 'selected' : null }),
            el('option', { value: 'python', text: 'Python', selected: type === 'python' ? 'selected' : null }),
        ])),
        field('gw.sources.transport', el('select', { name: 'transport' }, [
            el('option', { value: 'streamable-http', text: 'streamable-http', selected: transport === 'streamable-http' ? 'selected' : null }),
            el('option', { value: 'sse', text: 'sse', selected: transport === 'sse' ? 'selected' : null }),
            el('option', { value: 'stdio', text: 'stdio', selected: transport === 'stdio' ? 'selected' : null }),
        ])),
        field('gw.sources.url', el('input', {
            name: 'target', autocomplete: 'off',
            value: declaration.url || declaration.class_path || declaration.class || '',
        })),
        field('gw.sources.command', el('input', {
            name: 'command', autocomplete: 'off', value: command,
        }), 'gw.sources.commandHint'),
        field('gw.sources.namespace', el('input', {
            name: 'namespace', autocomplete: 'off',
            value: declaration.namespace || '',
        })),
        el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.sources.enabled') }),
            el('input', {
                type: 'checkbox', name: 'enabled',
                checked: declaration.enabled === false ? null : 'checked',
            }),
        ]),
        hasSecret ? el('p', { class: 'gw-hint', text: T('gw.sources.secretStored') }) : null,
        field('gw.sources.auth', el('select', { name: 'auth_type' }, [
            el('option', { value: 'none', text: T('gw.sources.authNone') }),
            el('option', { value: 'bearer', text: T('gw.sources.authBearer') }),
            el('option', { value: 'header', text: T('gw.sources.authHeader') }),
        ])),
        field('gw.sources.authToken', el('input', {
            name: 'auth_token', type: 'password', autocomplete: 'new-password',
        }), 'gw.sources.authTokenHint'),
        field('gw.sources.authHeaderName', el('input', {
            name: 'auth_header', autocomplete: 'off', value: 'X-API-Key',
        })),
        editing ? el('label', { class: 'gw-field' }, [
            el('span', { text: T('gw.sources.clearSecrets') }),
            el('input', { type: 'checkbox', name: 'clear_secrets' }),
        ]) : null,
        el('div', { class: 'gw-actions' }, [
            el('button', {
                class: 'gw-btn gw-btn-primary',
                type: 'submit',
                text: editing ? T('gw.sources.save') : T('gw.sources.add'),
            }),
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.sources.test'),
                onclick: (event) => testDeclaration(event.currentTarget.closest('form')),
            }),
            editing ? el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.sources.cancel'),
                onclick: () => {
                    state.sourceEditing = null;
                    renderSources();
                },
            }) : null,
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

async function editSource(sourceId) {
    const host = root.querySelector('#gw-sources');
    try {
        const data = await call('get', '/sources/' + encodeURIComponent(sourceId));
        const record = data.source || {};
        state.sourceEditing = {
            id: sourceId,
            detail: record.detail || sourceId,
            declaration: record.declaration || {},
        };
        renderSources();
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

async function toggleSourceTools(sourceId) {
    if (state.sourceExpanded === sourceId) {
        state.sourceExpanded = null;
        renderSources();
        return;
    }
    state.sourceExpanded = sourceId;
    if (state.sourceTools[sourceId] === undefined) {
        state.sourceTools[sourceId] = null;
        renderSources();
        try {
            const data = await call(
                'get', '/sources/' + encodeURIComponent(sourceId) + '/tools');
            state.sourceTools[sourceId] = data.tools || [];
        } catch (error) {
            state.sourceTools[sourceId] = { error: error.message };
        }
    }
    renderSources();
}

function sourcePayload(form, editing) {
    const data = new FormData(form);
    const type = String(data.get('type') || 'openapi');
    const payload = { type, enabled: data.get('enabled') === 'on' };
    const namespace = String(data.get('namespace') || '').trim();
    const target = String(data.get('target') || '').trim();
    if (namespace) payload.namespace = namespace;
    if (type === 'openapi') payload.url = target;
    else if (type === 'python') payload.class = target;
    else {
        const transport = String(data.get('transport') || 'streamable-http');
        payload.transport = transport;
        if (transport === 'stdio') {
            const command = String(data.get('command') || '').trim();
            if (command) payload.command = command.split(/\s+/);
        } else {
            payload.url = target;
        }
        const token = String(data.get('auth_token') || '').trim();
        if (token) {
            const authType = String(data.get('auth_type') || 'bearer');
            payload.auth = { type: authType === 'header' ? 'header' : 'bearer', token };
            const headerName = String(data.get('auth_header') || '').trim();
            if (authType === 'header' && headerName) {
                payload.auth.header_name = headerName;
            }
        }
    }
    // Applies to every kind: an OpenAPI token is cleared the same way as an
    // MCP header.
    if (editing && data.get('clear_secrets') === 'on' && !payload.auth) {
        payload.clear_secrets = true;
    }
    return payload;
}

function testNote(test) {
    if (!test) return null;
    if (!test.ok) {
        return message(T('gw.sources.testFailed', { error: test.error || '' }), 'error');
    }
    const server = [test.server_name, test.server_version].filter(Boolean).join(' ');
    return message(T('gw.sources.testOk', {
        count: test.tool_count ?? 0,
        server: server || '—',
    }), 'ok');
}

async function testSource(sourceId) {
    const host = root.querySelector('#gw-sources');
    try {
        const data = await call(
            'post', '/sources/' + encodeURIComponent(sourceId) + '/test');
        host.appendChild(testNote(data.test));
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

async function testDeclaration(form) {
    if (!form) return;
    const host = root.querySelector('#gw-sources');
    try {
        const data = await call('post', '/sources/test', sourcePayload(form, false));
        host.appendChild(testNote(data.test));
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

async function saveSource(form, sourceId) {
    const host = root.querySelector('#gw-sources');
    try {
        const payload = sourcePayload(form, Boolean(sourceId));
        const result = sourceId
            ? await call('put', '/sources/' + encodeURIComponent(sourceId), payload)
            : await call('post', '/sources', payload);
        state.sourceEditing = null;
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

// ---------------------------------------------------------------- tools

// A JSON skeleton from the input schema: one entry per property, using the
// first value of an enum and a type-appropriate placeholder otherwise.
function schemaSkeleton(schema) {
    const properties = (schema && schema.properties) || {};
    const skeleton = {};
    for (const [name, spec] of Object.entries(properties)) {
        const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
        if (Array.isArray(spec.enum) && spec.enum.length) skeleton[name] = spec.enum[0];
        else if (type === 'number' || type === 'integer') skeleton[name] = 0;
        else if (type === 'boolean') skeleton[name] = false;
        else if (type === 'array') skeleton[name] = [];
        else if (type === 'object') skeleton[name] = {};
        else skeleton[name] = '';
    }
    return skeleton;
}

function renderTools() {
    const host = root.querySelector('#gw-tools');
    if (!host) return;
    host.replaceChildren();

    const rows = state.tools.map((record) => el('tr', {}, [
        el('td', { class: 'gw-mono', text: record.name }),
        el('td', { text: record.namespace || '—' }),
        el('td', { text: record.source || '—' }),
        el('td', {}, [pill(
            record.enabled ? T('gw.tools.enabled') : T('gw.sources.statusExcluded'),
            record.enabled ? 'applied' : 'excluded',
        )]),
        el('td', { text: (record.tags || []).join(', ') || '—' }),
        el('td', {}, [el('button', {
            class: 'gw-btn',
            type: 'button',
            text: T('gw.tools.inspect'),
            onclick: () => inspectTool(record.name),
        })]),
    ]));

    host.appendChild(
        section('gw.tools.title', 'gw.tools.hint', [
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.refresh'),
                onclick: () => loadTools(),
            }),
        ], el('div', {}, [
            table(
                [
                    'gw.tools.name',
                    'gw.tools.namespace',
                    'gw.tools.source',
                    'gw.tools.enabled',
                    'gw.tools.tags',
                    'gw.tools.detail',
                ],
                rows,
            ),
            toolDetailPanel(),
        ])),
    );
}

function toolDetailPanel() {
    if (!state.toolDetail) {
        return el('p', { class: 'gw-hint', text: T('gw.tools.noSelection') });
    }
    const detail = state.toolDetail;
    const dangerous = detail.dangerous_tags || [];
    const meta = [
        [T('gw.tools.tags'), (detail.tags || []).join(', ') || '—'],
        [T('gw.tools.hints'), JSON.stringify(detail.execution_hints || {}, null, 2)],
        [T('gw.tools.source'), detail.source_detail || detail.source || '—'],
    ];
    return el('div', { class: 'gw-tool-detail' }, [
        el('h4', { class: 'gw-mono', text: detail.name }),
        el('p', { text: detail.description || '' }),
        detail.enabled ? null : message(
            T('gw.tools.disabled', { reason: detail.disable_reason || '' }), 'warn'),
        dangerous.length
            ? message(T('gw.tools.dangerous', { tags: dangerous.join(', ') }), 'warn')
            : null,
        el('dl', { class: 'gw-meta' }, meta.flatMap(([label, value]) => [
            el('dt', { text: label }),
            el('dd', {}, [el('pre', { text: value })]),
        ])),
        el('div', { class: 'gw-schema-grid' }, [
            el('div', {}, [
                el('h5', { text: T('gw.tools.input') }),
                el('pre', { text: JSON.stringify(detail.input_schema || {}, null, 2) }),
            ]),
            el('div', {}, [
                el('h5', { text: T('gw.tools.output') }),
                el('pre', {
                    text: detail.output_schema
                        ? JSON.stringify(detail.output_schema, null, 2)
                        : '—',
                }),
            ]),
        ]),
        toolCallForm(detail),
    ]);
}

function toolCallForm(detail) {
    const textarea = el('textarea', {
        name: 'arguments',
        rows: '8',
        spellcheck: 'false',
        text: JSON.stringify(schemaSkeleton(detail.input_schema), null, 2),
    });
    const confirm = el('input', {
        type: 'checkbox', name: 'confirm', disabled: detail.enabled ? null : 'disabled',
    });
    const callButton = el('button', {
        class: 'gw-btn gw-btn-primary',
        type: 'submit',
        text: T('gw.tools.call'),
        disabled: detail.enabled ? null : 'disabled',
    });
    const result = el('div', { class: 'gw-call-result' });
    return el('form', {
        class: 'gw-form',
        onsubmit: async (event) => {
            event.preventDefault();
            if (!confirm.checked) return;
            let parsed;
            try {
                parsed = JSON.parse(textarea.value || '{}');
            } catch (error) {
                result.replaceChildren(message(error.message, 'error'));
                return;
            }
            callButton.disabled = true;
            callButton.textContent = T('gw.tools.running');
            try {
                const data = await call(
                    'post',
                    '/tools/' + encodeURIComponent(detail.name) + '/call',
                    { arguments: parsed, confirm: true },
                );
                result.replaceChildren(callResultView(data.call || {}));
            } catch (error) {
                result.replaceChildren(message(error.message, 'error'));
            } finally {
                callButton.disabled = false;
                callButton.textContent = T('gw.tools.call');
            }
        },
    }, [
        field('gw.tools.arguments', textarea),
        el('label', { class: 'gw-field gw-inline' }, [confirm, el('span', {
            text: T('gw.tools.confirm'),
        })]),
        el('div', { class: 'gw-actions' }, [callButton]),
        result,
    ]);
}

function callResultView(result) {
    const duration = T('gw.tools.duration', {
        ms: Number(result.duration_ms || 0).toFixed(2),
    });
    if (result.ok === false) {
        return el('div', {}, [
            message(T('gw.tools.error') + ': ' + (result.error || ''), 'error'),
            el('p', { class: 'gw-hint', text: duration }),
        ]);
    }
    return el('div', {}, [
        el('h5', { text: T('gw.tools.result') }),
        el('pre', { text: JSON.stringify(result.result, null, 2) }),
        el('p', { class: 'gw-hint', text: duration }),
    ]);
}

async function loadTools() {
    const host = root.querySelector('#gw-tools');
    if (!host) return;
    try {
        const data = await call('get', '/tools');
        state.tools = data.tools || [];
        renderTools();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

async function inspectTool(name) {
    const host = root.querySelector('#gw-tools');
    try {
        const data = await call('get', '/tools/' + encodeURIComponent(name));
        state.toolName = name;
        state.toolDetail = data.tool;
        renderTools();
    } catch (error) {
        host.appendChild(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- logs

function logQuery() {
    const filters = state.logFilters;
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(filters)) {
        if (value !== undefined && value !== null && value !== '') {
            query.set(name, value);
        }
    }
    const text = query.toString();
    return text ? '?' + text : '';
}

function logFiltersForm() {
    const filters = state.logFilters;
    const input = (name, type, placeholder) => el('input', {
        name, type: type || 'text', value: filters[name] || '',
        autocomplete: 'off', placeholder: placeholder || '',
    });
    return el('form', {
        class: 'gw-form gw-form-inline',
        onsubmit: (event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            state.logFilters = {
                key_id: String(data.get('key_id') || '').trim(),
                key_label: String(data.get('key_label') || '').trim(),
                outcome: String(data.get('outcome') || ''),
                has_warnings: String(data.get('has_warnings') || ''),
                tool_name: String(data.get('tool_name') || '').trim(),
                since: String(data.get('since') || ''),
                until: String(data.get('until') || ''),
                limit: String(data.get('limit') || '').trim(),
            };
            refreshCurrent();
        },
    }, [
        field('gw.logs.keyId', input('key_id', 'text', 'key_…')),
        field('gw.logs.keyLabel', input('key_label', 'text', 'build-bot')),
        field('gw.logs.outcome', el('select', { name: 'outcome' }, [
            el('option', { value: '', text: T('gw.logs.any') }),
            el('option', { value: 'ok', text: T('gw.logs.ok') }),
            el('option', { value: 'error', text: T('gw.logs.error') }),
            el('option', { value: 'busy', text: T('gw.logs.busy') }),
            el('option', { value: 'denied', text: T('gw.logs.denied') }),
        ])),
        field('gw.logs.warnings', el('select', { name: 'has_warnings' }, [
            el('option', {
                value: '', text: T('gw.logs.warningsAny'),
                selected: filters.has_warnings ? null : 'selected',
            }),
            el('option', {
                value: 'true', text: T('gw.logs.warningsOnly'),
                selected: filters.has_warnings === 'true' ? 'selected' : null,
            }),
            el('option', {
                value: 'false', text: T('gw.logs.warningsNone'),
                selected: filters.has_warnings === 'false' ? 'selected' : null,
            }),
        ])),
        field('gw.logs.tool', input('tool_name', 'text', 'calc-add')),
        field('gw.logs.since', input('since', 'datetime-local')),
        field('gw.logs.until', input('until', 'datetime-local')),
        field('gw.logs.limit', input('limit', 'number')),
        el('div', { class: 'gw-actions' }, [
            el('button', {
                class: 'gw-btn gw-btn-primary', type: 'submit', text: T('gw.logs.apply'),
            }),
            el('button', {
                class: 'gw-btn',
                type: 'button',
                text: T('gw.logs.reset'),
                onclick: () => {
                    state.logFilters = {};
                    renderLogs();
                    refreshCurrent();
                },
            }),
        ]),
    ]);
}

function renderLogs() {
    const host = root.querySelector('#gw-logs');
    if (!host) return;
    host.replaceChildren();

    const rows = state.logs.map((entry) => {
        const principal = entry.principal || {};
        return el('tr', {}, [
            el('td', { class: 'text-muted', text: formatTime(entry.timestamp) }),
            el('td', { class: 'gw-mono', text: entry.tool_name }),
            el('td', { text: principal.label || '—' }),
            el('td', { class: 'gw-mono', text: principal.key_id || '—' }),
            el('td', {}, [pill(entry.status, entry.status === 'success' ? 'applied' : 'failed')]),
            el('td', {
                text: entry.duration_ms ? entry.duration_ms.toFixed(2) + ' ms' : '—',
            }),
        ]);
    });

    const stats = state.logStats || {};
    const byKey = Object.entries(stats.by_key || {})
        .map(([label, count]) => label + ': ' + count)
        .join(', ');

    host.appendChild(
        section('gw.logs.title', 'gw.logs.hint', [
            el('button', {
                class: 'gw-btn', type: 'button', text: T('gw.refresh'),
                onclick: () => refreshCurrent(),
            }),
        ], el('div', {}, [
            logFiltersForm(),
            el('p', { class: 'gw-hint', text: T('gw.logs.busyDenied') }),
            el('div', { class: 'gw-stat-row' }, [
                el('span', { text: T('gw.logs.count', { count: state.logs.length }) }),
                el('span', { text: T('gw.logs.total') + ': ' + (stats.total_entries ?? 0) }),
                el('span', {
                    text: T('gw.logs.avg') + ': '
                        + Number(stats.avg_duration_ms || 0).toFixed(2) + ' ms',
                }),
                el('span', { text: T('gw.logs.byKey') + ': ' + (byKey || '—') }),
            ]),
            table(
                [
                    'gw.logs.time',
                    'gw.logs.tool',
                    'gw.logs.principal',
                    'gw.logs.keyId',
                    'gw.logs.status',
                    'gw.logs.duration',
                ],
                rows,
            ),
        ])),
    );
}

async function loadLogs() {
    const host = root.querySelector('#gw-logs');
    if (!host) return;
    try {
        const query = logQuery();
        const [entries, stats] = await Promise.all([
            call('get', '/logs' + query),
            call('get', '/logs/stats' + query),
        ]);
        state.logs = entries.entries || [];
        state.logStats = stats;
        renderLogs();
    } catch (error) {
        host.replaceChildren(message(error.message, 'error'));
    }
}

// ---------------------------------------------------------------- shell

function renderTabs() {
    const tabs = [
        ['keys', 'gw.tab.keys'],
        ['clients', 'gw.tab.clients'],
        ['frontends', 'gw.tab.frontends'],
        ['sources', 'gw.tab.sources'],
        ['tools', 'gw.tab.tools'],
        ['logs', 'gw.tab.logs'],
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
        tools: el('div', { id: 'gw-tools', class: 'gw-pane', role: 'tabpanel' }),
        logs: el('div', { id: 'gw-logs', class: 'gw-pane', role: 'tabpanel' }),
    };
    for (const [name, pane] of Object.entries(panes)) {
        pane.style.display = name === state.tab ? '' : 'none';
        shell.appendChild(pane);
    }
    root.appendChild(shell);

    if (state.tab === 'keys') renderKeys();
    else if (state.tab === 'clients') renderClients();
    else if (state.tab === 'frontends') renderFrontends();
    else if (state.tab === 'sources') renderSources();
    else if (state.tab === 'tools') renderTools();
    else renderLogs();
}

async function refreshCurrent() {
    if (state.tab === 'keys') await loadKeys();
    else if (state.tab === 'clients') await loadClients();
    else if (state.tab === 'frontends') await loadFrontends();
    else if (state.tab === 'sources') await loadSources();
    else if (state.tab === 'tools') await loadTools();
    else await loadLogs();
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

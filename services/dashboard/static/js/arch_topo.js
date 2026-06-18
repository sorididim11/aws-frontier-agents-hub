// ================================================================
// ARCHITECTURE ANALYSIS — Drill-down Navigation
// ================================================================
var ARCH = {
    nodes: [], edges: [], recs: null, analysis: null, discovering: false,
    customPos: {}, customProps: {}, _pos: {}, _edgePaths: {},
    nav: { level: 'L1', selectedApp: null, selectedService: null, history: [] },
    mode: 'multi_app',
    viewMode: 'group',
    tierFilter: {core: true, data: true, observe: false, platform: false, ops: false},
    l1AppFilter: {},
    l2ConnectedApps: {},
    _l2ConnectedForApp: null,
    chatMode: 'summary',
    _es: null,
    crossNav: null,
    _zoom: {scale: 1, tx: 0, ty: 0}
};
var ARCH_AGENT_LABELS = {naming: '앱 이름 협상', L1: 'L1 Service Architect', L2: 'L2 Component Architect', L3: 'L3 Infra Architect', Q2_K8S: 'K8s Resource 보충'};

// Official AWS/K8s icon paths (served from /static/icons/)
var ARCH_ICON_PATHS = {
    'k8s-deploy':  '/static/icons/k8s/deploy-clean.svg',
    'k8s-svc':     '/static/icons/k8s/svc-clean.svg',
    'k8s-pod':     '/static/icons/k8s/pod-clean.svg',
    'k8s-ns':      '/static/icons/k8s/ns-clean.svg',
    'k8s-ds':      '/static/icons/k8s/ds-clean.svg',
    'k8s-ing':     '/static/icons/k8s/ing-clean.svg',
    'k8s-cm':      '/static/icons/k8s/cm-clean.svg',
    'k8s-secret':  '/static/icons/k8s/secret-clean.svg',
    'k8s-cj':      '/static/icons/k8s/cj-clean.svg',
    'k8s-rs':      '/static/icons/k8s/rs-clean.svg',
    'k8s-sa':      '/static/icons/k8s/sa-clean.svg',
    'k8s-job':     '/static/icons/k8s/job-clean.svg',
    'k8s-sc':      '/static/icons/k8s/sc-clean.svg',
    'k8s-sts':     '/static/icons/k8s/sts-clean.svg',
    'k8s-hpa':     '/static/icons/k8s/hpa-clean.svg',
    'k8s-pdb':     '/static/icons/k8s/pdb-clean.svg',
    'k8s-pv':      '/static/icons/k8s/pv-clean.svg',
    'k8s-pvc':     '/static/icons/k8s/pvc-clean.svg',
    'k8s-netpol':  '/static/icons/k8s/netpol-clean.svg',
    'k8s-role':    '/static/icons/k8s/role-clean.svg',
    'k8s-rb':      '/static/icons/k8s/rb-clean.svg',
    'k8s-ep':      '/static/icons/k8s/ep-clean.svg',
    'k8s-limits':  '/static/icons/k8s/limits-clean.svg',
    'k8s-quota':   '/static/icons/k8s/quota-clean.svg',
    'aws-rds':             '/static/icons/aws/rds.svg',
    'aws-elasticache':     '/static/icons/aws/elasticache.svg',
    'aws-dynamodb':        '/static/icons/aws/dynamodb.svg',
    'aws-s3':              '/static/icons/aws/s3.svg',
    'aws-lambda':          '/static/icons/aws/lambda.svg',
    'aws-sns':             '/static/icons/aws/sns.svg',
    'aws-sqs':             '/static/icons/aws/sqs.svg',
    'aws-cloudwatch':      '/static/icons/aws/cloudwatch.svg',
    'aws-bedrock':         '/static/icons/aws/bedrock.svg',
    'aws-eventbridge':     '/static/icons/aws/eventbridge.svg',
    'aws-eks':             '/static/icons/aws/eks.svg',
    'aws-ecr':             '/static/icons/aws/ecr.svg',
    'aws-elb':             '/static/icons/aws/elb.svg',
    'aws-vpc':             '/static/icons/aws/vpc.svg',
    'aws-fis':             '/static/icons/aws/fis.svg',
    'aws-secrets-manager': '/static/icons/aws/secrets-manager.svg',
    'aws-xray':            '/static/icons/aws/xray.svg',
    'aws-generic':         '/static/icons/aws/aws-cloud.svg',
    'aws-devops-agent':    '/static/icons/aws/devops-agent.svg'
};
var ARCH_TYPE_COLOR = {app: '#326CE5', gateway: '#06b6d4', cache: '#22c55e', db: '#f59e0b', queue: '#a855f7', worker: '#326CE5'};

// Group color palette
var _ARCH_GROUP_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#f97316', '#84cc16'];

// ── Settings Panel ──
function archToggleSettings() {
    var p = $('archSettings');
    if (p.style.display === 'none') { archLoadConfig(); p.style.display = ''; }
    else { p.style.display = 'none'; }
}
function archLoadConfig() {
    fetch('/api/arch/config?space_id='+encodeURIComponent(SELECTED)).then(function(r) { return r.json() }).then(function(d) {
        if (!d.ok) return;
        ['L1', 'L2', 'L3'].forEach(function(k) {
            var c = d.agents[k] || {};
            $('cfg' + k + 'Prompt').value = c.system_prompt || '';
            $('cfg' + k + 'MaxTurns').value = c.max_turns || 10;
            $('cfg' + k + 'Quality').value = c.quality_threshold || 75;
        });
    });
}
function archSaveConfig() {
    var payload = {};
    ['L1', 'L2', 'L3'].forEach(function(k) {
        payload[k] = {system_prompt: $('cfg' + k + 'Prompt').value, max_turns: parseInt($('cfg' + k + 'MaxTurns').value) || 10, quality_threshold: parseInt($('cfg' + k + 'Quality').value) || 75};
    });
    payload.space_id = SELECTED;
    fetch('/api/arch/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)})
        .then(function(r) { return r.json() }).then(function(d) {
            if (d.ok) $('archSettings').style.display = 'none';
        });
}
function archResetConfig() {
    fetch('/api/arch/config?space_id='+encodeURIComponent(SELECTED), {method: 'DELETE'}).then(function(r) { return r.json() }).then(function(d) {
        if (d.ok) archLoadConfig();
    });
}

// ── Analysis Progress (rendered inside chat summary panel) ──
var _PROGRESS_STEPS = [
    {id: 'init', label: 'Session 생성'},
    {id: 'Q1', label: 'L1 App 식별'},
    {id: 'app_select', label: 'App 선택'},
    {id: 'Q2', label: 'L2 Service 분석'},
    {id: 'done', label: '완료'}
];
var _PROGRESS_START = 0;

function _archShowProgress(msg) {
    var panel = $('archChatSummary');
    if (!panel) return;
    _PROGRESS_START = Date.now();
    var h = '<div class="arch-sum-progress" id="archSumProgress">';
    h += '<div class="arch-sum-progress-hdr">';
    h += '<div class="arch-spinner" style="width:14px;height:14px"></div>';
    h += '<span class="arch-sum-progress-label" id="archProgressLabel">' + esc(msg || 'Topology 분석 중...') + '</span>';
    h += '<span class="arch-sum-progress-time" id="archProgressElapsed">0초</span>';
    h += '</div>';
    h += '<div class="arch-sum-progress-steps" id="archProgressSteps">';
    _PROGRESS_STEPS.forEach(function(s) {
        h += '<span class="arch-sum-step" id="archStep_' + s.id + '">';
        h += '<span class="step-dot">&#9679;</span>' + esc(s.label);
        h += '</span>';
    });
    h += '</div>';
    h += '</div>';
    panel.innerHTML = h;
    _archProgressTick();
}

function _archProgressTick() {
    if (!ARCH.discovering) return;
    var el = $('archProgressElapsed');
    if (!el) return;
    var sec = Math.floor((Date.now() - _PROGRESS_START) / 1000);
    var m = Math.floor(sec / 60); var s = sec % 60;
    el.textContent = (m > 0 ? m + '분 ' : '') + s + '초';
    setTimeout(_archProgressTick, 1000);
}

function _archUpdateProgress(stepId, label) {
    var found = false;
    _PROGRESS_STEPS.forEach(function(s) {
        var el = $('archStep_' + s.id);
        if (!el) return;
        if (s.id === stepId) {
            el.className = 'arch-sum-step active';
            el.querySelector('.step-dot').innerHTML = '&#9686;';
            found = true;
        } else if (!found) {
            el.className = 'arch-sum-step done';
            el.querySelector('.step-dot').innerHTML = '&#10003;';
        } else {
            el.className = 'arch-sum-step';
            el.querySelector('.step-dot').innerHTML = '&#9679;';
        }
    });
    if (label) {
        var lbl = $('archProgressLabel');
        if (lbl) lbl.textContent = label;
    }
}

function _archUpdateProgressSub() {}

function _archHideProgress() {
    var el = document.getElementById('archSumProgress');
    if (el) el.remove();
}

// ── Chat Mode Toggle (summary / detail) ──
function archSetChatMode(mode) {
    ARCH.chatMode = mode;
    var chatPanel = $('archChatPanel');
    var summaryPanel = $('archChatSummary');
    var btnS = $('btnChatSummary');
    var btnD = $('btnChatDetail');
    if (mode === 'summary') {
        if (chatPanel) chatPanel.style.display = 'none';
        if (summaryPanel) summaryPanel.style.display = '';
        if (btnS) btnS.classList.add('active');
        if (btnD) btnD.classList.remove('active');
    } else {
        if (chatPanel) chatPanel.style.display = '';
        if (summaryPanel) summaryPanel.style.display = 'none';
        if (btnS) btnS.classList.remove('active');
        if (btnD) btnD.classList.add('active');
    }
}

function _formatLen(n) {
    if (n >= 1000) return (Math.round(n / 100) / 10) + 'k자';
    return n + '자';
}

var _SUM_ICONS = {
    phase: '&#9654;', question: '&#10148;', answer: '&#9664;',
    thinking: '&#9679;', eval: '&#10003;', 'eval-fail': '&#10007;',
    error: '&#9888;', done: '&#10003;', app_list: '&#9776;',
    app_confirm: '&#10003;', waiting: '&#8987;'
};

function _archAddSummaryEntry(text, type) {
    var panel = $('archChatSummary');
    if (!panel) return;
    var elapsed = '';
    if (_PROGRESS_START) {
        var sec = Math.floor((Date.now() - _PROGRESS_START) / 1000);
        var m = Math.floor(sec / 60); var s = sec % 60;
        elapsed = (m > 0 ? m + ':' : '') + (s < 10 ? '0' : '') + s;
    }
    var cls = 'arch-summary-entry sum-' + (type || 'phase');
    var icon = _SUM_ICONS[type] || '&#9679;';
    var el = document.createElement('div');
    el.className = cls;
    el.innerHTML = '<span class="sum-time">' + elapsed + '</span>'
        + '<span class="sum-icon">' + icon + '</span>'
        + '<span class="sum-text">' + esc(text) + '</span>';
    panel.appendChild(el);
    var wrap = panel.parentElement;
    if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

function _archUpdateSummaryThinking(text) {
    var panel = $('archChatSummary');
    if (!panel) return;
    var existing = document.getElementById('archSummaryThinking');
    if (!existing) {
        existing = document.createElement('div');
        existing.className = 'arch-summary-entry sum-thinking';
        existing.id = 'archSummaryThinking';
        var elapsed = '';
        if (_PROGRESS_START) {
            var sec = Math.floor((Date.now() - _PROGRESS_START) / 1000);
            var m = Math.floor(sec / 60); var s = sec % 60;
            elapsed = (m > 0 ? m + ':' : '') + (s < 10 ? '0' : '') + s;
        }
        existing.innerHTML = '<span class="sum-time">' + elapsed + '</span>'
            + '<span class="sum-icon">&#9679;</span>'
            + '<span class="sum-text"></span>';
        panel.appendChild(existing);
    }
    var textSpan = existing.querySelector('.sum-text');
    if (textSpan) textSpan.textContent = text;
    var wrap = panel.parentElement;
    if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

function _archRemoveSummaryThinking() {
    var el = document.getElementById('archSummaryThinking');
    if (el) el.remove();
}

// ── Chat UI Helpers ──
function _archAddAgentHeader(panel, agent, label) {
    var clsMap = {L1: 'app', L2: 'l2', L3: 'infra', SVC: 'svc'};
    var iconMap = {L1: 'L1', L2: 'L2', L3: 'L3', SVC: 'CD'};
    var cls = clsMap[agent] || 'app';
    var icon = iconMap[agent] || agent;
    var el = document.createElement('div'); el.className = 'arch-agent-hdr';
    el.innerHTML = '<div class="agent-icon ' + cls + '">' + icon + '</div><div class="agent-label">' + esc(label) + '</div>';
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}
function _archAddQuestion(panel, agent, question, turn) {
    var agentLabel = ARCH_AGENT_LABELS[agent] || agent;
    var el = document.createElement('div'); el.className = 'arch-q';
    el.innerHTML = '<span class="phase-tag">' + esc(agentLabel) + '</span>'
        + '<span class="turn-tag">Turn ' + turn + '</span>'
        + '<div class="q-text">' + esc((question || '').substring(0, 500)) + '</div>';
    el.id = 'archLastQ';
    var old = document.getElementById('archLastQ'); if (old) old.id = '';
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}
function _archAddAnswer(panel, agent, answer, toolCalls) {
    var aDiv = document.createElement('div'); aDiv.className = 'arch-a';
    aDiv.textContent = (answer || '').substring(0, 800);
    panel.appendChild(aDiv);
    if (toolCalls.length) {
        var toolDiv = document.createElement('div'); toolDiv.className = 'arch-tools';
        toolDiv.innerHTML = toolCalls.slice(0, 5).map(function(t) { return '<span class="arch-tool-badge">' + esc((t || '').substring(0, 80)) + '</span>' }).join('');
        panel.appendChild(toolDiv);
    }
    panel.scrollTop = panel.scrollHeight;
}
function _archUpdateThinking(panel, agent, thought) {
    _archRemoveThinking(panel);
    var el = document.createElement('div'); el.className = 'arch-thinking'; el.id = 'archThinking';
    el.textContent = (thought || '').substring(0, 500);
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}
function _archRemoveThinking(panel) { var w = document.getElementById('archThinking'); if (w) w.remove() }
function _archAddEvaluation(panel, agent, score, verdict) {
    var cls = verdict === 'pass' ? 'pass' : 'fail';
    var el = document.createElement('div');
    el.innerHTML = '<span class="arch-eval-badge ' + cls + '">자체 평가: ' + score + '/100 — ' + (verdict === 'pass' ? '통과' : '보완 필요') + '</span>';
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}
function _archAddWaiting(p, label) { _archRemoveWaiting(p); var e = document.createElement('div'); e.className = 'arch-waiting'; e.id = 'archWait';
    e.innerHTML = '<div class="wait-spin"></div><div style="font-size:.68rem;font-weight:500">' + label + '</div>'; p.appendChild(e) }
function _archRemoveWaiting(p) { var w = document.getElementById('archWait'); if (w) w.remove() }
function _archAddError(p, msg) { var e = document.createElement('div'); e.className = 'arch-turn';
    e.innerHTML = '<div class="arch-result"><span class="arch-res-badge" style="background:rgba(239,68,68,.12);color:#fca5a5">' + esc(msg) + '</span></div>'; p.appendChild(e) }
// ── App Selection UI ──
function _archBuildAppSelectHTML(apps) {
    var h = '<div class="arch-app-select-hdr">';
    h += '<span style="font-weight:600;color:#e2e8f0;font-size:.72rem">분석할 App 선택</span>';
    h += '<span style="font-size:.6rem;color:#64748b;margin-left:8px">' + apps.length + '개 App 발견</span>';
    h += '</div><div class="arch-app-select-list">';
    apps.forEach(function(app) {
        var chk = app.has_tag_coverage ? ' checked' : '';
        h += '<label class="arch-app-select-item">';
        h += '<input type="checkbox" data-app="' + esc(app.name) + '" value="' + esc(app.name) + '"' + chk + '>';
        h += '<div class="arch-app-select-info">';
        h += '<span class="arch-app-select-name">' + esc(app.name) + '</span>';
        h += '<span class="arch-app-select-desc">' + esc(app.description || '').substring(0, 80) + '</span>';
        if (app.has_tag_coverage) h += '<span class="arch-app-tag-badge">tag scope</span>';
        h += '</div></label>';
    });
    h += '</div><div class="arch-app-select-actions">';
    h += '<button class="arch-btn" onclick="_archSelectAllApps(true)" style="background:#334155;color:#94a3b8;font-size:.6rem;padding:4px 10px">전체 선택</button>';
    h += '<button class="arch-btn" onclick="_archSelectAllApps(false)" style="background:#334155;color:#94a3b8;font-size:.6rem;padding:4px 10px">전체 해제</button>';
    h += '<button class="arch-btn arch-btn-primary" onclick="_archConfirmAppSelection()" style="font-size:.62rem;padding:5px 14px;margin-left:auto">분석 시작</button>';
    h += '</div>';
    return h;
}
function _archShowAppSelection(panel, apps) {
    var el = document.createElement('div');
    el.className = 'arch-app-select';
    el.innerHTML = _archBuildAppSelectHTML(apps);
    el.addEventListener('change', function(ev) {
        if (ev.target && ev.target.dataset && ev.target.dataset.app) {
            _archSyncAppCheckbox(ev.target.dataset.app, ev.target.checked);
        }
    });
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}
function _archSyncAppCheckbox(appName, checked) {
    document.querySelectorAll('.arch-app-select input[data-app="' + appName + '"]').forEach(function(cb) {
        cb.checked = checked;
    });
}
function _archSelectAllApps(state) {
    document.querySelectorAll('.arch-app-select input[type=checkbox]').forEach(function(cb) { cb.checked = state; });
}
function _archConfirmAppSelection() {
    var first = document.querySelector('.arch-app-select');
    if (!first) return;
    var selected = [];
    first.querySelectorAll('input[type=checkbox]:checked').forEach(function(cb) { selected.push(cb.value); });
    if (!selected.length) { alert('최소 1개 App을 선택하세요'); return; }
    // Disable button to prevent duplicate clicks
    var btn = first.querySelector('.arch-btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '처리 중...'; }
    fetch('/api/arch/discover/select-apps', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: SELECTED, apps: selected})
    }).then(function(r) { return r.json(); }).then(function(d) {
        if (!d.ok) return;
        _archRemoveAppSelection();
        if (d.gate_missing || !ARCH.discovering) {
            archDiscover(true);
        }
    }).catch(function() {
        if (btn) { btn.disabled = false; btn.textContent = '분석 시작'; }
    });
}
function _archRemoveAppSelection() {
    document.querySelectorAll('.arch-app-select').forEach(function(el) { el.remove(); });
}
function _archShowAppStatusBar(panel, selected, unselected) {
    _archRemoveAppStatusBar();
    var el = document.createElement('div');
    el.className = 'arch-app-status-bar'; el.id = 'archAppStatusBar';
    var h = '<div class="arch-app-status-label">분석 대상</div><div class="arch-app-status-chips">';
    selected.forEach(function(name) { h += '<span class="arch-app-chip arch-app-chip-on">' + esc(name) + '</span>'; });
    unselected.forEach(function(name) { h += '<span class="arch-app-chip arch-app-chip-off">' + esc(name) + '</span>'; });
    h += '</div>';
    el.innerHTML = h;
    panel.insertBefore(el, panel.firstChild);
}
function _archRemoveAppStatusBar() { var el = document.getElementById('archAppStatusBar'); if (el) el.remove(); }
function _archHighlightActiveApp(name) {
    var bar = document.getElementById('archAppStatusBar');
    if (!bar) return;
    bar.querySelectorAll('.arch-app-chip-on').forEach(function(chip) {
        chip.classList.remove('arch-app-chip-active');
        if (chip.textContent.trim() === name) chip.classList.add('arch-app-chip-active');
    });
}

function _archShowResumeIfNeeded() {
    var sid = SELECTED || '';
    fetch('/api/arch/status?space_id=' + encodeURIComponent(sid)).then(function(r) { return r.json() }).then(function(d) {
        if (d.has_checkpoint) {
            $('btnArchResume').style.display = '';
            $('btnArchResume').innerHTML = '이어서 분석';
        } else {
            $('btnArchResume').style.display = 'none';
        }
    }).catch(function() { $('btnArchResume').style.display = 'none'; });
}
function _archAddLayerDone(panel, layer) {
    var el = document.createElement('div');
    el.className = 'arch-layer-done';
    el.innerHTML = '<span style="color:#22c55e">&#10003;</span> ' + esc(layer) + ' 분석 완료';
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}

// ── Icon preload cache ──
var _ICON_CACHE = {};
(function _preloadIcons() {
    Object.keys(ARCH_ICON_PATHS).forEach(function(id) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', ARCH_ICON_PATHS[id], true);
        xhr.onload = function() {
            if (xhr.status === 200) {
                _ICON_CACHE[id] = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(xhr.responseText)));
            }
        };
        xhr.send();
    });
})();

// ── Client-side icon_key + tier enrichment (mirrors Python _enrich_graph_nodes) ──
var _ICON_RULES_JS = [
    // K8s workload types — specific before generic
    [/\bstatefulset\b/i, 'k8s-sts'],
    [/\bdaemonset\b/i, 'k8s-ds'],
    [/\b(fluent.?bit|adot|otel.?collector)\b/i, 'k8s-ds'],
    [/\bcronjob\b/i, 'k8s-cj'],
    [/\breplicaset\b/i, 'k8s-rs'],
    [/\b(horizontalpodautoscaler|hpa)\b/i, 'k8s-hpa'],
    [/\b(poddisruptionbudget|pdb)\b/i, 'k8s-pdb'],
    [/\bserviceaccount\b/i, 'k8s-sa'],
    [/\bconfigmap\b/i, 'k8s-cm'],
    [/\bnamespace\b/i, 'k8s-ns'],
    [/\bpersistentvolumeclaim\b|\bpvc\b/i, 'k8s-pvc'],
    [/\bpersistentvolume\b/i, 'k8s-pv'],
    [/\bnetworkpolic/i, 'k8s-netpol'],
    [/\b(clusterrolebinding|rolebinding)\b/i, 'k8s-rb'],
    [/\b(clusterrole|role)\b/i, 'k8s-role'],
    [/\bendpoint/i, 'k8s-ep'],
    [/\blimitrange\b/i, 'k8s-limits'],
    [/\bresourcequota\b/i, 'k8s-quota'],
    [/\bjob\b/i, 'k8s-job'],
    [/\beks\b.*\bdeployment\b/i, 'k8s-deploy'],
    [/\beks\b.*\bpod\b/i, 'k8s-pod'],
    [/\beks\b.*\bnodegroup\b/i, 'aws-eks'],
    // AWS managed services
    [/\bsecrets?\s*manager\b/i, 'aws-secrets-manager'],
    [/\bfis\b|\bfault\s*injection\b/i, 'aws-fis'],
    [/\bprivatelink\b/i, 'aws-vpc'],
    [/\bvpc\b|subnet\b|nat\s*gateway\b|internet\s*gateway\b/i, 'aws-vpc'],
    [/\beks\s*(cluster|nodegroup)\b/i, 'aws-eks'],
    [/\b(amazon\s+)?ecr\b/i, 'aws-ecr'],
    [/\brds\b|db\s*instance\b/i, 'aws-rds'],
    [/\baurora\b/i, 'aws-rds'],
    [/\belasticache\b/i, 'aws-elasticache'],
    [/\bdynamodb\b/i, 'aws-dynamodb'],
    [/\bs3\b/i, 'aws-s3'],
    [/\bsqs\b/i, 'aws-sqs'],
    [/\bsns\b/i, 'aws-sns'],
    [/\blambda\b/i, 'aws-lambda'],
    [/\bcloudwatch\b|application\s*signals\b/i, 'aws-cloudwatch'],
    [/\bx-?ray\b/i, 'aws-xray'],
    [/\bbedrock\b/i, 'aws-bedrock'],
    [/\beventbridge\b|event\s*bus\b/i, 'aws-eventbridge'],
    [/\b(nlb|alb|elb)\b/i, 'aws-elb'],
    [/\bload\s*balancer\b/i, 'aws-elb'],
    [/\bingress\b/i, 'aws-elb'],
    [/\bemr\b/i, 'aws-generic'],
    [/\blog\s*group\b/i, 'aws-cloudwatch'],
    [/\bsampling\s*rule\b/i, 'aws-xray'],
    [/\b(devops.?agent|agent\s*space)\b/i, 'aws-devops-agent']
];
var _TIER_OBS_RE = /CloudWatch|X-Ray|Application Signals|Alarm|Logs Log Group|SNS Topic/i;
var _TIER_PLAT_RE = /ECR |EKS Cluster|VPC|Subnet|NAT Gateway|Internet Gateway|EC2 Instance|IAM |Secrets Manager|ConfigMap|PersistentVolume/i;
var _TIER_OPS_RE = /FIS |Systems Manager/i;
var _TIER_OPS_GRP_RE = /Chaos|Simulator|Scenario/i;

function _resolveIconKey(kind, name, serviceType, ns) {
    var combined = (kind || '') + ' ' + (name || '');
    for (var i = 0; i < _ICON_RULES_JS.length; i++) {
        if (_ICON_RULES_JS[i][0].test(combined)) return _ICON_RULES_JS[i][1];
    }
    var isExt = ns === 'external' || ns === 'managed' || kind === 'ExternalService';
    if (isExt) return ({db:'aws-rds', cache:'aws-elasticache', queue:'aws-sqs', gateway:'aws-generic'})[serviceType] || 'aws-generic';
    return ({app:'k8s-deploy', gateway:'k8s-svc', cache:'k8s-deploy', db:'k8s-deploy', queue:'k8s-deploy', worker:'k8s-deploy'})[serviceType] || 'k8s-deploy';
}

function _classifyTier(n, appGroup) {
    var kind = n.kind || '', name = n.name || '', svc = n.service_type || '', grp = n.group || '', ns = n.namespace || '';
    var combined = kind + ' ' + name;
    var isSame = appGroup && grp === appGroup;
    if (isSame && /^(worker|app|gateway|queue)$/.test(svc)) return 'core';
    if (_TIER_OPS_RE.test(combined)) return 'ops';
    if (_TIER_OPS_GRP_RE.test(grp) && !isSame) return 'ops';
    if (_TIER_OBS_RE.test(combined)) return 'observe';
    if (_TIER_PLAT_RE.test(combined)) return 'platform';
    if (svc === 'platform' && !isSame) return 'platform';
    if ((svc === 'db' || svc === 'cache') && !isSame) return 'data';
    if (ns === 'external' && svc !== 'app' && svc !== 'worker') return 'data';
    if (isSame) return 'core';
    if (ns === 'external') return 'data';
    return 'core';
}

function _enrichNodes(nodes) {
    (nodes || []).forEach(function(n) {
        if (!n.icon_key) n.icon_key = _resolveIconKey(n.kind, n.name, n.service_type, n.namespace);
        if (!n.tier) n.tier = _classifyTier(n, '');
    });
}

// ── SVG Helpers ──
function _svgE(t, a) { var e = document.createElementNS('http://www.w3.org/2000/svg', t); if (a) for (var k in a) e.setAttribute(k, a[k]); return e }
function _archIconImg(iconId, x, y, w, h) {
    var src = _ICON_CACHE[iconId] || ARCH_ICON_PATHS[iconId] || _ICON_CACHE['aws-generic'] || ARCH_ICON_PATHS['aws-generic'];
    var img = _svgE('image', {x: x, y: y, width: w, height: h});
    img.setAttribute('href', src);
    img.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', src);
    return img;
}
function _archMaxY(pos) { var my = 0; for (var k in pos) if (pos[k].y > my) my = pos[k].y; return my }
function _archBindTip(grp, nodeObj) {
    grp.addEventListener('mouseenter', function(ev) {
        var lines = ['<div class="tt">' + esc(nodeObj.name) + '</div>'];
        if (nodeObj.role) lines.push(esc(nodeObj.role));
        if (nodeObj.group) lines.push('<span style="color:#94a3b8">group:</span> ' + esc(nodeObj.group));
        if (nodeObj.service_type) lines.push('<span style="color:#94a3b8">type:</span> ' + esc(nodeObj.service_type));
        if (nodeObj.kind) lines.push('<span style="color:#94a3b8">kind:</span> ' + esc(nodeObj.kind));
        if (nodeObj.namespace && nodeObj.namespace !== 'external' && nodeObj.namespace !== 'managed') lines.push('<span style="color:#94a3b8">ns:</span> ' + esc(nodeObj.namespace));
        if (nodeObj.ports && nodeObj.ports.length) lines.push('<span style="color:#94a3b8">ports:</span> ' + nodeObj.ports.join(', '));
        if (nodeObj.description) lines.push('<span style="color:#94a3b8">desc:</span> ' + esc(nodeObj.description));
        showTip(ev, lines.join('<br>'));
    });
    grp.addEventListener('mousemove', function(ev) { moveTip(ev); });
    grp.addEventListener('mouseleave', function() { hideTip(); });
}

// ── Cross-Navigation ──

function _crossNavExecute(action, entity) {
    ARCH.crossNav = {entity: entity, highlight: true};
    if (action === 'seq') {
        var wfIdx = _crossNavFindWorkflow(entity);
        if (wfIdx >= 0) ARCH._seqSelectedWf = wfIdx;
        archSwitchView('seq');
    } else if (action === 'topo-l3') {
        archSwitchView('topo');
        archNavigateTo('L3', {service: entity});
    } else if (action === 'topo-l2') {
        var node = null;
        ARCH.nodes.forEach(function(n) { if (n.name === entity) node = n; });
        var appGroup = node ? (node.group || '') : '';
        archSwitchView('topo');
        archNavigateTo('L2', {app: appGroup});
    } else if (action === 'k8s') {
        archSwitchView('k8s');
    }
    setTimeout(function() { ARCH.crossNav = null; }, 2000);
}

function _crossNavFindWorkflow(entity) {
    var a = ARCH.analysis;
    if (!a || !a.workflows) return -1;
    var best = -1;
    a.workflows.forEach(function(wf, i) {
        if (!wf.hops) return;
        wf.hops.forEach(function(hop) {
            if ((hop.from || hop.source) === entity || (hop.to || hop.target) === entity) best = i;
        });
    });
    return best;
}

// ── Layout Algorithm (Workflow lanes: each flow = horizontal row) ──
function _archLayout(nodes, edges, W, H) {
    var XGAP = 160, YGAP = 120, PAD = 80;
    var nameSet = {}; nodes.forEach(function(n) { nameSet[n.name] = n });

    // Build workflow chains from ARCH.analysis.workflows
    var workflows = (ARCH.analysis && ARCH.analysis.workflows) || [];
    var lanes = [];  // each lane = [node1, node2, ...] in order

    workflows.forEach(function(wf) {
        if (!wf || !wf.hops || !wf.hops.length) return;
        var chain = [];
        wf.hops.forEach(function(hop, i) {
            var src = hop.from || hop.source || '';
            var tgt = hop.to || hop.target || '';
            if (i === 0 && src && nameSet[src]) chain.push(src);
            if (tgt && nameSet[tgt]) chain.push(tgt);
        });
        if (chain.length) lanes.push(chain);
    });

    // If no workflows, fall back to BFS-based layout
    if (!lanes.length) {
        return _archLayoutFallback(nodes, edges, W, H);
    }

    // Assign positions: each lane gets a y-row, x = position in chain
    var pos = {};
    var placed = {};  // track which nodes are placed

    // For shared nodes, find first occurrence position
    lanes.forEach(function(chain, laneIdx) {
        var y = PAD + laneIdx * YGAP;
        chain.forEach(function(name, stepIdx) {
            if (placed[name]) return;  // already placed by earlier lane
            var x = PAD + stepIdx * XGAP;
            pos[name] = {x: x, y: y};
            placed[name] = true;
        });
    });

    // Place remaining nodes not in any workflow
    // Find max x from placed nodes for right-side positioning
    var maxX = PAD;
    for (var k in pos) { if (pos[k].x > maxX) maxX = pos[k].x; }
    var rightX = maxX + XGAP;
    var orphanIdx = 0;

    nodes.forEach(function(n) {
        if (placed[n.name]) return;
        var neighbors = [];
        edges.forEach(function(e) {
            if (e.source === n.name && pos[e.target]) neighbors.push(pos[e.target]);
            if (e.target === n.name && pos[e.source]) neighbors.push(pos[e.source]);
        });
        if (neighbors.length) {
            // Has connections — place near neighbors
            var avgX = 0, avgY = 0;
            neighbors.forEach(function(p) { avgX += p.x; avgY += p.y });
            avgX /= neighbors.length; avgY /= neighbors.length;
            pos[n.name] = {x: avgX, y: avgY + YGAP * 0.5};
        } else {
            // No connections — right side column
            pos[n.name] = {x: rightX, y: PAD + orphanIdx * YGAP};
            orphanIdx++;
        }
        placed[n.name] = true;
    });

    // Resolve overlaps: if two nodes are too close, push apart
    var nodeNames = nodes.map(function(n) { return n.name });
    for (var pass = 0; pass < 3; pass++) {
        for (var i = 0; i < nodeNames.length; i++) {
            for (var j = i + 1; j < nodeNames.length; j++) {
                var a = nodeNames[i], b = nodeNames[j];
                var dx = Math.abs(pos[a].x - pos[b].x);
                var dy = Math.abs(pos[a].y - pos[b].y);
                if (dx < XGAP * 0.7 && dy < YGAP * 0.7) {
                    // Too close — push vertically
                    if (pos[a].y <= pos[b].y) { pos[b].y = pos[a].y + YGAP * 0.8; }
                    else { pos[a].y = pos[b].y + YGAP * 0.8; }
                }
            }
        }
    }

    return pos;
}

// Fallback: simple BFS + barycenter when no workflows available
function _archLayoutFallback(nodes, edges, W, H) {
    var XGAP = 160, YGAP = 100, PAD = 80;
    var nameSet = {}; nodes.forEach(function(n) { nameSet[n.name] = n });

    var edgePairSeen = {};
    var dagEdges = [];
    edges.forEach(function(e) {
        if (!nameSet[e.source] || !nameSet[e.target]) return;
        var fwd = e.source + '→' + e.target;
        var rev = e.target + '→' + e.source;
        if (edgePairSeen[rev]) return;
        if (!edgePairSeen[fwd]) { edgePairSeen[fwd] = true; dagEdges.push(e); }
    });

    var adj = {}, radj = {}, inD = {}, outD = {};
    nodes.forEach(function(n) { adj[n.name] = []; radj[n.name] = []; inD[n.name] = 0; outD[n.name] = 0 });
    dagEdges.forEach(function(e) {
        adj[e.source].push(e.target); radj[e.target].push(e.source);
        outD[e.source] = (outD[e.source] || 0) + 1; inD[e.target] = (inD[e.target] || 0) + 1;
    });

    var layer = {}, maxL = 0, visited = {}, extNames = {};
    nodes.forEach(function(n) {
        if (n.namespace === 'external' || n.kind === 'ExternalService') extNames[n.name] = true;
    });

    function bfsPass(startNodes) {
        startNodes.forEach(function(r) { if (layer[r] === undefined) layer[r] = 0; visited[r] = true; });
        var queue = startNodes.slice(), cap = nodes.length * nodes.length + 1, iter = 0;
        while (queue.length && iter < cap) {
            var cur = queue.shift(); iter++;
            (adj[cur] || []).forEach(function(nb) {
                var nl = layer[cur] + 1;
                if (layer[nb] === undefined || nl > layer[nb]) { layer[nb] = nl; if (nl > maxL) maxL = nl; }
                if (!visited[nb]) { visited[nb] = true; queue.push(nb); }
            });
        }
    }

    var roots = [];
    nodes.forEach(function(n) { if (!extNames[n.name] && inD[n.name] === 0) roots.push(n.name) });
    if (roots.length) bfsPass(roots);
    var changed = true;
    while (changed) {
        changed = false;
        var best = null, bestOut = -1;
        nodes.forEach(function(n) {
            if (!visited[n.name] && !extNames[n.name]) {
                if (outD[n.name] > bestOut) { bestOut = outD[n.name]; best = n.name; }
            }
        });
        if (best) { bfsPass([best]); changed = true; }
    }
    nodes.forEach(function(n) {
        if (extNames[n.name]) layer[n.name] = maxL + 1;
        else if (layer[n.name] === undefined) layer[n.name] = 0;
    });
    maxL = 0; nodes.forEach(function(n) { if (layer[n.name] > maxL) maxL = layer[n.name] });

    var layers = [];
    for (var i = 0; i <= maxL; i++) layers.push([]);
    nodes.forEach(function(n) { layers[layer[n.name]].push(n.name) });

    function orderIdx(arr) { var m = {}; arr.forEach(function(n, i) { m[n] = i }); return m }
    function barySort(layerArr, refArr, adjMap) {
        var refIdx = orderIdx(refArr);
        layerArr.sort(function(a, b) {
            var na = adjMap[a] || [], nb = adjMap[b] || [];
            var ba = 0, bb = 0, ca = 0, cb = 0;
            na.forEach(function(x) { if (refIdx[x] !== undefined) { ba += refIdx[x]; ca++ } });
            nb.forEach(function(x) { if (refIdx[x] !== undefined) { bb += refIdx[x]; cb++ } });
            ba = ca ? ba / ca : 999; bb = cb ? bb / cb : 999;
            return ba - bb;
        });
    }
    for (var i = 1; i <= maxL; i++) barySort(layers[i], layers[i - 1], radj);
    for (var i = maxL - 1; i >= 0; i--) barySort(layers[i], layers[i + 1], adj);
    for (var i = 1; i <= maxL; i++) barySort(layers[i], layers[i - 1], radj);
    for (var i = maxL - 1; i >= 0; i--) barySort(layers[i], layers[i + 1], adj);

    var numLayers = maxL + 1;
    var usableW = Math.max(W - PAD * 2, numLayers * XGAP);
    var xStep = numLayers > 1 ? usableW / (numLayers - 1) : 0;
    var pos = {};
    layers.forEach(function(col, li) {
        var x = PAD + li * xStep;
        var totalH = col.length * YGAP;
        var startY = Math.max(PAD, (H - totalH) / 2);
        col.forEach(function(name, ni) { pos[name] = {x: x, y: startY + ni * YGAP} });
    });
    return pos;
}

// ── Layout Persistence ──
function archSaveLayout() {
    var data = {positions: ARCH.customPos || {}, props: ARCH.customProps || {}, space_id: SELECTED};
    fetch('/api/arch/layout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)})
        .then(function(r) { return r.json() }).then(function(d) {
            if (d.ok) $('archTopoInfo').textContent = '레이아웃 저장됨';
            setTimeout(function() { $('archTopoInfo').textContent = '' }, 2000);
        });
}
function archLoadLayout() {
    fetch('/api/arch/layout?space_id='+encodeURIComponent(SELECTED)).then(function(r) { return r.json() }).then(function(d) {
        if (d.ok && d.layout) {
            if (d.layout.positions && Object.keys(d.layout.positions).length) ARCH.customPos = d.layout.positions;
            if (d.layout.props) ARCH.customProps = d.layout.props;
        }
    }).catch(function() {});
}
function archResetLayout() {
    ARCH.customPos = {}; ARCH.customProps = {};
    fetch('/api/arch/layout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({positions: {}, props: {}, space_id: SELECTED})});
    archNavigateTo(ARCH.nav.level, {app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});
    $('archTopoInfo').textContent = '초기화됨';
    setTimeout(function() { $('archTopoInfo').textContent = '' }, 2000);
}

// ── AI Recommendation ──
function archRecommend() {
    var btn = $('btnArchRec');
    if (!btn) return;
    btn.disabled = true; btn.innerHTML = '<span class="arch-spinner"></span> 분석 중...';
    $('archRecSection').style.display = '';
    $('archRecContent').innerHTML = '<div class="arch-waiting"><div class="wait-spin"></div><div style="font-size:.68rem">Bedrock Claude Topology 분석 중...</div></div>';

    var model = $('archModel').value;
    fetch('/api/arch/recommend', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({model: model, space_id: SELECTED})}).then(function(r) { return r.json() }).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        ARCH.recs = data.result;
        archRenderRecs(data.result);
        btn.disabled = false; btn.innerHTML = 'AI 추천 분석';
    }).catch(function(e) {
        $('archRecContent').innerHTML = '<div class="arch-turn"><div class="arch-result"><span class="arch-res-badge" style="background:rgba(239,68,68,.12);color:#fca5a5">' + esc(e.message) + '</span></div></div>';
        btn.disabled = false; btn.innerHTML = 'AI 추천 분석';
    });
}
function archRenderRecs(result) {
    var el = $('archRecContent');
    var a = result.architecture_analysis || {}, recs = result.recommendations || [];
    var h = '<div class="arch-rec-analysis"><h4>Topology Analysis</h4>';
    if (a.critical_path) h += '<div class="arch-rec-field"><div class="lbl">핵심 경로</div><div class="val">' + esc(a.critical_path) + '</div></div>';
    if (a.single_points_of_failure && a.single_points_of_failure.length)
        h += '<div class="arch-rec-field"><div class="lbl">단일 장애점 (SPOF)</div><div style="display:flex;flex-wrap:wrap;gap:4px">' + a.single_points_of_failure.map(function(s) { return '<span class="arch-spof-tag">' + esc(s) + '</span>' }).join('') + '</div></div>';
    if (a.risk_areas && a.risk_areas.length) {
        h += '<div class="arch-rec-field"><div class="lbl">위험 영역</div>';
        a.risk_areas.forEach(function(r) { h += '<div class="val" style="margin-bottom:4px">' + esc(r) + '</div>' });
        h += '</div>';
    }
    h += '</div>';

    recs.forEach(function(r, i) {
        var t = typeof r.target === 'object' ? JSON.stringify(r.target) : r.target;
        h += '<div class="arch-rec-card"><div class="arch-rec-head"><div class="arch-rec-num">' + (i + 1) + '</div><div class="arch-rec-title">' + esc(r.name) + '</div>'
            + '<span class="arch-pri ' + r.priority + '">' + r.priority + '</span></div>'
            + '<div class="arch-rec-body">'
            + '<div style="margin-bottom:4px"><strong>Template:</strong> ' + esc(r.template_id) + '</div>'
            + '<div style="margin-bottom:4px"><strong>근거:</strong> ' + esc(r.rationale) + '</div>'
            + (r.expected_impact ? '<div style="margin-bottom:4px"><strong>예상 영향:</strong> ' + esc(r.expected_impact) + '</div>' : '')
            + (r.detection_challenge ? '<div style="margin-bottom:4px"><strong>탐지 난이도:</strong> ' + esc(r.detection_challenge) + '</div>' : '')
            + '</div>'
            + '<div class="arch-rec-target">' + esc(trun(t, 120)) + '</div>'
            + '<div style="margin-top:8px;text-align:right">'
            + '<button class="arch-gen-btn" id="archGenBtn' + i + '" onclick="archGenerateScenario(' + i + ')">'
            + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>'
            + ' Scenario 생성</button></div></div>';
    });
    el.innerHTML = h;
}

// ── Scenario Generation ──
function archGenerateScenario(index) {
    var recs = (ARCH.recs || {}).recommendations || [];
    if (index >= recs.length) return;
    var rec = recs[index];
    var btn = $('archGenBtn' + index);
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="arch-spinner"></span> 생성 중...'; }

    var model = $('archModel') ? $('archModel').value : 'opus';
    fetch('/api/arch/generate-scenario', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({recommendation: rec, model: model, space_id: SELECTED})
    }).then(function(r) { return r.json() }).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        ARCH._generatedScenario = data.scenario;
        archShowGenModal(data.scenario);
        if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg> Scenario 생성'; }
    }).catch(function(e) {
        alert('Scenario 생성 실패:' + e.message);
        if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg> Scenario 생성'; }
    });
}

function archShowGenModal(scenario) {
    var modal = $('archGenModal');
    if (!modal) return;
    modal.style.display = 'flex';

    var s = scenario;
    var h = '';
    h += '<div class="gen-meta">';
    h += '<div class="gen-meta-row"><span class="gen-label">ID</span><span class="gen-val">' + esc(s.id || '') + '</span></div>';
    h += '<div class="gen-meta-row"><span class="gen-label">이름</span><span class="gen-val">' + esc(s.name || '') + '</span></div>';
    h += '<div class="gen-meta-row"><span class="gen-label">카테고리</span><span class="gen-val">' + esc(s.category || '') + ' / ' + esc(s.layer || '') + '</span></div>';
    h += '<div class="gen-meta-row"><span class="gen-label">목적</span><span class="gen-val" style="white-space:pre-wrap">' + esc(s.purpose || '') + '</span></div>';
    h += '<div class="gen-meta-row"><span class="gen-label">예상 근본원인</span><span class="gen-val">' + esc(s.expected_root_cause || '') + '</span></div>';
    h += '</div>';

    if (s.trigger) {
        h += '<div class="gen-section"><div class="gen-section-title">트리거</div>';
        h += '<pre class="gen-code">' + esc(s.trigger.command || '') + '</pre></div>';
    }
    if (s.restore) {
        h += '<div class="gen-section"><div class="gen-section-title">복원</div>';
        h += '<pre class="gen-code">' + esc(s.restore.command || '') + '</pre></div>';
    }

    if (s.verification && s.verification.steps) {
        h += '<div class="gen-section"><div class="gen-section-title">검증 단계 (' + s.verification.steps.length + ')</div>';
        h += '<div class="gen-steps">';
        s.verification.steps.forEach(function(st, i) {
            h += '<div class="gen-step"><span class="gen-step-num">' + (i+1) + '</span>'
                + '<span class="gen-step-name">' + esc(st.name || '') + '</span>'
                + '<span class="gen-step-type">' + esc(st.type || '') + '</span></div>';
        });
        h += '</div></div>';
    }

    if (s.evaluation_rubric) {
        h += '<div class="gen-section"><div class="gen-section-title">평가 루브릭</div>';
        h += '<table class="gen-rubric"><tr><th>기준</th><th>비중</th><th>설명</th></tr>';
        Object.keys(s.evaluation_rubric).forEach(function(k) {
            var r = s.evaluation_rubric[k];
            h += '<tr><td>' + esc(k) + '</td><td>' + (r.weight || 0) + '</td><td>' + esc(r.criteria || '') + '</td></tr>';
        });
        h += '</table></div>';
    }

    $('archGenPreview').innerHTML = h;
    $('archGenJsonArea').value = JSON.stringify(s, null, 2);
    $('archGenJsonWrap').style.display = 'none';
}

function archToggleGenJson() {
    var w = $('archGenJsonWrap');
    w.style.display = w.style.display === 'none' ? 'block' : 'none';
}

function archSaveGenScenario(openSim) {
    var jsonStr = $('archGenJsonArea').value;
    var scenario;
    try { scenario = JSON.parse(jsonStr); } catch(e) {
        alert('JSON 파싱 오류: ' + e.message); return;
    }

    var saveBtn = $('archGenSaveBtn');
    var saveRunBtn = $('archGenSaveRunBtn');
    if (saveBtn) saveBtn.disabled = true;
    if (saveRunBtn) saveRunBtn.disabled = true;

    fetch('/api/arch/save-scenario', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario: scenario, space_id: SELECTED})
    }).then(function(r) { return r.json() }).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        archCloseGenModal();
        if (openSim) {
            window.open('//' + location.hostname + ':8080', '_blank');
        } else {
            alert('Scenario 저장 완료:' + data.file);
        }
    }).catch(function(e) {
        alert('저장 실패: ' + e.message);
        if (saveBtn) saveBtn.disabled = false;
        if (saveRunBtn) saveRunBtn.disabled = false;
    });
}

function archCloseGenModal() {
    var modal = $('archGenModal');
    if (modal) modal.style.display = 'none';
}

// ================================================================
// NAVIGATION — Drill-down System
// ================================================================

function archNavigateTo(level, ctx) {
    ctx = ctx || {};
    ARCH._zoom = {scale: 1, tx: 0, ty: 0};
    // Push to history
    ARCH.nav.history.push({level: ARCH.nav.level, app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});

    ARCH.nav.level = level;
    if (level === 'L1') {
        if (ARCH.mode === 'single_app') {
            ARCH.nav.selectedApp = ARCH.nav.selectedApp || ctx.app || 'Application';
        } else {
            ARCH.nav.selectedApp = null;
        }
        ARCH.nav.selectedService = null;
    } else if (level === 'L2') {
        ARCH.nav.selectedApp = ctx.app || ARCH.nav.selectedApp || null;
        ARCH.nav.selectedService = ctx.service || null;
    } else if (level === 'L3') {
        ARCH.nav.selectedApp = ctx.app || ARCH.nav.selectedApp;
        ARCH.nav.selectedService = ctx.service || null;
    }

    // Show/hide containers
    var views = ['archViewL1', 'archViewL2', 'archViewL3'];
    var viewMap = {L1: 'archViewL1', L2: 'archViewL2', L3: 'archViewL3'};
    if (ARCH.mode === 'single_app') {
        // Single-app: L1=service topo (L2 container), L2=K8s detail (L3 container), L3=component (L3 container)
        viewMap.L1 = 'archViewL2';
        viewMap.L2 = 'archViewL3';
        viewMap.L3 = 'archViewL3';
    }
    views.forEach(function(vid) {
        var el = $(vid);
        if (el) el.style.display = (vid === viewMap[level]) ? '' : 'none';
    });

    // Back button
    var backBtn = $('archBackBtn');
    if (backBtn) backBtn.style.display = (level === 'L1') ? 'none' : '';

    // Breadcrumb
    archUpdateBreadcrumb();

    // Title
    var titles;
    if (ARCH.mode === 'single_app') {
        titles = {
            L1: 'Service Topology',
            L2: esc(ARCH.nav.selectedService || '') + ' K8s Detail',
            L3: esc(ARCH.nav.selectedService || '') + ' Component'
        };
    } else {
        titles = {
            L1: 'App Topology',
            L2: esc(ARCH.nav.selectedApp || '') + ' Component',
            L3: esc(ARCH.nav.selectedService || '') + ' Detail'
        };
    }
    var titleEl = $('archTopoTitle');
    if (titleEl) titleEl.textContent = titles[level] || level;

    // Info
    var infoEl = $('archTopoInfo');

    // Toggle button visibility
    var toggleEl = $('archViewToggle');
    if (ARCH.mode === 'single_app') {
        if (toggleEl) toggleEl.style.display = (level === 'L1') ? '' : 'none';
    } else {
        if (toggleEl) toggleEl.style.display = (level === 'L2') ? '' : 'none';
    }
    _archSyncToggleButtons();
    var tierEl = $('archTierFilter');
    if (tierEl) tierEl.style.display = 'none';
    _archSyncTierButtons();

    // L1 App Filter bar — hide in single-app mode
    var l1FilterEl = $('archL1AppFilter');
    if (l1FilterEl) l1FilterEl.style.display = (level === 'L1' && ARCH.mode !== 'single_app') ? 'flex' : 'none';

    // L2 Connected Apps bar
    var l2FilterEl = $('archL2ConnectedFilter');
    if (ARCH.mode === 'single_app') {
        if (l2FilterEl) l2FilterEl.style.display = (level === 'L1') ? 'flex' : 'none';
    } else {
        if (l2FilterEl) l2FilterEl.style.display = (level === 'L2') ? 'flex' : 'none';
    }

    // Render appropriate view
    if (ARCH.mode === 'single_app') {
        _archNavigateSingleApp(level, infoEl);
    } else {
        _archNavigateMultiApp(level, infoEl);
    }

    // Description
    archRenderDesc();
}

function _archNavigateSingleApp(level, infoEl) {
    var appGroup = ARCH.nav.selectedApp || 'Application';
    if (level === 'L1') {
        // Single-app L1 = service topology (use L2 renderer)
        _archBuildL2ConnectedFilter(appGroup);
        if (ARCH.viewMode === 'flow') archRenderL2Flow(appGroup); else archRenderL2(appGroup);
        if (infoEl) {
            var l2u = _archDataL2Unified(ARCH.nodes, ARCH.edges, appGroup);
            infoEl.textContent = l2u.nodes.length + ' Service, ' + l2u.edges.length + ' 연결';
        }
    } else if (level === 'L2') {
        // Single-app L2 = K8s resource detail (use L3 renderer)
        archRenderL3(ARCH.nav.selectedService);
        if (infoEl) {
            var l3d = _archDataL3(ARCH.nodes, ARCH.edges, ARCH.nav.selectedService, ARCH.analysis);
            infoEl.textContent = l3d.connectedNodes.length + ' 연결, ' + (l3d.spof.length ? 'SPOF' : '정상');
        }
    } else if (level === 'L3') {
        // Single-app L3 = component diagram (code analysis)
        _archRenderComponentView(ARCH.nav.selectedService);
    }
}

function _archNavigateMultiApp(level, infoEl) {
    if (level === 'L1') {
        _archBuildL1AppFilter();
        archRenderL1();
        if (infoEl) {
            var l1d = _archDataL1(ARCH.nodes, ARCH.edges);
            infoEl.textContent = l1d.appNodes.length + ' App, ' + ARCH.nodes.filter(function(n) { return !_archIsManaged(n); }).length + ' Service';
        }
    } else if (level === 'L2') {
        _archBuildL2ConnectedFilter(ARCH.nav.selectedApp);
        if (ARCH.viewMode === 'flow') archRenderL2Flow(ARCH.nav.selectedApp); else archRenderL2(ARCH.nav.selectedApp);
        if (infoEl) {
            if ($('archSvgL2Unified')) {
                var l2u = _archDataL2Unified(ARCH.nodes, ARCH.edges, ARCH.nav.selectedApp);
                infoEl.textContent = l2u.nodes.length + ' 노드, ' + l2u.edges.length + ' 연결';
            } else {
                var l2d = _archDataL2(ARCH.nodes, ARCH.edges, ARCH.nav.selectedApp);
                infoEl.textContent = l2d.appDiagram.nodes.length + ' Service, ' + l2d.infraDiagram.nodes.length + ' Infra Node';
            }
        }
    } else if (level === 'L3') {
        archRenderL3(ARCH.nav.selectedService);
        if (infoEl) {
            var l3d = _archDataL3(ARCH.nodes, ARCH.edges, ARCH.nav.selectedService, ARCH.analysis);
            infoEl.textContent = l3d.connectedNodes.length + ' 연결, ' + (l3d.spof.length ? 'SPOF' : '정상');
        }
    }
}

function archGoBack() {
    if (!ARCH.nav.history.length) return;
    var prev = ARCH.nav.history.pop();
    // Don't push to history again — directly set state
    ARCH.nav.level = prev.level;
    ARCH.nav.selectedApp = prev.app;
    ARCH.nav.selectedService = prev.service;
    // Re-enter the navigation flow without pushing history
    var fakeCtx = {app: prev.app, service: prev.service};
    // We need to render without pushing to history, so temporarily store and restore
    var savedHistory = ARCH.nav.history.slice();
    archNavigateTo(prev.level, fakeCtx);
    // Remove the extra history entry that archNavigateTo pushed
    ARCH.nav.history = savedHistory;
}

function archBreadcrumbClick(index) {
    if (index < 0 || index >= ARCH.nav.history.length) return;
    var target = ARCH.nav.history[index];
    // Slice history to that point
    ARCH.nav.history = ARCH.nav.history.slice(0, index);
    ARCH.nav.level = target.level;
    ARCH.nav.selectedApp = target.app;
    ARCH.nav.selectedService = target.service;
    var savedHistory = ARCH.nav.history.slice();
    archNavigateTo(target.level, {app: target.app, service: target.service});
    ARCH.nav.history = savedHistory;
}

function archUpdateBreadcrumb() {
    var el = $('archBreadcrumb');
    if (!el) return;
    var items = [];
    var level = ARCH.nav.level;
    var app = ARCH.nav.selectedApp;
    var svc = ARCH.nav.selectedService || ARCH.nav.selectedService;

    if (ARCH.mode === 'single_app') {
        if (level === 'L1') {
            items.push({label: (app || 'Service') + ' Topology', active: true, index: -1});
        } else if (level === 'L2') {
            items.push({label: (app || 'Service') + ' Topology', active: false, index: 0});
            items.push({label: (svc || '') + ' Detail', active: true, index: -1});
        } else if (level === 'L3') {
            items.push({label: (app || 'Service') + ' Topology', active: false, index: 0});
            items.push({label: svc || '', active: false, index: 1});
            items.push({label: 'Component', active: true, index: -1});
        }
    } else {
        if (level === 'L1') {
            items.push({label: 'L1 Apps', active: true, index: -1});
        } else if (level === 'L2') {
            items.push({label: 'L1 Apps', active: false, index: 0});
            items.push({label: app || '', active: true, index: -1});
        } else if (level === 'L3') {
            items.push({label: 'L1 Apps', active: false, index: 0});
            items.push({label: app || '', active: false, index: 1});
            items.push({label: svc || '', active: true, index: -1});
        }
    }

    var h = '';
    items.forEach(function(item, i) {
        if (i > 0) h += '<span style="color:#475569;margin:0 6px">›</span>';
        if (item.active) {
            h += '<span style="color:#e2e8f0;font-weight:600">' + esc(item.label) + '</span>';
        } else {
            h += '<span style="color:#38bdf8;cursor:pointer;font-weight:500" onclick="archBreadcrumbClick(' + item.index + ')">' + esc(item.label) + '</span>';
        }
    });
    el.innerHTML = h;
}

// ================================================================
// DATA DERIVATION
// ================================================================

function _archIsManaged(n) {
    if (!n.group) {
        if (n.namespace === 'managed' || n.namespace === 'external' || n.kind === 'ExternalService') return true;
        var name = (n.name || '').toLowerCase();
        var kind = (n.kind || '').toLowerCase();
        if (/^(browser|client)$/i.test(name)) return true;
        if (/cloudwatch|eks cluster|eks worker|ecs cluster/i.test(name)) return true;
        if (/amazon |aws |elastic |lambda|sns|sqs|dynamodb|rds|s3|bedrock|cloudfront/i.test(kind)) return true;
    }
    return false;
}

function _archDataL1(nodes, edges) {
    var groups = {};
    nodes.forEach(function(n) {
        if (_archIsManaged(n)) return;
        var g = n.group || '기타';
        if (!groups[g]) groups[g] = [];
        groups[g].push(n);
    });

    var appNodes = [];
    for (var gName in groups) {
        var svcs = groups[gName];
        appNodes.push({
            name: gName,
            count: svcs.length,
            services: svcs.map(function(n) { return n.name })
        });
    }

    // Aggregate edges: for each edge between different groups, create one app-level edge
    var nodeGroupMap = {};
    nodes.forEach(function(n) {
        var g = n.group || '';
        if (g) nodeGroupMap[n.name] = g;
    });

    var edgeKey = {};
    edges.forEach(function(e) {
        var sg = nodeGroupMap[e.source];
        var tg = nodeGroupMap[e.target];
        if (!sg || !tg || sg === tg) return;
        var key = sg + '|||' + tg;
        if (!edgeKey[key]) edgeKey[key] = {source: sg, target: tg, count: 0, descriptions: []};
        edgeKey[key].count++;
        var desc = e.description || '';
        if (desc && edgeKey[key].descriptions.indexOf(desc) < 0) edgeKey[key].descriptions.push(desc);
    });

    var appEdges = [];
    for (var k in edgeKey) appEdges.push(edgeKey[k]);

    // Apply L1 app filter
    if (ARCH.l1AppFilter) {
        var visible = {};
        appNodes.forEach(function(an) { if (ARCH.l1AppFilter[an.name] !== false) visible[an.name] = true; });
        appNodes = appNodes.filter(function(an) { return visible[an.name]; });
        appEdges = appEdges.filter(function(ae) { return visible[ae.source] && visible[ae.target]; });
    }

    return {appNodes: appNodes, appEdges: appEdges};
}

function _archDataL2(nodes, edges, appGroup) {
    var appGroupName = appGroup || '';
    var nodeMap = {};
    nodes.forEach(function(n) { nodeMap[n.name] = n });

    // Build enabled groups (selected app + toggled connected apps)
    var enabledGroups = {};
    enabledGroups[appGroupName] = true;
    if (ARCH.l2ConnectedApps) {
        for (var cg in ARCH.l2ConnectedApps) { if (ARCH.l2ConnectedApps[cg]) enabledGroups[cg] = true; }
    }

    // Compute nodes in enabled groups (non-managed)
    var appNodeNames = {};
    var appNodes = [];
    nodes.forEach(function(n) {
        if (_archIsManaged(n)) return;
        var g = n.group || '기타';
        if (enabledGroups[g]) {
            appNodes.push(n);
            appNodeNames[n.name] = true;
        }
    });

    // Same-group managed nodes (from all enabled groups)
    var groupManagedNames = {};
    var groupManagedNodes = [];
    nodes.forEach(function(n) {
        if (!_archIsManaged(n)) return;
        if (enabledGroups[n.group || '']) {
            groupManagedNames[n.name] = true;
            groupManagedNodes.push(n);
        }
    });

    // All "in-group" names (compute + managed)
    var inGroupNames = {};
    for (var k in appNodeNames) inGroupNames[k] = true;
    for (var k in groupManagedNames) inGroupNames[k] = true;

    // Edges within app (compute-to-compute only)
    var appEdges = edges.filter(function(e) {
        return appNodeNames[e.source] && appNodeNames[e.target];
    });

    // Infra: managed + cross-group dependencies
    var infraNodeNames = {};
    var infraNodes = [];
    var infraEdges = [];

    function addInfraNode(n) {
        if (infraNodeNames[n.name]) return;
        infraNodeNames[n.name] = true;
        infraNodes.push(n);
    }

    // Add same-group managed nodes
    groupManagedNodes.forEach(addInfraNode);

    edges.forEach(function(e) {
        var src = nodeMap[e.source], tgt = nodeMap[e.target];
        if (!src || !tgt) return;
        var srcIn = inGroupNames[e.source];
        var tgtIn = inGroupNames[e.target];
        if (!srcIn && !tgtIn) return;

        // Edge from in-group to out-of-group
        if (srcIn && !tgtIn) {
            addInfraNode(tgt);
            infraEdges.push(e);
        }
        // Edge from out-of-group to in-group
        else if (!srcIn && tgtIn) {
            addInfraNode(src);
            infraEdges.push(e);
        }
        // Edge between in-group managed and compute
        else if (srcIn && tgtIn && (groupManagedNames[e.source] || groupManagedNames[e.target])) {
            infraEdges.push(e);
        }
    });

    // Dedup edges
    function _dedupEdges(arr) {
        var seen = {};
        return arr.filter(function(e) {
            var k = e.source + '→' + e.target;
            if (seen[k]) return false;
            seen[k] = true;
            return true;
        });
    }
    appEdges = _dedupEdges(appEdges);
    infraEdges = _dedupEdges(infraEdges);

    // Tier filter
    var tf = ARCH.tierFilter;
    var anyActive = tf.core || tf.data || tf.observe || tf.platform || tf.ops;
    if (anyActive) {
        var allowed = {};
        appNodes.forEach(function(n) { if (tf[n.tier || 'core']) allowed[n.name] = true; });
        infraNodes.forEach(function(n) { if (tf[n.tier || 'core']) allowed[n.name] = true; });
        appNodes = appNodes.filter(function(n) { return allowed[n.name]; });
        appEdges = appEdges.filter(function(e) { return allowed[e.source] && allowed[e.target]; });
        infraNodes = infraNodes.filter(function(n) { return allowed[n.name]; });
        infraEdges = infraEdges.filter(function(e) { return allowed[e.source] && allowed[e.target]; });
    }

    var allInfraNodes = appNodes.concat(infraNodes);

    return {
        appDiagram: {nodes: appNodes, edges: appEdges},
        infraDiagram: {nodes: allInfraNodes, edges: infraEdges, managedNodes: infraNodes, appNodes: appNodes}
    };
}

// ================================================================
// NESTED BOX: ELK.js compound graph layout + rendering
// AWS-style architecture diagram with VPC > EKS > pods nesting
// ================================================================

var _ARCH_ELK = (typeof ELK !== 'undefined') ? new ELK() : null;

function _archDataL2Unified(nodes, edges, appGroup) {
    var nodeMap = {};
    nodes.forEach(function(n) { nodeMap[n.name] = n });
    var appGroupName = appGroup || '';

    // Build enabled groups (selected app + toggled connected apps)
    var enabledGroups = {};
    enabledGroups[appGroupName] = true;
    if (ARCH.l2ConnectedApps) {
        for (var cg in ARCH.l2ConnectedApps) { if (ARCH.l2ConnectedApps[cg]) enabledGroups[cg] = true; }
    }

    var appNodeNames = {};
    nodes.forEach(function(n) {
        var g = n.group || '기타';
        if (enabledGroups[g]) appNodeNames[n.name] = true;
    });

    var resultNodes = [];
    var resultEdges = [];
    var added = {};

    function addNode(n) {
        if (added[n.name]) return;
        added[n.name] = true;
        resultNodes.push(n);
    }

    nodes.forEach(function(n) {
        if (appNodeNames[n.name]) addNode(n);
    });

    edges.forEach(function(e) {
        var src = nodeMap[e.source], tgt = nodeMap[e.target];
        if (!src || !tgt) return;
        var srcIn = appNodeNames[e.source];
        var tgtIn = appNodeNames[e.target];
        if (!srcIn && !tgtIn) return;
        addNode(src); addNode(tgt);
        resultEdges.push(e);
    });

    nodes.forEach(function(n) {
        if ((n.group || '') === appGroupName) addNode(n);
    });

    // Tier filter
    var tf = ARCH.tierFilter;
    var anyActive = tf.core || tf.data || tf.observe || tf.platform || tf.ops;
    if (anyActive) {
        var allowed = {};
        resultNodes.forEach(function(n) {
            var t = n.tier || 'core';
            if (tf[t]) allowed[n.name] = true;
        });
        resultNodes = resultNodes.filter(function(n) { return allowed[n.name]; });
        resultEdges = resultEdges.filter(function(e) { return allowed[e.source] && allowed[e.target]; });
    }

    var seen = {};
    resultEdges = resultEdges.filter(function(e) {
        var k = e.source + '→' + e.target;
        if (seen[k]) return false;
        seen[k] = true;
        return true;
    });

    return {nodes: resultNodes, edges: resultEdges};
}

function archSetTier(tier) {
    ARCH.tierFilter[tier] = !ARCH.tierFilter[tier];
    _archSyncTierButtons();
    if (ARCH.nav.level === 'L2') {
        archNavigateTo('L2', {app: ARCH.nav.selectedApp});
    }
}

function _archSyncTierButtons() {
    var tiers = ['core', 'data', 'observe', 'platform', 'ops'];
    tiers.forEach(function(t) {
        var btn = $('btnTier_' + t);
        if (!btn) return;
        var on = ARCH.tierFilter[t];
        btn.style.background = on ? '#334155' : '#1e293b';
        btn.style.color = on ? '#e2e8f0' : '#64748b';
        btn.style.borderColor = on ? '#475569' : '#334155';
    });
}

// ── L1 App Filter ──
function _archBuildL1AppFilter() {
    var groups = {};
    ARCH.nodes.forEach(function(n) {
        if (_archIsManaged(n)) return;
        var g = n.group || '기타';
        groups[g] = true;
    });
    var currentApps = Object.keys(groups);
    currentApps.forEach(function(name) {
        if (ARCH.l1AppFilter[name] === undefined) ARCH.l1AppFilter[name] = true;
    });
    for (var k in ARCH.l1AppFilter) { if (!groups[k]) delete ARCH.l1AppFilter[k]; }
    _archSyncL1AppFilter();
}

function _archSyncL1AppFilter() {
    var container = $('archL1AppChips');
    if (!container) return;
    var html = '';
    Object.keys(ARCH.l1AppFilter).sort().forEach(function(name) {
        var on = ARCH.l1AppFilter[name];
        var cls = on ? 'arch-app-chip arch-app-chip-on' : 'arch-app-chip arch-app-chip-off';
        html += '<span class="' + cls + '" style="cursor:pointer" onclick="archToggleL1App(\'' + esc(name) + '\')">' + esc(name) + '</span>';
    });
    container.innerHTML = html;
}

function archToggleL1App(appName) {
    ARCH.l1AppFilter[appName] = !ARCH.l1AppFilter[appName];
    _archSyncL1AppFilter();
    if (ARCH.nav.level === 'L1') archRenderL1();
}

// ── L2 Connected Apps Filter ──
function _archFindConnectedApps(appGroup) {
    var nodeGroupMap = {};
    ARCH.nodes.forEach(function(n) { nodeGroupMap[n.name] = n.group || '기타'; });
    var connected = {};
    ARCH.edges.forEach(function(e) {
        var sg = nodeGroupMap[e.source], tg = nodeGroupMap[e.target];
        if (!sg || !tg) return;
        if (sg === appGroup && tg !== appGroup) connected[tg] = true;
        if (tg === appGroup && sg !== appGroup) connected[sg] = true;
    });
    return Object.keys(connected).sort();
}

function _archBuildL2ConnectedFilter(appGroup) {
    if (ARCH._l2ConnectedForApp === appGroup) { _archSyncL2ConnectedFilter(); return; }
    ARCH._l2ConnectedForApp = appGroup;
    var connected = _archFindConnectedApps(appGroup);
    ARCH.l2ConnectedApps = {};
    connected.forEach(function(name) { ARCH.l2ConnectedApps[name] = false; });
    _archSyncL2ConnectedFilter();
}

function _archSyncL2ConnectedFilter() {
    var container = $('archL2ConnectedChips');
    var wrapper = $('archL2ConnectedFilter');
    if (!container || !wrapper) return;
    wrapper.style.display = 'flex';

    var html = '';
    var apps = Object.keys(ARCH.l2ConnectedApps);
    apps.forEach(function(name) {
        var on = ARCH.l2ConnectedApps[name];
        var cls = on ? 'arch-app-chip arch-app-chip-on' : 'arch-app-chip arch-app-chip-off';
        html += '<span class="' + cls + '" style="cursor:pointer" onclick="archToggleL2Connected(\'' + esc(name) + '\')">' + esc(name) + '</span>';
    });
    if (!apps.length) html = '<span style="font-size:.56rem;color:#475569">없음</span>';
    container.innerHTML = html;

    var tierContainer = $('archL2TierChips');
    if (tierContainer) {
        var tierHtml = '';
        var tiers = ['core', 'data', 'observe', 'platform', 'ops'];
        tiers.forEach(function(t) {
            var on = ARCH.tierFilter[t];
            var cls = on ? 'arch-app-chip arch-app-chip-on' : 'arch-app-chip arch-app-chip-off';
            tierHtml += '<span class="' + cls + '" style="cursor:pointer;text-transform:capitalize" onclick="archSetTier(\'' + t + '\')">' + t + '</span>';
        });
        tierContainer.innerHTML = tierHtml;
    }
}

function archToggleL2Connected(appName) {
    ARCH.l2ConnectedApps[appName] = !ARCH.l2ConnectedApps[appName];
    _archSyncL2ConnectedFilter();
    if (ARCH.nav.level === 'L2') {
        if (ARCH.viewMode === 'flow') archRenderL2Flow(ARCH.nav.selectedApp);
        else archRenderL2(ARCH.nav.selectedApp);
    }
}

var _ARCH_CONTAINER_DEFS = {
    'eks':     {label: 'EKS Cluster',         icon: 'aws-eks',     parent: 'vpc',     fill: 'rgba(50,108,229,0.05)',  stroke: 'rgba(50,108,229,0.3)',  dash: '',    textColor: '#326CE5'},
    'vpc':     {label: 'VPC',                  icon: 'aws-vpc',     parent: 'account', fill: 'rgba(56,189,248,0.05)',  stroke: 'rgba(56,189,248,0.3)',  dash: '',    textColor: '#38bdf8'},
    'managed': {label: 'AWS Managed Services', icon: 'aws-generic', parent: 'account', fill: 'rgba(255,153,0,0.04)',   stroke: 'rgba(255,153,0,0.2)',   dash: '8 4', textColor: '#FF9900'},
    'account': {label: 'AWS Account',          icon: 'aws-generic', parent: null,       fill: 'rgba(255,153,0,0.03)',   stroke: 'rgba(255,153,0,0.15)',  dash: '',    textColor: '#FF9900'},
    'external':{label: 'External',             icon: null,          parent: null,       fill: 'rgba(148,163,184,0.05)', stroke: 'rgba(148,163,184,0.3)', dash: '6 3', textColor: '#94a3b8'}
};

function _archClassifyContainers(nodes) {
    var containers = {};
    for (var cid in _ARCH_CONTAINER_DEFS) {
        containers[cid] = {id: cid, def: _ARCH_CONTAINER_DEFS[cid], children: [], childContainers: []};
    }
    var nodeContainer = {};

    nodes.forEach(function(n) {
        var kind = n.kind || '';
        var ns = n.namespace || '';
        var cid;
        if (ns === 'external' || kind === 'ExternalService' || /^(External|Internet|Slack)/i.test(kind)) {
            cid = 'external';
        } else if (kind === 'Amazon EKS Cluster') {
            cid = 'vpc';
        } else if (/^Amazon EKS /.test(kind)) {
            cid = 'eks';
        } else if (/^Amazon EC2 VPC$/.test(kind)) {
            cid = 'vpc';
        } else if (/^Amazon EC2 |^Amazon RDS /.test(kind)) {
            cid = 'vpc';
        } else if (ns === 'managed' || /^(Amazon |AWS |Elastic)/.test(kind)) {
            cid = 'managed';
        } else if (ns === 'platform') {
            cid = 'vpc';
        } else {
            cid = 'eks';
        }
        nodeContainer[n.name] = cid;
        containers[cid].children.push(n.name);
    });

    for (var cid in containers) {
        var p = containers[cid].def.parent;
        if (p && containers[p]) containers[p].childContainers.push(cid);
    }

    var used = {};
    for (var cid in containers) {
        if (containers[cid].children.length > 0) {
            used[cid] = true;
            var p = _ARCH_CONTAINER_DEFS[cid].parent;
            while (p) { used[p] = true; p = _ARCH_CONTAINER_DEFS[p] ? _ARCH_CONTAINER_DEFS[p].parent : null; }
        }
    }
    var result = {};
    for (var cid in containers) { if (used[cid]) result[cid] = containers[cid]; }
    return {containers: result, nodeContainer: nodeContainer};
}

function _archBuildElkGraph(classResult, nodes, edges) {
    var nodeContainer = classResult.nodeContainer;
    var containers = classResult.containers;
    var NODE_W = 120, NODE_H = 80;

    function makeContainer(cid) {
        var def = _ARCH_CONTAINER_DEFS[cid];
        var c = containers[cid];
        if (!c) return null;
        var elkNode = {
            id: 'c_' + cid,
            labels: [{text: def.label}],
            layoutOptions: {
                'elk.padding': '[top=40,left=20,bottom=20,right=20]',
                'elk.algorithm': 'layered',
                'elk.direction': 'RIGHT',
                'elk.spacing.nodeNode': '30',
                'elk.layered.spacing.nodeNodeBetweenLayers': '60',
                'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP'
            },
            children: [],
            edges: []
        };
        c.childContainers.forEach(function(ccid) {
            if (containers[ccid]) {
                var child = makeContainer(ccid);
                if (child) elkNode.children.push(child);
            }
        });
        c.children.forEach(function(name) {
            elkNode.children.push({id: name, labels: [{text: name}], width: NODE_W, height: NODE_H});
        });
        return elkNode;
    }

    var rootChildren = [];
    for (var cid in containers) {
        var parentCid = _ARCH_CONTAINER_DEFS[cid].parent;
        if (!parentCid || !containers[parentCid]) {
            var cn = makeContainer(cid);
            if (cn) rootChildren.push(cn);
        }
    }

    var allContainerNodeIds = {};
    function collectIds(elkNode) {
        allContainerNodeIds[elkNode.id] = true;
        (elkNode.children || []).forEach(collectIds);
    }
    rootChildren.forEach(collectIds);

    var rootEdges = [];
    edges.forEach(function(e, i) {
        if (!allContainerNodeIds[e.source] || !allContainerNodeIds[e.target]) return;
        if (e.source === e.target) return;
        rootEdges.push({id: 'e_' + i, sources: [e.source], targets: [e.target]});
    });

    return {
        id: 'root',
        layoutOptions: {
            'elk.algorithm': 'layered',
            'elk.direction': 'RIGHT',
            'elk.spacing.nodeNode': '40',
            'elk.layered.spacing.nodeNodeBetweenLayers': '80',
            'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
            'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
            'elk.spacing.componentComponent': '60',
            'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES'
        },
        children: rootChildren,
        edges: rootEdges
    };
}

function _archElkLayout(classResult, nodes, edges) {
    var elkGraph = _archBuildElkGraph(classResult, nodes, edges);

    return _ARCH_ELK.layout(elkGraph).then(function(laid) {
        var pos = {};
        var containerBounds = {};

        function extractPositions(elkNode, offsetX, offsetY) {
            var nx = (elkNode.x || 0) + offsetX;
            var ny = (elkNode.y || 0) + offsetY;
            var nw = elkNode.width || 0;
            var nh = elkNode.height || 0;

            if (elkNode.id.indexOf('c_') === 0) {
                var cid = elkNode.id.slice(2);
                containerBounds[cid] = {absX: nx, absY: ny, w: nw, h: nh};
            } else {
                pos[elkNode.id] = {x: nx + nw / 2, y: ny + nh / 2};
            }

            (elkNode.children || []).forEach(function(child) {
                extractPositions(child, nx, ny);
            });
        }

        (laid.children || []).forEach(function(child) {
            extractPositions(child, 0, 0);
        });

        return {pos: pos, containerBounds: containerBounds};
    });
}

function _archRenderNested(svgId, nodes, edges, opts) {
    opts = opts || {};
    var svg = $(svgId);
    if (!svg) return Promise.resolve();
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!nodes.length) return Promise.resolve();

    var classResult = _archClassifyContainers(nodes);

    if (!_ARCH_ELK) {
        console.error('ELK.js not loaded');
        return Promise.resolve();
    }

    return _archElkLayout(classResult, nodes, edges).then(function(layoutResult) {
        var pos = layoutResult.pos;
        var containerBounds = layoutResult.containerBounds;

        if (ARCH.customPos) {
            for (var k in ARCH.customPos) {
                if (pos[k]) pos[k] = {x: ARCH.customPos[k].x, y: ARCH.customPos[k].y};
            }
        }

        var maxX = 0, maxY = 0;
        for (var k in pos) {
            if (pos[k].x + 60 > maxX) maxX = pos[k].x + 60;
            if (pos[k].y + 50 > maxY) maxY = pos[k].y + 50;
        }
        for (var cid in containerBounds) {
            var b = containerBounds[cid];
            var bx = b.absX + b.w, by = b.absY + b.h;
            if (bx > maxX) maxX = bx;
            if (by > maxY) maxY = by;
        }

        var W = Math.max(900, maxX + 40);
        var H = Math.max(400, maxY + 40);
        svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
        if (svg.parentElement) svg.parentElement.style.height = H + 'px';

        var defs = _svgE('defs');
        var markerId = 'archArrowNested_' + svgId;
        defs.innerHTML = '<marker id="' + markerId + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" orient="auto"><path d="M0 1L8 5L0 9z" fill="#64748b"/></marker>';
        svg.appendChild(defs);

        var renderOrder = ['account', 'external', 'vpc', 'managed', 'eks'];
        renderOrder.forEach(function(cid) {
            var b = containerBounds[cid];
            if (!b) return;
            var def = _ARCH_CONTAINER_DEFS[cid];
            if (!def) return;

            var g = _svgE('g');
            g.appendChild(_svgE('rect', {
                x: b.absX, y: b.absY, width: b.w, height: b.h, rx: 12,
                fill: def.fill, stroke: def.stroke, 'stroke-width': '1.5',
                'stroke-dasharray': def.dash || ''
            }));

            if (def.icon && ARCH_ICON_PATHS[def.icon]) {
                var cImg = _archIconImg(def.icon, b.absX + 8, b.absY + 6, 22, 22);
                g.appendChild(cImg);
            }

            var lbl = _svgE('text', {
                x: b.absX + (def.icon ? 34 : 10), y: b.absY + 22,
                fill: def.textColor || def.stroke, 'font-size': '12', 'font-weight': '700',
                'font-family': '-apple-system,sans-serif', opacity: '0.9'
            });
            lbl.textContent = def.label;
            g.appendChild(lbl);
            svg.appendChild(g);
        });

        var NW2 = 60, NH2 = 40;
        function edgePath(src, tgt, key) {
            var f = pos[src], t = pos[tgt]; if (!f || !t) return '';
            if (src === tgt) return 'M' + (f.x + 50) + ' ' + f.y + 'C' + (f.x + 90) + ' ' + (f.y - 60) + ' ' + (f.x - 90) + ' ' + (f.y - 60) + ' ' + (f.x - 50) + ' ' + f.y;
            var dx = t.x - f.x, dy = t.y - f.y;
            var dist = Math.sqrt(dx * dx + dy * dy) || 1;
            var ux = dx / dist, uy = dy / dist;
            var sx = f.x + ux * NW2, sy = f.y + uy * NH2;
            var ex = t.x - ux * NW2, ey = t.y - uy * NH2;
            var ddx = ex - sx;
            if (Math.abs(ddx) < 10) { var off = (key % 2 ? 1 : -1) * 60; return 'M' + sx + ' ' + sy + 'C' + (sx + off) + ' ' + sy + ' ' + (ex + off) + ' ' + ey + ' ' + ex + ' ' + ey; }
            var cx = ddx * 0.4;
            return 'M' + sx + ' ' + sy + 'C' + (sx + cx) + ' ' + sy + ' ' + (ex - cx) + ' ' + ey + ' ' + ex + ' ' + ey;
        }

        var localEdgePaths = {};

        edges.forEach(function(e, ei) {
            var f = pos[e.source], t = pos[e.target]; if (!f || !t) return;
            var d = edgePath(e.source, e.target, ei);
            var mx = (f.x + t.x) / 2, my = (f.y + t.y) / 2;
            var g = _svgE('g'); g.setAttribute('data-edge', ei);
            var path = _svgE('path', {d: d, fill: 'none', stroke: '#334155', 'stroke-width': '1.5', 'marker-end': 'url(#' + markerId + ')'});
            g.appendChild(path);
            localEdgePaths[ei] = {el: path, src: e.source, tgt: e.target};

            var label1 = (e.protocol || '') + (e.port ? ':' + e.port : '') + (e.paths && e.paths.length ? ' ' + e.paths[0] : '');
            var label2 = e.description || '';
            if (label1) {
                var bg = _svgE('rect', {x: mx - 60, y: my - 18, width: 120, height: label2 ? 28 : 16, rx: 4, fill: '#0f172a', 'fill-opacity': '0.85'});
                bg.setAttribute('data-edge-label', ei); g.appendChild(bg);
                var t1 = _svgE('text', {x: mx, y: my - 5, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '9', 'font-family': '-apple-system,sans-serif'});
                t1.textContent = label1; t1.setAttribute('data-edge-label', ei); g.appendChild(t1);
            }
            if (label2) {
                var t2 = _svgE('text', {x: mx, y: my + 8, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
                t2.textContent = trun(label2, 30); t2.setAttribute('data-edge-label', ei); g.appendChild(t2);
            }
            svg.appendChild(g);
        });

        function updateEdges(nodeName) {
            edges.forEach(function(e, ei) {
                var ep = localEdgePaths[ei]; if (!ep) return;
                if (ep.src !== nodeName && ep.tgt !== nodeName) return;
                var nd = edgePath(ep.src, ep.tgt, ei);
                ep.el.setAttribute('d', nd);
                var f2 = pos[ep.src], t2 = pos[ep.tgt]; if (!f2 || !t2) return;
                var mx2 = (f2.x + t2.x) / 2, my2 = (f2.y + t2.y) / 2;
                var parent = ep.el.parentNode; if (!parent) return;
                parent.querySelectorAll('[data-edge-label="' + ei + '"]').forEach(function(lb) {
                    if (lb.tagName === 'rect') { lb.setAttribute('x', mx2 - 60); lb.setAttribute('y', my2 - 18); }
                    else if (lb.tagName === 'text') {
                        lb.setAttribute('x', mx2);
                        lb.setAttribute('y', lb.getAttribute('font-style') ? my2 + 8 : my2 - 5);
                    }
                });
            });
        }

        nodes.forEach(function(n) {
            var p = pos[n.name]; if (!p) return;
            var isManagedOrExt = (n.namespace === 'external' || n.namespace === 'managed' || n.kind === 'ExternalService');
            var iconId = n.icon_key || 'k8s-deploy';

            var cp = ARCH.customProps[n.name] || {};
            var nw = cp.size === 'large' ? 120 : cp.size === 'small' ? 80 : 100;
            var nh = cp.size === 'large' ? 100 : cp.size === 'small' ? 60 : 80;
            var label = cp.label || n.name;

            var g = _svgE('g', {transform: 'translate(' + (p.x - nw / 2) + ',' + (p.y - nh / 2) + ')', style: 'cursor:grab', 'data-node': n.name});
            g.appendChild(_svgE('rect', {x: 0, y: 0, width: nw, height: nh, rx: 8, fill: '#1e293b', stroke: isManagedOrExt ? '#FF9900' : '#334155', 'stroke-width': isManagedOrExt ? '1.5' : '1'}));
            g.appendChild(_archIconImg(iconId, nw / 2 - 20, 4, 40, 40));

            var txt = _svgE('text', {x: nw / 2, y: nh - 24, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '10', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
            txt.textContent = trun(label, 16); g.appendChild(txt);

            var meta = (isManagedOrExt ? (n.namespace === 'managed' ? 'Managed' : 'External') : n.kind || 'Deployment') + ((n.ports && n.ports.length) ? ' :' + n.ports[0] : '');
            var mt = _svgE('text', {x: nw / 2, y: nh - 12, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
            mt.textContent = meta; g.appendChild(mt);

            _archBindTip(g, n);

            (function(nodeName, grp, halfW, halfH, nodeObj) {
                var dragging = false, ox, oy, startX, startY, moved;
                grp.addEventListener('mousedown', function(ev) {
                    if (ev.detail > 1) return;
                    dragging = true; moved = false;
                    ox = ev.clientX; oy = ev.clientY; startX = ev.clientX; startY = ev.clientY;
                    grp.style.cursor = 'grabbing'; ev.preventDefault();
                });
                svg.addEventListener('mousemove', function(ev) {
                    if (!dragging) return;
                    var svgR = svg.getBoundingClientRect();
                    var scaleX = parseFloat(svg.getAttribute('viewBox').split(' ')[2]) / svgR.width;
                    var scaleY = parseFloat(svg.getAttribute('viewBox').split(' ')[3]) / svgR.height;
                    pos[nodeName].x += (ev.clientX - ox) * scaleX;
                    pos[nodeName].y += (ev.clientY - oy) * scaleY;
                    grp.setAttribute('transform', 'translate(' + (pos[nodeName].x - halfW) + ',' + (pos[nodeName].y - halfH) + ')');
                    updateEdges(nodeName);
                    ox = ev.clientX; oy = ev.clientY;
                    if (Math.abs(ev.clientX - startX) > 5 || Math.abs(ev.clientY - startY) > 5) moved = true;
                });
                svg.addEventListener('mouseup', function() {
                    if (!dragging) return;
                    dragging = false; grp.style.cursor = 'grab';
                    if (moved) {
                        ARCH.customPos[nodeName] = {x: pos[nodeName].x, y: pos[nodeName].y};
                    } else {
                        if (opts.onClick) opts.onClick(nodeName, nodeObj);
                    }
                });
            })(n.name, g, nw / 2, nh / 2, n);

            svg.appendChild(g);
        });

        if (opts.showSpof && ARCH.analysis && ARCH.analysis.spof) {
            ARCH.analysis.spof.forEach(function(s) {
                var p2 = pos[s.service]; if (!p2) return;
                var badge = _svgE('g', {transform: 'translate(' + (p2.x + 40) + ',' + (p2.y - 45) + ')'});
                badge.appendChild(_svgE('circle', {cx: 0, cy: 0, r: 10, fill: '#ef4444'}));
                var bt = _svgE('text', {x: 0, y: 5, 'text-anchor': 'middle', fill: '#fff', 'font-size': '13', 'font-weight': '700'});
                bt.textContent = '!'; badge.appendChild(bt);
                svg.appendChild(badge);
            });
        }
    });
}

function _archDataL3(nodes, edges, serviceName, analysis) {
    var centerNode = null;
    nodes.forEach(function(n) { if (n.name === serviceName) centerNode = n; });

    var connectedEdges = [];
    var connectedNames = {};
    edges.forEach(function(e) {
        if (e.source === serviceName || e.target === serviceName) {
            connectedEdges.push(e);
            if (e.source === serviceName) connectedNames[e.target] = true;
            if (e.target === serviceName) connectedNames[e.source] = true;
        }
    });

    var connectedNodes = [];
    nodes.forEach(function(n) {
        if (connectedNames[n.name]) connectedNodes.push(n);
    });

    var spof = [];
    var blastRadius = [];
    if (analysis) {
        (analysis.spof || []).forEach(function(s) {
            if (s.service === serviceName) spof.push(s);
        });
        (analysis.blast_radius || []).forEach(function(b) {
            if (b.failed_service === serviceName) blastRadius.push(b);
        });
    }

    return {
        centerNode: centerNode,
        connectedNodes: connectedNodes,
        connectedEdges: connectedEdges,
        spof: spof,
        blastRadius: blastRadius
    };
}

// ================================================================
// RENDER: L1 — App-level Overview
// ================================================================

function archRenderL1() {
    var svg = $('archSvgL1');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!ARCH.nodes.length) return;

    var data = _archDataL1(ARCH.nodes, ARCH.edges);
    var appNodes = data.appNodes;
    var appEdges = data.appEdges;
    if (!appNodes.length) return;

    var BOX_W = 180, BOX_H = 120, PAD = 60, XGAP = 250, YGAP = 160;

    // BFS longest-path layer assignment for flow-based layout
    var nameMap = {};
    appNodes.forEach(function(an) { nameMap[an.name] = an });
    var adj = {}, inD = {};
    appNodes.forEach(function(an) { adj[an.name] = []; inD[an.name] = 0 });
    appEdges.forEach(function(ae) {
        if (!nameMap[ae.source] || !nameMap[ae.target]) return;
        adj[ae.source].push(ae.target);
        inD[ae.target] = (inD[ae.target] || 0) + 1;
    });
    var roots = [];
    appNodes.forEach(function(an) { if (inD[an.name] === 0) roots.push(an.name) });
    if (!roots.length) roots.push(appNodes[0].name);

    var layerOf = {};
    roots.forEach(function(r) { layerOf[r] = 0 });
    var q = roots.slice(), maxL = 0;
    var safety = appNodes.length * appNodes.length + 1, iter = 0;
    while (q.length && iter < safety) {
        var cur = q.shift(); iter++;
        adj[cur].forEach(function(nb) {
            var nl = layerOf[cur] + 1;
            if (layerOf[nb] === undefined || nl > layerOf[nb]) {
                layerOf[nb] = nl; if (nl > maxL) maxL = nl;
                q.push(nb);
            }
        });
    }
    appNodes.forEach(function(an) { if (layerOf[an.name] === undefined) layerOf[an.name] = 0 });

    // Normalize: shift so minimum layer = 0, then compact empty layers
    var minLayer = maxL;
    appNodes.forEach(function(an) { if (layerOf[an.name] < minLayer) minLayer = layerOf[an.name] });
    if (minLayer > 0) {
        appNodes.forEach(function(an) { layerOf[an.name] -= minLayer });
        maxL -= minLayer;
    }
    var usedLayers = {};
    appNodes.forEach(function(an) { usedLayers[layerOf[an.name]] = true });
    var compactMap = {}, ci = 0;
    for (var li = 0; li <= maxL; li++) { if (usedLayers[li]) { compactMap[li] = ci; ci++; } }
    appNodes.forEach(function(an) { layerOf[an.name] = compactMap[layerOf[an.name]] !== undefined ? compactMap[layerOf[an.name]] : 0 });
    maxL = ci - 1;
    if (maxL < 0) maxL = 0;

    var columns = [];
    for (var i = 0; i <= maxL; i++) columns.push([]);
    appNodes.forEach(function(an) { columns[layerOf[an.name]].push(an) });

    var maxPerCol = 1;
    columns.forEach(function(c) { if (c.length > maxPerCol) maxPerCol = c.length });
    var W = Math.max(700, (maxL + 1) * XGAP + PAD * 2);
    var H = Math.max(350, maxPerCol * YGAP + PAD * 2);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    // Defs
    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="archArrowL1" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" orient="auto"><path d="M0 1L8 5L0 9z" fill="#64748b"/></marker>';
    svg.appendChild(defs);

    // Position app boxes — left-to-right by layer, centered vertically
    var appPos = {};
    columns.forEach(function(col, li) {
        var x = PAD + li * XGAP + BOX_W / 2;
        var totalH = col.length * YGAP;
        var startY = Math.max(PAD, (H - totalH) / 2) + YGAP / 2;
        col.forEach(function(an, ni) {
            appPos[an.name] = { x: x, y: startY + ni * YGAP };
        });
    });

    // Edges — start/end at box boundary, not center
    var BW2 = BOX_W / 2, BH2 = BOX_H / 2;
    var l1EdgePaths = {};
    appEdges.forEach(function(ae, ei) {
        var f = appPos[ae.source], t = appPos[ae.target];
        if (!f || !t) return;
        var dx = t.x - f.x, dy = t.y - f.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var ux = dx / dist, uy = dy / dist;
        var sx = f.x + ux * BW2, sy = f.y + uy * BH2;
        var ex = t.x - ux * BW2, ey = t.y - uy * BH2;
        var cx = (ex - sx) * 0.4;
        var d = 'M' + sx + ' ' + sy + 'C' + (sx + cx) + ' ' + sy + ' ' + (ex - cx) + ' ' + ey + ' ' + ex + ' ' + ey;
        var path = _svgE('path', {d: d, fill: 'none', stroke: '#475569', 'stroke-width': '2', 'marker-end': 'url(#archArrowL1)'});
        svg.appendChild(path);
        l1EdgePaths[ei] = {el: path, src: ae.source, tgt: ae.target, bg: null, lbl: null};
        var edgeLabel = (ae.descriptions && ae.descriptions.length)
            ? trun(ae.descriptions[0], 28) + (ae.descriptions.length > 1 ? ' +' + (ae.descriptions.length - 1) : '')
            : (ae.count > 1 ? ae.count + '' : '');
        if (edgeLabel) {
            var mx = (sx + ex) / 2, my = (sy + ey) / 2;
            var labelW = Math.min(edgeLabel.length * 5.5 + 12, 200);
            var bg = _svgE('rect', {x: mx - labelW / 2, y: my - 10, width: labelW, height: 16, rx: 4, fill: '#0f172a', 'fill-opacity': '0.85'});
            svg.appendChild(bg);
            var lbl = _svgE('text', {x: mx, y: my + 2, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
            lbl.textContent = edgeLabel;
            svg.appendChild(lbl);
            l1EdgePaths[ei].bg = bg;
            l1EdgePaths[ei].lbl = lbl;
        }
    });

    function updateL1Edges(nodeName) {
        appEdges.forEach(function(ae, ei) {
            var ep = l1EdgePaths[ei]; if (!ep) return;
            if (ep.src !== nodeName && ep.tgt !== nodeName) return;
            var f2 = appPos[ep.src], t2 = appPos[ep.tgt]; if (!f2 || !t2) return;
            var dx2 = t2.x - f2.x, dy2 = t2.y - f2.y;
            var dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
            var ux2 = dx2 / dist2, uy2 = dy2 / dist2;
            var sx2 = f2.x + ux2 * BW2, sy2 = f2.y + uy2 * BH2;
            var ex2 = t2.x - ux2 * BW2, ey2 = t2.y - uy2 * BH2;
            var cx2 = (ex2 - sx2) * 0.4;
            ep.el.setAttribute('d', 'M' + sx2 + ' ' + sy2 + 'C' + (sx2 + cx2) + ' ' + sy2 + ' ' + (ex2 - cx2) + ' ' + ey2 + ' ' + ex2 + ' ' + ey2);
            var mx2 = (sx2 + ex2) / 2, my2 = (sy2 + ey2) / 2;
            if (ep.bg) { var bw = parseFloat(ep.bg.getAttribute('width')) || 24; ep.bg.setAttribute('x', mx2 - bw / 2); ep.bg.setAttribute('y', my2 - 10); }
            if (ep.lbl) { ep.lbl.setAttribute('x', mx2); ep.lbl.setAttribute('y', my2 + 2); }
        });
    }

    // App boxes
    appNodes.forEach(function(an, ai) {
        var p = appPos[an.name];
        var gc = _ARCH_GROUP_COLORS[ai % _ARCH_GROUP_COLORS.length];
        var g = _svgE('g', {transform: 'translate(' + (p.x - BOX_W / 2) + ',' + (p.y - BOX_H / 2) + ')', style: 'cursor:pointer', 'data-app': an.name});

        // Box
        g.appendChild(_svgE('rect', {x: 0, y: 0, width: BOX_W, height: BOX_H, rx: 12, fill: '#1e293b', stroke: gc, 'stroke-width': '2'}));

        // App name
        var title = _svgE('text', {x: BOX_W / 2, y: 28, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '14', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
        title.textContent = trun(an.name, 18);
        g.appendChild(title);

        // Service count
        var cnt = _svgE('text', {x: BOX_W / 2, y: 48, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '10', 'font-family': '-apple-system,sans-serif'});
        cnt.textContent = an.count + ' Service';
        g.appendChild(cnt);

        // Service names (max 3 + ...)
        var svcList = an.services.slice(0, 3);
        svcList.forEach(function(sn, si) {
            var st = _svgE('text', {x: BOX_W / 2, y: 65 + si * 13, 'text-anchor': 'middle', fill: '#475569', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
            st.textContent = trun(sn, 24);
            g.appendChild(st);
        });
        if (an.services.length > 3) {
            var more = _svgE('text', {x: BOX_W / 2, y: 65 + 3 * 13, 'text-anchor': 'middle', fill: '#475569', 'font-size': '8', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            more.textContent = '+' + (an.services.length - 3) + ' more...';
            g.appendChild(more);
        }

        _archBindTip(g, {name: an.name, service_type: 'app_group', description: an.count + ' services: ' + an.services.slice(0, 5).join(', ')});

        // Drag + click handler
        (function(appName, grp) {
            var dragging = false, ox, oy, startX, startY, moved;
            grp.style.cursor = 'grab';
            grp.addEventListener('mousedown', function(ev) {
                if (ev.detail > 1) return;
                dragging = true; moved = false;
                ox = ev.clientX; oy = ev.clientY; startX = ev.clientX; startY = ev.clientY;
                grp.style.cursor = 'grabbing'; ev.preventDefault();
            });
            svg.addEventListener('mousemove', function(ev) {
                if (!dragging) return;
                var svgR = svg.getBoundingClientRect();
                var scaleX = parseFloat(svg.getAttribute('viewBox').split(' ')[2]) / svgR.width;
                var scaleY = parseFloat(svg.getAttribute('viewBox').split(' ')[3]) / svgR.height;
                appPos[appName].x += (ev.clientX - ox) * scaleX;
                appPos[appName].y += (ev.clientY - oy) * scaleY;
                grp.setAttribute('transform', 'translate(' + (appPos[appName].x - BOX_W / 2) + ',' + (appPos[appName].y - BOX_H / 2) + ')');
                updateL1Edges(appName);
                ox = ev.clientX; oy = ev.clientY;
                if (Math.abs(ev.clientX - startX) > 5 || Math.abs(ev.clientY - startY) > 5) moved = true;
            });
            svg.addEventListener('mouseup', function() {
                if (!dragging) return;
                dragging = false; grp.style.cursor = 'grab';
                if (!moved) archNavigateTo('L2', {app: appName});
            });
        })(an.name, g);

        svg.appendChild(g);
    });
}

// ================================================================
// TOGGLE: Group ↔ Workflow view
// ================================================================
function _archSyncToggleButtons() {
    var btnG = $('btnArchViewGroup'), btnF = $('btnArchViewFlow');
    if (btnG && btnF) {
        btnG.style.background = ARCH.viewMode === 'group' ? '#334155' : '#1e293b';
        btnG.style.color = ARCH.viewMode === 'group' ? '#e2e8f0' : '#94a3b8';
        btnF.style.background = ARCH.viewMode === 'flow' ? '#334155' : '#1e293b';
        btnF.style.color = ARCH.viewMode === 'flow' ? '#e2e8f0' : '#94a3b8';
    }
}

function archToggleView(mode) {
    ARCH.viewMode = mode;
    _archSyncToggleButtons();
    var level = ARCH.nav.level;
    if (level === 'L1') {
        if (mode === 'flow') archRenderL1Flow(); else archRenderL1();
    } else if (level === 'L2') {
        if (mode === 'flow') archRenderL2Flow(ARCH.nav.selectedApp); else archRenderL2(ARCH.nav.selectedApp);
    }
    archRenderDesc();
}

// ================================================================
// RENDER: L1 Workflow Flow view
// ================================================================
function archRenderL1Flow() {
    var svg = $('archSvgL1');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var a = ARCH.analysis;
    if (!a || !a.workflows || !a.workflows.length) { archRenderL1(); return; }

    var workflows = a.workflows.filter(function(w) { return typeof w === 'object' && w.hops && w.hops.length });
    if (!workflows.length) { archRenderL1(); return; }

    var PAD = 40, ROW_H = 80, NODE_R = 22, STEP_W = 150, LABEL_H = 24;
    var maxSteps = 0;
    workflows.forEach(function(w) { if (w.hops.length + 1 > maxSteps) maxSteps = w.hops.length + 1 });
    var W = Math.max(700, PAD * 2 + maxSteps * STEP_W);
    var H = PAD * 2 + workflows.length * (ROW_H + LABEL_H);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="archArrowWF" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#06b6d4"/></marker>';
    svg.appendChild(defs);

    var WF_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4'];

    workflows.forEach(function(wf, wi) {
        var yBase = PAD + wi * (ROW_H + LABEL_H);
        var color = WF_COLORS[wi % WF_COLORS.length];

        // Workflow name label
        var lbl = _svgE('text', {x: PAD, y: yBase + 12, fill: color, 'font-size': '11', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
        lbl.textContent = wf.name || '(unnamed)';
        svg.appendChild(lbl);

        // Build node chain from hops
        var chain = [];
        wf.hops.forEach(function(hop, i) {
            if (i === 0) chain.push(hop.from || hop.source || '');
            chain.push(hop.to || hop.target || '');
        });

        var yCenter = yBase + LABEL_H + ROW_H / 2;

        // Draw nodes
        chain.forEach(function(name, si) {
            var x = PAD + 40 + si * STEP_W;

            // Find node for icon
            var node = null;
            ARCH.nodes.forEach(function(n) { if (n.name === name) node = n });
            var iconKey = node ? (node.icon_key || 'k8s-deploy') : 'k8s-deploy';
            var iconPath = ARCH_ICON_PATHS[iconKey];

            // Circle background
            svg.appendChild(_svgE('circle', {cx: x, cy: yCenter, r: NODE_R, fill: '#1e293b', stroke: color, 'stroke-width': '2'}));

            // Icon
            if (iconPath) {
                svg.appendChild(_svgE('image', {href: iconPath, x: x - 12, y: yCenter - 12, width: 24, height: 24}));
            }

            // Label
            var t = _svgE('text', {x: x, y: yCenter + NODE_R + 14, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '9', 'font-weight': '500', 'font-family': '-apple-system,sans-serif'});
            t.textContent = trun(name, 16);
            svg.appendChild(t);

            // Arrow to next
            if (si < chain.length - 1) {
                var x2 = PAD + 40 + (si + 1) * STEP_W;
                svg.appendChild(_svgE('line', {
                    x1: x + NODE_R + 4, y1: yCenter,
                    x2: x2 - NODE_R - 4, y2: yCenter,
                    stroke: color, 'stroke-width': '2', 'stroke-opacity': '0.6',
                    'marker-end': 'url(#archArrowWF)'
                }));
            }
        });
    });
}

// ================================================================
// RENDER: Internal helper — _archRenderNodesTopo
// Reused by L2 app diagram and L2 infra diagram
// ================================================================

function _archRenderNodesTopo(svgId, nodes, edges, opts) {
    opts = opts || {};
    var svg = $(svgId);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!nodes.length) return;

    var W = Math.max(700, svg.parentElement.clientWidth - 2);
    var H = Math.max(350, nodes.length * 80);

    var defs = _svgE('defs');
    var markerId = 'archArrow_' + svgId;
    defs.innerHTML = '<marker id="' + markerId + '" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#475569"/></marker>';
    svg.appendChild(defs);

    var pos;
    if (opts.layoutFn) {
        pos = opts.layoutFn(nodes, edges, W, H);
    } else {
        pos = _archLayout(nodes, edges, W, H);
    }
    H = _archMaxY(pos) + 80;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    // Apply saved custom positions
    if (ARCH.customPos) {
        for (var k in ARCH.customPos) {
            if (pos[k]) pos[k] = {x: ARCH.customPos[k].x, y: ARCH.customPos[k].y};
        }
    }

    // Group bounding boxes (only if not suppressed)
    if (!opts.noGroups) {
        var groups = {};
        nodes.forEach(function(n) {
            var g = n.group || ''; if (!g) return;
            if (!groups[g]) groups[g] = [];
            var p = pos[n.name]; if (p) groups[g].push(p);
        });
        var GC = ['rgba(56,189,248,', 'rgba(34,197,94,', 'rgba(245,158,11,', 'rgba(168,85,247,', 'rgba(236,72,153,'];
        var gi = 0;
        for (var gName in groups) {
            var pts = groups[gName]; if (!pts.length) continue;
            var pad = 30, labelH = 20;
            var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
            pts.forEach(function(p) { x1 = Math.min(x1, p.x - 60); y1 = Math.min(y1, p.y - 50); x2 = Math.max(x2, p.x + 60); y2 = Math.max(y2, p.y + 50); });
            var c = GC[gi % GC.length]; gi++;
            svg.appendChild(_svgE('rect', {x: x1 - pad, y: y1 - pad - labelH, width: x2 - x1 + pad * 2, height: y2 - y1 + pad * 2 + labelH, rx: 12, fill: c + '0.05)', stroke: c + '0.3)', 'stroke-width': '1.5', 'stroke-dasharray': '8 4'}));
            var lbl = _svgE('text', {x: x1 - pad + 10, y: y1 - pad - 6, fill: c + '0.8)', 'font-size': '12', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
            lbl.textContent = gName; svg.appendChild(lbl);
        }
    }

    // bezier edge path helper
    function edgePath(src, tgt, key) {
        var f = pos[src], t = pos[tgt]; if (!f || !t) return '';
        if (src === tgt) { return 'M' + (f.x + 50) + ' ' + f.y + 'C' + (f.x + 90) + ' ' + (f.y - 60) + ' ' + (f.x - 90) + ' ' + (f.y - 60) + ' ' + (f.x - 50) + ' ' + f.y; }
        var dx = t.x - f.x, dy = t.y - f.y;
        if (Math.abs(dx) < 10) { var off = (key % 2 ? 1 : -1) * 60; return 'M' + f.x + ' ' + f.y + 'C' + (f.x + off) + ' ' + f.y + ' ' + (t.x + off) + ' ' + t.y + ' ' + t.x + ' ' + t.y; }
        var cx = dx * 0.4;
        return 'M' + f.x + ' ' + f.y + 'C' + (f.x + cx) + ' ' + f.y + ' ' + (t.x - cx) + ' ' + t.y + ' ' + t.x + ' ' + t.y;
    }

    // Store edge paths for drag updates
    var localEdgePaths = {};

    // Edges (bezier) — workflow-aware coloring
    edges.forEach(function(e, ei) {
        var f = pos[e.source], t = pos[e.target]; if (!f || !t) return;
        var d = edgePath(e.source, e.target, ei);
        var mx = (f.x + t.x) / 2, my = (f.y + t.y) / 2;
        var g = _svgE('g'); g.setAttribute('data-edge', ei);
        var path = _svgE('path', {d: d, fill: 'none', stroke: '#334155', 'stroke-width': '1.5', 'marker-end': 'url(#' + markerId + ')'});
        g.appendChild(path);
        localEdgePaths[ei] = {el: path, src: e.source, tgt: e.target};

        var label1 = (e.protocol || '') + (e.port ? ':' + e.port : '') + (e.paths && e.paths.length ? ' ' + e.paths[0] : '');
        var label2 = e.description || '';
        if (label1) {
            var bg = _svgE('rect', {x: mx - 60, y: my - 18, width: 120, height: label2 ? 28 : 16, rx: 4, fill: '#0f172a', 'fill-opacity': '0.85'});
            bg.setAttribute('data-edge-label', ei); g.appendChild(bg);
            var t1 = _svgE('text', {x: mx, y: my - 5, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '9', 'font-family': '-apple-system,sans-serif'});
            t1.textContent = label1; t1.setAttribute('data-edge-label', ei); g.appendChild(t1);
        }
        if (label2) {
            var t2 = _svgE('text', {x: mx, y: my + 8, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            t2.textContent = trun(label2, 30); t2.setAttribute('data-edge-label', ei); g.appendChild(t2);
        }
        svg.appendChild(g);
    });

    // update edges helper (for drag)
    function updateEdges(nodeName) {
        edges.forEach(function(e, ei) {
            var ep = localEdgePaths[ei]; if (!ep) return;
            if (ep.src !== nodeName && ep.tgt !== nodeName) return;
            var nd = edgePath(ep.src, ep.tgt, ei);
            ep.el.setAttribute('d', nd);
            var f2 = pos[ep.src], t2 = pos[ep.tgt]; if (!f2 || !t2) return;
            var mx2 = (f2.x + t2.x) / 2, my2 = (f2.y + t2.y) / 2;
            var parent = ep.el.parentNode; if (!parent) return;
            var labels = parent.querySelectorAll('[data-edge-label="' + ei + '"]');
            labels.forEach(function(lb) {
                if (lb.tagName === 'rect') { lb.setAttribute('x', mx2 - 60); lb.setAttribute('y', my2 - 18) }
                else if (lb.tagName === 'text') {
                    lb.setAttribute('x', mx2);
                    lb.setAttribute('y', lb.getAttribute('font-style') ? my2 + 8 : my2 - 5);
                }
            });
        });
    }

    // Nodes with drag + click + double-click edit
    nodes.forEach(function(n) {
        var p = pos[n.name]; if (!p) return;
        var isManagedOrExt = (n.namespace === 'external' || n.namespace === 'managed' || n.kind === 'ExternalService');
        var color = isManagedOrExt ? '#FF9900' : (ARCH_TYPE_COLOR[n.service_type] || '#326CE5');
        var iconId = n.icon_key || 'k8s-deploy';

        var cp = ARCH.customProps[n.name] || {};
        var nw = cp.size === 'large' ? 120 : cp.size === 'small' ? 80 : 100;
        var nh = cp.size === 'large' ? 100 : cp.size === 'small' ? 60 : 80;
        var label = cp.label || n.name;

        var isHL = ARCH.crossNav && ARCH.crossNav.highlight && ARCH.crossNav.entity === n.name;
        var g = _svgE('g', {transform: 'translate(' + (p.x - nw / 2) + ',' + (p.y - nh / 2) + ')', style: 'cursor:grab', 'data-node': n.name});

        var defaultStroke = isManagedOrExt ? '#FF9900' : '#334155';
        var defaultSW = isManagedOrExt ? '1.5' : '1';
        g.appendChild(_svgE('rect', {x: 0, y: 0, width: nw, height: nh, rx: 8, fill: isHL ? '#1e3a5f' : '#1e293b', stroke: isHL ? '#38bdf8' : defaultStroke, 'stroke-width': isHL ? '2.5' : defaultSW}));
        g.appendChild(_archIconImg(iconId, nw / 2 - 20, 4, 40, 40));

        var txt = _svgE('text', {x: nw / 2, y: nh - 24, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '10', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        txt.textContent = trun(label, 16); g.appendChild(txt);

        var meta = (isManagedOrExt ? (n.namespace === 'managed' ? 'Managed' : 'External') : n.kind || 'Deployment') + ((n.ports && n.ports.length) ? ' :' + n.ports[0] : '');
        var mt = _svgE('text', {x: nw / 2, y: nh - 12, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
        mt.textContent = meta; g.appendChild(mt);

        _archBindTip(g, n);

        // Drag + click discrimination
        (function(nodeName, grp, halfW, halfH, nodeObj) {
            var dragging = false, ox, oy, startX, startY, moved;
            grp.addEventListener('mousedown', function(ev) {
                if (ev.detail > 1) return;
                dragging = true; moved = false;
                ox = ev.clientX; oy = ev.clientY;
                startX = ev.clientX; startY = ev.clientY;
                grp.style.cursor = 'grabbing';
                ev.preventDefault();
            });
            svg.addEventListener('mousemove', function(ev) {
                if (!dragging) return;
                var svgR = svg.getBoundingClientRect();
                var scaleX = parseFloat(svg.getAttribute('viewBox').split(' ')[2]) / svgR.width;
                var scaleY = parseFloat(svg.getAttribute('viewBox').split(' ')[3]) / svgR.height;
                var dx2 = (ev.clientX - ox) * scaleX, dy2 = (ev.clientY - oy) * scaleY;
                pos[nodeName].x += dx2; pos[nodeName].y += dy2;
                grp.setAttribute('transform', 'translate(' + (pos[nodeName].x - halfW) + ',' + (pos[nodeName].y - halfH) + ')');
                updateEdges(nodeName);
                ox = ev.clientX; oy = ev.clientY;
                var totalDx = Math.abs(ev.clientX - startX);
                var totalDy = Math.abs(ev.clientY - startY);
                if (totalDx > 5 || totalDy > 5) moved = true;
            });
            svg.addEventListener('mouseup', function(ev) {
                if (!dragging) return;
                dragging = false; grp.style.cursor = 'grab';
                if (moved) {
                    // Was a drag — save position
                    ARCH.customPos[nodeName] = {x: pos[nodeName].x, y: pos[nodeName].y};
                } else {
                    // Was a click (< 5px movement)
                    if (opts.onClick) {
                        opts.onClick(nodeName, nodeObj);
                    }
                }
            });
        })(n.name, g, nw / 2, nh / 2, n);

        // double-click edit
        (function(nodeName, grp, nWidth, nHeight) {
            grp.addEventListener('dblclick', function(ev) {
                ev.preventDefault(); ev.stopPropagation();
                var cp2 = ARCH.customProps[nodeName] || {};
                var curLabel = cp2.label || nodeName;
                var curSize = cp2.size || 'normal';
                var overlay = document.createElement('div');
                overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:9999;display:flex;align-items:center;justify-content:center';
                var box = document.createElement('div');
                box.style.cssText = 'background:#1e293b;border:1px solid #475569;border-radius:10px;padding:20px;min-width:240px;color:#e2e8f0;font-size:.72rem';
                box.innerHTML = '<div style="font-weight:600;margin-bottom:12px">' + esc(nodeName) + ' 편집</div>'
                    + '<label style="display:block;margin-bottom:8px">라벨<br><input id="_editLabel" value="' + esc(curLabel) + '" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:4px 8px;border-radius:4px;margin-top:4px"></label>'
                    + '<label style="display:block;margin-bottom:12px">크기<br><select id="_editSize" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:4px 8px;border-radius:4px;margin-top:4px">'
                    + '<option value="small"' + (curSize === 'small' ? ' selected' : '') + '>작게 (80x60)</option>'
                    + '<option value="normal"' + (curSize === 'normal' ? ' selected' : '') + '>보통 (100x80)</option>'
                    + '<option value="large"' + (curSize === 'large' ? ' selected' : '') + '>크게 (120x100)</option></select></label>'
                    + '<div style="display:flex;gap:8px;justify-content:flex-end">'
                    + '<button id="_editCancel" style="padding:4px 12px;background:#334155;border:none;border-radius:4px;color:#e2e8f0;cursor:pointer">취소</button>'
                    + '<button id="_editOk" style="padding:4px 12px;background:#3b82f6;border:none;border-radius:4px;color:#fff;cursor:pointer">적용</button></div>';
                overlay.appendChild(box); document.body.appendChild(overlay);
                document.getElementById('_editLabel').focus();
                document.getElementById('_editCancel').onclick = function() { overlay.remove() };
                document.getElementById('_editOk').onclick = function() {
                    var nl = document.getElementById('_editLabel').value.trim() || nodeName;
                    var ns = document.getElementById('_editSize').value;
                    ARCH.customProps[nodeName] = {label: nl, size: ns};
                    overlay.remove();
                    // Re-render current view
                    archNavigateTo(ARCH.nav.level, {app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});
                };
                overlay.addEventListener('click', function(ev2) { if (ev2.target === overlay) overlay.remove() });
            });
        })(n.name, g, nw, nh);

        svg.appendChild(g);
    });

    // SPOF badges (if requested)
    if (opts.showSpof && ARCH.analysis && ARCH.analysis.spof) {
        ARCH.analysis.spof.forEach(function(s) {
            var p2 = pos[s.service]; if (!p2) return;
            var badge = _svgE('g', {transform: 'translate(' + (p2.x + 40) + ',' + (p2.y - 45) + ')'});
            badge.appendChild(_svgE('circle', {cx: 0, cy: 0, r: 10, fill: '#ef4444'}));
            var bt = _svgE('text', {x: 0, y: 5, 'text-anchor': 'middle', fill: '#fff', 'font-size': '13', 'font-weight': '700'});
            bt.textContent = '!'; badge.appendChild(bt);
            svg.appendChild(badge);
        });
    }
}

// ================================================================
// RENDER: L2 — Component View (App + Infra)
// ================================================================

function _archShowL2Sections(showAll) {
    var infra = $('archSvgL2Infra');
    var wfSec = $('archL2WorkflowSection');
    if (infra && infra.parentElement && infra.parentElement.parentElement)
        infra.parentElement.parentElement.style.display = showAll ? '' : 'none';
    if (wfSec) wfSec.style.display = showAll ? '' : 'none';
}

function archRenderL2(appGroup) {
    var drillLevel = (ARCH.mode === 'single_app' && ARCH.nav.level === 'L1') ? 'L2' : 'L3';
    var unified = $('archSvgL2Unified');
    if (unified) {
        var data = _archDataL2Unified(ARCH.nodes, ARCH.edges, appGroup);
        _archRenderNested('archSvgL2Unified', data.nodes, data.edges, {
            showSpof: true,
            onClick: function(nodeName, nodeObj) {
                if (nodeObj && nodeObj.service_type === 'boundary') {
                    _archAnalyzeBoundary(nodeObj);
                } else {
                    archNavigateTo(drillLevel, {service: nodeName});
                }
            }
        }).catch(function(err) { console.error('ELK layout error:', err); });
        return;
    }
    _archShowL2Sections(true);
    var data = _archDataL2(ARCH.nodes, ARCH.edges, appGroup);

    _archRenderNodesTopo('archSvgL2App', data.appDiagram.nodes, data.appDiagram.edges, {
        noGroups: true,
        showSpof: true,
        onClick: function(nodeName, nodeObj) {
            if (nodeObj && nodeObj.service_type === 'boundary') {
                _archAnalyzeBoundary(nodeObj);
            } else {
                archNavigateTo(drillLevel, {service: nodeName});
            }
        }
    });

    _archRenderL2Infra('archSvgL2Infra', data);
    _archRenderL2Workflows('archSvgL2Wf', data, appGroup);
}

function archRenderL2Flow(appGroup) {
    if ($('archSvgL2Unified')) { archRenderL2(appGroup); return; }
    _archShowL2Sections(false);
    var a = ARCH.analysis;
    if (!a || !a.workflows) { archRenderL2(appGroup); return; }

    var data = _archDataL2(ARCH.nodes, ARCH.edges, appGroup);
    var allGroupNames = {};
    data.appDiagram.nodes.forEach(function(n) { allGroupNames[n.name] = true; });
    if (data.infraDiagram.managedNodes) {
        data.infraDiagram.managedNodes.forEach(function(n) { allGroupNames[n.name] = true; });
    }

    var wfs = a.workflows.filter(function(w) {
        if (typeof w !== 'object' || !w.hops || !w.hops.length) return false;
        return w.hops.some(function(hop) {
            return allGroupNames[hop.from || hop.source || ''] || allGroupNames[hop.to || hop.target || ''];
        });
    });

    if (!wfs.length) { archRenderL2(appGroup); return; }

    var svg = $('archSvgL2App');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var nodeMap = {};
    ARCH.nodes.forEach(function(n) { nodeMap[n.name] = n; });

    var PAD = 30, NODE_W = 90, NODE_H = 70, STEP_X = 140, ROW_PAD = 16;
    var LABEL_H = 28, ROW_GAP = 20, BOX_PAD = 12;
    var WF_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4'];

    // Pre-calc dimensions
    var maxSteps = 0;
    wfs.forEach(function(w) {
        var c = w.hops.length + 1;
        if (c > maxSteps) maxSteps = c;
    });

    var rowH = LABEL_H + BOX_PAD + NODE_H + BOX_PAD;
    var W = Math.max(700, PAD * 2 + maxSteps * STEP_X + NODE_W);
    var H = PAD + wfs.length * (rowH + ROW_GAP);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(300, H) + 'px';

    // Defs
    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="archArrowL2F" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#06b6d4"/></marker>';
    svg.appendChild(defs);

    wfs.forEach(function(wf, wi) {
        var color = WF_COLORS[wi % WF_COLORS.length];
        var yTop = PAD + wi * (rowH + ROW_GAP);

        // Build chain
        var chain = [];
        wf.hops.forEach(function(hop, i) {
            if (i === 0) chain.push(hop.from || hop.source || '');
            chain.push(hop.to || hop.target || '');
        });

        var boxW = PAD + chain.length * STEP_X + BOX_PAD;
        var boxH = rowH;

        // Group box background
        svg.appendChild(_svgE('rect', {
            x: PAD - BOX_PAD, y: yTop, width: boxW, height: boxH,
            rx: 10, fill: '#0f172a', stroke: color, 'stroke-width': '1', 'stroke-opacity': '0.4'
        }));

        // Workflow name label
        var lbl = _svgE('text', {
            x: PAD, y: yTop + LABEL_H - 8,
            fill: color, 'font-size': '11', 'font-weight': '700',
            'font-family': '-apple-system,sans-serif'
        });
        lbl.textContent = wf.name || '(unnamed)';
        svg.appendChild(lbl);

        var yCenterNode = yTop + LABEL_H + BOX_PAD + NODE_H / 2;

        // Draw nodes
        chain.forEach(function(name, si) {
            var cx = PAD + si * STEP_X + NODE_W / 2;
            var inGroup = !!allGroupNames[name];
            var node = nodeMap[name];
            var iconKey = node ? (node.icon_key || 'k8s-deploy') : 'k8s-deploy';
            var isMng = node && (node.namespace === 'managed' || node.namespace === 'external' || node.kind === 'ExternalService');

            var nx = cx - NODE_W / 2, ny = yCenterNode - NODE_H / 2;

            // Node box
            svg.appendChild(_svgE('rect', {
                x: nx, y: ny, width: NODE_W, height: NODE_H, rx: 8,
                fill: '#1e293b',
                stroke: inGroup ? color : '#475569',
                'stroke-width': inGroup ? '1.5' : '1',
                'stroke-dasharray': inGroup ? '' : '4 2'
            }));

            // Icon
            svg.appendChild(_archIconImg(iconKey, cx - 16, ny + 4, 32, 32));

            // Name label
            var t = _svgE('text', {
                x: cx, y: ny + NODE_H - 8,
                'text-anchor': 'middle', fill: inGroup ? '#e2e8f0' : '#64748b',
                'font-size': '9', 'font-weight': '500', 'font-family': '-apple-system,sans-serif'
            });
            t.textContent = trun(name, 14);
            svg.appendChild(t);

            // Arrow to next
            if (si < chain.length - 1) {
                var x1 = cx + NODE_W / 2 + 4;
                var x2 = PAD + (si + 1) * STEP_X + NODE_W / 2 - NODE_W / 2 - 4;
                svg.appendChild(_svgE('line', {
                    x1: x1, y1: yCenterNode, x2: x2, y2: yCenterNode,
                    stroke: color, 'stroke-width': '1.5', 'stroke-opacity': '0.7',
                    'marker-end': 'url(#archArrowL2F)'
                }));
            }
        });
    });
}

function _archRenderL2Infra(svgId, data) {
    var svg = $(svgId);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var appNodes = data.infraDiagram.appNodes || [];
    var managedNodes = data.infraDiagram.managedNodes || [];
    var infraEdges = data.infraDiagram.edges || [];

    if (!appNodes.length && !managedNodes.length) return;

    var PAD = 60, NODE_W = 100, NODE_H = 80, YGAP = 90;
    var leftX = PAD + NODE_W / 2 + 40;
    var rightX = Math.max(500, svg.parentElement.clientWidth - PAD - NODE_W / 2 - 40);
    var maxRows = Math.max(appNodes.length, managedNodes.length);
    var W = Math.max(700, svg.parentElement.clientWidth - 2);
    var H = Math.max(300, maxRows * YGAP + PAD * 2);

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    // Defs
    var defs = _svgE('defs');
    var markerId = 'archArrow_' + svgId;
    defs.innerHTML = '<marker id="' + markerId + '" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#475569"/></marker>';
    svg.appendChild(defs);

    // Column labels
    var leftLabel = _svgE('text', {x: leftX, y: PAD - 20, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    leftLabel.textContent = 'App Service (K8s)';
    svg.appendChild(leftLabel);

    var rightLabel = _svgE('text', {x: rightX, y: PAD - 20, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    rightLabel.textContent = 'Managed Service (AWS)';
    svg.appendChild(rightLabel);

    // Position nodes
    var pos = {};
    appNodes.forEach(function(n, i) {
        pos[n.name] = {x: leftX, y: PAD + i * YGAP};
    });
    managedNodes.forEach(function(n, i) {
        pos[n.name] = {x: rightX, y: PAD + i * YGAP};
    });

    // Edges
    infraEdges.forEach(function(e) {
        var f = pos[e.source], t = pos[e.target]; if (!f || !t) return;
        var dx = t.x - f.x;
        var cx = dx * 0.35;
        var d = 'M' + f.x + ' ' + f.y + 'C' + (f.x + cx) + ' ' + f.y + ' ' + (t.x - cx) + ' ' + t.y + ' ' + t.x + ' ' + t.y;
        var path = _svgE('path', {d: d, fill: 'none', stroke: '#334155', 'stroke-width': '1.5', 'marker-end': 'url(#' + markerId + ')'});
        svg.appendChild(path);

        var mx = (f.x + t.x) / 2, my = (f.y + t.y) / 2;
        var label1 = (e.protocol || '') + (e.port ? ':' + e.port : '');
        if (label1) {
            var bg = _svgE('rect', {x: mx - 40, y: my - 10, width: 80, height: 16, rx: 4, fill: '#0f172a', 'fill-opacity': '0.85'});
            svg.appendChild(bg);
            var t1 = _svgE('text', {x: mx, y: my + 3, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '9', 'font-family': '-apple-system,sans-serif'});
            t1.textContent = label1;
            svg.appendChild(t1);
        }
    });

    // Render node boxes
    var allNodes = appNodes.concat(managedNodes);
    allNodes.forEach(function(n) {
        var p = pos[n.name]; if (!p) return;
        var isManagedOrExt = (n.namespace === 'external' || n.namespace === 'managed' || n.kind === 'ExternalService');
        var color = isManagedOrExt ? '#FF9900' : (ARCH_TYPE_COLOR[n.service_type] || '#326CE5');
        var iconId = n.icon_key || 'k8s-deploy';

        var nw = NODE_W, nh = NODE_H;
        var g = _svgE('g', {transform: 'translate(' + (p.x - nw / 2) + ',' + (p.y - nh / 2) + ')', style: 'cursor:pointer', 'data-node': n.name});

        g.appendChild(_svgE('rect', {x: 0, y: 0, width: nw, height: nh, rx: 8, fill: '#1e293b', stroke: isManagedOrExt ? '#FF9900' : '#334155', 'stroke-width': isManagedOrExt ? '1.5' : '1'}));
        g.appendChild(_archIconImg(iconId, nw / 2 - 20, 4, 40, 40));

        var txt = _svgE('text', {x: nw / 2, y: nh - 24, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '10', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        txt.textContent = trun(n.name, 14); g.appendChild(txt);

        var meta = (isManagedOrExt ? (n.kind || 'Managed') : (n.kind || 'Deployment'));
        var mt = _svgE('text', {x: nw / 2, y: nh - 12, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
        mt.textContent = meta; g.appendChild(mt);

        _archBindTip(g, n);

        svg.appendChild(g);
    });
}

// ================================================================
// RENDER: L2 Workflows — Graph-based hop chain per workflow
// ================================================================

function _archRenderL2Workflows(svgId, l2data, appGroup) {
    var section = $('archL2WorkflowSection');
    var svg = $(svgId);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var a = ARCH.analysis;
    if (!a || !a.workflows) { if (section) section.style.display = 'none'; return; }

    var allGroupNames = {};
    l2data.appDiagram.nodes.forEach(function(n) { allGroupNames[n.name] = true; });
    if (l2data.infraDiagram.managedNodes) {
        l2data.infraDiagram.managedNodes.forEach(function(n) { allGroupNames[n.name] = true; });
    }

    var wfs = a.workflows.filter(function(w) {
        if (typeof w !== 'object' || !w.hops || !w.hops.length) return false;
        return w.hops.some(function(hop) {
            return allGroupNames[hop.from || hop.source || ''] || allGroupNames[hop.to || hop.target || ''];
        });
    });

    if (!wfs.length) { if (section) section.style.display = 'none'; return; }
    if (section) section.style.display = '';

    var PAD = 40, ROW_H = 70, NODE_R = 20, STEP_W = 140, LABEL_H = 22;
    var maxSteps = 0;
    wfs.forEach(function(w) { var c = w.hops.length + 1; if (c > maxSteps) maxSteps = c; });
    var W = Math.max(600, PAD * 2 + maxSteps * STEP_W);
    var H = PAD + wfs.length * (ROW_H + LABEL_H) + PAD;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="archArrowL2Wf" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#06b6d4"/></marker>';
    svg.appendChild(defs);

    var WF_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4'];

    wfs.forEach(function(wf, wi) {
        var yBase = PAD + wi * (ROW_H + LABEL_H);
        var color = WF_COLORS[wi % WF_COLORS.length];

        var lbl = _svgE('text', {x: PAD, y: yBase + 12, fill: color, 'font-size': '11', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
        lbl.textContent = wf.name || '(unnamed)';
        svg.appendChild(lbl);

        var chain = [];
        wf.hops.forEach(function(hop, i) {
            if (i === 0) chain.push(hop.from || hop.source || '');
            chain.push(hop.to || hop.target || '');
        });

        var yCenter = yBase + LABEL_H + ROW_H / 2;

        chain.forEach(function(name, si) {
            var x = PAD + 40 + si * STEP_W;
            var inGroup = allGroupNames[name];
            var node = null;
            ARCH.nodes.forEach(function(n) { if (n.name === name) node = n; });
            var iconKey = node ? (node.icon_key || 'k8s-deploy') : 'k8s-deploy';

            var strokeColor = inGroup ? color : '#475569';
            var fillColor = inGroup ? '#1e293b' : '#0f172a';
            svg.appendChild(_svgE('circle', {cx: x, cy: yCenter, r: NODE_R, fill: fillColor, stroke: strokeColor, 'stroke-width': inGroup ? '2' : '1', 'stroke-dasharray': inGroup ? '' : '4 2'}));

            svg.appendChild(_archIconImg(iconKey, x - 12, yCenter - 12, 24, 24));

            var t = _svgE('text', {x: x, y: yCenter + NODE_R + 13, 'text-anchor': 'middle', fill: inGroup ? '#e2e8f0' : '#64748b', 'font-size': '9', 'font-weight': '500', 'font-family': '-apple-system,sans-serif'});
            t.textContent = trun(name, 16);
            svg.appendChild(t);

            if (si < chain.length - 1) {
                var x2 = PAD + 40 + (si + 1) * STEP_W;
                svg.appendChild(_svgE('line', {
                    x1: x + NODE_R + 4, y1: yCenter,
                    x2: x2 - NODE_R - 4, y2: yCenter,
                    stroke: color, 'stroke-width': '1.5', 'stroke-opacity': '0.6',
                    'marker-end': 'url(#archArrowL2Wf)'
                }));
            }
        });
    });
}

// ================================================================
// RENDER: L3 — Service Detail (Radial/Star Layout)
// ================================================================

function archRenderL3(serviceName) {
    var svg = $('archSvgL3');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!serviceName) return;

    var data = _archDataL3(ARCH.nodes, ARCH.edges, serviceName, ARCH.analysis);
    if (!data.centerNode) return;

    var RADIUS = 200, CENTER_W = 140, CENTER_H = 100, NODE_W = 100, NODE_H = 80;
    var numConnected = data.connectedNodes.length;
    var W = Math.max(700, svg.parentElement.clientWidth - 2);
    var H = Math.max(500, RADIUS * 2 + CENTER_H + 120);
    var cx = W / 2, cy = H / 2;

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = H + 'px';

    // Defs
    var defs = _svgE('defs');
    var markerId = 'archArrowL3';
    defs.innerHTML = '<marker id="' + markerId + '" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#475569"/></marker>';
    svg.appendChild(defs);

    // Position connected nodes in a circle
    var connPos = {};
    data.connectedNodes.forEach(function(n, i) {
        var angle = (2 * Math.PI * i / numConnected) - Math.PI / 2;
        connPos[n.name] = {
            x: cx + RADIUS * Math.cos(angle),
            y: cy + RADIUS * Math.sin(angle)
        };
    });

    // Edges from center to connected nodes
    data.connectedEdges.forEach(function(e) {
        var targetName = (e.source === serviceName) ? e.target : e.source;
        var tp = connPos[targetName]; if (!tp) return;

        var dx = tp.x - cx, dy = tp.y - cy;
        var curveFactor = 0.2;
        var perpX = -dy * curveFactor, perpY = dx * curveFactor;
        var d = 'M' + cx + ' ' + cy + 'Q' + (cx + dx * 0.5 + perpX) + ' ' + (cy + dy * 0.5 + perpY) + ' ' + tp.x + ' ' + tp.y;

        var isOutgoing = (e.source === serviceName);
        var path = _svgE('path', {
            d: d, fill: 'none', stroke: '#334155', 'stroke-width': '1.5',
            'marker-end': isOutgoing ? 'url(#' + markerId + ')' : 'none',
            'marker-start': isOutgoing ? 'none' : 'none'
        });
        svg.appendChild(path);

        // Edge label
        var mx = (cx + tp.x) / 2 + perpX * 0.5, my = (cy + tp.y) / 2 + perpY * 0.5;
        var label = (e.protocol || '') + (e.port ? ':' + e.port : '');
        if (label) {
            var bg = _svgE('rect', {x: mx - 35, y: my - 8, width: 70, height: 14, rx: 3, fill: '#0f172a', 'fill-opacity': '0.85'});
            svg.appendChild(bg);
            var lt = _svgE('text', {x: mx, y: my + 4, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
            lt.textContent = label;
            svg.appendChild(lt);
        }

        // Direction label
        var dirLabel = _svgE('text', {x: mx, y: my + 16, 'text-anchor': 'middle', fill: '#475569', 'font-size': '7', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
        dirLabel.textContent = isOutgoing ? esc(serviceName) + ' → ' + esc(targetName) : esc(targetName) + ' → ' + esc(serviceName);
        svg.appendChild(dirLabel);
    });

    // Center node (highlighted)
    var cn = data.centerNode;
    var isCenterManaged = (cn.namespace === 'external' || cn.namespace === 'managed' || cn.kind === 'ExternalService');
    var centerColor = isCenterManaged ? '#FF9900' : (ARCH_TYPE_COLOR[cn.service_type] || '#326CE5');
    var centerIconId = cn.icon_key || 'k8s-deploy';

    var cg = _svgE('g', {transform: 'translate(' + (cx - CENTER_W / 2) + ',' + (cy - CENTER_H / 2) + ')', style: 'cursor:pointer'});
    cg.appendChild(_svgE('rect', {x: 0, y: 0, width: CENTER_W, height: CENTER_H, rx: 12, fill: '#1e293b', stroke: '#38bdf8', 'stroke-width': '2.5'}));
    cg.appendChild(_archIconImg(centerIconId, CENTER_W / 2 - 20, 6, 40, 40));
    var ct = _svgE('text', {x: CENTER_W / 2, y: CENTER_H - 30, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '12', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
    ct.textContent = trun(cn.name, 18); cg.appendChild(ct);
    var cm = _svgE('text', {x: CENTER_W / 2, y: CENTER_H - 16, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '9', 'font-family': '-apple-system,sans-serif'});
    cm.textContent = (cn.kind || 'Deployment') + ((cn.ports && cn.ports.length) ? ' :' + cn.ports[0] : '');
    cg.appendChild(cm);
    var codeLabel = _svgE('text', {x: CENTER_W / 2, y: CENTER_H - 2, 'text-anchor': 'middle', fill: '#a78bfa', 'font-size': '8', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    codeLabel.textContent = '클릭: 코드 분석';
    cg.appendChild(codeLabel);
    _archBindTip(cg, cn);
    (function(centerName, connNodes) {
        cg.addEventListener('click', function() {
            _svcShowScopeOverlay(centerName, connNodes);
        });
    })(cn.name, data.connectedNodes);
    svg.appendChild(cg);

    // SPOF badge on center
    if (data.spof.length) {
        var badge = _svgE('g', {transform: 'translate(' + (cx + CENTER_W / 2 - 5) + ',' + (cy - CENTER_H / 2 - 5) + ')'});
        badge.appendChild(_svgE('circle', {cx: 0, cy: 0, r: 12, fill: '#ef4444'}));
        var bt = _svgE('text', {x: 0, y: 5, 'text-anchor': 'middle', fill: '#fff', 'font-size': '14', 'font-weight': '700'});
        bt.textContent = '!'; badge.appendChild(bt);
        svg.appendChild(badge);
    }

    // Connected nodes
    data.connectedNodes.forEach(function(n) {
        var p = connPos[n.name]; if (!p) return;
        var isManagedOrExt = (n.namespace === 'external' || n.namespace === 'managed' || n.kind === 'ExternalService');
        var color = isManagedOrExt ? '#FF9900' : (ARCH_TYPE_COLOR[n.service_type] || '#326CE5');
        var iconId = n.icon_key || 'k8s-deploy';

        var g = _svgE('g', {transform: 'translate(' + (p.x - NODE_W / 2) + ',' + (p.y - NODE_H / 2) + ')', style: 'cursor:pointer', 'data-node': n.name});

        g.appendChild(_svgE('rect', {x: 0, y: 0, width: NODE_W, height: NODE_H, rx: 8, fill: '#1e293b', stroke: isManagedOrExt ? '#FF9900' : '#334155', 'stroke-width': isManagedOrExt ? '1.5' : '1'}));
        g.appendChild(_archIconImg(iconId, NODE_W / 2 - 20, 4, 40, 40));

        var txt = _svgE('text', {x: NODE_W / 2, y: NODE_H - 24, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': '10', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        txt.textContent = trun(n.name, 14); g.appendChild(txt);

        var mt = _svgE('text', {x: NODE_W / 2, y: NODE_H - 12, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '8', 'font-family': '-apple-system,sans-serif'});
        mt.textContent = (isManagedOrExt ? (n.kind || 'Managed') : (n.kind || 'Deployment'));
        g.appendChild(mt);

        _archBindTip(g, n);

        (function(nodeName, nodeData) {
            g.addEventListener('click', function() {
                if (nodeData.service_type === 'boundary') {
                    _archAnalyzeBoundary(nodeData);
                } else {
                    archNavigateTo('L3', {service: nodeName});
                }
            });
        })(n.name, n);

        svg.appendChild(g);
    });
}

// ================================================================
// COMPONENT DIAGRAM (L3 in single-app mode)
// ================================================================

function _archRenderComponentView(serviceName) {
    var container = $('archViewL3');
    if (!container) return;
    if (!serviceName) {
        container.innerHTML = '<div style="padding:2rem;color:#64748b;text-align:center">서비스를 선택하세요</div>';
        return;
    }

    // Check if we already have cached component data
    if (ARCH._componentCache && ARCH._componentCache[serviceName]) {
        _archDrawComponentDiagram(container, ARCH._componentCache[serviceName]);
        return;
    }

    container.innerHTML = '<div style="padding:2rem;color:#94a3b8;text-align:center">' +
        '<div style="margin-bottom:1rem"><span class="arch-spinner"></span></div>' +
        '<p style="font-size:1.1rem">Component Diagram</p>' +
        '<p>' + esc(serviceName) + ' 코드 분석 중...</p></div>';

    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    var url = '/api/arch/component/stream?space_id=' + encodeURIComponent(sid) +
              '&service_name=' + encodeURIComponent(serviceName);
    var es = new EventSource(url);
    es.onmessage = function(ev) {
        var d = JSON.parse(ev.data);
        if (d.type === 'complete' && d.data) {
            es.close();
            if (!ARCH._componentCache) ARCH._componentCache = {};
            ARCH._componentCache[serviceName] = d.data;
            _archDrawComponentDiagram(container, d.data);
        } else if (d.type === 'error') {
            es.close();
            container.innerHTML = '<div style="padding:2rem;color:#f87171;text-align:center">' +
                '<p>컴포넌트 분석 실패</p><p style="font-size:0.85rem">' + esc(d.error || '') + '</p></div>';
        }
    };
    es.onerror = function() { es.close(); };
}

function _archDrawComponentDiagram(container, data) {
    var components = data.components || [];
    var relationships = data.relationships || [];
    var providedIf = data.provided_interfaces || [];
    var requiredIf = data.required_interfaces || [];

    var h = '<div style="padding:1rem">';
    h += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:1rem">';
    h += '<span style="color:#e2e8f0;font-weight:600;font-size:1rem">' + esc(data.service_name || '') + '</span>';
    if (data.language) h += '<span style="background:#1e293b;color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:0.75rem">' + esc(data.language) + '</span>';
    if (data.repo_path) h += '<span style="color:#64748b;font-size:0.75rem">' + esc(data.repo_path) + '</span>';
    h += '</div>';

    // Component boxes
    h += '<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:1.5rem">';
    components.forEach(function(c) {
        h += '<div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px;min-width:200px;max-width:320px;flex:1">';
        h += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">';
        h += '<span style="color:#38bdf8;font-size:0.7rem">' + esc(c.stereotype || '<<component>>') + '</span>';
        h += '</div>';
        h += '<div style="color:#e2e8f0;font-weight:600;font-size:0.9rem;margin-bottom:4px">' + esc(c.name || '') + '</div>';
        h += '<div style="color:#94a3b8;font-size:0.8rem;margin-bottom:8px">' + esc(c.description || '') + '</div>';
        if (c.files && c.files.length) {
            h += '<div style="font-size:0.7rem;color:#64748b">';
            c.files.forEach(function(f) { h += '<div>' + esc(f) + '</div>'; });
            h += '</div>';
        }
        // Provided interfaces
        if (c.provided_interfaces && c.provided_interfaces.length) {
            h += '<div style="margin-top:6px;border-top:1px solid #1e293b;padding-top:4px">';
            c.provided_interfaces.forEach(function(pi) {
                h += '<div style="font-size:0.75rem;color:#4ade80">&#9679; ' + esc(pi.name || '') + ' <span style="color:#64748b">' + esc(pi.protocol || '') + '</span></div>';
            });
            h += '</div>';
        }
        // Required interfaces
        if (c.required_interfaces && c.required_interfaces.length) {
            h += '<div style="margin-top:4px">';
            c.required_interfaces.forEach(function(ri) {
                h += '<div style="font-size:0.75rem;color:#fb923c">&#9675; ' + esc(ri.name || '') + ' → ' + esc(ri.target || '') + '</div>';
            });
            h += '</div>';
        }
        h += '</div>';
    });
    h += '</div>';

    // Relationships
    if (relationships.length) {
        h += '<div style="margin-bottom:1rem"><div style="color:#94a3b8;font-size:0.8rem;font-weight:600;margin-bottom:6px">Relationships</div>';
        relationships.forEach(function(r) {
            h += '<div style="font-size:0.8rem;color:#cbd5e1;margin-bottom:2px">';
            h += esc(r.source || '') + ' <span style="color:#64748b">' + esc(r.type || '') + '</span> → ' + esc(r.target || '');
            if (r.description) h += ' <span style="color:#64748b">(' + esc(r.description) + ')</span>';
            h += '</div>';
        });
        h += '</div>';
    }

    // Service-level interfaces summary
    if (providedIf.length || requiredIf.length) {
        h += '<div style="border-top:1px solid #1e293b;padding-top:8px">';
        if (providedIf.length) {
            h += '<div style="color:#4ade80;font-size:0.8rem;font-weight:600;margin-bottom:4px">Provided Interfaces</div>';
            providedIf.forEach(function(pi) {
                h += '<div style="font-size:0.8rem;color:#cbd5e1;margin-bottom:2px">';
                h += esc(pi.name || '') + ' (' + esc(pi.protocol || '') + (pi.port ? ':' + pi.port : '') + ')';
                if (pi.operations && pi.operations.length) h += ' — ' + esc(pi.operations.join(', '));
                h += '</div>';
            });
        }
        if (requiredIf.length) {
            h += '<div style="color:#fb923c;font-size:0.8rem;font-weight:600;margin-top:6px;margin-bottom:4px">Required Interfaces</div>';
            requiredIf.forEach(function(ri) {
                h += '<div style="font-size:0.8rem;color:#cbd5e1;margin-bottom:2px">';
                h += esc(ri.name || '') + ' → ' + esc(ri.target_service || '') + ' (' + esc(ri.protocol || '') + ')';
                h += '</div>';
            });
        }
        h += '</div>';
    }

    h += '</div>';
    container.innerHTML = h;
}

// ================================================================
// DESCRIPTION PANEL — Context-sensitive
// ================================================================

function archRenderDesc() {
    var el = $('archDescContent');
    if (!el) return;
    var a = ARCH.analysis;
    var level = ARCH.nav.level;

    if (!a || !a.graph) { el.innerHTML = '<span style="color:#64748b">분석 완료 후 표시됩니다</span>'; return; }

    var h = '';

    if (level === 'L1') {
        // System overview + workflows + app list with service counts
        h += '<div class="arch-desc-section"><h4>시스템 개요</h4>';
        h += '<div class="arch-desc-item"><strong>' + esc(a.system_name || '') + '</strong><br>' + esc(a.description || '') + '</div></div>';
        if (a.workflows && a.workflows.length) {
            h += '<div class="arch-desc-section"><h4>워크플로우 (' + a.workflows.length + ')</h4>';
            a.workflows.forEach(function(w, wi) {
                if (typeof w === 'string') { h += '<div class="arch-desc-item">' + esc(w) + '</div>'; return; }
                var name = w.name || '(unnamed)';
                var hops = w.hops || w.steps || [];
                h += '<div class="arch-desc-item arch-wf-link" data-wf-idx="' + wi + '" style="cursor:pointer">';
                h += '<strong>' + esc(name) + '</strong>';
                h += ' <span style="color:#22c55e;font-size:.55rem;margin-left:4px">Sequence 보기 →</span>';
                if (hops.length) {
                    var chain = [];
                    hops.forEach(function(hop, i) {
                        if (i === 0) chain.push(esc(hop.from || hop.source || ''));
                        chain.push(esc(hop.to || hop.target || ''));
                    });
                    h += '<div style="margin-top:4px;font-size:.65rem;color:#94a3b8;line-height:1.6">';
                    h += chain.join(' <span style="color:#06b6d4">→</span> ');
                    h += '</div>';
                }
                h += '</div>';
            });
            h += '</div>';
        }
        // App list with service counts
        var l1data = _archDataL1(ARCH.nodes, ARCH.edges);
        if (l1data.appNodes.length) {
            h += '<div class="arch-desc-section"><h4>App Group (' + l1data.appNodes.length + ')</h4>';
            l1data.appNodes.forEach(function(an) {
                h += '<div class="arch-desc-item"><strong>' + esc(an.name) + '</strong> <span style="color:#64748b">(' + an.count + ' Service)</span>';
                h += '<br><span style="color:#475569;font-size:.6rem">' + esc(an.services.join(', ')) + '</span></div>';
            });
            h += '</div>';
        }
    } else if (level === 'L2') {
        var appGroup = ARCH.nav.selectedApp;
        var l2data = _archDataL2(ARCH.nodes, ARCH.edges, appGroup);

        // Services in this app
        h += '<div class="arch-desc-section"><h4>' + esc(appGroup || '') + ' Service (' + l2data.appDiagram.nodes.length + ')</h4>';
        l2data.appDiagram.nodes.forEach(function(n) {
            var role = (n.labels && n.labels.role) ? n.labels.role : '';
            var isK8s = (n.kind || '').indexOf('EKS') >= 0 || (n.kind || '').indexOf('Deployment') >= 0;
            h += '<div class="arch-desc-item"><strong>' + esc(n.name) + '</strong>' + (role ? ' — ' + esc(role) : '');
            if (isK8s) h += ' <span class="arch-code-btn" onclick="archStartServiceAnalysis(\'' + esc(n.name) + '\')" style="cursor:pointer;font-size:.5rem;color:#a78bfa;border:1px solid #7c3aed40;padding:1px 4px;border-radius:3px;margin-left:4px">코드 분석</span>';
            h += '<br><span style="color:#64748b">' + esc(n.service_type || 'app') + ' / ' + esc(n.kind || '') + '</span></div>';
        });
        h += '</div>';

        // Communication paths within app
        if (l2data.appDiagram.edges.length) {
            h += '<div class="arch-desc-section"><h4>내부 통신 (' + l2data.appDiagram.edges.length + ')</h4>';
            l2data.appDiagram.edges.forEach(function(e) {
                var proto = (e.protocol || 'tcp') + (e.port ? ':' + e.port : '');
                h += '<div class="arch-desc-item"><strong>' + esc(e.source) + ' → ' + esc(e.target) + '</strong>';
                h += ' <span style="color:#06b6d4">' + esc(proto) + '</span>';
                if (e.description) h += '<br><span style="color:#94a3b8">' + esc(e.description) + '</span>';
                h += '</div>';
            });
            h += '</div>';
        }

        // Managed services used by this app
        if (l2data.infraDiagram.managedNodes && l2data.infraDiagram.managedNodes.length) {
            h += '<div class="arch-desc-section"><h4>Managed Service (' + l2data.infraDiagram.managedNodes.length + ')</h4>';
            l2data.infraDiagram.managedNodes.forEach(function(m) {
                h += '<div class="arch-desc-item"><strong>' + esc(m.name) + '</strong> <span style="color:#f59e0b">' + esc(m.kind || m.service_type || '') + '</span></div>';
            });
            h += '</div>';
        }

        // Workflows involving this app group
        if (a.workflows && a.workflows.length) {
            var groupSvcNames = {};
            l2data.appDiagram.nodes.forEach(function(n) { groupSvcNames[n.name] = true; });
            if (l2data.infraDiagram.managedNodes) {
                l2data.infraDiagram.managedNodes.forEach(function(n) { groupSvcNames[n.name] = true; });
            }
            var groupWorkflows = a.workflows.filter(function(w) {
                if (typeof w === 'string') return false;
                var hops = w.hops || w.steps || [];
                return hops.some(function(hop) {
                    return groupSvcNames[hop.from || hop.source || ''] || groupSvcNames[hop.to || hop.target || ''];
                });
            });
            if (groupWorkflows.length) {
                h += '<div class="arch-desc-section"><h4>워크플로우 (' + groupWorkflows.length + ')</h4>';
                groupWorkflows.forEach(function(w) {
                    var name = w.name || '(unnamed)';
                    var hops = w.hops || w.steps || [];
                    var origIdx = a.workflows.indexOf(w);
                    h += '<div class="arch-desc-item arch-wf-link" data-wf-idx="' + origIdx + '" style="cursor:pointer">';
                    h += '<strong>' + esc(name) + '</strong>';
                    h += ' <span style="color:#22c55e;font-size:.55rem;margin-left:4px">Sequence 보기 →</span>';
                    if (hops.length) {
                        var chain = [];
                        hops.forEach(function(hop, i) {
                            if (i === 0) chain.push(esc(hop.from || hop.source || ''));
                            chain.push(esc(hop.to || hop.target || ''));
                        });
                        h += '<div style="margin-top:4px;font-size:.65rem;color:#94a3b8;line-height:1.6">';
                        h += chain.join(' <span style="color:#06b6d4">→</span> ');
                        h += '</div>';
                    }
                    h += '</div>';
                });
                h += '</div>';
            }
        }
    } else if (level === 'L3') {
        var svcName = ARCH.nav.selectedService;
        var l3data = _archDataL3(ARCH.nodes, ARCH.edges, svcName, a);

        // Service info
        if (l3data.centerNode) {
            var cn = l3data.centerNode;
            h += '<div class="arch-desc-section"><h4>Service Info</h4>';
            h += '<div class="arch-desc-item"><strong>' + esc(cn.name) + '</strong>';
            h += '<br>종류: <span style="color:#06b6d4">' + esc(cn.kind || 'Deployment') + '</span>';
            h += '<br>네임스페이스: <span style="color:#64748b">' + esc(cn.namespace || 'default') + '</span>';
            if (cn.ports && cn.ports.length) h += '<br>포트: <span style="color:#f59e0b">' + esc(cn.ports.join(', ')) + '</span>';
            if (cn.service_type) h += '<br>타입: <span style="color:#64748b">' + esc(cn.service_type) + '</span>';
            h += '</div></div>';
        }

        // SPOF
        if (l3data.spof.length) {
            h += '<div class="arch-desc-section"><h4>단일 장애점 (SPOF)</h4>';
            l3data.spof.forEach(function(s) {
                h += '<div class="arch-desc-item spof"><strong>' + esc(s.service || '') + '</strong><br>' + esc(s.reason || '');
                if (s.dependents && s.dependents.length) h += '<br><span style="color:#fca5a5">영향: ' + esc(s.dependents.join(', ')) + '</span>';
                h += '</div>';
            });
            h += '</div>';
        }

        // Blast radius
        if (l3data.blastRadius.length) {
            h += '<div class="arch-desc-section"><h4>Blast Radius</h4>';
            l3data.blastRadius.forEach(function(b) {
                h += '<div class="arch-desc-item warn"><strong>' + esc(b.failed_service || '') + '</strong>';
                if (b.affected && b.affected.length) h += ' → ' + esc(b.affected.join(', '));
                if (b.impact) h += '<br><span style="color:#94a3b8">' + esc(b.impact) + '</span>';
                h += '</div>';
            });
            h += '</div>';
        }

        // Connected nodes
        if (l3data.connectedNodes.length) {
            h += '<div class="arch-desc-section"><h4>Connected Service (' + l3data.connectedNodes.length + ')</h4>';
            l3data.connectedNodes.forEach(function(n) {
                var isManagedOrExt = (n.namespace === 'external' || n.namespace === 'managed' || n.kind === 'ExternalService');
                h += '<div class="arch-desc-item"><strong>' + esc(n.name) + '</strong> <span style="color:' + (isManagedOrExt ? '#FF9900' : '#326CE5') + '">' + esc(isManagedOrExt ? 'Managed' : (n.kind || 'Deployment')) + '</span></div>';
            });
            h += '</div>';
        }

        // Observability gaps for this service
        if (a.observability_gaps) {
            var svcGaps = a.observability_gaps.filter(function(g) { return g.service === svcName; });
            if (svcGaps.length) {
                h += '<div class="arch-desc-section"><h4>Observability Gaps</h4>';
                svcGaps.forEach(function(g) {
                    h += '<div class="arch-desc-item warn"><span style="color:#fbbf24">누락: ' + (g.missing || []).join(', ') + '</span></div>';
                });
                h += '</div>';
            }
        }
    }

    el.innerHTML = h || '<span style="color:#64748b">데이터 없음</span>';

    el.querySelectorAll('.arch-wf-link').forEach(function(item) {
        item.addEventListener('click', function() {
            var idx = parseInt(this.dataset.wfIdx);
            if (isNaN(idx) || idx < 0) return;
            ARCH._seqSelectedWf = idx;
            archSwitchView('seq');
        });
        item.addEventListener('mouseenter', function() { this.style.background = '#1e293b'; });
        item.addEventListener('mouseleave', function() { this.style.background = ''; });
    });
}

// ================================================================
// SSE DISCOVERY
// ================================================================

function archDiscoverFull() {
    archDiscover(false);
}
function archDeleteData() {
    if (!confirm('기존 분석 데이터를 모두 삭제합니다. 계속하시겠습니까?')) return;
    var sid = SELECTED || '';
    fetch('/api/arch/data?space_id=' + encodeURIComponent(sid), {method: 'DELETE'}).then(function(r) { return r.json() }).then(function(d) {
        if (d.ok) {
            ARCH.analysis = null; ARCH.nodes = []; ARCH.edges = [];
            ARCH.nav = {level: 'L1', selectedApp: null, selectedService: null, history: []};
            $('archTopoHdr').style.display = 'none'; $('archTopoEmpty').style.display = '';
            $('archDescContent').innerHTML = '';
            var panel = $('archChatPanel'); if (panel) panel.innerHTML = '';
            $('btnArchResume').style.display = 'none';
            archLoadVersions();
        }
    });
}
// ── App Name Management ──
function archLoadAppName() {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    fetch('/api/arch/app-name?space_id=' + encodeURIComponent(sid))
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok && d.app_name) {
                var inp = $('archAppNameInput');
                if (inp) inp.value = d.app_name;
                var st = $('archAppNameStatus');
                if (st) st.textContent = '저장됨';
            }
        }).catch(function() {});
}

function archAskAppName() {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    var st = $('archAppNameStatus');
    if (st) st.textContent = 'Agent에게 질의 중...';
    fetch('/api/arch/app-name?space_id=' + encodeURIComponent(sid) + '&ask_agent=true')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok && d.app_name) {
                var inp = $('archAppNameInput');
                if (inp) inp.value = d.app_name;
                if (st) st.textContent = 'Agent 제안: "' + d.app_name + '"' + (d.saved_name ? ' (기존: ' + d.saved_name + ')' : '');
            } else {
                if (st) st.textContent = '질의 실패: ' + (d.error || '');
            }
        }).catch(function(e) { if (st) st.textContent = '오류: ' + e; });
}

function archSaveAppName() {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    var inp = $('archAppNameInput');
    var name = inp ? inp.value.trim() : '';
    if (!name) return;
    var st = $('archAppNameStatus');
    fetch('/api/arch/app-name', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: sid, app_name: name})
    }).then(function(r) { return r.json(); })
      .then(function(d) {
          if (d.ok) {
              if (st) st.textContent = '"' + name + '" 저장 완료. 다음 분석부터 적용됩니다.';
          } else {
              if (st) st.textContent = '저장 실패: ' + (d.error || '');
          }
      }).catch(function(e) { if (st) st.textContent = '오류: ' + e; });
}

function _archSetRunning() {
    $('btnArchFull').disabled = true; $('btnArchFull').innerHTML = '<span class="arch-spinner"></span> 분석 중...';
    $('btnArchResume').style.display = 'none';
    if ($('btnArchDelete')) $('btnArchDelete').disabled = true;
    if ($('btnArchRec')) $('btnArchRec').disabled = true;
}
function _archSetIdle() {
    $('btnArchFull').disabled = false; $('btnArchFull').innerHTML = '분석';
    if ($('btnArchDelete')) $('btnArchDelete').disabled = false;
    if ($('btnArchRec')) $('btnArchRec').disabled = false;
}
function _archPollUntilDone() {
    var sid = SELECTED || '';
    var iv = setInterval(function() {
        fetch('/api/arch/status?space_id=' + encodeURIComponent(sid))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.current_layer) {
                    _archShowProgress(d.current_layer + ' 분석 중...');
                }
                if (d.status === 'complete' || d.status === 'idle') {
                    clearInterval(iv);
                    ARCH.discovering = false;
                    _archSetIdle(); _archHideProgress();
                    fetch('/api/arch/topology?space_id=' + encodeURIComponent(sid))
                        .then(function(r2) { return r2.json(); })
                        .then(function(t) {
                            if (t.ok && t.graph) {
                                ARCH.analysis = t;
                                ARCH.nodes = t.graph.nodes || []; ARCH.edges = t.graph.edges || [];
                                _enrichNodes(ARCH.nodes);
                                $('archTopoHdr').style.display = 'flex'; $('archTopoEmpty').style.display = 'none';
                                archNavigateTo('L1');
                            }
                        });
                } else if (d.status === 'error' || d.status === 'interrupted') {
                    clearInterval(iv);
                    ARCH.discovering = false;
                    _archSetIdle(); _archHideProgress();
                    if (d.status === 'interrupted') $('btnArchResume').style.display = '';
                }
            }).catch(function() {});
    }, 10000);
}
function archDiscover(resume) {
    archLoadLayout();
    var model = $('archModel').value;
    _archSetRunning();
    ARCH.discovering = true;

    var chat = $('archChatPanel');
    if (!resume) {
        ARCH.nodes = []; ARCH.edges = []; ARCH.recs = null; ARCH.analysis = null;
        ARCH.customPos = {}; ARCH.customProps = {};
        ARCH.mode = 'multi_app';
        ARCH.nav = {level: 'L1', selectedApp: null, selectedService: null, history: []};
        chat.innerHTML = '';
        $('archTopoHdr').style.display = 'none'; $('archTopoEmpty').style.display = '';
        $('archRecSection').style.display = 'none';
        $('archDescContent').innerHTML = '<span style="color:#64748b">분석 중...</span>';
    }
    // Initialize summary mode
    var summaryPanel = $('archChatSummary');
    if (summaryPanel) summaryPanel.innerHTML = '';
    ARCH.chatMode = 'summary';
    archSetChatMode('summary');
    var toggle = $('archChatToggle');
    if (toggle) toggle.style.display = '';

    _archShowProgress(resume ? 'Checkpoint에서 이어서 분석 중...' : 'Topology 분석 시작');
    _archUpdateProgress('init', resume ? 'Checkpoint에서 이어서 분석 중...' : 'Topology 분석 시작');
    _archAddWaiting(chat, resume ? 'Checkpoint에서 이어서 분석 중...' : 'DevOps Agent Session 생성 중...');

    var chatWrap = chat.parentElement;
    var sid = SELECTED || '';
    var url = '/api/arch/discover/stream?model=' + encodeURIComponent(model) + '&space_id=' + encodeURIComponent(sid);
    if (resume) url += '&resume=1';

    if (ARCH._es) { try { ARCH._es.close(); } catch(e){} }
    var es = new EventSource(url);
    ARCH._es = es;
    es.onmessage = function(ev) {
        var d = JSON.parse(ev.data);
        var agent = d.agent || d.phase || '';

        if (d.type === 'mode') {
            ARCH.mode = d.mode || 'multi_app';
            if (ARCH.mode === 'single_app') {
                ARCH.nav.selectedApp = d.app_name || 'Application';
            }
        }
        if (d.type === 'phase_start') {
            _archRemoveWaiting(chat);
            _archRemoveSummaryThinking();
            if (agent !== 'init') {
                _archAddAgentHeader(chat, agent, d.label || d.description || agent);
            }
            var phaseLabel = d.label || d.description || agent;
            if (agent === 'init') {
                _archUpdateProgress('init', '분석 준비 중...');
                _archUpdateProgressSub('Agent Session 생성 및 사전 정보 수집 중...');
                _archAddSummaryEntry('분석 준비 — Session 생성 중', 'phase');
            } else if (agent === 'Q1' || agent === 'L1') {
                _archUpdateProgress('Q1', 'L1 App 식별 중...');
                _archUpdateProgressSub('Agent가 System의 App 구성을 파악하고 있습니다');
                _archAddSummaryEntry(phaseLabel + ' 시작', 'phase');
            } else if (agent === 'naming') {
                _archUpdateProgress('naming', '앱 이름 협상 중...');
                _archUpdateProgressSub('Agent와 앱 이름을 합의하고 있습니다');
                _archAddSummaryEntry('앱 이름 협상', 'phase');
            } else if (agent === 'Q2' || agent === 'L2') {
                if (ARCH.mode === 'single_app') {
                    _archUpdateProgress('Q2', 'L1 서비스 토폴로지 분석 중...');
                    _archUpdateProgressSub('서비스 간 연결 관계를 분석하고 있습니다');
                } else {
                    _archUpdateProgress('Q2', 'L2 Service 분석 중...');
                    _archUpdateProgressSub('각 App의 Component와 연결 관계를 분석하고 있습니다');
                }
                _archAddSummaryEntry(phaseLabel + ' 시작', 'phase');
            } else if (agent === 'Q2_K8S') {
                if (ARCH.mode === 'single_app') {
                    _archUpdateProgress('Q2_K8S', 'L2 K8s 리소스 상세 수집 중...');
                    _archUpdateProgressSub('K8s Workload의 상세 리소스를 수집합니다');
                } else {
                    _archUpdateProgress('Q2_K8S', 'K8s Resource 보충 중...');
                    _archUpdateProgressSub('K8s Workload Type과 Resource를 상세 수집합니다');
                }
                _archAddSummaryEntry(phaseLabel + ' 시작', 'phase');
            } else {
                _archAddSummaryEntry(phaseLabel + ' 시작', 'phase');
            }
        }
        if (d.type === 'agent_thinking') {
            _archUpdateThinking(chat, agent, d.thought);
            _archUpdateProgressSub(d.thought ? d.thought.substring(0, 60) + '...' : '분석 진행 중...');
            _archUpdateSummaryThinking(d.thought ? d.thought.substring(0, 60) + '...' : '분석 진행 중...');
        }
        if (d.type === 'agent_question') {
            _archRemoveThinking(chat);
            _archRemoveSummaryThinking();
            _archAddQuestion(chat, agent, d.question, d.turn);
            var qLen = (d.question || '').length;
            var qLabel = (ARCH_AGENT_LABELS[agent] || agent) + ' Turn ' + d.turn + ' 질문 전송';
            if (qLen > 0) qLabel += ' (' + _formatLen(qLen) + ')';
            if (d.app_name) qLabel += ' — ' + d.app_name;
            _archAddSummaryEntry(qLabel, 'question');
            if (d.app_name) {
                _archHighlightActiveApp(d.app_name);
                _archUpdateProgressSub(d.app_name + ' 분석 중...');
            }
        }
        if (d.type === 'agent_answer') {
            _archAddAnswer(chat, agent, d.answer, d.tool_calls || []);
            chatWrap.scrollTop = chatWrap.scrollHeight;
            var toolCount = (d.tool_calls || []).length;
            var aLabel = 'Agent 응답 수신';
            if (toolCount) aLabel += ' — 도구 ' + toolCount + '개 사용';
            _archAddSummaryEntry(aLabel, 'answer');
            if (toolCount) {
                _archUpdateProgressSub('Agent 도구 ' + toolCount + '개 실행 완료');
            }
        }
        if (d.type === 'agent_evaluation') {
            _archRemoveThinking(chat);
            _archRemoveSummaryThinking();
            _archAddEvaluation(chat, agent, d.score, d.verdict);
            var evalType = d.verdict === 'pass' ? 'eval' : 'eval-fail';
            _archAddSummaryEntry('자체 평가: ' + d.score + '/100 ' + (d.verdict === 'pass' ? '통과' : '보완 필요'), evalType);
        }
        if (d.type === 'phase_complete') {
            _archRemoveThinking(chat);
            _archRemoveSummaryThinking();
        }
        if (d.type === 'app_list') {
            _archRemoveWaiting(chat); _archRemoveThinking(chat);
            _archRemoveSummaryThinking();
            _archShowAppSelection(chat, d.apps);
            var sp2 = $('archChatSummary');
            if (sp2) _archShowAppSelection(sp2, d.apps);
            _archUpdateProgress('app_select', d.apps.length + '개 App 발견 — 선택 대기');
            _archUpdateProgressSub('분석할 App을 선택하세요');
            _archAddSummaryEntry(d.apps.length + '개 App 발견 — 선택 대기', 'app_list');
        }
        if (d.type === 'app_selection_confirmed') {
            _archRemoveAppSelection();
            _archShowAppStatusBar(chat, d.selected, d.unselected || []);
            _archAddWaiting(chat, d.selected.length + '개 App Service 상세 분석 중...');
            _archUpdateProgress('Q2', d.selected.length + '개 App Service 분석 시작');
            _archUpdateProgressSub('선택된 App의 Component를 분석합니다');
            _archAddSummaryEntry(d.selected.length + '개 App 선택 — Service 분석 시작', 'app_confirm');
            archSetChatMode('summary');
        }
        if (d.type === 'layer_complete') {
            _archRemoveThinking(chat); _archRemoveWaiting(chat);
            _archRemoveSummaryThinking();
            var a2 = d.analysis || {};
            ARCH.analysis = a2;
            var graph = a2.graph || {};
            ARCH.nodes = graph.nodes || []; ARCH.edges = graph.edges || [];
            _enrichNodes(ARCH.nodes);
            $('archTopoHdr').style.display = 'flex'; $('archTopoEmpty').style.display = 'none';
            var navH = ARCH.nav.history.slice();
            archNavigateTo(ARCH.nav.level, {app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});
            ARCH.nav.history = navH;
            if (!d.restored) {
                _archAddLayerDone(chat, d.layer);
                _archAddSummaryEntry((d.layer || '') + ' 분석 완료', 'done');
            }
        }
        if (d.type === 'complete') {
            _archRemoveWaiting(chat); _archRemoveThinking(chat); es.close();
            _archRemoveSummaryThinking();
            var a3 = d.analysis || {};
            var graph2 = a3.graph || {};
            var newNodes = graph2.nodes || [];
            // Only update if complete event carries data; otherwise keep layer_complete state
            if (newNodes.length) {
                ARCH.analysis = a3;
                ARCH.nodes = newNodes; ARCH.edges = graph2.edges || [];
                _enrichNodes(ARCH.nodes);
            }
            ARCH.discovering = false;
            if (ARCH.nodes.length) {
                $('archTopoHdr').style.display = 'flex'; $('archTopoEmpty').style.display = 'none';
            }
            var navH2 = ARCH.nav.history.slice();
            archNavigateTo(ARCH.nav.level, {app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});
            ARCH.nav.history = navH2;
            _archAddSummaryEntry('분석 완료', 'done');
            archSetChatMode('detail');
            _archSetIdle();
            $('btnArchResume').style.display = 'none';
            archLoadVersions();
        }
        if (d.type === 'error') {
            _archRemoveWaiting(chat); _archRemoveThinking(chat); es.close(); ARCH.discovering = false;
            _archRemoveSummaryThinking();
            _archAddError(chat, d.error);
            if (d.raw_answer) {
                _archAddAnswer(chat, 'Agent 원문', d.raw_answer, []);
            }
            _archAddSummaryEntry('오류: ' + (d.error || '알 수 없는 오류'), 'error');
            archSetChatMode('detail');
            _archHideProgress();
            _archSetIdle();
            _archShowResumeIfNeeded();
        }
    };
    es.onerror = function() {
        es.close(); ARCH._es = null;
        _archRemoveWaiting(chat); _archRemoveThinking(chat);
        ARCH.discovering = false;
        _archHideProgress();
        _archSetIdle();
        _archShowResumeIfNeeded();
    };
}

// ================================================================
// ARCH INIT (restore state on page load)
// ================================================================
// ================================================================
// VERSION MANAGEMENT
// ================================================================

function archLoadVersions() {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    fetch('/api/arch/versions?space_id=' + encodeURIComponent(sid))
        .then(function(r) { return r.json() })
        .then(function(d) {
            var sel = $('archVersionSelect');
            if (!sel) return;
            var cur = sel.value;
            sel.innerHTML = '<option value="">최신</option>';
            (d.versions || []).forEach(function(v) {
                var opt = document.createElement('option');
                opt.value = v.run_id;
                var dt = (v.created_at || '').replace('T', ' ').substring(0, 19);
                opt.textContent = dt + ' (' + (v.system_name || '분석') + ')';
                sel.appendChild(opt);
            });
            sel.value = cur;
        })
        .catch(function() {});
}

function archLoadVersion(runId) {
    var url = runId
        ? '/api/arch/versions/' + encodeURIComponent(runId)
        : '/api/arch/topology?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' ? SELECTED : '');
    fetch(url).then(function(r) { return r.json() }).then(function(t) {
        if (!t.ok) return;
        var nodes = t.graph ? t.graph.nodes : t.nodes || [];
        var edges = t.graph ? t.graph.edges : t.edges || [];
        if (!nodes.length) return;
        ARCH.analysis = t;
        ARCH.nodes = nodes;
        ARCH.edges = edges;
        _enrichNodes(ARCH.nodes);
        // Detect mode: single-app if only one non-boundary group
        var _mg = {};
        nodes.forEach(function(n) { if (n.group && n.service_type !== 'boundary') _mg[n.group] = true; });
        var mainGroupCount = Object.keys(_mg).length;
        ARCH.mode = (mainGroupCount <= 1) ? 'single_app' : 'multi_app';
        var appName = (mainGroupCount === 1) ? Object.keys(_mg)[0] : null;
        ARCH.nav = {level: 'L1', selectedApp: appName, selectedService: null, history: []};
        ARCH.l1AppFilter = {}; ARCH.l2ConnectedApps = {}; ARCH._l2ConnectedForApp = null;
        $('archTopoHdr').style.display = 'flex'; $('archTopoEmpty').style.display = 'none';
        archNavigateTo('L1');
        archRenderDesc();
        archRestoreInterviews(t.conversations);
    }).catch(function(e) { console.error('archLoadVersion error', e); });
}

function archRestoreInterviews(conversations) {
    var panel = $('archChatPanel');
    if (!panel || !conversations) return;
    panel.innerHTML = '';
    ARCH.chatMode = 'detail';
    archSetChatMode('detail');
    var tgl = $('archChatToggle'); if (tgl) tgl.style.display = '';
    var layers = ['Q1', 'Q2', 'Q2_K8S', 'L1', 'L2', 'L3'];
    layers.forEach(function(layer) {
        var turns = conversations[layer];
        if (!turns || !turns.length) return;
        var label = ARCH_AGENT_LABELS[layer] || layer;
        _archAddAgentHeader(panel, layer, label);
        turns.forEach(function(t) {
            _archAddQuestion(panel, layer, t.question, t.turn);
            _archAddAnswer(panel, layer, t.answer, t.tool_calls || []);
        });
    });
}

function archReset() {
    if (ARCH._es) { try { ARCH._es.close(); } catch(e){} ARCH._es = null; }
    ARCH.analysis = null; ARCH.nodes = []; ARCH.edges = [];
    ARCH.nav = {level: 'L1', selectedApp: null, selectedService: null, history: []};
    ARCH.discovering = false;
    ARCH.l1AppFilter = {}; ARCH.l2ConnectedApps = {}; ARCH._l2ConnectedForApp = null;
    ARCH.chatMode = 'summary';
    ['archSvgL1','archSvgL2Unified','archSvgL2App','archSvgL2Infra'].forEach(function(id){var el=$(id);if(el)el.innerHTML='';});
    var hdr = $('archTopoHdr'); if (hdr) hdr.style.display = 'none';
    var emp = $('archTopoEmpty'); if (emp) { emp.style.display = ''; emp.innerHTML = '"분석 시작" 버튼으로 Topology를 발견하세요'; }
    var panel = $('archChatPanel'); if (panel) panel.innerHTML = '';
    var sp = $('archChatSummary'); if (sp) sp.innerHTML = '';
    var tgl = $('archChatToggle'); if (tgl) tgl.style.display = 'none';
    var l1f = $('archL1AppFilter'); if (l1f) l1f.style.display = 'none';
    var l2f = $('archL2ConnectedFilter'); if (l2f) l2f.style.display = 'none';
    _archSetIdle();
}

// ================================================================
// ARCH INIT (restore state on space select)
// ================================================================
function archInit() {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    if (!sid) return;
    ARCH.analysis = null; ARCH.nodes = []; ARCH.edges = [];
    ARCH.nav = {level: 'L1', selectedApp: null, selectedService: null, history: []};
    ARCH.discovering = false;
    ARCH.l1AppFilter = {}; ARCH.l2ConnectedApps = {}; ARCH._l2ConnectedForApp = null;
    ARCH.chatMode = 'summary';
    var hdr = $('archTopoHdr'); if (hdr) hdr.style.display = 'none';
    var emp = $('archTopoEmpty'); if (emp) emp.style.display = '';
    _archSetIdle();
    $('btnArchResume').style.display = 'none';
    var panel = $('archChatPanel'); if (panel) panel.innerHTML = '';
    var sp = $('archChatSummary'); if (sp) sp.innerHTML = '';
    var tgl = $('archChatToggle'); if (tgl) tgl.style.display = 'none';
    archLoadVersions();
    archLoadAppName();
    fetch('/api/arch/status?space_id=' + encodeURIComponent(sid)).then(function(r) { return r.json() }).then(function(d) {
        if (!d.ok) return;
        if (d.has_analysis) {
            fetch('/api/arch/topology?space_id=' + encodeURIComponent(sid)).then(function(r) { return r.json() }).then(function(t) {
                if (t.ok) {
                    var nodes = t.graph ? t.graph.nodes : t.nodes || [];
                    var edges = t.graph ? t.graph.edges : t.edges || [];
                    if (nodes.length) {
                        ARCH.analysis = t;
                        ARCH.nodes = nodes;
                        ARCH.edges = edges;
                        _enrichNodes(ARCH.nodes);
                        var _mg = {};
                        nodes.forEach(function(n) { if (n.group && n.service_type !== 'boundary') _mg[n.group] = true; });
                        ARCH.mode = (Object.keys(_mg).length <= 1) ? 'single_app' : 'multi_app';
                        if (ARCH.mode === 'single_app') {
                            ARCH.nav.selectedApp = Object.keys(_mg)[0] || t.app_name || 'Application';
                        }
                        $('archTopoHdr').style.display = 'flex'; $('archTopoEmpty').style.display = 'none';
                        archNavigateTo('L1');
                        archRestoreInterviews(t.conversations);
                        _svcRestoreHistory(sid);
                    }
                }
            });
        }
        if (d.status === 'running') {
            _archSetRunning();
            ARCH.discovering = true;
            _archPollUntilDone();
        } else if (d.status === 'interrupted' || d.has_checkpoint) {
            $('btnArchResume').style.display = '';
        }
    }).catch(function() {});
}
window.addEventListener('beforeunload', function() {
    if (ARCH._es) { try { ARCH._es.close(); } catch(e){} ARCH._es = null; }
});

// ── K8s View ──
ARCH.viewMode = ARCH.viewMode || 'topo';

var _K8S_KIND_ICON = {
    'Deployment': 'k8s-deploy', 'StatefulSet': 'k8s-sts', 'DaemonSet': 'k8s-ds',
    'CronJob': 'k8s-cj', 'Job': 'k8s-job', 'ServiceAccount': 'k8s-sa',
    'Secret': 'k8s-secret', 'ConfigMap': 'k8s-cm', 'PersistentVolumeClaim': 'k8s-pvc',
    'NetworkPolicy': 'k8s-netpol', 'Ingress': 'k8s-ing', 'HPA': 'k8s-hpa',
    'PDB': 'k8s-pdb', 'Namespace': 'k8s-ns'
};

function _k8sIcon(kind, size) {
    var s = size || 20;
    var key = _K8S_KIND_ICON[kind] || 'k8s-deploy';
    var src = _ICON_CACHE[key] || ARCH_ICON_PATHS[key] || '';
    if (!src) return '';
    return '<img src="' + esc(src) + '" width="' + s + '" height="' + s + '" style="vertical-align:middle;margin-right:4px">';
}

function archSwitchView(mode) {
    ARCH.viewMode = mode;
    var topoIds = ['archViewL1', 'archViewL2', 'archViewL3', 'archL1AppFilter', 'archL2ConnectedFilter'];
    var k8sEl = $('archK8sView');
    var seqEl = $('archSeqView');
    var msgEl = $('archMsgView');
    var svcEl = $('archServiceView');
    var tabs = document.querySelectorAll('#archViewTabs button');
    tabs.forEach(function(b) {
        var isActive = b.dataset.view === mode;
        b.style.background = isActive ? '#1e293b' : '#0f172a';
        b.style.color = isActive ? '#e2e8f0' : '#64748b';
        b.className = isActive ? 'arch-tab-active' : '';
    });
    if (mode === 'k8s') {
        topoIds.forEach(function(id) { var el = $(id); if (el) el.style.display = 'none'; });
        if (seqEl) seqEl.style.display = 'none';
        if (msgEl) msgEl.style.display = 'none';
        if (svcEl) svcEl.style.display = 'none';
        if (k8sEl) { k8sEl.style.display = ''; archLoadK8sView(); }
    } else if (mode === 'seq') {
        topoIds.forEach(function(id) { var el = $(id); if (el) el.style.display = 'none'; });
        if (k8sEl) k8sEl.style.display = 'none';
        if (msgEl) msgEl.style.display = 'none';
        if (svcEl) svcEl.style.display = 'none';
        if (seqEl) { seqEl.style.display = ''; archRenderSeqView(); }
    } else if (mode === 'msg') {
        topoIds.forEach(function(id) { var el = $(id); if (el) el.style.display = 'none'; });
        if (k8sEl) k8sEl.style.display = 'none';
        if (seqEl) seqEl.style.display = 'none';
        if (svcEl) svcEl.style.display = 'none';
        if (msgEl) { msgEl.style.display = ''; archRenderMsgView(); }
    } else if (mode.indexOf('svc-') === 0) {
        topoIds.forEach(function(id) { var el = $(id); if (el) el.style.display = 'none'; });
        if (k8sEl) k8sEl.style.display = 'none';
        if (seqEl) seqEl.style.display = 'none';
        if (msgEl) msgEl.style.display = 'none';
        if (svcEl) svcEl.style.display = '';
        var dtype = mode.replace('svc-', '');
        ARCH.serviceAnalysis.activeTab = dtype;
        _svcRenderDiagram(dtype);
    } else {
        if (k8sEl) k8sEl.style.display = 'none';
        if (seqEl) seqEl.style.display = 'none';
        if (msgEl) msgEl.style.display = 'none';
        if (svcEl) svcEl.style.display = 'none';
        archNavigateTo(ARCH.nav.level, {app: ARCH.nav.selectedApp, service: ARCH.nav.selectedService});
    }
}

// ═══════════════════════════════════════════════════════════
// MESSAGE PATTERN VIEW
// ═══════════════════════════════════════════════════════════

var _MSG_VIEW_MODE = 'matrix';

function archRenderMsgView() {
    var edges = ARCH.edges || [];
    var nodes = ARCH.nodes || [];
    if (!edges.length) {
        var c = $('archMsgContent');
        if (c) c.innerHTML = '<div style="text-align:center;padding:40px;color:#475569;font-size:.7rem">분석 데이터 없음 — 토폴로지 분석을 먼저 실행하세요</div>';
        return;
    }
    _renderMsgChips();
    if (_MSG_VIEW_MODE === 'matrix') _renderMsgMatrix(edges, nodes);
    else _renderMsgCards(edges, nodes);
}

function _renderMsgChips() {
    var el = $('archMsgViewChips');
    if (!el) return;
    el.innerHTML = '';
    ['matrix', 'cards'].forEach(function(m) {
        var btn = document.createElement('button');
        btn.textContent = m === 'matrix' ? '매트릭스' : '카드';
        btn.style.cssText = 'padding:2px 8px;border-radius:4px;border:1px solid #475569;font-size:.55rem;cursor:pointer;' +
            (m === _MSG_VIEW_MODE ? 'background:#334155;color:#e2e8f0' : 'background:#0f172a;color:#64748b');
        btn.onclick = function() { _MSG_VIEW_MODE = m; archRenderMsgView(); };
        el.appendChild(btn);
    });
}

function _classifyProtocol(edge) {
    var p = (edge.protocol || '').toLowerCase();
    if (p.indexOf('grpc') >= 0) return {type: 'sync', proto: 'gRPC', color: '#22c55e'};
    if (p.indexOf('tcp') >= 0 || p.indexOf('redis') >= 0) return {type: 'sync', proto: 'TCP', color: '#eab308'};
    if (p.indexOf('sqs') >= 0 || p.indexOf('sns') >= 0 || p.indexOf('event') >= 0 || p.indexOf('kafka') >= 0)
        return {type: 'async', proto: p.toUpperCase(), color: '#a855f7'};
    return {type: 'sync', proto: 'HTTP', color: '#38bdf8'};
}

function _renderMsgMatrix(edges, nodes) {
    var container = $('archMsgContent');
    if (!container) return;

    var services = [];
    var svcSet = {};
    edges.forEach(function(e) {
        if (!svcSet[e.source]) { svcSet[e.source] = true; services.push(e.source); }
        if (!svcSet[e.target]) { svcSet[e.target] = true; services.push(e.target); }
    });

    var matrix = {};
    edges.forEach(function(e) {
        var key = e.source + '→' + e.target;
        var cls = _classifyProtocol(e);
        matrix[key] = {proto: cls.proto, color: cls.color, type: cls.type, port: e.port || '', paths: (e.paths || []).join(', '), description: e.description || ''};
    });

    var html = '<div style="overflow-x:auto">';
    html += '<table style="border-collapse:collapse;font-size:.6rem;width:100%">';
    html += '<thead><tr><th style="padding:4px 6px;border:1px solid #1e293b;background:#0f172a;color:#64748b;min-width:80px"></th>';
    services.forEach(function(s) {
        html += '<th style="padding:4px 6px;border:1px solid #1e293b;background:#0f172a;color:#94a3b8;white-space:nowrap;writing-mode:vertical-lr;transform:rotate(180deg);max-width:30px">' + s + '</th>';
    });
    html += '</tr></thead><tbody>';

    services.forEach(function(src) {
        html += '<tr>';
        html += '<td style="padding:4px 6px;border:1px solid #1e293b;background:#0f172a;color:#94a3b8;font-weight:600;white-space:nowrap">' + src + '</td>';
        services.forEach(function(tgt) {
            var key = src + '→' + tgt;
            var cell = matrix[key];
            if (cell) {
                html += '<td style="padding:3px 4px;border:1px solid #1e293b;background:#1e293b;text-align:center" title="' +
                    src + ' → ' + tgt + '\n' + cell.proto + (cell.port ? ':' + cell.port : '') + '\n' + cell.paths + '\n' + cell.description + '">';
                html += '<span style="display:inline-block;padding:1px 4px;border-radius:3px;font-size:.5rem;font-weight:600;background:' + cell.color + '20;color:' + cell.color + ';border:1px solid ' + cell.color + '40">' + cell.proto + '</span>';
                if (cell.port) html += '<div style="font-size:.45rem;color:#64748b;margin-top:1px">:' + cell.port + '</div>';
                html += '</td>';
            } else if (src === tgt) {
                html += '<td style="padding:3px;border:1px solid #1e293b;background:#0a0f1a;text-align:center"><span style="color:#334155">—</span></td>';
            } else {
                html += '<td style="padding:3px;border:1px solid #1e293b;background:#0f172a"></td>';
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table></div>';

    var legend = '<div style="display:flex;gap:12px;padding:8px 4px;flex-wrap:wrap">';
    legend += '<span style="font-size:.55rem;color:#64748b;font-weight:600">범례:</span>';
    [{c:'#38bdf8',l:'HTTP (동기)'},{c:'#22c55e',l:'gRPC (동기)'},{c:'#eab308',l:'TCP (동기)'},{c:'#a855f7',l:'비동기 (SQS/SNS/Event)'}].forEach(function(item) {
        legend += '<span style="font-size:.5rem;display:flex;align-items:center;gap:3px"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:' + item.c + '30;border:1px solid ' + item.c + '60"></span><span style="color:#94a3b8">' + item.l + '</span></span>';
    });
    legend += '</div>';

    container.innerHTML = legend + html;
}

function _renderMsgCards(edges, nodes) {
    var container = $('archMsgContent');
    if (!container) return;

    var byService = {};
    edges.forEach(function(e) {
        if (!byService[e.source]) byService[e.source] = {outbound: [], inbound: []};
        if (!byService[e.target]) byService[e.target] = {outbound: [], inbound: []};
        var cls = _classifyProtocol(e);
        var info = {target: e.target, source: e.source, proto: cls.proto, color: cls.color, type: cls.type, port: e.port || '', paths: (e.paths || []).join(', '), description: e.description || ''};
        byService[e.source].outbound.push(info);
        byService[e.target].inbound.push(Object.assign({}, info));
    });

    var html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px">';
    Object.keys(byService).sort().forEach(function(svc) {
        var d = byService[svc];
        html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:8px">';
        html += '<div style="font-size:.65rem;font-weight:700;color:#e2e8f0;margin-bottom:6px;border-bottom:1px solid #334155;padding-bottom:4px">' + svc + '</div>';

        if (d.outbound.length) {
            html += '<div style="font-size:.5rem;color:#64748b;font-weight:600;margin-bottom:3px">OUTBOUND (' + d.outbound.length + ')</div>';
            d.outbound.forEach(function(o) {
                html += '<div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:.55rem">';
                html += '<span style="padding:1px 3px;border-radius:2px;font-size:.45rem;font-weight:600;background:' + o.color + '20;color:' + o.color + '">' + o.proto + '</span>';
                html += '<span style="color:#94a3b8">→ ' + o.target + '</span>';
                if (o.port) html += '<span style="color:#475569">:' + o.port + '</span>';
                if (o.paths) html += '<span style="color:#64748b;font-style:italic;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100px" title="' + o.paths + '">' + o.paths + '</span>';
                html += '</div>';
            });
        }
        if (d.inbound.length) {
            html += '<div style="font-size:.5rem;color:#64748b;font-weight:600;margin:4px 0 3px">INBOUND (' + d.inbound.length + ')</div>';
            d.inbound.forEach(function(o) {
                html += '<div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:.55rem">';
                html += '<span style="padding:1px 3px;border-radius:2px;font-size:.45rem;font-weight:600;background:' + o.color + '20;color:' + o.color + '">' + o.proto + '</span>';
                html += '<span style="color:#94a3b8">← ' + o.source + '</span>';
                if (o.port) html += '<span style="color:#475569">:' + o.port + '</span>';
                html += '</div>';
            });
        }
        html += '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════

function archLoadK8sView() {
    var el = $('archK8sView');
    if (!el) return;
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    fetch('/api/arch/k8s-view?space_id=' + encodeURIComponent(sid))
        .then(function(r) { return r.json() })
        .then(function(d) {
            if (!d.ok || !d.namespaces || !d.namespaces.length) return;
            _renderK8sTopo(d.namespaces);
        })
        .catch(function(e) {
            console.error('K8s View load error:', e);
        });
}

function _renderK8sTopo(namespaces) {
    var nodes = [], edges = [], seen = {};

    namespaces.forEach(function(ns) {
        // Namespace node
        var nsName = 'ns-' + ns.name;
        nodes.push({name: nsName, kind: 'Kubernetes Namespace', service_type: 'platform',
            group: ns.name, icon_key: 'k8s-ns', labels: {role: 'Namespace: ' + ns.name}, ports: []});
        seen[nsName] = true;

        (ns.workloads || []).forEach(function(w) {
            var wName = w.name;
            var kindMap = {Deployment:'k8s-deploy', StatefulSet:'k8s-sts', DaemonSet:'k8s-ds', CronJob:'k8s-cj', Job:'k8s-job'};
            var meta = [];
            if (w.replicas) meta.push(w.replicas + ' replicas');
            if (w.service) meta.push(w.service.type || 'ClusterIP');
            if (w.hpa) meta.push('HPA');

            nodes.push({name: wName, kind: 'Amazon EKS ' + (w.kind || 'Deployment'),
                service_type: 'app', group: ns.name,
                icon_key: kindMap[w.kind] || 'k8s-deploy',
                labels: {role: meta.join(' · ') || w.kind || 'Deployment'},
                ports: (w.containers || []).reduce(function(a, c) { return a.concat(c.ports || []); }, [])});
            seen[wName] = true;

            // Workload → SA edge
            if (w.service_account && w.service_account !== 'default' && w.service_account !== null) {
                var saName = 'sa-' + ns.name + '-' + w.service_account;
                if (!seen[saName]) {
                    var irsaRole = '';
                    (ns.service_accounts || []).forEach(function(sa) {
                        if (sa.name === w.service_account && sa.irsa_role_arn) irsaRole = 'IRSA';
                    });
                    nodes.push({name: saName, kind: 'Kubernetes ServiceAccount',
                        service_type: 'platform', group: ns.name, icon_key: 'k8s-sa',
                        labels: {role: irsaRole ? 'IRSA ServiceAccount' : 'ServiceAccount'}, ports: []});
                    seen[saName] = true;
                }
                edges.push({source: wName, target: saName, description: 'uses SA'});
            }

            // Workload → Secret/ConfigMap edges (env_from)
            (w.containers || []).forEach(function(c) {
                (c.env_from || []).forEach(function(ref) {
                    var parts = ref.split('/');
                    if (parts.length !== 2) return;
                    var refType = parts[0], refName = parts[1];
                    var nodeName = refType + '-' + ns.name + '-' + refName;
                    if (!seen[nodeName]) {
                        var ik = refType === 'secret' ? 'k8s-secret' : 'k8s-cm';
                        var kd = refType === 'secret' ? 'Kubernetes Secret' : 'Kubernetes ConfigMap';
                        var keysList = '';
                        if (refType === 'secret') {
                            (ns.secrets || []).forEach(function(s) {
                                if (s.name === refName) keysList = (s.keys || []).join(', ');
                            });
                        } else {
                            (ns.configmaps || []).forEach(function(cm) {
                                if (cm.name === refName) keysList = (cm.keys || []).join(', ');
                            });
                        }
                        nodes.push({name: nodeName, kind: kd, service_type: 'platform',
                            group: ns.name, icon_key: ik,
                            labels: {role: keysList ? 'keys: ' + keysList : refType}, ports: []});
                        seen[nodeName] = true;
                    }
                    edges.push({source: wName, target: nodeName, description: 'env from ' + refType});
                });
            });

            // Workload → PVC edges (volumes)
            (w.volumes || []).forEach(function(v) {
                if (v.type === 'PVC' && v.claim) {
                    var pvcName = 'pvc-' + ns.name + '-' + v.claim;
                    if (!seen[pvcName]) {
                        nodes.push({name: pvcName, kind: 'Kubernetes PVC',
                            service_type: 'db', group: ns.name, icon_key: 'k8s-pvc',
                            labels: {role: 'PersistentVolumeClaim'}, ports: []});
                        seen[pvcName] = true;
                    }
                    edges.push({source: wName, target: pvcName, description: 'mount'});
                }
            });
        });

        // Ingress → workload edges
        (ns.ingresses || []).forEach(function(ing) {
            var ingName = 'ing-' + ns.name + '-' + ing.name;
            nodes.push({name: ingName, kind: 'Kubernetes Ingress', service_type: 'gateway',
                group: ns.name, icon_key: 'k8s-ing',
                labels: {role: ing.tls ? 'Ingress (TLS)' : 'Ingress'}, ports: []});
            seen[ingName] = true;
            (ing.rules || []).forEach(function(r) {
                (r.paths || []).forEach(function(p) {
                    var backend = (p.backend || '').split(':')[0];
                    if (backend && seen[backend]) {
                        edges.push({source: ingName, target: backend, description: (r.host || '*') + (p.path || '/')});
                    }
                });
            });
        });

        // Add edges from ARCH.edges that connect K8s workloads in this namespace
        (ARCH.edges || []).forEach(function(e) {
            if (seen[e.source] && seen[e.target]) {
                var dup = edges.some(function(ex) { return ex.source === e.source && ex.target === e.target; });
                if (!dup) edges.push({source: e.source, target: e.target,
                    protocol: e.protocol, port: e.port, paths: e.paths,
                    description: e.description || ''});
            }
        });
    });

    _enrichNodes(nodes);
    _archRenderNodesTopo('archSvgK8s', nodes, edges, {});
}

// ================================================================
// RENDER: UML Sequence Diagram per workflow
// ================================================================

ARCH._seqSelectedWf = null;

function archRenderSeqView() {
    var a = ARCH.analysis;
    if (!a || !a.workflows || !a.workflows.length) return;
    var wfs = a.workflows.filter(function(w) { return w && w.hops && w.hops.length; });
    if (!wfs.length) return;

    var chipsEl = $('archSeqWfChips');
    if (chipsEl) {
        chipsEl.innerHTML = '';
        var WF_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#f97316'];
        wfs.forEach(function(wf, i) {
            var btn = document.createElement('button');
            btn.textContent = wf.name || 'Workflow ' + (i + 1);
            btn.dataset.idx = i;
            var color = WF_COLORS[i % WF_COLORS.length];
            var isActive = (ARCH._seqSelectedWf === i) || (ARCH._seqSelectedWf == null && i === 0);
            btn.style.cssText = 'padding:2px 10px;border-radius:12px;border:1px solid ' + color + ';font-size:.58rem;cursor:pointer;' +
                'background:' + (isActive ? color : 'transparent') + ';color:' + (isActive ? '#0f172a' : color);
            btn.onclick = function() {
                ARCH._seqSelectedWf = parseInt(this.dataset.idx);
                archRenderSeqView();
            };
            chipsEl.appendChild(btn);
        });
    }

    var idx = (ARCH._seqSelectedWf != null) ? ARCH._seqSelectedWf : 0;
    if (idx >= wfs.length) idx = 0;
    _renderSequenceDiagram('archSvgSeq', wfs[idx], idx);
}

function _seqTextWidth(str, fontSize) {
    return (str || '').length * fontSize * 0.52;
}

function _renderSequenceDiagram(svgId, wf, wfIndex) {
    var svg = $(svgId);
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var edgeMap = {};
    (ARCH.edges || []).forEach(function(e) {
        edgeMap[e.source + '→' + e.target] = e;
    });

    var nodeMap = {};
    (ARCH.nodes || []).forEach(function(n) { nodeMap[n.name] = n; });

    var participants = [];
    var pSet = {};
    wf.hops.forEach(function(hop) {
        var from = hop.from || hop.source || '';
        var to = hop.to || hop.target || '';
        if (from && !pSet[from]) { pSet[from] = true; participants.push(from); }
        if (to && !pSet[to]) { pSet[to] = true; participants.push(to); }
    });

    if (!participants.length) return;

    var WF_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#f97316'];
    var wfColor = WF_COLORS[(wfIndex || 0) % WF_COLORS.length];

    var pIdx = {};
    participants.forEach(function(name, i) { pIdx[name] = i; });

    var hopMeta = wf.hops.map(function(hop) {
        var from = hop.from || hop.source || '';
        var to = hop.to || hop.target || '';
        var edge = edgeMap[from + '→' + to];
        var protoLabel = '';
        if (edge) {
            var proto = edge.protocol || '';
            var port = edge.port || '';
            var paths = (edge.paths && edge.paths.length) ? edge.paths[0] : '';
            if (proto || port) protoLabel = (proto ? proto.toUpperCase() + ' ' : '') + (port ? ':' + port : '') + (paths ? ' ' + paths : '');
        }
        var desc = edge ? (edge.description || '') : '';
        var lines = 0;
        if (protoLabel) lines++;
        if (desc) lines++;
        return {from: from, to: to, edge: edge, protoLabel: protoLabel, desc: desc, lines: lines};
    });

    var FONT_PROTO = 8.5, FONT_DESC = 8, FONT_NAME = 9;
    var PAD_X = 50, PAD_TOP = 30, HEAD_H = 70, PAD_BOTTOM = 30;
    var MIN_COL_W = 140, LABEL_PAD = 40;

    var headerWidths = participants.map(function(name) {
        return Math.max(MIN_COL_W, _seqTextWidth(name, FONT_NAME) + 40);
    });

    var gapNeeded = [];
    for (var gi = 0; gi < participants.length; gi++) gapNeeded[gi] = MIN_COL_W;

    hopMeta.forEach(function(hm) {
        var fi = pIdx[hm.from], ti = pIdx[hm.to];
        if (fi == null || ti == null || fi === ti) return;
        var lo = Math.min(fi, ti), hi = Math.max(fi, ti);
        var span = hi - lo;
        var maxLabelW = 0;
        if (hm.protoLabel) maxLabelW = Math.max(maxLabelW, _seqTextWidth(hm.protoLabel, FONT_PROTO));
        if (hm.desc) maxLabelW = Math.max(maxLabelW, _seqTextWidth(hm.desc, FONT_DESC));
        var needed = (maxLabelW + LABEL_PAD) / span;
        for (var s = lo; s < hi; s++) {
            gapNeeded[s] = Math.max(gapNeeded[s], needed);
        }
    });

    headerWidths.forEach(function(hw, i) {
        gapNeeded[i] = Math.max(gapNeeded[i], hw);
    });

    var pPos = {};
    var cx = PAD_X;
    participants.forEach(function(name, i) {
        cx += gapNeeded[i] / 2;
        if (i > 0) cx += gapNeeded[i - 1] / 2;
        if (i === 0) cx = PAD_X + gapNeeded[0] / 2;
        pPos[name] = cx;
    });

    var cumX = PAD_X + gapNeeded[0] / 2;
    participants.forEach(function(name, i) {
        if (i === 0) { pPos[name] = cumX; return; }
        cumX += gapNeeded[i - 1] / 2 + gapNeeded[i] / 2;
        pPos[name] = cumX;
    });

    var MSG_LINE_H = 14;
    var LABEL_ABOVE = 30;
    var ARROW_BELOW = 16;
    var msgYs = [];
    var runY = PAD_TOP + 22 + HEAD_H;
    hopMeta.forEach(function(hm, hi) {
        var labelH = Math.max(1, hm.lines) * MSG_LINE_H;
        runY += LABEL_ABOVE + labelH;
        msgYs.push(runY);
        runY += ARROW_BELOW;
    });

    var W = cumX + gapNeeded[gapNeeded.length - 1] / 2 + PAD_X;
    var H = runY + PAD_BOTTOM;

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(350, H) + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="seqArrowFill" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="' + wfColor + '"/></marker>';
    svg.appendChild(defs);

    var titleEl = _svgE('text', {x: W / 2, y: PAD_TOP + 10, 'text-anchor': 'middle', fill: wfColor, 'font-size': '13', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
    titleEl.textContent = wf.name || '';
    svg.appendChild(titleEl);

    var headY = PAD_TOP + 22;

    participants.forEach(function(name, i) {
        var pcx = pPos[name];
        var node = nodeMap[name];
        var iconKey = node ? (node.icon_key || 'k8s-deploy') : 'aws-generic';

        var boxW = Math.max(100, _seqTextWidth(name, FONT_NAME) + 24);
        var boxH = 54;
        var isHighlight = ARCH.crossNav && ARCH.crossNav.highlight && ARCH.crossNav.entity === name;
        var pg = _svgE('g', {style: 'cursor:pointer', 'data-participant': name});
        pg.appendChild(_svgE('rect', {x: pcx - boxW / 2, y: headY, width: boxW, height: boxH, rx: 6,
            fill: isHighlight ? '#1e3a5f' : '#1e293b', stroke: isHighlight ? '#38bdf8' : '#334155', 'stroke-width': isHighlight ? '2' : '1'}));
        pg.appendChild(_archIconImg(iconKey, pcx - 12, headY + 4, 24, 24));
        var t = _svgE('text', {x: pcx, y: headY + boxH - 10, 'text-anchor': 'middle', fill: '#e2e8f0',
            'font-size': FONT_NAME, 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        t.textContent = name;
        pg.appendChild(t);
        svg.appendChild(pg);

        (function(eName, nd) {
            var tipObj = {name: eName, kind: nd ? nd.kind : '', group: nd ? nd.group : ''};
            _archBindTip(pg, tipObj);
        })(name, node);

        svg.appendChild(_svgE('line', {x1: pcx, y1: headY + boxH, x2: pcx, y2: H - PAD_BOTTOM + 10,
            stroke: '#334155', 'stroke-width': '1', 'stroke-dasharray': '4 3'}));
    });

    hopMeta.forEach(function(hm, hi) {
        var x1 = pPos[hm.from], x2 = pPos[hm.to];
        if (x1 == null || x2 == null) return;
        var y = msgYs[hi];
        var isSelf = (hm.from === hm.to);

        if (isSelf) {
            var selfW = 40, selfH = 20;
            svg.appendChild(_svgE('path', {
                d: 'M' + x1 + ' ' + y + ' L' + (x1 + selfW) + ' ' + y + ' L' + (x1 + selfW) + ' ' + (y + selfH) + ' L' + x1 + ' ' + (y + selfH),
                fill: 'none', stroke: wfColor, 'stroke-width': '1.5', 'marker-end': 'url(#seqArrowFill)'}));
        } else {
            svg.appendChild(_svgE('line', {x1: x1, y1: y, x2: x2, y2: y,
                stroke: wfColor, 'stroke-width': '1.5', 'marker-end': 'url(#seqArrowFill)'}));
        }

        var stepEl = _svgE('text', {x: Math.min(x1, x2) - 10, y: y + 4, 'text-anchor': 'end', fill: '#475569',
            'font-size': '9', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        stepEl.textContent = (hi + 1);
        svg.appendChild(stepEl);

        var mx = isSelf ? (x1 + 50) : (x1 + x2) / 2;
        var hasTwo = hm.protoLabel && hm.desc;
        if (!isSelf) {
            if (hm.protoLabel) {
                var protoY = hasTwo ? y - 18 : y - 10;
                svg.appendChild(_svgE('text', {x: mx, y: protoY, 'text-anchor': 'middle', fill: '#94a3b8',
                    'font-size': FONT_PROTO, 'font-weight': '500', 'font-family': 'SFMono-Regular,Menlo,monospace'})).textContent = hm.protoLabel;
            }
            if (hm.desc) {
                var descY = hasTwo ? y - 5 : y - 10;
                var maxDescChars = Math.max(20, Math.floor(Math.abs(x2 - x1) / (FONT_DESC * 0.48)));
                var descEl = _svgE('text', {x: mx, y: descY, 'text-anchor': 'middle', fill: '#64748b',
                    'font-size': FONT_DESC, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
                descEl.textContent = hm.desc.length > maxDescChars ? hm.desc.slice(0, maxDescChars) + '…' : hm.desc;
                svg.appendChild(descEl);
            }
        } else {
            if (hm.protoLabel) {
                svg.appendChild(_svgE('text', {x: mx, y: y + 10, 'text-anchor': 'start', fill: '#94a3b8',
                    'font-size': FONT_PROTO, 'font-weight': '500', 'font-family': 'SFMono-Regular,Menlo,monospace'})).textContent = hm.protoLabel;
            }
            if (hm.desc) {
                svg.appendChild(_svgE('text', {x: mx, y: y + (hm.protoLabel ? 22 : 10), 'text-anchor': 'start', fill: '#64748b',
                    'font-size': FONT_DESC, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'})).textContent = trun(hm.desc, 40);
            }
        }

        svg.appendChild(_svgE('line', {x1: x1, y1: y - 3, x2: x1, y2: y + 3, stroke: wfColor, 'stroke-width': '1', 'stroke-opacity': '0.4'}));
        svg.appendChild(_svgE('line', {x1: x2, y1: y - 3, x2: x2, y2: y + 3, stroke: wfColor, 'stroke-width': '1', 'stroke-opacity': '0.4'}));
    });
}

// ═══════════════════════════════════════════════════════════
// SERVICE CODE ANALYSIS (drill-down)
// ═══════════════════════════════════════════════════════════

ARCH.serviceAnalysis = {services: [], status: 'idle', diagrams: {}, activeTab: null};

function _svcShowScopeOverlay(centerName, connNodes) {
    var existing = document.getElementById('svcScopeOverlay');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.id = 'svcScopeOverlay';
    overlay.style.cssText = 'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:#1e293b;border:1px solid #475569;border-radius:8px;padding:16px;z-index:100;min-width:240px;box-shadow:0 8px 32px rgba(0,0,0,.5)';

    var html = '<div style="font-size:.65rem;font-weight:700;color:#e2e8f0;margin-bottom:10px">코드 분석 범위 선택</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">';

    html += '<label style="display:flex;align-items:center;gap:4px;padding:3px 8px;border-radius:4px;border:1px solid #38bdf8;background:#1e3a5f;font-size:.55rem;color:#38bdf8;cursor:default">' +
        '<input type="checkbox" checked disabled data-svc="' + centerName + '"> ' + centerName + ' (주)</label>';

    connNodes.forEach(function(n) {
        html += '<label style="display:flex;align-items:center;gap:4px;padding:3px 8px;border-radius:4px;border:1px solid #334155;background:#0f172a;font-size:.55rem;color:#e2e8f0;cursor:pointer">' +
            '<input type="checkbox" checked class="svcScopeCheck" data-svc="' + n.name + '"> ' + n.name + '</label>';
    });

    html += '</div>';
    html += '<div style="display:flex;gap:8px">';
    html += '<button id="svcScopeStart" style="padding:5px 14px;border-radius:4px;border:none;background:#38bdf8;color:#0f172a;font-size:.6rem;font-weight:700;cursor:pointer">분석 시작</button>';
    html += '<button id="svcScopeCancel" style="padding:5px 14px;border-radius:4px;border:1px solid #475569;background:transparent;color:#94a3b8;font-size:.6rem;cursor:pointer">취소</button>';
    html += '</div>';
    overlay.innerHTML = html;

    var parent = document.querySelector('#archViewL3') || document.querySelector('.arch-topo-canvas');
    if (parent) {
        parent.style.position = 'relative';
        parent.appendChild(overlay);
    } else {
        document.body.appendChild(overlay);
    }

    document.getElementById('svcScopeCancel').onclick = function() { overlay.remove(); };
    document.getElementById('svcScopeStart').onclick = function() {
        var services = [centerName];
        overlay.querySelectorAll('.svcScopeCheck').forEach(function(cb) {
            if (cb.checked) services.push(cb.getAttribute('data-svc'));
        });
        overlay.remove();
        _svcRunCodeAnalysis(services);
    };
}

function _svcRunCodeAnalysis(services) {
    var sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
    ARCH.serviceAnalysis = {services: services, status: 'loading', diagrams: {}, activeTab: null};

    // Log to Topology Analysis panel
    var chatPanel = $('archChatPanel');
    _archAddAgentHeader(chatPanel, 'SVC', 'Code Analyzer — ' + services.join(', '));
    _svcChatMsg(chatPanel, 'loading', '코드 분석 시작 (' + services.length + '개 서비스)');

    _svcEnsureViewTabs(services);

    var url = '/api/arch/service-analysis/stream?space_id=' + encodeURIComponent(sid) +
        '&service_name=' + encodeURIComponent(services.join(',')) +
        '&diagrams=component,dynamic';

    var es = new EventSource(url);
    es.onmessage = function(ev) {
        var event;
        try { event = JSON.parse(ev.data); } catch(e) { return; }

        if (event.type === 'diagram_cached' || event.type === 'diagram_done') {
            var dtype = event.key.split('/').pop();
            ARCH.serviceAnalysis.diagrams[dtype] = event.data;
            var labels = {component: '컴포넌트', dynamic: '시퀀스'};
            _svcChatMsg(chatPanel, 'done', (labels[dtype] || dtype) + ' 다이어그램 완료 — 탭 클릭으로 확인');
            if (!ARCH.serviceAnalysis.activeTab) {
                ARCH.serviceAnalysis.activeTab = dtype;
                archSwitchView('svc-' + dtype);
            }
        } else if (event.type === 'service_done') {
            _svcChatMsg(chatPanel, 'info', event.service + ' / ' + event.diagram + ' 분석 완료');
        } else if (event.type === 'verify_start') {
            _svcChatMsg(chatPanel, 'loading', (event.service || '') + ' 검증 중 — 할루시네이션 확인...');
        } else if (event.type === 'diagram_start') {
            var dname = event.key.split('/').pop();
            _svcChatMsg(chatPanel, 'loading', dname + ' 분석 중...');
        } else if (event.type === 'diagram_error') {
            _svcChatMsg(chatPanel, 'error', event.key.split('/').pop() + ' 분석 실패');
        } else if (event.type === 'complete') {
            ARCH.serviceAnalysis.status = 'done';
            _svcChatMsg(chatPanel, 'done', '전체 코드 분석 완료 — 상단 UML 탭에서 확인');
            es.close();
        } else if (event.type === 'error') {
            ARCH.serviceAnalysis.status = 'error';
            _svcChatMsg(chatPanel, 'error', '분석 오류: ' + (event.error || ''));
            es.close();
        }
    };
    es.onerror = function() { es.close(); ARCH.serviceAnalysis.status = 'error'; };
}

function _svcChatMsg(panel, status, text) {
    if (!panel) return;
    var colors = {loading: '#60a5fa', done: '#4ade80', error: '#f87171', info: '#94a3b8'};
    var icons = {loading: '⟳', done: '✓', error: '✗', info: '·'};
    var el = document.createElement('div');
    el.style.cssText = 'padding:4px 8px;font-size:.55rem;color:' + (colors[status] || '#94a3b8');
    el.textContent = (icons[status] || '') + ' ' + text;
    panel.appendChild(el);
    panel.scrollTop = panel.scrollHeight;
}

function _svcRestoreHistory(spaceId) {
    fetch('/api/arch/service-analysis/history?space_id=' + encodeURIComponent(spaceId))
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (!d.ok || !d.analyses || !d.analyses.length) return;

            // Merge all service analyses into one composite
            var merged = {component: {components: [], relationships: [], provided_interfaces: [], required_interfaces: []}, dynamic: {call_flow: []}};
            var allServices = [];
            d.analyses.forEach(function(entry) {
                (entry.services || []).forEach(function(s) { if (allServices.indexOf(s) < 0) allServices.push(s); });
                var diags = entry.diagrams || {};
                if (diags.component) {
                    var svcName = diags.component.service_name || '';
                    (diags.component.components || []).forEach(function(comp) {
                        if (!comp.source_service) comp.source_service = svcName;
                        merged.component.components.push(comp);
                    });
                    merged.component.relationships = merged.component.relationships.concat(diags.component.relationships || []);
                    merged.component.provided_interfaces = merged.component.provided_interfaces.concat(diags.component.provided_interfaces || []);
                    merged.component.required_interfaces = merged.component.required_interfaces.concat(diags.component.required_interfaces || []);
                    merged.component.language = merged.component.language || diags.component.language || '';
                }
                if (diags.dynamic) {
                    merged.dynamic.call_flow = merged.dynamic.call_flow.concat(diags.dynamic.call_flow || []);
                }
            });

            var hasData = merged.component.components.length || merged.dynamic.call_flow.length;
            if (!hasData) return;

            merged.component.service_name = allServices.join('+');
            merged.dynamic.service_name = allServices.join('+');

            ARCH.serviceAnalysis = {services: allServices, status: 'done', diagrams: merged, activeTab: null};
            _svcEnsureViewTabs(allServices);

            // Append to chat panel (don't clear existing)
            var chatPanel = $('archChatPanel');
            if (chatPanel) {
                _archAddAgentHeader(chatPanel, 'SVC', 'Code Analyzer — ' + allServices.join(', '));
                _svcChatMsg(chatPanel, 'done', '이전 분석 복원: ' + allServices.join(', '));
            }
        })
        .catch(function() {});
}

function _svcEnsureViewTabs(services) {
    var hdr = $('archTopoHdr');
    if (hdr) hdr.style.display = 'flex';
    var tabsEl = $('archViewTabs');
    if (!tabsEl) return;

    var svcLabel = (services && services.length) ? services.slice(0, 2).join('+') : '';
    if (services && services.length > 2) svcLabel += '+' + (services.length - 2);
    var prefix = svcLabel ? 'L4 ' + svcLabel : 'L4';

    var svcTabs = [
        {view: 'svc-component', label: prefix + ' 컴포넌트'},
        {view: 'svc-dynamic', label: prefix + ' 시퀀스'}
    ];
    svcTabs.forEach(function(st) {
        var existing = document.querySelector('#archViewTabs button[data-view="' + st.view + '"]');
        if (existing) { existing.textContent = st.label; return; }
        var btn = document.createElement('button');
        btn.setAttribute('data-view', st.view);
        btn.textContent = st.label;
        btn.style.cssText = 'padding:3px 12px;border-radius:4px 4px 0 0;border:1px solid #334155;border-bottom:0;font-size:.6rem;cursor:pointer;background:#0f172a;color:#64748b';
        btn.onclick = function() { archSwitchView(st.view); };
        tabsEl.appendChild(btn);
    });
}

function _svcRenderDiagram(dtype) {
    var contentEl = $('archServiceContent');
    if (!contentEl) return;
    var data = ARCH.serviceAnalysis.diagrams[dtype];
    if (!data) {
        contentEl.innerHTML = '<div style="color:#475569;padding:20px;text-align:center;font-size:.6rem">분석 대기 중...</div>';
        return;
    }
    if (dtype === 'component') _renderServiceComponent(contentEl, data);
    else if (dtype === 'dynamic') _renderServiceSequence(contentEl, data);
}

function archStartServiceAnalysis(serviceName) {
    var data = _archDataL3(ARCH.nodes, ARCH.edges, serviceName, ARCH.analysis);
    _svcShowScopeOverlay(serviceName, data.connectedNodes || []);
}

function archServiceBack() {
    archSwitchView('topo');
}

function _svcUpdateProgress(dtype, status) {
    var el = $('archServiceProgress');
    if (!el) return;
    var badge = document.getElementById('svcProg_' + dtype);
    if (!badge) {
        badge = document.createElement('span');
        badge.id = 'svcProg_' + dtype;
        badge.style.cssText = 'display:inline-block;padding:2px 6px;border-radius:3px;font-size:.5rem;';
        el.appendChild(badge);
    }
    if (status === 'loading') { badge.style.background = '#1e40af20'; badge.style.color = '#60a5fa'; badge.textContent = dtype + ' ...'; }
    else if (status === 'done') { badge.style.background = '#16a34a20'; badge.style.color = '#4ade80'; badge.textContent = dtype + ' ✓'; }
    else { badge.style.background = '#dc262620'; badge.style.color = '#f87171'; badge.textContent = dtype + ' ✗'; }
}

// ── UML SVG Renderers (Service-level diagrams) ──

function _umlTextWidth(text, fontSize) {
    return (text || '').length * fontSize * 0.52;
}

function _renderServiceComponent(container, data) {
    if (!data || !data.components || !data.components.length) {
        container.innerHTML = '<div style="color:#475569;padding:20px;text-align:center;font-size:.6rem">컴포넌트 데이터 없음</div>';
        return;
    }
    container.innerHTML = '<div class="arch-topo-canvas" style="min-height:300px;background:#0f172a;border:1px solid #1e293b;border-radius:8px;overflow:auto"><svg id="archSvgService" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:100%"></svg></div>';
    var svg = $('archSvgService');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var FONT = 9, FONT_SM = 8, FONT_XS = 7.5;
    var PAD = 40, COMP_W = 160, COMP_H_BASE = 60, COMP_GAP_X = 50, COMP_GAP_Y = 40;
    var LINE_H = 12, IFACE_R = 5;
    var GRP_PAD = 16, GRP_GAP = 30, GRP_HEADER = 24;
    var SVC_COLORS = ['#38bdf8', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4'];

    // Group components by source_service
    var serviceGroups = {};
    var serviceOrder = [];
    data.components.forEach(function(c) {
        var svc = c.source_service || data.service_name || 'unknown';
        if (!serviceGroups[svc]) { serviceGroups[svc] = []; serviceOrder.push(svc); }
        serviceGroups[svc].push(c);
    });

    // Calculate layouts per group
    var groupLayouts = [];
    serviceOrder.forEach(function(svc, gi) {
        var comps = serviceGroups[svc];
        var compLayouts = [];
        comps.forEach(function(c) {
            var w = Math.max(COMP_W, _umlTextWidth(c.name || '', FONT) + 40);
            var h = COMP_H_BASE;
            var provOps = [];
            (c.provided_interfaces || []).forEach(function(pi) { provOps = provOps.concat(pi.operations || []); });
            var reqOps = [];
            (c.required_interfaces || []).forEach(function(ri) { reqOps.push(ri.name || ri.target || ''); });
            h += Math.max(provOps.length, reqOps.length) * LINE_H;
            compLayouts.push({comp: c, w: w, h: h, provOps: provOps, reqOps: reqOps});
        });
        var cols = Math.min(2, compLayouts.length);
        var maxW = 0;
        compLayouts.forEach(function(cl) { maxW = Math.max(maxW, cl.w); });
        var cellW = maxW + COMP_GAP_X + 60;
        var cellH = 0;
        compLayouts.forEach(function(cl) { cellH = Math.max(cellH, cl.h); });
        cellH += COMP_GAP_Y;
        var rows = Math.ceil(compLayouts.length / cols);
        var grpW = GRP_PAD * 2 + cols * cellW;
        var grpH = GRP_HEADER + GRP_PAD + rows * cellH;
        groupLayouts.push({svc: svc, color: SVC_COLORS[gi % SVC_COLORS.length], compLayouts: compLayouts, cols: cols, cellW: cellW, cellH: cellH, rows: rows, w: grpW, h: grpH});
    });

    // Arrange groups horizontally (wrap if > 2)
    var grpCols = Math.min(2, groupLayouts.length);
    var grpColWidths = [];
    for (var gc = 0; gc < grpCols; gc++) grpColWidths[gc] = 0;
    groupLayouts.forEach(function(gl, i) { grpColWidths[i % grpCols] = Math.max(grpColWidths[i % grpCols], gl.w); });
    var grpRowHeights = [];
    var grpRows = Math.ceil(groupLayouts.length / grpCols);
    for (var gr = 0; gr < grpRows; gr++) grpRowHeights[gr] = 0;
    groupLayouts.forEach(function(gl, i) { grpRowHeights[Math.floor(i / grpCols)] = Math.max(grpRowHeights[Math.floor(i / grpCols)], gl.h); });

    var totalW = PAD * 2;
    grpColWidths.forEach(function(w) { totalW += w + GRP_GAP; });
    totalW -= GRP_GAP;
    var totalH = PAD * 2 + 20;
    grpRowHeights.forEach(function(h) { totalH += h + GRP_GAP; });
    totalH -= GRP_GAP;

    svg.setAttribute('viewBox', '0 0 ' + totalW + ' ' + totalH);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(350, totalH) + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="umlCompArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#475569"/></marker>';
    svg.appendChild(defs);

    // Title
    var titleEl = _svgE('text', {x: totalW / 2, y: 18, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    titleEl.textContent = '<<component diagram>> ' + (data.service_name || '');
    svg.appendChild(titleEl);

    // Draw groups + components
    var compPositions = {};
    groupLayouts.forEach(function(gl, gi) {
        var grpCol = gi % grpCols, grpRow = Math.floor(gi / grpCols);
        var gx = PAD;
        for (var gc2 = 0; gc2 < grpCol; gc2++) gx += grpColWidths[gc2] + GRP_GAP;
        var gy = PAD + 20;
        for (var gr2 = 0; gr2 < grpRow; gr2++) gy += grpRowHeights[gr2] + GRP_GAP;

        // Group boundary box
        svg.appendChild(_svgE('rect', {x: gx, y: gy, width: gl.w, height: gl.h, rx: 6, fill: 'none', stroke: gl.color, 'stroke-width': '1.5', 'stroke-dasharray': '6 3', opacity: '0.6'}));
        // Group label (service name)
        var grpLabel = _svgE('text', {x: gx + 10, y: gy + 15, fill: gl.color, 'font-size': '10', 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
        grpLabel.textContent = '<<service>> ' + gl.svc;
        svg.appendChild(grpLabel);

        // Place components inside group
        gl.compLayouts.forEach(function(cl, i) {
            var col = i % gl.cols, row = Math.floor(i / gl.cols);
            var cx = gx + GRP_PAD + col * gl.cellW + gl.cellW / 2;
            var cy = gy + GRP_HEADER + GRP_PAD + row * gl.cellH + gl.cellH / 2;
            compPositions[cl.comp.name] = {x: cx, y: cy, w: cl.w, h: cl.h};

            var x = cx - cl.w / 2, y2 = cy - cl.h / 2;

            // Component box
            svg.appendChild(_svgE('rect', {x: x, y: y2, width: cl.w, height: cl.h, rx: 4, fill: '#1e293b', stroke: gl.color, 'stroke-width': '1.2'}));

            // Component icon (small double-rect)
            var iconX = x + cl.w - 20, iconY = y2 + 4;
            svg.appendChild(_svgE('rect', {x: iconX, y: iconY, width: 14, height: 10, rx: 1, fill: 'none', stroke: gl.color, 'stroke-width': '0.8'}));
            svg.appendChild(_svgE('rect', {x: iconX - 3, y: iconY + 2, width: 5, height: 2.5, rx: 0.5, fill: gl.color}));
            svg.appendChild(_svgE('rect', {x: iconX - 3, y: iconY + 6, width: 5, height: 2.5, rx: 0.5, fill: gl.color}));

            // Stereotype + name
            var stereo = _svgE('text', {x: cx, y: y2 + 14, 'text-anchor': 'middle', fill: '#64748b', 'font-size': FONT_XS, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            stereo.textContent = '<<component>>';
            svg.appendChild(stereo);

            var nameEl = _svgE('text', {x: cx, y: y2 + 28, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': FONT, 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
            nameEl.textContent = cl.comp.name;
            svg.appendChild(nameEl);

            // Description
            if (cl.comp.description) {
                var descEl = _svgE('text', {x: cx, y: y2 + 40, 'text-anchor': 'middle', fill: '#64748b', 'font-size': FONT_XS, 'font-family': '-apple-system,sans-serif'});
                descEl.textContent = cl.comp.description.length > 30 ? cl.comp.description.slice(0, 30) + '...' : cl.comp.description;
                svg.appendChild(descEl);
            }

            // Provided interfaces (left side — lollipop)
            cl.provOps.forEach(function(op, oi) {
                var iy = y2 + COMP_H_BASE - 10 + oi * LINE_H;
                svg.appendChild(_svgE('line', {x1: x - 18, y1: iy, x2: x, y2: iy, stroke: '#4ade80', 'stroke-width': '1'}));
                svg.appendChild(_svgE('circle', {cx: x - 18, cy: iy, r: IFACE_R, fill: '#0f172a', stroke: '#4ade80', 'stroke-width': '1.2'}));
                var opEl = _svgE('text', {x: x - 26, y: iy + 3, 'text-anchor': 'end', fill: '#4ade80', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                opEl.textContent = op.length > 20 ? op.slice(0, 20) + '..' : op;
                svg.appendChild(opEl);
            });

            // Required interfaces (right side — socket/half-circle)
            cl.reqOps.forEach(function(ri, ri2) {
                var iy = y2 + COMP_H_BASE - 10 + ri2 * LINE_H;
                svg.appendChild(_svgE('line', {x1: x + cl.w, y1: iy, x2: x + cl.w + 18, y2: iy, stroke: '#f59e0b', 'stroke-width': '1'}));
                var arc = _svgE('path', {d: 'M' + (x + cl.w + 18) + ' ' + (iy - IFACE_R) + ' A' + IFACE_R + ' ' + IFACE_R + ' 0 0 1 ' + (x + cl.w + 18) + ' ' + (iy + IFACE_R), fill: 'none', stroke: '#f59e0b', 'stroke-width': '1.2'});
                svg.appendChild(arc);
                var riEl = _svgE('text', {x: x + cl.w + 26, y: iy + 3, fill: '#f59e0b', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                riEl.textContent = ri.length > 20 ? ri.slice(0, 20) + '..' : ri;
                svg.appendChild(riEl);
            });
        });
    });

    // Relationships (cross-group arrows)
    (data.relationships || []).forEach(function(rel) {
        var from = compPositions[rel.source], to = compPositions[rel.target];
        if (!from || !to) return;
        var x1 = from.x, y1 = from.y + from.h / 2;
        var x2 = to.x, y2b = to.y - to.h / 2;
        if (Math.abs(from.x - to.x) > from.w) {
            x1 = from.x + (to.x > from.x ? from.w / 2 : -from.w / 2);
            y1 = from.y;
            x2 = to.x + (to.x > from.x ? -to.w / 2 : to.w / 2);
            y2b = to.y;
        }
        svg.appendChild(_svgE('line', {x1: x1, y1: y1, x2: x2, y2: y2b, stroke: '#475569', 'stroke-width': '1', 'stroke-dasharray': '4 2', 'marker-end': 'url(#umlCompArrow)'}));
        var mx = (x1 + x2) / 2, my = (y1 + y2b) / 2;
        var relLabel = _svgE('text', {x: mx, y: my - 4, 'text-anchor': 'middle', fill: '#64748b', 'font-size': '7', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
        relLabel.textContent = rel.type || '<<use>>';
        svg.appendChild(relLabel);
    });
}

function _renderServicePackage(container, data) {
    if (!data || !data.modules) {
        container.innerHTML = '<div style="color:#475569">모듈 데이터 없음</div>';
        return;
    }
    container.innerHTML = '<div class="arch-topo-canvas" style="min-height:300px;background:#0f172a;border:1px solid #1e293b;border-radius:8px;overflow:auto"><svg id="archSvgService" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:100%"></svg></div>';
    var svg = $('archSvgService');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var FONT = 9, FONT_SM = 8, FONT_XS = 7.5;
    var PAD = 30, PKG_PAD = 12, PKG_GAP = 20, CLASS_PAD = 8;
    var LINE_H = 14, TAB_H = 16, TAB_W = 60, HEADER_H = 22;

    var pkgLayouts = [];
    data.modules.forEach(function(m) {
        var maxW = Math.max(120, _umlTextWidth(m.file || '', FONT) + 30);
        var innerH = 0;

        var classBoxes = [];
        if (m.classes && m.classes.length) {
            m.classes.forEach(function(cls) {
                var cName = cls.name + (cls.bases && cls.bases.length ? ' : ' + cls.bases[0] : '');
                var cW = Math.max(100, _umlTextWidth(cName, FONT_SM) + 20);
                var attrs = cls.attributes || [];
                var methods = cls.methods || [];
                attrs.forEach(function(a) { cW = Math.max(cW, _umlTextWidth((a.visibility || '+') + ' ' + a.name + ': ' + (a.type || ''), FONT_XS) + 20); });
                methods.forEach(function(mt) { cW = Math.max(cW, _umlTextWidth((mt.visibility || '+') + ' ' + mt.name + '(' + (mt.params || []).join(', ') + ')' + (mt.returns ? ': ' + mt.returns : ''), FONT_XS) + 20); });
                var cH = HEADER_H + Math.max(1, attrs.length) * LINE_H + 4 + Math.max(1, methods.length) * LINE_H + 4;
                classBoxes.push({cls: cls, w: cW, h: cH, name: cName});
                maxW = Math.max(maxW, cW + PKG_PAD * 2);
                innerH += cH + 6;
            });
        }

        var fnLines = [];
        if (m.functions && m.functions.length) {
            m.functions.forEach(function(fn) {
                var dec = (fn.decorators && fn.decorators.length) ? fn.decorators[0] + ' ' : '';
                var line = dec + fn.name + '(' + (fn.params || []).join(', ') + ')' + (fn.returns ? ': ' + fn.returns : '');
                fnLines.push(line);
                maxW = Math.max(maxW, _umlTextWidth(line, FONT_XS) + PKG_PAD * 2 + 10);
            });
            innerH += fnLines.length * LINE_H + 8;
        }

        if (m.endpoints && m.endpoints.length) {
            m.endpoints.forEach(function(ep) {
                var epLine = ep.method + ' ' + ep.path;
                maxW = Math.max(maxW, _umlTextWidth(epLine, FONT_XS) + PKG_PAD * 2 + 10);
            });
            innerH += m.endpoints.length * LINE_H + 12;
        }

        var totalH = TAB_H + HEADER_H + innerH + PKG_PAD;
        pkgLayouts.push({module: m, w: maxW, h: totalH, classBoxes: classBoxes, fnLines: fnLines});
    });

    var COL_MAX = 2;
    var cols = Math.min(COL_MAX, pkgLayouts.length);
    var colWidths = [];
    for (var ci = 0; ci < cols; ci++) colWidths[ci] = 0;
    pkgLayouts.forEach(function(pl, i) { colWidths[i % cols] = Math.max(colWidths[i % cols], pl.w); });

    var positions = [];
    var colY = [];
    for (var ci2 = 0; ci2 < cols; ci2++) colY[ci2] = PAD;
    pkgLayouts.forEach(function(pl, i) {
        var col = i % cols;
        var x = PAD;
        for (var c = 0; c < col; c++) x += colWidths[c] + PKG_GAP;
        positions.push({x: x, y: colY[col]});
        colY[col] += pl.h + PKG_GAP;
    });

    var totalW = PAD * 2;
    for (var ci3 = 0; ci3 < cols; ci3++) totalW += colWidths[ci3];
    totalW += (cols - 1) * PKG_GAP;
    var totalH2 = Math.max.apply(null, colY) + PAD;

    svg.setAttribute('viewBox', '0 0 ' + totalW + ' ' + totalH2);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(400, totalH2) + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="umlDepArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10" fill="none" stroke="#64748b" stroke-width="1.5"/></marker>';
    svg.appendChild(defs);

    var titleEl = _svgE('text', {x: totalW / 2, y: 16, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    titleEl.textContent = '<<package>> ' + (data.service_name || '') + ' — ' + (data.language || '');
    svg.appendChild(titleEl);

    var pkgCenters = {};
    pkgLayouts.forEach(function(pl, i) {
        var px = positions[i].x, py = positions[i].y;
        var pw = colWidths[i % cols], ph = pl.h;
        pkgCenters[pl.module.file] = {x: px + pw / 2, y: py + ph / 2, w: pw, h: ph};

        var g = _svgE('g', {transform: 'translate(' + px + ',' + py + ')'});

        g.appendChild(_svgE('rect', {x: 0, y: TAB_H, width: pw, height: ph - TAB_H, rx: 2, fill: '#0f172a', stroke: '#475569', 'stroke-width': '1'}));
        g.appendChild(_svgE('rect', {x: 0, y: 0, width: TAB_W, height: TAB_H, rx: 2, fill: '#0f172a', stroke: '#475569', 'stroke-width': '1'}));

        var stereo = _svgE('text', {x: pw / 2, y: TAB_H + 14, 'text-anchor': 'middle', fill: '#64748b', 'font-size': FONT_XS, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
        stereo.textContent = '<<module>>';
        g.appendChild(stereo);

        var nameEl = _svgE('text', {x: pw / 2, y: TAB_H + 14 + LINE_H, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': FONT, 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
        nameEl.textContent = pl.module.file || 'unknown';
        g.appendChild(nameEl);

        var iy = TAB_H + HEADER_H + 8;

        pl.classBoxes.forEach(function(cb) {
            var cx = PKG_PAD, cy = iy;
            var cw = pw - PKG_PAD * 2, ch = cb.h;

            g.appendChild(_svgE('rect', {x: cx, y: cy, width: cw, height: ch, rx: 2, fill: '#1e293b', stroke: '#a78bfa', 'stroke-width': '1'}));

            var cnEl = _svgE('text', {x: cx + cw / 2, y: cy + 14, 'text-anchor': 'middle', fill: '#a78bfa', 'font-size': FONT_SM, 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
            cnEl.textContent = cb.name;
            g.appendChild(cnEl);

            g.appendChild(_svgE('line', {x1: cx, y1: cy + HEADER_H - 4, x2: cx + cw, y2: cy + HEADER_H - 4, stroke: '#475569', 'stroke-width': '0.5'}));

            var ay = cy + HEADER_H;
            var attrs = cb.cls.attributes || [];
            if (attrs.length === 0) {
                var noA = _svgE('text', {x: cx + 6, y: ay + 10, fill: '#475569', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                noA.textContent = '(no attributes)';
                g.appendChild(noA);
                ay += LINE_H;
            } else {
                attrs.forEach(function(a) {
                    var at = _svgE('text', {x: cx + 6, y: ay + 10, fill: '#94a3b8', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                    at.textContent = (a.visibility || '+') + ' ' + a.name + (a.type ? ': ' + a.type : '');
                    g.appendChild(at);
                    ay += LINE_H;
                });
            }

            g.appendChild(_svgE('line', {x1: cx, y1: ay + 2, x2: cx + cw, y2: ay + 2, stroke: '#475569', 'stroke-width': '0.5'}));
            ay += 4;

            var methods = cb.cls.methods || [];
            if (methods.length === 0) {
                var noM = _svgE('text', {x: cx + 6, y: ay + 10, fill: '#475569', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                noM.textContent = '(no methods)';
                g.appendChild(noM);
            } else {
                methods.forEach(function(mt) {
                    var vis = mt.visibility || '+';
                    var mtLine = vis + ' ' + mt.name + '(' + (mt.params || []).join(', ') + ')' + (mt.returns ? ': ' + mt.returns : '');
                    var mtEl = _svgE('text', {x: cx + 6, y: ay + 10, fill: '#94a3b8', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                    mtEl.textContent = mtLine;
                    g.appendChild(mtEl);
                    ay += LINE_H;
                });
            }
            iy += ch + 6;
        });

        if (pl.fnLines.length) {
            g.appendChild(_svgE('line', {x1: PKG_PAD, y1: iy, x2: pw - PKG_PAD, y2: iy, stroke: '#334155', 'stroke-width': '0.5', 'stroke-dasharray': '3 2'}));
            iy += 4;
            pl.fnLines.forEach(function(fl) {
                var fEl = _svgE('text', {x: PKG_PAD + 4, y: iy + 10, fill: '#38bdf8', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
                fEl.textContent = '+ ' + fl;
                g.appendChild(fEl);
                iy += LINE_H;
            });
        }

        if (pl.module.endpoints && pl.module.endpoints.length) {
            iy += 4;
            g.appendChild(_svgE('line', {x1: PKG_PAD, y1: iy, x2: pw - PKG_PAD, y2: iy, stroke: '#334155', 'stroke-width': '0.5', 'stroke-dasharray': '3 2'}));
            iy += 4;
            var epLabel = _svgE('text', {x: PKG_PAD + 4, y: iy + 10, fill: '#64748b', 'font-size': FONT_XS, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            epLabel.textContent = '<<endpoints>>';
            g.appendChild(epLabel);
            iy += LINE_H;
            pl.module.endpoints.forEach(function(ep) {
                var mc = {'GET': '#22c55e', 'POST': '#3b82f6', 'PUT': '#f59e0b', 'DELETE': '#ef4444'}[ep.method] || '#94a3b8';
                var epEl = _svgE('text', {x: PKG_PAD + 4, y: iy + 10, fill: mc, 'font-size': FONT_XS, 'font-weight': '600', 'font-family': 'SFMono-Regular,Menlo,monospace'});
                epEl.textContent = ep.method + ' ' + ep.path;
                g.appendChild(epEl);
                iy += LINE_H;
            });
        }

        svg.appendChild(g);
    });

    if (data.dependencies && data.dependencies.length) {
        data.dependencies.forEach(function(dep) {
            var fromC = pkgCenters[dep.from];
            var toC = pkgCenters[dep.to];
            if (!fromC || !toC) return;
            var x1 = fromC.x, y1 = fromC.y + fromC.h / 2;
            var x2 = toC.x, y2 = toC.y - toC.h / 2;
            if (Math.abs(fromC.x - toC.x) > 50) {
                x1 = fromC.x + (toC.x > fromC.x ? fromC.w / 2 : -fromC.w / 2);
                y1 = fromC.y;
                x2 = toC.x + (toC.x > fromC.x ? -toC.w / 2 : toC.w / 2);
                y2 = toC.y;
            }
            svg.appendChild(_svgE('line', {x1: x1, y1: y1, x2: x2, y2: y2, stroke: '#64748b', 'stroke-width': '1', 'stroke-dasharray': '5 3', 'marker-end': 'url(#umlDepArrow)'}));
            var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
            var lbl = _svgE('text', {x: mx + 4, y: my - 4, fill: '#475569', 'font-size': '7', 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            lbl.textContent = '<<' + (dep.type || 'import') + '>>';
            svg.appendChild(lbl);
        });
    }
}

function _renderServiceApiCards(container, data) {
    if (!data || !data.endpoints) {
        container.innerHTML = '<div style="color:#475569">API 데이터 없음</div>';
        return;
    }
    container.innerHTML = '<div class="arch-topo-canvas" style="min-height:300px;background:#0f172a;border:1px solid #1e293b;border-radius:8px;overflow:auto"><svg id="archSvgService" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:100%"></svg></div>';
    var svg = $('archSvgService');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var FONT = 9, FONT_SM = 8, FONT_XS = 7.5;
    var PAD = 30, COMP_W = 200, COMP_GAP = 30, IFACE_R = 6;
    var LINE_H = 14, HEADER_H = 28;

    var endpoints = data.endpoints || [];
    var COMP_H = HEADER_H + 20 + endpoints.length * (LINE_H * 3 + 20);

    var svcName = data.service_name || ARCH.serviceAnalysis.serviceName || 'Service';
    var compX = PAD + 80, compY = PAD + 20;
    var compW = COMP_W;

    var maxEpW = compW;
    endpoints.forEach(function(ep) {
        var epLine = ep.method + ' ' + ep.path + (ep.description ? ' — ' + ep.description : '');
        maxEpW = Math.max(maxEpW, _umlTextWidth(epLine, FONT_SM) + 60);
    });
    compW = maxEpW;

    COMP_H = HEADER_H + 16;
    var ifaceLayouts = [];
    endpoints.forEach(function(ep, i) {
        var paramLines = [];
        if (ep.request && ep.request.params) {
            ep.request.params.forEach(function(p) { paramLines.push('  ' + p.name + ': ' + (p.type || 'any') + (p.required ? ' [required]' : '')); });
        }
        var respLine = '';
        if (ep.response && ep.response.success) respLine = ep.response.success.status + ' → ' + (ep.response.success.body || 'void');
        var errorLines = [];
        if (ep.response && ep.response.errors) ep.response.errors.forEach(function(err) { errorLines.push(err.status + ': ' + (err.condition || '')); });

        var blockH = LINE_H + paramLines.length * LINE_H + (respLine ? LINE_H : 0) + errorLines.length * LINE_H + 12;
        ifaceLayouts.push({ep: ep, paramLines: paramLines, respLine: respLine, errorLines: errorLines, h: blockH});
        COMP_H += blockH;
    });

    var totalW = compX + compW + 80 + PAD;
    var totalH = compY + COMP_H + PAD;

    svg.setAttribute('viewBox', '0 0 ' + totalW + ' ' + totalH);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(400, totalH) + 'px';

    var defs = _svgE('defs');
    svg.appendChild(defs);

    var titleEl = _svgE('text', {x: totalW / 2, y: 16, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    titleEl.textContent = '<<component>> ' + svcName + (data.framework ? ' [' + data.framework + ']' : '');
    svg.appendChild(titleEl);

    svg.appendChild(_svgE('rect', {x: compX, y: compY, width: compW, height: COMP_H, rx: 3, fill: '#1e293b', stroke: '#38bdf8', 'stroke-width': '1.5'}));

    var compIcon = _svgE('g', {transform: 'translate(' + (compX + compW - 22) + ',' + (compY + 4) + ')'});
    compIcon.appendChild(_svgE('rect', {x: 0, y: 0, width: 16, height: 12, rx: 1, fill: 'none', stroke: '#38bdf8', 'stroke-width': '1'}));
    compIcon.appendChild(_svgE('rect', {x: -3, y: 2, width: 6, height: 3, rx: 0.5, fill: '#38bdf8'}));
    compIcon.appendChild(_svgE('rect', {x: -3, y: 7, width: 6, height: 3, rx: 0.5, fill: '#38bdf8'}));
    svg.appendChild(compIcon);

    var compName = _svgE('text', {x: compX + 10, y: compY + 18, fill: '#e2e8f0', 'font-size': FONT, 'font-weight': '700', 'font-family': '-apple-system,sans-serif'});
    compName.textContent = svcName;
    svg.appendChild(compName);

    svg.appendChild(_svgE('line', {x1: compX, y1: compY + HEADER_H, x2: compX + compW, y2: compY + HEADER_H, stroke: '#334155', 'stroke-width': '0.5'}));

    var iy = compY + HEADER_H + 8;
    ifaceLayouts.forEach(function(il) {
        var ep = il.ep;
        var mc = {'GET': '#22c55e', 'POST': '#3b82f6', 'PUT': '#f59e0b', 'DELETE': '#ef4444', 'PATCH': '#a855f7'}[ep.method] || '#94a3b8';

        var ifaceY = iy + il.h / 2;
        svg.appendChild(_svgE('line', {x1: compX - 20, y1: ifaceY, x2: compX, y2: ifaceY, stroke: mc, 'stroke-width': '1.2'}));
        svg.appendChild(_svgE('circle', {cx: compX - 20, cy: ifaceY, r: IFACE_R, fill: '#0f172a', stroke: mc, 'stroke-width': '1.5'}));

        var methEl = _svgE('text', {x: compX + 10, y: iy + 11, fill: mc, 'font-size': FONT_SM, 'font-weight': '700', 'font-family': 'SFMono-Regular,Menlo,monospace'});
        methEl.textContent = ep.method + ' ' + ep.path;
        svg.appendChild(methEl);

        var dy = iy + LINE_H + 4;
        if (ep.description) {
            var descEl = _svgE('text', {x: compX + 14, y: dy + 9, fill: '#64748b', 'font-size': FONT_XS, 'font-style': 'italic', 'font-family': '-apple-system,sans-serif'});
            descEl.textContent = ep.description;
            svg.appendChild(descEl);
            dy += LINE_H;
        }

        il.paramLines.forEach(function(pl) {
            var pEl = _svgE('text', {x: compX + 14, y: dy + 9, fill: '#94a3b8', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
            pEl.textContent = pl;
            svg.appendChild(pEl);
            dy += LINE_H;
        });

        if (il.respLine) {
            var rEl = _svgE('text', {x: compX + 14, y: dy + 9, fill: '#4ade80', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
            rEl.textContent = '>> ' + il.respLine;
            svg.appendChild(rEl);
            dy += LINE_H;
        }

        il.errorLines.forEach(function(el) {
            var eEl = _svgE('text', {x: compX + 14, y: dy + 9, fill: '#f87171', 'font-size': FONT_XS, 'font-family': 'SFMono-Regular,Menlo,monospace'});
            eEl.textContent = '!! ' + el;
            svg.appendChild(eEl);
            dy += LINE_H;
        });

        iy += il.h;
        if (ifaceLayouts.indexOf(il) < ifaceLayouts.length - 1) {
            svg.appendChild(_svgE('line', {x1: compX + 10, y1: iy - 2, x2: compX + compW - 10, y2: iy - 2, stroke: '#1e293b', 'stroke-width': '0.5', 'stroke-dasharray': '3 2'}));
        }
    });
}

function _renderServiceSequence(container, data) {
    if (!data || !data.call_flow || !data.call_flow.length) {
        container.innerHTML = '<div style="color:#475569">시퀀스 데이터 없음</div>';
        return;
    }
    container.innerHTML = '<div class="arch-topo-canvas" style="min-height:300px;background:#0f172a;border:1px solid #1e293b;border-radius:8px;overflow:auto"><svg id="archSvgService" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:100%"></svg></div>';
    var svg = $('archSvgService');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var FONT = 9, FONT_SM = 8;
    var PAD_X = 40, PAD_TOP = 40, HEAD_H = 40, PAD_BOTTOM = 30;
    var MIN_COL_W = 120, MSG_GAP = 40;

    var participants = [];
    var pSet = {};
    data.call_flow.forEach(function(hop) {
        if (hop.caller && !pSet[hop.caller]) { pSet[hop.caller] = true; participants.push(hop.caller); }
        if (hop.callee && !pSet[hop.callee]) { pSet[hop.callee] = true; participants.push(hop.callee); }
    });
    if (!participants.length) return;

    var pIdx = {};
    participants.forEach(function(name, i) { pIdx[name] = i; });

    var colW = [];
    participants.forEach(function(name) { colW.push(Math.max(MIN_COL_W, _umlTextWidth(name, FONT) + 30)); });

    data.call_flow.forEach(function(hop) {
        var fi = pIdx[hop.caller], ti = pIdx[hop.callee];
        if (fi == null || ti == null || fi === ti) return;
        var label = (hop.type || '') + (hop.method ? ' ' + hop.method : '');
        var lo = Math.min(fi, ti), hi = Math.max(fi, ti);
        var span = hi - lo;
        var needed = (_umlTextWidth(label, FONT_SM) + 40) / span;
        for (var s = lo; s <= hi; s++) colW[s] = Math.max(colW[s], needed);
    });

    var pPos = {};
    var cumX = PAD_X + colW[0] / 2;
    participants.forEach(function(name, i) {
        if (i === 0) { pPos[name] = cumX; return; }
        cumX += colW[i - 1] / 2 + colW[i] / 2;
        pPos[name] = cumX;
    });

    var H_MSGS = data.call_flow.length * MSG_GAP;
    var W = cumX + colW[colW.length - 1] / 2 + PAD_X;
    var H = PAD_TOP + HEAD_H + H_MSGS + PAD_BOTTOM;

    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (svg.parentElement) svg.parentElement.style.height = Math.max(350, H) + 'px';

    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="umlSeqArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="#38bdf8"/></marker>' +
        '<marker id="umlSeqReturn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10" fill="none" stroke="#64748b" stroke-width="1.5"/></marker>';
    svg.appendChild(defs);

    var titleEl = _svgE('text', {x: W / 2, y: 18, 'text-anchor': 'middle', fill: '#94a3b8', 'font-size': '11', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
    titleEl.textContent = '<<sequence>> ' + (data.endpoint || data.scenario || 'Internal Flow');
    svg.appendChild(titleEl);

    var headY = PAD_TOP;
    participants.forEach(function(name) {
        var pcx = pPos[name];
        var boxW = Math.max(90, _umlTextWidth(name, FONT) + 16);
        svg.appendChild(_svgE('rect', {x: pcx - boxW / 2, y: headY, width: boxW, height: HEAD_H - 6, rx: 4, fill: '#1e293b', stroke: '#475569', 'stroke-width': '1'}));
        var t = _svgE('text', {x: pcx, y: headY + HEAD_H / 2 + 2, 'text-anchor': 'middle', fill: '#e2e8f0', 'font-size': FONT, 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        t.textContent = name;
        svg.appendChild(t);
        svg.appendChild(_svgE('line', {x1: pcx, y1: headY + HEAD_H - 6, x2: pcx, y2: H - PAD_BOTTOM, stroke: '#334155', 'stroke-width': '1', 'stroke-dasharray': '4 3'}));
    });

    data.call_flow.forEach(function(hop, hi) {
        var x1 = pPos[hop.caller], x2 = pPos[hop.callee];
        if (x1 == null || x2 == null) return;
        var y = headY + HEAD_H + (hi + 0.5) * MSG_GAP;
        var isReturn = hop.type === 'return';
        var dash = isReturn ? '4 3' : '';
        var marker = isReturn ? 'url(#umlSeqReturn)' : 'url(#umlSeqArrow)';
        var color = isReturn ? '#64748b' : '#38bdf8';

        svg.appendChild(_svgE('line', {x1: x1, y1: y, x2: x2, y2: y, stroke: color, 'stroke-width': '1.2', 'stroke-dasharray': dash, 'marker-end': marker}));

        var label = hop.method || hop.type || '';
        if (label) {
            var mx = (x1 + x2) / 2;
            var lbl = _svgE('text', {x: mx, y: y - 6, 'text-anchor': 'middle', fill: isReturn ? '#64748b' : '#94a3b8', 'font-size': FONT_SM, 'font-family': 'SFMono-Regular,Menlo,monospace'});
            lbl.textContent = label;
            svg.appendChild(lbl);
        }

        var stepEl = _svgE('text', {x: Math.min(x1, x2) - 8, y: y + 3, 'text-anchor': 'end', fill: '#475569', 'font-size': '8', 'font-weight': '600', 'font-family': '-apple-system,sans-serif'});
        stepEl.textContent = String(hi + 1);
        svg.appendChild(stepEl);
    });
}

// ================================================================
// ZOOM / PAN Controls
// ================================================================

function _archGetActiveSvg() {
    var level = ARCH.nav.level;
    if (ARCH.mode === 'single_app') {
        if (level === 'L1') return $('archSvgL2Unified') || $('archSvgL2App');
        if (level === 'L2') return $('archSvgL3');
    } else {
        if (level === 'L1') return $('archSvgL1');
        if (level === 'L2') return $('archSvgL2Unified') || $('archSvgL2App');
        if (level === 'L3') return $('archSvgL3');
    }
    return null;
}

function _archApplyZoom() {
    var svg = _archGetActiveSvg();
    if (!svg) return;
    var orig = svg.getAttribute('data-orig-viewbox') || svg.getAttribute('viewBox');
    if (!orig) return;
    if (!svg.getAttribute('data-orig-viewbox')) svg.setAttribute('data-orig-viewbox', orig);
    var parts = orig.split(/\s+/).map(Number);
    var ox = parts[0], oy = parts[1], origW = parts[2], origH = parts[3];
    var z = ARCH._zoom;
    var newW = origW / z.scale;
    var newH = origH / z.scale;
    var cx = ox + origW / 2 + z.tx;
    var cy = oy + origH / 2 + z.ty;
    var newX = cx - newW / 2;
    var newY = cy - newH / 2;
    svg.setAttribute('viewBox', newX + ' ' + newY + ' ' + newW + ' ' + newH);
}

function archZoomIn() {
    ARCH._zoom.scale = Math.min(ARCH._zoom.scale * 1.3, 5);
    _archApplyZoom();
}

function archZoomOut() {
    ARCH._zoom.scale = Math.max(ARCH._zoom.scale / 1.3, 0.3);
    _archApplyZoom();
}

function archZoomFit() {
    ARCH._zoom = {scale: 1, tx: 0, ty: 0};
    var svg = _archGetActiveSvg();
    if (!svg) return;
    var vb = svg.getAttribute('data-orig-viewbox') || svg.getAttribute('viewBox');
    if (vb) svg.setAttribute('viewBox', vb);
}

function _archSaveOrigViewBox(svg) {
    if (svg && !svg.getAttribute('data-orig-viewbox')) {
        var vb = svg.getAttribute('viewBox');
        if (vb) svg.setAttribute('data-orig-viewbox', vb);
    }
}

(function _archInitWheelZoom() {
    var wrap = document.getElementById('archTopoWrap');
    if (!wrap) { setTimeout(_archInitWheelZoom, 500); return; }
    wrap.addEventListener('wheel', function(ev) {
        var svg = _archGetActiveSvg();
        if (!svg || !svg.getAttribute('viewBox')) return;
        if (!ev.ctrlKey && !ev.metaKey) return;
        ev.preventDefault();
        var delta = ev.deltaY > 0 ? 0.85 : 1.18;
        ARCH._zoom.scale = Math.min(Math.max(ARCH._zoom.scale * delta, 0.3), 5);
        _archApplyZoom();
    }, {passive: false});
})();

// ================================================================
// BOUNDARY ANALYSIS — 외부 앱 클릭 → 분석 → 토폴로지에 merge
// ================================================================

function _archAnalyzeBoundary(nodeObj) {
    var appName = nodeObj.group || nodeObj.name || '';
    if (!appName) return;

    var overlay = document.createElement('div');
    overlay.id = 'archBoundaryOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = '<div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px 32px;min-width:320px;text-align:center;">' +
        '<div style="color:#e2e8f0;font-size:14px;font-weight:600;margin-bottom:4px;">' + esc(appName) + '</div>' +
        '<div style="color:#94a3b8;font-size:12px;margin-bottom:16px;">이 외부 앱의 토폴로지를 분석하시겠습니까?</div>' +
        '<div style="display:flex;gap:8px;justify-content:center;">' +
        '<button id="archBoundaryConfirm" style="background:#38bdf8;color:#0f172a;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:12px;font-weight:600;">분석</button>' +
        '<button onclick="this.closest(\'#archBoundaryOverlay\').remove()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:12px;">취소</button>' +
        '</div></div>';
    document.body.appendChild(overlay);

    document.getElementById('archBoundaryConfirm').addEventListener('click', function() {
        overlay.remove();
        archDiscoverBoundary(appName);
    });
}

function archDiscoverBoundary(appName) {
    archLoadLayout();
    var model = $('archModel').value;
    _archSetRunning();
    ARCH.discovering = true;

    var chat = $('archChatPanel');
    chat.innerHTML = '';
    var summaryPanel = $('archChatSummary');
    if (summaryPanel) summaryPanel.innerHTML = '';
    ARCH.chatMode = 'summary';
    archSetChatMode('summary');
    var toggle = $('archChatToggle');
    if (toggle) toggle.style.display = '';

    _archShowProgress(appName + ' 토폴로지 분석');
    _archUpdateProgress('init', appName + ' 분석 시작');
    _archAddWaiting(chat, appName + ' Agent Session 생성 중...');

    var sid = SELECTED || '';
    var url = '/api/arch/discover/stream?model=' + encodeURIComponent(model)
        + '&space_id=' + encodeURIComponent(sid)
        + '&app_name=' + encodeURIComponent(appName);

    if (ARCH._es) { try { ARCH._es.close(); } catch(e){} }
    var es = new EventSource(url);
    ARCH._es = es;
    es.onmessage = function(ev) {
        var d = JSON.parse(ev.data);
        var agent = d.agent || d.phase || '';

        if (d.type === 'phase_start') {
            _archUpdateProgress(agent, d.description || d.label || agent);
            _archAddSummaryEntry(d.description || d.label || agent, 'phase');
            _archRemoveWaiting(chat);
            _archAddWaiting(chat, d.description || d.label || '분석 중...');
        } else if (d.type === 'agent_question') {
            _archRemoveWaiting(chat);
            _archAddQuestion(chat, agent, d.question, d.turn);
            _archAddSummaryEntry('질문 전송', 'question');
        } else if (d.type === 'agent_answer') {
            _archAddAnswer(chat, agent, d.answer, d.tool_calls);
            _archAddSummaryEntry('답변 수신', 'answer');
        } else if (d.type === 'layer_complete') {
            _archAddLayerDone(chat, d.layer);
            if (d.analysis) {
                var a = d.analysis;
                ARCH.nodes = (a.graph && a.graph.nodes) || [];
                ARCH.edges = (a.graph && a.graph.edges) || [];
                ARCH.analysis = a;
                if (a.mode) ARCH.mode = a.mode;
                archNavigateTo('L1', {app: ARCH.nav.selectedApp});
            }
        } else if (d.type === 'complete') {
            es.close(); ARCH._es = null;
            ARCH.discovering = false;
            _archHideProgress();
            _archRemoveWaiting(chat);
            _archSetIdle();
            _archAddSummaryEntry(appName + ' 분석 완료', 'done');
            // Reload integrated topology from API (read-time expansion)
            var _sid = typeof SELECTED !== 'undefined' ? SELECTED : '';
            fetch('/api/arch/topology?space_id=' + encodeURIComponent(_sid)).then(function(r) { return r.json() }).then(function(t) {
                if (!t.ok) return;
                var nodes = t.graph ? t.graph.nodes : t.nodes || [];
                var edges = t.graph ? t.graph.edges : t.edges || [];
                if (!nodes.length) return;
                ARCH.analysis = t;
                ARCH.nodes = nodes;
                ARCH.edges = edges;
                _enrichNodes(ARCH.nodes);
                var _mg = {};
                nodes.forEach(function(n) { if (n.group && n.service_type !== 'boundary') _mg[n.group] = true; });
                ARCH.mode = (Object.keys(_mg).length <= 1) ? 'single_app' : 'multi_app';
                if (ARCH.mode === 'single_app' && Object.keys(_mg).length === 1) {
                    ARCH.nav.selectedApp = Object.keys(_mg)[0];
                }
                archNavigateTo('L1', {app: ARCH.nav.selectedApp});
            }).catch(function(e) { console.error('Boundary reload error', e); });
        } else if (d.type === 'error') {
            es.close(); ARCH._es = null;
            ARCH.discovering = false;
            _archHideProgress();
            _archRemoveWaiting(chat);
            _archSetIdle();
            _archAddError(chat, d.error || '알 수 없는 오류');
            _archAddSummaryEntry('오류: ' + (d.error || ''), 'error');
        }
    };
    es.onerror = function() {
        es.close(); ARCH._es = null;
        ARCH.discovering = false;
        _archHideProgress();
        _archSetIdle();
    };
}

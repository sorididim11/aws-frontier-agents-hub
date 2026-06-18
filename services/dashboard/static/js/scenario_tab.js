// ================================================================
// SCENARIO TAB — scenario_tab.js
// ================================================================
/* global $ esc trun ARCH SELECTED */

var SCEN = {
    scenarios: [],
    byCategory: {},
    templates: [],
    selectedTemplate: null,
    selectedApp: null,
    executorMode: 'multi_agent',
    apps: [],
    current: null,
    runId: null,
    pollTimer: null,
    chatSessionId: null,
    chatOpen: false,
    _loaded: false,
};

var SCEN_CAT_META = {};

var SCEN_TPL_LAYERS = [
    {key: 'infrastructure',  label: 'Infrastructure', color: '#f97316'},
    {key: 'application',     label: 'Application',    color: '#ef4444'},
];

function _scenSpaceId() {
    return (typeof SELECTED !== 'undefined' && SELECTED) ? SELECTED : '';
}

// ── Agent Console (고정, 시뮬레이션 탭 하단) ──
var SCEN_CONSOLE = {lines: [], filter: 'All'};

function scenConsoleAppend(phase, msg) {
    SCEN_CONSOLE.lines.push({t: Date.now(), phase: phase, msg: msg});
    if (SCEN_CONSOLE.lines.length > 200) SCEN_CONSOLE.lines.shift();
    _scenRenderConsole();
}

function scenConsoleSetFilter(f) {
    SCEN_CONSOLE.filter = f;
    _scenRenderConsole();
}

function _scenRenderConsole() {
    var el = $('scenAgentConsole');
    if (!el) return;
    var filter = SCEN_CONSOLE.filter;
    var phases = ['All', 'Trigger', 'Verify', 'Restore', 'Generator', 'Investigate'];
    var h = '<div class="scen-console-filters">';
    phases.forEach(function(p) {
        h += '<span class="scen-console-filter' + (filter === p ? ' active' : '') + '" onclick="scenConsoleSetFilter(\'' + p + '\')">' + p + '</span>';
    });
    h += '</div><div class="scen-console-body" id="scenConsoleBody">';
    var lines = SCEN_CONSOLE.lines;
    if (filter !== 'All') {
        lines = lines.filter(function(l) {
            if (!l.phase) return false;
            var p = l.phase.toLowerCase();
            var f = filter.toLowerCase();
            return p.indexOf(f) !== -1 || (f === 'trigger' && p.indexOf('주입') !== -1)
                || (f === 'verify' && (p.indexOf('검증') !== -1 || p.indexOf('verif') !== -1))
                || (f === 'restore' && (p.indexOf('복원') !== -1 || p.indexOf('restor') !== -1))
                || (f === 'investigate' && (p.indexOf('조사') !== -1 || p.indexOf('investig') !== -1));
        });
    }
    lines.forEach(function(l) {
        var cls = '';
        if (l.msg.indexOf('🔧') === 0) cls = ' tool';
        else if (l.msg.indexOf('💭') === 0) cls = ' reasoning';
        else if (l.msg.indexOf('📝') === 0) cls = ' text';
        h += '<div class="scen-output-line' + cls + '"><span class="scen-output-phase">[' + esc((l.phase||'').substring(0,10)) + ']</span> ' + esc(l.msg) + '</div>';
    });
    h += '</div>';
    el.innerHTML = h;
    var body = $('scenConsoleBody');
    if (body) body.scrollTop = body.scrollHeight;
}

// ── Init ──
function scenarioInit() {
    if (SCEN._loaded) return;
    SCEN._loaded = true;
    scenLoadList();
}

// ── Load List + Templates ──
function scenLoadList() {
    $('scenListView').innerHTML = '<div style="text-align:center;padding:40px;color:#475569;font-size:.68rem"><div class="arch-spinner" style="width:18px;height:18px;margin:0 auto 8px"></div>로딩 중...</div>';

    Promise.all([
        fetch('/api/scenarios?space_id=' + encodeURIComponent(_scenSpaceId())).then(function(r){return r.json()}).catch(function(){return {ok:true, scenarios:[]}}),
        fetch('/api/scenario-templates').then(function(r){return r.json()}).catch(function(){return {ok:true, templates:[]}}),
        fetch('/api/active-runs').then(function(r){return r.json()}).catch(function(){return {ok:true, runs:{}}})
    ]).then(function(results) {
        var scenData = results[0];
        var tplData = results[1];
        var activeData = results[2];

        SCEN.scenarios = (scenData.ok ? scenData.scenarios : []) || [];
        if (scenData.categories) SCEN_CAT_META = scenData.categories;
        SCEN.byCategory = {};
        SCEN.scenarios.forEach(function(s) {
            var cat = s.category || 'etc';
            if (!SCEN.byCategory[cat]) SCEN.byCategory[cat] = [];
            SCEN.byCategory[cat].push(s);
        });
        $('scenCount').textContent = SCEN.scenarios.length + '건';

        if (tplData.ok) SCEN.templates = tplData.templates || [];
        SCEN._activeRuns = activeData.runs || {};

        scenRenderList();
    });
}

// ── Render List (등록 Scenario만) ──
function scenRenderList() {
    var h = '';

    // Active runs section
    var activeRuns = SCEN._activeRuns || {};
    var activeIds = Object.keys(activeRuns);
    if (activeIds.length) {
        h += '<div class="scen-layer-block" style="border:1px solid #f59e0b30;border-radius:8px;margin-bottom:12px;padding:8px">';
        h += '<div style="font-size:.6rem;font-weight:600;color:#fbbf24;margin-bottom:6px">실행 중 (' + activeIds.length + ')</div>';
        activeIds.forEach(function(rid) {
            var r = activeRuns[rid];
            var sid = r.scenario_id || '';
            var st = r.status || '';
            var stColor = st === 'running' || st === 'verifying' || st === 'executing' ? '#fbbf24' : st === 'completed' ? '#4ade80' : '#fb923c';
            h += '<div class="scen-card" style="border-left:3px solid ' + stColor + ';cursor:pointer" onclick="SCEN.runId=\'' + esc(rid) + '\';scenSelectScenario(\'' + esc(sid) + '\')">';
            h += '<div class="scen-card-name">' + esc(r.name || sid) + '</div>';
            h += '<div class="scen-card-meta"><span style="color:' + stColor + '">' + esc(st) + '</span>';
            if (r.started_at) h += '<span>' + esc(r.started_at.substring(11, 19)) + '</span>';
            h += '<span style="color:#64748b">' + esc(rid.substring(0, 8)) + '</span></div></div>';
        });
        h += '</div>';
    }

    var cats = Object.keys(SCEN.byCategory);
    cats.sort(function(a, b) {
        var oa = (SCEN_CAT_META[a] || {}).order; if (oa == null) oa = 99;
        var ob = (SCEN_CAT_META[b] || {}).order; if (ob == null) ob = 99;
        return oa - ob;
    });

    cats.forEach(function(cat) {
        var items = SCEN.byCategory[cat];
        if (!items || !items.length) return;
        var meta = SCEN_CAT_META[cat] || {label: cat, color: '#64748b'};
        h += '<div class="scen-layer-block">';
        h += '<div class="scen-layer-hdr" onclick="scenToggleLayer(\'' + cat + '\')">';
        h += '<div class="scen-layer-dot" style="background:' + meta.color + '"></div>';
        h += '<div class="scen-layer-label">' + esc(meta.label) + '</div>';
        h += '<div class="scen-layer-cnt">' + items.length + '</div>';
        h += '<div class="scen-layer-arrow open" id="scenArrow_' + cat + '">&#9654;</div>';
        h += '</div>';
        h += '<div class="scen-layer-items" id="scenItems_' + cat + '">';
        items.forEach(function(s) {
            h += '<div class="scen-card" onclick="scenSelectScenario(\'' + esc(s.id) + '\')">';
            h += '<div class="scen-card-name">' + esc(s.name) + '</div>';
            if (s.description) h += '<div class="scen-card-desc" style="color:#94a3b8;font-size:.5rem;margin:2px 0 4px;line-height:1.3;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">' + esc(s.description) + '</div>';
            h += '<div class="scen-card-meta">';
            h += '<span>' + esc(s.id) + '</span>';
            if (s.target_service) h += '<span style="color:#38bdf8">' + esc(s.target_service) + '</span>';
            if (s.layer) h += '<span>' + esc(s.layer) + '</span>';
            if (s.verification_count) h += '<span>' + s.verification_count + ' steps</span>';
            if (s.source === 'ai-generated') h += '<span class="scen-card-badge" style="background:rgba(56,189,248,.12);color:#38bdf8">AI</span>';
            if (s.source === 'security-agent') {
                var _rlColor = {CRITICAL:'#ef4444',HIGH:'#f97316',MEDIUM:'#f59e0b',LOW:'#22c55e'}[s.risk_level] || '#64748b';
                h += '<span class="scen-card-badge" style="background:rgba(220,38,38,.12);color:' + _rlColor + '">' + esc(s.risk_level || 'SEC') + '</span>';
                if (s.last_result) {
                    var _lrColor = s.last_result.status === 'defended' ? '#4ade80' : s.last_result.status === 'vulnerable' ? '#ef4444' : '#f59e0b';
                    h += '<span class="scen-card-badge" style="background:rgba(0,0,0,.2);color:' + _lrColor + '">' + esc(s.last_result.status) + '</span>';
                }
            }
            if (s.app_name) h += '<span class="scen-card-badge" style="background:rgba(168,85,247,.12);color:#c084fc">' + esc(s.app_name) + '</span>';
            h += '</div></div>';
        });
        h += '</div></div>';
    });

    if (!h) h = '<div style="text-align:center;padding:20px;color:#475569;font-size:.62rem">등록된 Scenario가 없습니다</div>';

    $('scenListView').innerHTML = h;
}

// ── Render Template Picker (오른쪽 패널) ──
function scenRenderTemplatePicker() {
    var h = '<div class="scen-tpl-header">장애 유형 선택 <span style="color:#64748b;font-weight:400">' + SCEN.templates.length + '종</span></div>';

    SCEN_TPL_LAYERS.forEach(function(layer) {
        var items = SCEN.templates.filter(function(t) { return t.layer === layer.key; });
        if (!items.length) return;
        h += '<div class="scen-layer-block">';
        h += '<div class="scen-layer-hdr" onclick="scenToggleLayer(\'tpl_' + layer.key.replace(/\s/g,'_') + '\')">';
        h += '<div class="scen-layer-dot" style="background:' + layer.color + '"></div>';
        h += '<div class="scen-layer-label">' + esc(layer.label || layer.key) + '</div>';
        h += '<div class="scen-layer-cnt">' + items.length + '</div>';
        h += '<div class="scen-layer-arrow open" id="scenArrow_tpl_' + layer.key.replace(/\s/g,'_') + '">&#9654;</div>';
        h += '</div>';
        h += '<div class="scen-layer-items" id="scenItems_tpl_' + layer.key.replace(/\s/g,'_') + '">';
        items.forEach(function(t) {
            h += '<div class="scen-tpl-card" onclick="scenPickTemplate(\'' + esc(t.id) + '\')">';
            h += '<div class="scen-tpl-id">' + esc(t.id) + '</div>';
            h += '<div class="scen-tpl-name">' + esc(t.name) + '</div>';
            h += '<div class="scen-tpl-desc">' + esc(t.description) + '</div>';
            h += '</div>';
        });
        h += '</div></div>';
    });

    return h;
}

// ── Toggle Layer ──
function scenToggleLayer(cat) {
    var items = $('scenItems_' + cat);
    var arrow = $('scenArrow_' + cat);
    if (!items) return;
    if (items.classList.contains('collapsed')) {
        items.classList.remove('collapsed');
        if (arrow) arrow.classList.add('open');
    } else {
        items.classList.add('collapsed');
        if (arrow) arrow.classList.remove('open');
    }
}

// ── Open Template Picker (버튼 클릭 시) ──
function scenOpenPicker() {
    var side = $('scenChatSide');
    side.style.display = '';
    $('scenTplPicker').style.display = '';
    $('scenGenResult').style.display = 'none';
    $('scenGenLoading').style.display = 'none';
    $('scenGenRefine').style.display = 'none';

    $('scenTplPicker').innerHTML = '<div style="text-align:center;padding:40px;color:#475569"><div class="arch-spinner" style="width:18px;height:18px;margin:0 auto 8px"></div>로딩 중...</div>';

    var fetches = [];
    if (!SCEN.templates.length) {
        fetches.push(fetch('/api/scenario-templates').then(function(r){return r.json()}).then(function(d){ if(d.ok) SCEN.templates = d.templates || []; }).catch(function(){}));
    }
    if (!SCEN.apps.length) {
        fetches.push(fetch('/api/arch/view?level=L1&space_id=' + encodeURIComponent(_scenSpaceId())).then(function(r){return r.json()}).then(function(d){
            if(d.ok && d.app_nodes && d.app_nodes.length) {
                SCEN.apps = d.app_nodes.filter(function(a){ return a.name !== '기타'; });
            }
            if (!SCEN.apps.length) {
                return fetch('/api/space-info?space_id=' + encodeURIComponent(_scenSpaceId())).then(function(r2){return r2.json()}).then(function(d2){
                    if(d2.ok && d2.app_name) {
                        SCEN.apps = [{name: d2.app_name, count: 0, services: []}];
                    }
                });
            }
        }).catch(function(){}));
    }
    Promise.all(fetches).then(function(){ _scenRenderPickerPanel(); });
}

function _scenRenderPickerPanel() {
    var h = '';

    if (SCEN.apps.length >= 1) {
        h += '<div class="scen-tpl-header">대상 App 선택</div>';
        SCEN.apps.forEach(function(app) {
            var sel = SCEN.selectedApp === app.name;
            h += '<div class="scen-app-card' + (sel ? ' selected' : '') + '" onclick="scenSelectApp(\'' + esc(app.name) + '\')">';
            h += '<div class="scen-app-name">' + esc(app.name) + '</div>';
            h += '<div class="scen-app-info">' + app.count + '개 Service: ' + app.services.map(esc).join(', ') + '</div>';
            h += '</div>';
        });
        h += '<div style="border-top:1px solid #1e293b;margin:10px 0"></div>';
    }

    if (SCEN.selectedApp) {
        h += '<div class="scen-tpl-header">실행 모드</div>';
        h += '<div style="margin-bottom:10px">';
        h += '<select id="scenGenExecutorMode" onchange="SCEN.executorMode=this.value" style="font-size:.55rem;padding:4px 10px;border-radius:4px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;width:100%">';
        h += '<option value="">Rule Engine</option>';
        h += '<option value="agent">Single Agent</option>';
        h += '<option value="multi_agent">Multi Agent</option>';
        h += '<option value="simulation_v2" selected>Simulation v2 (Generate→Verify)</option>';
        h += '</select>';
        h += '</div>';
        h += scenRenderTemplatePicker();
    } else {
        h += '<div style="text-align:center;padding:20px;color:#64748b;font-size:.6rem">App을 선택하세요</div>';
    }
    $('scenTplPicker').innerHTML = h;
}

function scenSelectApp(name) {
    SCEN.selectedApp = name;
    _scenRenderPickerPanel();
}

// ── Pick Template → 바로 생성 ──
function scenPickTemplate(id) {
    var t = SCEN.templates.find(function(t) { return t.id === id; });
    if (!t) return;
    SCEN.selectedTemplate = t;

    // Simulation v2 모드 — SSE 기반 Generate→Verify 루프
    if (SCEN.executorMode === 'simulation_v2') {
        $('scenChatSide').style.display = 'none';
        $('scenSimSide').style.display = '';
        simLaunch(t.id, SCEN.selectedApp || '', 'default', SCEN.selectedApp || '');
        return;
    }

    $('scenTplPicker').style.display = 'none';
    $('scenGenLoading').style.display = '';
    $('scenGenResult').style.display = 'none';
    $('scenGenRefine').style.display = 'none';
    $('scenGenLoadingMsg').textContent = t.id + ' ' + t.name + ' Scenario를 생성하고 있습니다...';
    $('scenGenTitle').textContent = t.id + ' Scenario 생성';
    SCEN.chatSessionId = null;

    var appCtx = SCEN.selectedApp ? (SCEN.selectedApp + ' App의 ') : '';
    var autoMsg = t.id + ' (' + t.name + ') Template을 ' + appCtx + 'Infra에 적용해줘.\n'
        + '설명: ' + t.description + '\n'
        + '적용 조건: ' + t.applicable_when + '\n\n'
        + 'Infra를 파악해서 어떤 Service에 어떤 방식으로 적용할지 구체적으로 설명하고, '
        + 'Scenario JSON도 생성해줘.';

    fetch('/api/scenario-chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: autoMsg, session_id: null, space_id: _scenSpaceId(), template_id: t.id, app_name: SCEN.selectedApp || '', executor_mode: SCEN.executorMode || ''})
    }).then(function(r){return r.json()}).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        // Multi Agent: polling 방식
        if (data.job_id) {
            SCEN._genJobId = data.job_id;
            scenConsoleAppend('Generator', '📝 시나리오 생성 시작...');
            _scenPollGenJob(data.job_id);
            return;
        }
        SCEN.chatSessionId = data.session_id;
        scenShowGenResult(data.reply, data.scenario);
    }).catch(function(e) {
        $('scenGenLoading').style.display = 'none';
        $('scenGenResult').style.display = '';
        $('scenGenResult').innerHTML = '<div style="color:#fca5a5;font-size:.62rem;padding:20px">생성 실패: ' + esc(e.message) + '</div>';
        $('scenGenRefine').style.display = '';
    });
}

// ── 생성 job polling ──
function _scenPollGenJob(jobId) {
    fetch('/api/scenario-gen-job/' + jobId + '/status')
    .then(function(r){return r.json()})
    .then(function(data) {
        if (!data.ok) { setTimeout(function(){ _scenPollGenJob(jobId); }, 3000); return; }
        // Update console with new events
        var events = data.events || [];
        var shown = SCEN._genEventsShown || 0;
        for (var i = shown; i < events.length; i++) {
            scenConsoleAppend(events[i].phase || 'Generator', events[i].msg || '');
        }
        SCEN._genEventsShown = events.length;

        if (data.status === 'completed') {
            scenConsoleAppend('Generator', '✅ 시나리오 생성 완료');
            scenShowGenResult(data.reply || '', data.scenario);
        } else if (data.status === 'failed') {
            scenConsoleAppend('Generator', '❌ 생성 실패: ' + (data.error || ''));
            $('scenGenLoading').style.display = 'none';
            $('scenGenResult').style.display = '';
            $('scenGenResult').innerHTML = '<div style="color:#fca5a5;font-size:.62rem;padding:20px">생성 실패: ' + esc(data.error || '') + '</div>';
        } else {
            setTimeout(function(){ _scenPollGenJob(jobId); }, 2000);
        }
    }).catch(function(){
        setTimeout(function(){ _scenPollGenJob(jobId); }, 3000);
    });
}

// ── Show Generated Result as Detail View ──
function scenShowGenResult(reply, serverScenario) {
    $('scenGenLoading').style.display = 'none';
    $('scenGenResult').style.display = '';
    $('scenGenRefine').style.display = '';

    var scenario = serverScenario || null;
    if (!scenario) {
        var jsonMatch = reply.match(/```json\s*\n([\s\S]*?)\n```/);
        if (!jsonMatch) {
            $('scenGenResult').innerHTML = '<div style="padding:12px;color:#e2e8f0;font-size:.6rem;white-space:pre-wrap">' + esc(reply) + '</div>';
            return;
        }
        try { scenario = JSON.parse(jsonMatch[1]); } catch(e) {
            var fixed = jsonMatch[1]
                .replace(/,\s*([}\]])/g, '$1')
                .replace(/(["\d\w\]}])\s*\n(\s*")/g, '$1,\n$2')
                .replace(/(["\d\w\]}])\s*\n(\s*\{)/g, '$1,\n$2');
            try { scenario = JSON.parse(fixed); } catch(e2) {
                $('scenGenResult').innerHTML = '<div style="padding:12px;color:#fca5a5;font-size:.6rem">JSON 파싱 실패: ' + esc(e.message) + '</div>';
                return;
            }
        }
    }

    if (SCEN.selectedApp) scenario.app_name = SCEN.selectedApp;
    SCEN._generatedScenario = scenario;
    SCEN._generatedJson = JSON.stringify(scenario, null, 2);

    // executor가 지정된 시나리오는 해당 모듈이 검증을 담당 — UI 검증 불필요
    if (scenario.executor) {
        _scenRenderGenResult(scenario, [], []);
        return;
    }

    fetch('/api/validate-scenario', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario: scenario})
    }).then(function(r){return r.json()}).then(function(v){
        _scenRenderGenResult(scenario, v.errors || [], v.warnings || []);
    }).catch(function(){
        _scenRenderGenResult(scenario, [], []);
    });
}

function _scenRenderGenResult(scenario, errors, warnings) {
    var issues = errors.concat(warnings);
    SCEN._lastValidationIssues = issues;

    var h = '';
    if (issues.length > 0) {
        h += '<div class="scen-validation-bar">';
        h += '<div class="scen-validation-badges">';
        errors.forEach(function(e) {
            h += '<span class="scen-badge-error">' + esc(e) + '</span>';
        });
        warnings.forEach(function(w) {
            h += '<span class="scen-badge-warn">' + esc(w) + '</span>';
        });
        h += '</div>';
        h += '<button class="scen-run-btn primary" onclick="scenAutoFix()" style="background:#b45309;flex-shrink:0">자동 수정 요청</button>';
        h += '</div>';
    }

    h += '<div style="display:flex;gap:8px;margin-bottom:14px">';
    if (errors.length === 0) {
        h += '<button class="scen-run-btn primary" id="scenBtnSaveGen" onclick="scenSaveGenerated()">Scenario 저장</button>';
    } else {
        h += '<button class="scen-run-btn primary" disabled style="opacity:.4;cursor:not-allowed">검증 오류 해결 필요</button>';
    }
    h += '<button class="scen-run-btn secondary" onclick="scenOpenPicker()">다른 Template 선택</button>';
    h += '</div>';

    h += scenRenderScenarioBody(scenario);
    $('scenGenResult').innerHTML = h;
    scenFlushDetailTopo();
}

function scenAutoFix() {
    var issues = SCEN._lastValidationIssues || [];
    if (!issues.length || !SCEN.chatSessionId) return;

    var msg = '방금 생성한 Scenario에 검증 오류가 있습니다. 아래 항목을 수정해서 Scenario JSON을 다시 생성해주세요:\n\n';
    issues.forEach(function(issue, i) { msg += (i + 1) + '. ' + issue + '\n'; });
    msg += '\n수정된 전체 Scenario JSON을 ```json 코드블록으로 다시 보내주세요.';

    $('scenGenResult').style.display = 'none';
    $('scenGenLoading').style.display = '';
    $('scenGenLoadingMsg').textContent = '검증 오류를 수정하고 있습니다...';

    fetch('/api/scenario-chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg, session_id: SCEN.chatSessionId, space_id: _scenSpaceId()})
    }).then(function(r){return r.json()}).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        scenShowGenResult(data.reply, data.scenario);
    }).catch(function(e) {
        $('scenGenLoading').style.display = 'none';
        $('scenGenResult').style.display = '';
        $('scenGenResult').innerHTML = '<div style="color:#fca5a5;font-size:.62rem;padding:20px">수정 실패: ' + esc(e.message) + '</div>';
        $('scenGenRefine').style.display = '';
    });
}

function scenFlushDetailTopo() {
    if (SCEN._pendingDetailTopo) {
        var arch = SCEN._pendingDetailTopo;
        SCEN._pendingDetailTopo = null;
        setTimeout(function() { scenRenderFaultTopo('scenDetailFaultSvg', arch, []); }, 50);
    }
}

// ── Render scenario body (공통 — detail과 generated 모두 사용) ──
function scenRenderScenarioBody(s) {
    var h = '';

    h += '<div class="scen-detail-hdr">';
    h += '<div class="scen-detail-title">' + esc(s.name || '') + '</div>';
    h += '<div class="scen-detail-id">' + esc(s.id || '') + ' &middot; ' + esc(s.category || '') + ' &middot; ' + esc(s.layer || '') + '</div>';
    if (s.source === 'ai-generated' || s.app_name) {
        h += '<div style="margin-top:4px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">';
        if (s.source === 'ai-generated') {
            h += '<span style="background:rgba(56,189,248,.12);color:#38bdf8;padding:2px 8px;border-radius:4px;font-size:.5rem;font-weight:600">AI 생성</span>';
            if (s.failure_mode_id) h += '<span style="color:#64748b;font-size:.48rem">' + esc(s.failure_mode_id) + ' 기반</span>';
        }
        if (s.app_name) h += '<span style="background:rgba(168,85,247,.12);color:#c084fc;padding:2px 8px;border-radius:4px;font-size:.5rem;font-weight:600">' + esc(s.app_name) + '</span>';
        h += '</div>';
    }
    h += '</div>';

    if (s.description) {
        h += '<div class="scen-detail-section"><h4>설명</h4>';
        h += '<div class="scen-detail-val">' + esc(s.description) + '</div></div>';
    }
    if (s.target_service) {
        h += '<div class="scen-detail-section"><h4>대상 서비스</h4>';
        h += '<div class="scen-detail-val">' + esc(s.target_service) + '</div></div>';
    }
    if (s.purpose) {
        h += '<div class="scen-detail-section"><h4>목적</h4>';
        h += '<div class="scen-detail-val">' + esc(s.purpose) + '</div></div>';
    }
    if (s.expected_root_cause) {
        h += '<div class="scen-detail-section"><h4>예상 근본 원인</h4>';
        h += '<div class="scen-detail-val">' + esc(s.expected_root_cause) + '</div></div>';
    }
    // Fault topology visualization (architecture.components + fault_path)
    if (s.architecture && s.architecture.components && s.architecture.components.length) {
        h += '<div class="scen-detail-section"><h4>장애 전파 Topology</h4>';
        h += '<svg id="scenDetailFaultSvg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;min-height:180px;background:#0a0f1a;border-radius:8px;border:1px solid #1e293b"></svg>';
        h += '</div>';
        SCEN._pendingDetailTopo = s.architecture;
    } else if (s.architecture && typeof s.architecture === 'string') {
        h += '<div class="scen-detail-section"><h4>Architecture</h4>';
        h += '<div class="scen-detail-val">' + esc(s.architecture) + '</div></div>';
    }
    // Normal + Fault flow text
    if (s.normal_flow && s.normal_flow.length) {
        h += '<div class="scen-detail-section"><h4>정상 흐름</h4>';
        s.normal_flow.forEach(function(f) {
            h += '<div class="scen-flow-step"><span class="step-label">' + esc(f.step || '') + '</span><span class="step-desc">' + esc(f.desc || '') + '</span></div>';
        });
        h += '</div>';
    }
    if (s.fault_flow && s.fault_flow.length) {
        h += '<div class="scen-detail-section"><h4>장애 흐름</h4>';
        s.fault_flow.forEach(function(f) {
            h += '<div class="scen-flow-step"><span class="step-label" style="color:#fca5a5">' + esc(f.step || '') + '</span><span class="step-desc">' + esc(f.desc || '') + '</span></div>';
        });
        h += '</div>';
    }
    if (s.trigger && s.trigger.command) {
        h += '<div class="scen-detail-section"><h4>트리거 (' + esc(s.trigger.type || '') + ')</h4>';
        h += '<pre class="scen-code-block">' + esc(s.trigger.command) + '</pre></div>';
    }
    if (s.restore && s.restore.command) {
        h += '<div class="scen-detail-section"><h4>복원 명령</h4>';
        h += '<pre class="scen-code-block">' + esc(s.restore.command) + '</pre></div>';
    }

    // Security scenario: attack path + attack steps + execution
    if (s.source === 'security-agent') {
        if (s.topology_edges && s.topology_edges.length) {
            h += '<div class="scen-detail-section"><h4>공격 경로</h4>';
            h += '<svg id="scenSecAttackPathSvg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:80px;background:#0a0f1a;border-radius:8px;border:1px solid #1e293b"></svg>';
            h += '</div>';
            SCEN._pendingSecAttackPath = s.topology_edges;
        }
        if (s.attack_steps && s.attack_steps.length) {
            h += '<div class="scen-detail-section" id="scenSecStepsSection"><h4>Attack Steps (' + s.attack_steps.length + ')</h4>';
            s.attack_steps.forEach(function(step, i) {
                h += '<div style="margin-bottom:6px;padding:6px 8px;background:#0f172a;border-radius:4px;border-left:2px solid #dc2626">';
                h += '<div style="font-size:.5rem;font-weight:600;color:#f8fafc">' + (i + 1) + '. ' + esc(step.method || 'GET') + ' ' + esc(step.path || '/') + '</div>';
                if (step.description) h += '<div style="font-size:.45rem;color:#94a3b8;margin-top:2px">' + esc(step.description) + '</div>';
                if (step.body) h += '<pre style="font-size:.42rem;color:#64748b;margin:3px 0 0;overflow-x:auto">' + esc(step.body) + '</pre>';
                h += '</div>';
            });
            h += '</div>';
        }
        if (s.last_result) {
            var _lr = s.last_result;
            var _lrc = _lr.status === 'defended' ? '#4ade80' : _lr.status === 'vulnerable' ? '#ef4444' : '#f59e0b';
            h += '<div class="scen-detail-section"><h4>마지막 실행 결과</h4>';
            h += '<div style="padding:8px;background:#0f172a;border-radius:6px;border:1px solid ' + _lrc + '40">';
            h += '<div style="font-size:.56rem;font-weight:700;color:' + _lrc + '">' + esc(_lr.status) + '</div>';
            h += '<div style="font-size:.45rem;color:#94a3b8;margin-top:3px">' + esc(_lr.detail || '') + '</div>';
            if (_lr.executed_at) h += '<div style="font-size:.42rem;color:#475569;margin-top:3px">' + esc(_lr.executed_at.substring(0, 19).replace('T', ' ')) + ' · ' + (_lr.duration || 0).toFixed(1) + 's</div>';
            h += '</div></div>';
        }
    }

    return h;
}

// ── Save Generated Scenario ──
function scenSaveGenerated() {
    if (!SCEN._generatedScenario) return;
    var btn = $('scenBtnSaveGen');
    btn.disabled = true;
    btn.textContent = '저장 중...';
    fetch('/api/arch/save-scenario', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario: SCEN._generatedScenario, space_id: _scenSpaceId()})
    }).then(function(r){return r.json()}).then(function(data){
        if (!data.ok) throw new Error(data.error);
        btn.textContent = '저장 완료';
        btn.style.background = '#166534';
        scenLoadList();
    }).catch(function(e){
        btn.disabled = false;
        btn.textContent = 'Scenario 저장';
        alert('저장 실패: ' + e.message);
    });
}

// ── Refine: 추가 요청 전송 ──
function scenRefineSend() {
    var input = $('scenChatInput');
    var text = input.value.trim();
    if (!text || !SCEN.chatSessionId) return;
    input.value = '';

    $('scenGenResult').style.display = 'none';
    $('scenGenLoading').style.display = '';
    $('scenGenLoadingMsg').textContent = '수정된 Scenario를 생성하고 있습니다...';

    fetch('/api/scenario-chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text, session_id: SCEN.chatSessionId, space_id: (typeof SELECTED !== 'undefined' ? SELECTED : '')})
    }).then(function(r){return r.json()}).then(function(data) {
        if (!data.ok) throw new Error(data.error);
        scenShowGenResult(data.reply, data.scenario);
    }).catch(function(e) {
        $('scenGenLoading').style.display = 'none';
        $('scenGenResult').style.display = '';
        $('scenGenResult').innerHTML = '<div style="color:#fca5a5;font-size:.62rem;padding:20px">수정 실패: ' + esc(e.message) + '</div>';
    });
}

// ── Select Scenario ──
function scenSelectScenario(id) {
    $('scenListView').style.display = 'none';
    $('scenDetailView').style.display = '';
    $('scenDetailView').innerHTML = '<div style="text-align:center;padding:40px;color:#475569"><div class="arch-spinner" style="width:18px;height:18px;margin:0 auto 8px"></div></div>';

    var preservedRunId = SCEN.runId;
    fetch('/api/scenarios/' + encodeURIComponent(id) + '?space_id=' + encodeURIComponent(_scenSpaceId())).then(function(r){return r.json()}).then(function(data){
        if (!data.ok) throw new Error(data.error);
        SCEN.current = data.scenario;
        if (!preservedRunId) SCEN.runId = null;
        scenRenderDetail(data.scenario);
        scenLoadHistory(id);
        if (preservedRunId) {
            SCEN.runId = preservedRunId;
            scenPollStatus();
        }
    }).catch(function(e){
        $('scenDetailView').innerHTML = '<div style="color:#fca5a5;font-size:.62rem">로드 실패: ' + esc(e.message) + '</div>';
    });
}

// ── Render Detail ──
function scenRenderDetail(s) {
    SCEN._liveDagRecordCount = 0;
    SCEN._liveDagFetching = false;
    var h = '';
    h += '<div class="scen-detail-back" onclick="scenBackToList()">&larr; 목록으로</div>';

    h += scenRenderScenarioBody(s);

    h += '<div class="scen-run-bar">';
    if (s.source === 'security-agent') {
        h += '<button class="scen-run-btn primary" id="scenBtnRun" onclick="scenRunScenario()" style="background:#dc2626">&#9654; 방어 확인 (Attack Replay)</button>';
        h += '<span id="scenSecRunStatus" style="font-size:.52rem;color:#64748b;margin-left:8px"></span>';
    } else {
        h += '<button class="scen-run-btn primary" id="scenBtnRun" onclick="scenRunScenario()">&#9654; 실행 &amp; 검증</button>';
        h += '<button class="scen-run-btn secondary" id="scenBtnReview" onclick="scenReviewScenario()" style="background:#1e40af">리뷰</button>';
        h += '<button class="scen-run-btn danger" id="scenBtnCancel" onclick="scenCancelRun()" style="display:none">취소</button>';
        h += '<button class="scen-run-btn secondary" id="scenBtnRestore" onclick="scenRestore()"' + (s.restore && s.restore.command ? '' : ' style="display:none"') + '>복원</button>';
    }
    h += '<button class="scen-run-btn danger" id="scenBtnDelete" onclick="scenDeleteScenario()" style="margin-left:auto">삭제</button>';
    h += '<span id="scenRunTimer" style="font-size:.58rem;color:#64748b"></span>';
    h += '</div>';
    h += '<div id="scenRunArea"></div>';
    h += '<div id="scenAnalysisArea" style="display:none">';
    h += '<div class="scen-analysis-grid">';
    // Row 1: Rule-based DAG (left) + Bedrock DAG (right)
    h += '<div class="scen-analysis-cell" id="scenLiveDagCell">';
    h += '<div class="scen-analysis-title" style="display:flex;align-items:center;gap:8px">룰 기반 DAG <span id="scenLiveDagBadge" style="font-size:.5rem;padding:2px 8px;border-radius:4px;background:#f59e0b20;color:#fbbf24;font-weight:600">LIVE</span> <span id="scenLiveDagCount" style="font-size:.5rem;color:#64748b"></span></div>';
    h += '<svg id="scenLiveDagSvg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="display:block;width:100%;height:120px;background:#0a0f1a;border-radius:8px;border:1px solid #1e293b;transition:height 0.5s ease"></svg>';
    h += '</div>';
    h += '<div class="scen-analysis-cell" id="scenDagSection"></div>';
    // Row 2: Hypothesis (left) + Rubric (right)
    h += '<div class="scen-analysis-cell" id="scenHypothesisSection"></div>';
    h += '<div class="scen-analysis-cell" id="scenRubricSection"></div>';
    h += '</div>';
    h += '</div>';
    h += '<div id="scenHistoryArea"></div>';

    $('scenDetailView').innerHTML = h;
    scenFlushDetailTopo();
    scenFlushSecAttackPath();
}

// ── Back to List ──
function scenBackToList() {
    scenStopPoll();
    SCEN.current = null;
    SCEN.runId = null;
    $('scenDetailView').style.display = 'none';
    $('scenListView').style.display = '';
}

// ── Run Scenario ──
var _scenRunStart = 0;
var _scenTimerInterval = null;

function scenRunScenario() {
    if (!SCEN.current) return;

    // 보안 시나리오는 security-specific execution path 사용
    if (SCEN.current.source === 'security-agent') {
        scenRunSecurityCheck(SCEN.current.finding_id || '', SCEN.current.id || '');
        return;
    }

    var btn = $('scenBtnRun');
    btn.disabled = true;
    $('scenBtnCancel').style.display = '';
    _scenRunStart = Date.now();
    _scenTimerInterval = setInterval(function(){
        var sec = Math.floor((Date.now() - _scenRunStart) / 1000);
        $('scenRunTimer').textContent = sec + 's';
    }, 1000);

    $('scenRunArea').innerHTML = '<div class="scen-run-banner running">실행 중...</div><div id="scenTimeline"></div>';
    var analysisArea = $('scenAnalysisArea');
    if (analysisArea) { analysisArea.style.display = 'none'; }
    var liveSvg = $('scenLiveDagSvg'); if (liveSvg) liveSvg.innerHTML = '';
    var liveBadge = $('scenLiveDagBadge'); if (liveBadge) { liveBadge.textContent = 'LIVE'; liveBadge.style.background = '#f59e0b20'; liveBadge.style.color = '#fbbf24'; }
    var liveCount = $('scenLiveDagCount'); if (liveCount) liveCount.textContent = '';
    var dagSec = $('scenDagSection'); if (dagSec) dagSec.innerHTML = '';
    var hypSec = $('scenHypothesisSection'); if (hypSec) hypSec.innerHTML = '';
    var rubSec = $('scenRubricSection'); if (rubSec) rubSec.innerHTML = '';
    SCEN._liveDagRecordCount = 0;
    SCEN._liveDagFetching = false;
    SCEN._lastRunId = null;

    var executor = (SCEN.current.executor || '');
    if (executor === 'agent' && typeof EventSource !== 'undefined') {
        scenRunViaSSE();
    } else {
        scenRunViaPoll();
    }
}

function scenRunViaPoll() {
    var url = '/api/scenario-run/' + encodeURIComponent(SCEN.current.id) + '?space_id=' + encodeURIComponent(_scenSpaceId());
    fetch(url, {method: 'POST'})
    .then(function(r){return r.json()})
    .then(function(data){
        if (data.error) throw new Error(data.error);
        SCEN.runId = data.run_id;
        scenPollStatus();
    }).catch(function(e){
        $('scenRunArea').innerHTML = '<div class="scen-run-banner fail">실행 실패: ' + esc(e.message) + '</div>';
        $('scenBtnRun').disabled = false;
        $('scenBtnCancel').style.display = 'none';
        scenStopTimer();
    });
}

function scenRunViaSSE() {
    var url = '/api/scenario-execute/stream?scenario_id='
        + encodeURIComponent(SCEN.current.id)
        + '&space_id=' + encodeURIComponent(_scenSpaceId());
    var es = new EventSource(url);
    SCEN._sseSource = es;

    es.onmessage = function(ev) {
        var d;
        try { d = JSON.parse(ev.data); } catch(e) { return; }

        if (d.type === 'run_started') {
            SCEN.runId = d.run_id;
        }
        if (d.type === 'phase_start') {
            scenSSEUpdateBanner(d.label || d.phase);
        }
        if (d.type === 'preflight_result') {
            scenSSERenderPreflight(d.results, d.ok);
        }
        if (d.type === 'trigger_result') {
            scenSSERenderTrigger(d.output, d.ok);
        }
        if (d.type === 'step_update') {
            scenSSEUpdateStep(d.step);
        }
        if (d.type === 'agent_tools') {
            scenSSERenderTools(d.tools);
        }
        if (d.type === 'complete') {
            es.close();
            SCEN._sseSource = null;
            scenStopTimer();
            $('scenBtnRun').disabled = false;
            $('scenBtnCancel').style.display = 'none';
            if (d.run) {
                scenRenderTimeline(d.run);
                if (d.run.investigation_task_id) {
                    scenUpdateLiveDag(d.run.investigation_task_id, d.run.status);
                    scenFinalizeLiveDag();
                    scenShowInvestigationLink(d.run);
                }
            } else if (SCEN.runId) {
                scenPollStatus();
            }
            scenShowImproveBtn();
        }
        if (d.type === 'error') {
            es.close();
            SCEN._sseSource = null;
            scenStopTimer();
            $('scenBtnRun').disabled = false;
            $('scenBtnCancel').style.display = 'none';
            $('scenRunArea').innerHTML = '<div class="scen-run-banner fail">오류: ' + esc(d.error) + '</div>';
        }
    };

    es.onerror = function() {
        if (es.readyState === 2) {
            es.close();
            SCEN._sseSource = null;
            if (SCEN.runId) scenPollStatus();
        }
    };
}

// ── 실패 step 액션 패널 ──
function _scenFailActionHtml(step, runId) {
    var cat = step.error_category || 'command_error';
    var idx = step.index;
    var h = '<div class="scen-action-panel">';
    h += '<div class="scen-action-cat"><span class="scen-error-cat ' + esc(cat) + '">' + esc(cat) + '</span> ';
    h += '<span class="scen-action-reason">' + esc(step.error_reason || step.detail || '') + '</span></div>';

    if (cat === 'timeout') {
        h += '<div class="scen-action-row">';
        h += '<button class="scen-action-btn" onclick="scenRetryFromStep(' + idx + ')">이 단계부터 재실행</button>';
        h += '<button class="scen-action-btn secondary" onclick="scenResumeScript(' + idx + ')">스크립트 resume (CP' + (parseInt(idx)+1) + '~)</button>';
        h += '</div>';
    } else if (cat === 'command_error' || cat === 'config_error') {
        h += '<div class="scen-action-row">';
        h += '<button class="scen-action-btn" onclick="scenAskCorrection(\'' + esc(runId) + '\')">Agent 교정 요청</button>';
        h += '<button class="scen-action-btn secondary" onclick="scenRetryFromStep(' + idx + ')">그대로 재시도</button>';
        h += '</div>';
        h += '<div class="scen-action-cmd">';
        h += '<input type="text" class="scen-cmd-input" id="scenCmd_' + idx + '" placeholder="직접 명령어 입력 후 실행..." />';
        h += '<button class="scen-action-btn mini" onclick="scenRunCustomCmd(\'' + esc(runId) + '\',' + idx + ')">실행</button>';
        h += '</div>';
    } else if (cat === 'infra_missing') {
        h += '<div class="scen-action-row">';
        h += '<span class="scen-action-blocked">인프라 부재 — 수동 조치 필요</span>';
        h += '</div>';
        h += '<div class="scen-action-cmd">';
        h += '<input type="text" class="scen-cmd-input" id="scenCmd_' + idx + '" placeholder="수동 명령어 입력..." />';
        h += '<button class="scen-action-btn mini" onclick="scenRunCustomCmd(\'' + esc(runId) + '\',' + idx + ')">실행</button>';
        h += '</div>';
    } else if (cat === 'transient') {
        h += '<div class="scen-action-row">';
        h += '<button class="scen-action-btn" onclick="scenRetryFromStep(' + idx + ')">재시도</button>';
        h += '</div>';
    }
    h += '</div>';
    return h;
}

function scenRetryFromStep(stepIndex) {
    if (!SCEN.runId) return;
    fetch('/api/scenario-run/' + SCEN.runId + '/retry/' + stepIndex, {method:'POST'})
    .then(function(r){return r.json()})
    .then(function(d){
        if (d.action === 'new_run') {
            scenResumeScript(d.resume_from, d.scenario_id);
        } else if (d.success || d.ok) {
            SCEN.pollTimer = setTimeout(scenPollStatus, 1000);
        } else {
            alert(d.error || 'Retry failed');
        }
    });
}

function scenResumeScript(fromStep, scenarioId) {
    var sid = scenarioId || SCEN.scenarioId;
    if (!sid) return;
    fetch('/api/scenario-run/' + sid + '?resume_from=' + fromStep, {method:'POST'})
    .then(function(r){return r.json()})
    .then(function(d){
        if (d.ok) {
            SCEN.runId = d.run_id;
            SCEN.pollTimer = setTimeout(scenPollStatus, 1000);
        } else {
            alert(d.error || 'Resume failed');
        }
    });
}

function scenAskCorrection(runId) {
    var btn = event.target;
    btn.disabled = true;
    btn.textContent = '교정 요청 중...';
    fetch('/api/scenario-run/' + runId + '/correct', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({})
    })
    .then(function(r){return r.json()})
    .then(function(d){
        btn.disabled = false;
        btn.textContent = 'Agent 교정 요청';
        if (d.correction && d.correction.reply) {
            var el = document.getElementById('scenCorrectionResult');
            if (el) {
                el.style.display = 'block';
                el.innerHTML = '<div class="scen-correction-reply">' + esc(d.correction.reply.substring(0,500)) + '</div>';
                if (d.correction.has_corrected_json) {
                    el.innerHTML += '<div style="color:#4ade80;font-size:.48rem;margin-top:4px">교정된 시나리오 JSON 수신 — 재실행하면 반영됩니다</div>';
                }
            }
        }
        if (d.actions) {
            d.actions.forEach(function(a) {
                if (a.action === 'retry_success') scenPollStatus();
            });
        }
    })
    .catch(function(e){
        btn.disabled = false;
        btn.textContent = 'Agent 교정 요청';
    });
}

function scenRunCustomCmd(runId, stepIndex) {
    var input = document.getElementById('scenCmd_' + stepIndex);
    if (!input || !input.value.trim()) return;
    var cmd = input.value.trim();
    input.disabled = true;
    fetch('/api/scenario-run/' + runId + '/exec-cmd', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({command: cmd, step_index: stepIndex})
    })
    .then(function(r){return r.json()})
    .then(function(d){
        input.disabled = false;
        var resultEl = input.parentElement.querySelector('.scen-cmd-result');
        if (!resultEl) {
            resultEl = document.createElement('div');
            resultEl.className = 'scen-cmd-result';
            input.parentElement.appendChild(resultEl);
        }
        if (d.ok) {
            resultEl.innerHTML = '<pre class="scen-cmd-output">' + esc((d.stdout || '').substring(0,500)) + '</pre>';
            if (d.stderr) resultEl.innerHTML += '<pre class="scen-cmd-stderr">' + esc(d.stderr.substring(0,300)) + '</pre>';
        } else {
            resultEl.innerHTML = '<pre class="scen-cmd-stderr">' + esc(d.error || 'Failed') + '</pre>';
        }
    })
    .catch(function(){ input.disabled = false; });
}

function scenPollStatus() {
    if (!SCEN.runId) return;
    SCEN._pollNotFoundCount = SCEN._pollNotFoundCount || 0;
    fetch('/api/scenario-run/' + SCEN.runId + '/status')
    .then(function(r){
        if (r.status === 404) {
            SCEN._pollNotFoundCount++;
            if (SCEN._pollNotFoundCount >= 3) {
                scenStopTimer();
                var el = $('scenRunTimeline');
                if (el) el.innerHTML = '<div class="scen-run-banner fail">실행 데이터 소실 (앱 재시작으로 인한 세션 초기화)</div>';
                $('scenBtnRun').disabled = false;
                $('scenBtnCancel').style.display = 'none';
                return null;
            }
        } else { SCEN._pollNotFoundCount = 0; }
        return r.json();
    })
    .then(function(data){
        if (!data) return;
        if (data.error) { SCEN.pollTimer = setTimeout(scenPollStatus, 5000); return; }
        scenRenderTimeline(data);
        if (data.investigation_task_id)
            scenUpdateLiveDag(data.investigation_task_id, data.status);
        if (data.status === 'running' || data.status === 'verifying' || data.status === 'executing') {
            var interval = (data.status === 'executing') ? 2000 : 5000;
            SCEN.pollTimer = setTimeout(scenPollStatus, interval);
        } else {
            var invPending = data.steps && data.steps.some(function(s){ return s.name.indexOf('조사 종료') >= 0 && s.status === 'checking'; });
            if (invPending) {
                SCEN.pollTimer = setTimeout(scenPollStatus, 10000);
            } else {
                scenStopTimer();
                $('scenBtnRun').disabled = false;
                $('scenBtnCancel').style.display = 'none';
                if ((data.status === 'completed' || data.status === 'done') && data.investigation_task_id) {
                    scenFinalizeLiveDag();
                    scenShowInvestigationLink(data);
                }
            }
        }
    }).catch(function(){
        SCEN.pollTimer = setTimeout(scenPollStatus, 5000);
    });
}

var _scenLogOpen = false;

function _scenPhaseIcon(phase, currentPhase, isDone) {
    if (isDone) return 'done';
    if (phase === currentPhase) return 'active';
    return 'pending';
}

function _scenRenderEvents(st) {
    if (!st.events || !st.events.length) return '';
    var evLimit = (st.status === 'checking') ? st.events.length : Math.min(st.events.length, 8);
    var evStart = Math.max(0, st.events.length - evLimit);
    var h = '<div class="scen-tl-events" style="margin-top:3px;padding:3px 6px;background:rgba(15,23,42,.6);border-radius:4px;font-size:.48rem;color:#94a3b8;max-height:120px;overflow-y:auto;white-space:pre-wrap;word-break:break-word">';
    for (var ei = evStart; ei < st.events.length; ei++) {
        var ev = st.events[ei];
        var evTime = ev.t ? new Date(ev.t * 1000).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '';
        var evColor = (ev.msg && ev.msg.indexOf('PASS') >= 0) ? '#4ade80' : (ev.msg && (ev.msg.indexOf('FAIL') >= 0 || ev.msg.indexOf('ERROR') >= 0)) ? '#fca5a5' : (ev.msg && ev.msg.indexOf('retry') >= 0) ? '#fbbf24' : '#94a3b8';
        h += '<div style="color:' + evColor + '">' + esc(evTime) + ' ' + esc((ev.msg||'')) + '</div>';
    }
    if (evStart > 0) h += '<div style="color:#475569">... +' + evStart + '건 이전 이벤트</div>';
    h += '</div>';
    return h;
}

function scenRenderTimeline(data) {
    var status = data.status || '';
    var result = data.result || '';
    var steps = data.steps || [];
    var checkpoints = data.checkpoints || [];
    var scriptLog = data.script_log || [];
    var scriptOutput = data.script_output || {};
    var alarmResults = data.alarm_results || [];
    var currentPhase = data.current_phase || '';
    var jsonEvents = data.json_events || [];

    // Push step events to Agent Console
    var _lastConsoleIdx = SCEN._lastConsoleIdx || 0;
    var allEvts = [];
    steps.forEach(function(st) { (st.events || []).forEach(function(ev) { allEvts.push({phase: st.name, msg: ev.msg, t: ev.t}); }); });
    allEvts.sort(function(a,b){ return (a.t||0)-(b.t||0); });
    for (var ci = _lastConsoleIdx; ci < allEvts.length; ci++) {
        scenConsoleAppend(allEvts[ci].phase || '', allEvts[ci].msg || '');
    }
    SCEN._lastConsoleIdx = allEvts.length;
    var isDone = (status === 'completed' || status === 'done' || status === 'cancelled');
    var elapsed = data.started_ts ? Math.floor((Date.now()/1000) - data.started_ts) : 0;
    if (isDone && data.completed_at && data.started_ts) {
        elapsed = Math.floor(new Date(data.completed_at).getTime()/1000 - data.started_ts);
    }

    // Banner
    var bannerClass = 'running';
    var bannerText = '';
    if (status === 'cancelled') {
        bannerClass = 'fail';
        bannerText = 'CANCELLED (' + elapsed + 's)';
    } else if (isDone) {
        var invChecking = steps.some(function(s){ return s.name.indexOf('조사 종료') >= 0 && s.status === 'checking'; });
        if (invChecking) {
            bannerClass = 'checking';
            bannerText = 'INVESTIGATING (' + elapsed + 's)';
        } else {
            bannerClass = result === 'pass' ? 'pass' : 'fail';
            bannerText = (result === 'pass' ? 'PASS' : 'FAIL') + ' (' + elapsed + 's)';
        }
    } else if (status === 'executing') {
        var cpPass = 0, cpTotal = checkpoints.length;
        checkpoints.forEach(function(c){ if(c.status==='PASS') cpPass++; });
        bannerText = '스크립트 실행 중';
        if (cpTotal > 0) bannerText += ' (CP ' + cpPass + '/' + cpTotal + ')';
        bannerText += ' — ' + elapsed + 's';
    } else if (status === 'verifying') {
        bannerText = '검증 중 — ' + elapsed + 's';
    } else if (status === 'running' && currentPhase) {
        var cpPass = 0, cpTotal = checkpoints.length;
        checkpoints.forEach(function(c){ if(c.status==='PASS') cpPass++; });
        var phaseMatch = currentPhase.match(/step_(\d+)/);
        var stepName = '';
        if (phaseMatch) {
            var stepNum = parseInt(phaseMatch[1]);
            var lastEvt = jsonEvents.filter(function(e){ return e.event === 'step_start' && e.step === stepNum; });
            if (lastEvt.length) stepName = lastEvt[lastEvt.length-1].name || '';
        }
        bannerText = stepName ? ('Step ' + phaseMatch[1] + ': ' + stepName) : '실행 중';
        if (cpTotal > 0) bannerText += ' (' + cpPass + '/' + cpTotal + ')';
        bannerText += ' — ' + elapsed + 's';
    } else {
        bannerText = (data.trigger_output || '실행 중...') + ' — ' + elapsed + 's';
    }
    var h = '<div class="scen-run-banner ' + bannerClass + '">' + esc(bannerText) + '</div>';

    // ── 단일 타임라인: 모든 steps를 순서대로 렌더링 ──
    h += '<div class="scen-phase">';
    h += '<div class="scen-phase-hdr"><span class="phase-dot ' + (isDone ? (result === 'pass' ? 'done' : 'fail') : 'active') + '"></span> 시나리오 실행</div>';

    // ScriptExecutor script_log 파싱 (steps.py 기반 실행의 경우)
    var scriptSteps = [];
    var curStep = null;
    var stepRe = /^===\s*Step\s*(\d+)\s*:\s*(.+?)\s*(?:===|\(|$)/;
    var cpRe = /^CHECKPOINT\|(\d+)\|(.+?)\|(\w+)\|(.*)$/;
    var pollRe = /^\s*\[(\d+)s\/(\d+)s\]\s*(.+)/;

    // json_events 기반 파싱 (PythonScriptExecutor EVENT| 형식)
    if (jsonEvents.length > 0) {
        jsonEvents.forEach(function(ev) {
            if (ev.event === 'step_start' && ev.step > 0) {
                var existing = scriptSteps.findIndex(function(s){ return s.num === ev.step; });
                if (existing >= 0) {
                    curStep = scriptSteps[existing];
                    curStep.cp = null;
                    curStep.lines = [];
                    curStep.polls = [];
                } else {
                    curStep = { num: ev.step, name: ev.name || '', lines: [], cp: null, polls: [] };
                    scriptSteps.push(curStep);
                }
            } else if (ev.event === 'step_pass' && curStep) {
                curStep.cp = { step: ev.step, name: ev.name || curStep.name, status: 'PASS', detail: ev.detail || '' };
                curStep = null;
            } else if (ev.event === 'step_fail' && curStep) {
                curStep.cp = { step: ev.step, name: ev.name || curStep.name, status: 'FAIL', detail: ev.detail || '' };
                if (ev.error_category) curStep.cp.error_category = ev.error_category;
                curStep = null;
            } else if (ev.event === 'step_log' && curStep) {
                var msg = ev.message || '';
                var pm = msg.match(pollRe);
                if (pm) { curStep.polls.push({ elapsed: parseInt(pm[1]), total: parseInt(pm[2]), msg: pm[3] }); }
                else if (msg) { curStep.lines.push(msg); }
            } else if (ev.event === 'step_retry' && curStep) {
                curStep.lines.push('재시도 ' + ev.attempt + '/' + ev.max_attempts);
            }
        });
    } else {
        // Legacy text-based parsing (bash ScriptExecutor)
        scriptLog.forEach(function(line) {
            var sm = line.match(stepRe);
            if (sm) {
                curStep = { num: parseInt(sm[1]), name: sm[2].replace(/\s*===\s*$/, '').trim(), lines: [], cp: null, polls: [] };
                scriptSteps.push(curStep);
                return;
            }
            var cm = line.match(cpRe);
            if (cm && curStep) {
                curStep.cp = { step: parseInt(cm[1]), name: cm[2], status: cm[3], detail: cm[4] };
                return;
            }
            if (curStep) {
                var pm = line.match(pollRe);
                if (pm) { curStep.polls.push({ elapsed: parseInt(pm[1]), total: parseInt(pm[2]), msg: pm[3] }); }
                var trimmed = line.trim();
                if (trimmed && !trimmed.startsWith('RESULT|')) curStep.lines.push(trimmed);
            }
        });
    }
    checkpoints.forEach(function(cp) {
        var found = scriptSteps.find(function(s){ return s.num === cp.step; });
        if (found && !found.cp) found.cp = cp;
    });

    // ScriptExecutor steps (script_log 기반)
    if (scriptSteps.length) {
        scriptSteps.forEach(function(ss) {
            var cp = ss.cp;
            var stepStatus = cp ? cp.status : (isDone ? 'TIMEOUT' : 'RUNNING');
            var icon = stepStatus === 'PASS' ? '&#9989;' : (stepStatus === 'FAIL' ? '&#10060;' : (stepStatus === 'RUNNING' ? '&#9203;' : '&#9888;'));
            var stClass = stepStatus === 'PASS' ? 'pass' : (stepStatus === 'FAIL' ? 'fail' : (stepStatus === 'RUNNING' ? 'checking' : 'fail'));
            h += '<div class="scen-tl-step ' + stClass + '">';
            h += '<div class="scen-tl-icon">' + icon + '</div>';
            h += '<div class="scen-tl-body">';
            h += '<div class="scen-tl-name">Step ' + ss.num + ': ' + esc(ss.name);
            if (stepStatus === 'TIMEOUT') h += ' <span class="scen-error-cat timeout">timeout</span>';
            h += '</div>';
            if (cp && cp.detail) h += '<div class="scen-tl-detail">' + esc(cp.detail.substring(0,200)) + '</div>';
            if (ss.polls.length) {
                var lastPoll = ss.polls[ss.polls.length - 1];
                var pct = Math.min(100, Math.floor(lastPoll.elapsed / lastPoll.total * 100));
                h += '<div class="scen-alarm-bar" style="margin:3px 0"><div class="scen-alarm-fill" style="width:' + pct + '%"></div></div>';
                h += '<div class="scen-tl-detail" style="font-size:.45rem">' + lastPoll.elapsed + 's/' + lastPoll.total + 's — ' + esc(lastPoll.msg) + '</div>';
            }
            var keyLines = ss.lines.filter(function(l){ return !l.match(pollRe) && !l.startsWith('CHECKPOINT|') && l.length > 0; });
            if (keyLines.length) {
                h += '<div class="scen-tl-events" style="margin-top:3px;padding:3px 6px;background:rgba(15,23,42,.6);border-radius:4px;font-size:.48rem;color:#94a3b8;max-height:120px;overflow-y:auto">';
                keyLines.slice(0, 8).forEach(function(l){ h += '<div>' + esc(l.substring(0,150)) + '</div>'; });
                if (keyLines.length > 8) h += '<div style="color:#475569">... +' + (keyLines.length - 8) + '줄</div>';
                h += '</div>';
            }
            h += '</div>';
            if (isDone && (stepStatus === 'FAIL' || stepStatus === 'TIMEOUT')) {
                var fakeStep = {
                    index: ss.num - 1,
                    error_category: stepStatus === 'TIMEOUT' ? 'timeout' : (cp && cp.detail && cp.detail.match(/not found|NotFound/) ? 'infra_missing' : 'command_error'),
                    error_reason: cp ? cp.detail : 'timeout',
                    detail: cp ? cp.detail : 'script timeout'
                };
                h += _scenFailActionHtml(fakeStep, data.run_id || SCEN.runId);
            }
            h += '</div>';
        });
    }

    // Classic verifier steps (pipeline + verification 통합)
    steps.forEach(function(st) {
        var icon = '&#9723;';
        var stClass = 'pending';
        if (st.status === 'pass') { icon = '&#9989;'; stClass = 'pass'; }
        else if (st.status === 'fail') { icon = '&#10060;'; stClass = 'fail'; }
        else if (st.status === 'checking') { icon = '&#9203;'; stClass = 'checking'; }
        else if (st.status === 'warn') { icon = '&#9888;'; stClass = 'warn'; }
        else if (st.status === 'skipped') { icon = '&#9723;'; stClass = 'skipped'; }
        h += '<div class="scen-tl-step ' + stClass + '">';
        h += '<div class="scen-tl-icon">' + icon + '</div>';
        h += '<div class="scen-tl-body">';
        h += '<div class="scen-tl-name">' + esc(st.name || '');
        if (st.type && st.type.indexOf('pipeline_') !== 0) h += ' <span style="color:#64748b;font-size:.5rem">[' + esc(st.type) + ']</span>';
        if (st.error_category) h += ' <span class="scen-error-cat ' + esc(st.error_category) + '">' + esc(st.error_category) + '</span>';
        h += '</div>';
        if (st.detail) h += '<div class="scen-tl-detail">' + esc(String(st.detail).substring(0,200)) + '</div>';
        if (st.status === 'checking') {
            var timeout = (st.config || {}).timeout || 300;
            var pct = Math.min(100, Math.floor(elapsed / timeout * 100));
            h += '<div class="scen-alarm-bar" style="margin:3px 0"><div class="scen-alarm-fill" style="width:' + pct + '%"></div></div>';
        }
        h += _scenRenderEvents(st);
        h += '</div>';
        if (st.elapsed) h += '<div class="scen-tl-time">' + Math.floor(st.elapsed) + 's</div>';
        if (st.status === 'fail' && isDone) {
            h += _scenFailActionHtml(st, data.run_id || SCEN.runId);
        }
        h += '</div>';
    });

    h += '</div>';

    // Events are pushed to the fixed Agent Console at bottom (scenAgentConsole)

    // Raw 로그 (접기/펼치기)
    if (scriptLog.length) {
        h += '<span class="scen-log-toggle" onclick="_scenLogOpen=!_scenLogOpen;scenPollStatus()">&#9654; 전체 로그 (' + scriptLog.length + '줄)</span>';
        if (_scenLogOpen) {
            h += '<div class="scen-log-box">';
            scriptLog.forEach(function(line) { h += esc(line) + '\n'; });
            h += '</div>';
        }
    }

    // Stderr
    if (scriptOutput.stderr && scriptOutput.stderr.trim()) {
        var stderrLines = scriptOutput.stderr.split('\n').filter(function(l) {
            var t = l.trim();
            return t && !t.match(/Terminated.*kubectl.*port-forward/) && !t.match(/Forwarding from/);
        });
        if (stderrLines.length) {
            h += '<div class="scen-stderr-box">' + esc(stderrLines.join('\n').substring(0,1000)) + '</div>';
        }
    }

    // Agent 교정 결과 표시 영역
    if (isDone) {
        h += '<div id="scenCorrectionResult" style="display:none;margin:6px 0"></div>';
    }

    var _runArea = $('scenRunArea');
    if (_runArea) { _runArea.innerHTML = h; _runArea.style.display = ''; }
    scenUpdateTopoStates(steps);
}


// ── Fault Topology Renderer (reuses arch_topo.js primitives) ──
function scenRenderFaultTopo(svgId, arch, steps) {
    var svg = $(svgId);
    if (!svg) return;
    svg.innerHTML = '';

    var comps = arch.components || [];
    var rawEdges = arch.edges || [];
    var faultPath = arch.fault_path || [];
    var faultSet = {};
    faultPath.forEach(function(n) { faultSet[n] = true; });

    // Build fault edge set (consecutive pairs in fault_path)
    var faultEdgeSet = {};
    for (var fi = 0; fi < faultPath.length - 1; fi++) {
        faultEdgeSet[faultPath[fi] + '→' + faultPath[fi + 1]] = true;
        faultEdgeSet[faultPath[fi + 1] + '→' + faultPath[fi]] = true;
    }

    // Map step verification results to nodes
    var stepResultMap = {};
    (steps || []).forEach(function(st) {
        if (st.pod) stepResultMap[st.pod] = st.status;
    });

    // Convert to renderer format (components can be strings or objects)
    var nodes = comps.map(function(c) {
        if (typeof c === 'string') return {name: c, label: c, type: 'app', desc: ''};
        return {name: c.id || c.name, label: c.label || c.id || c.name, type: c.type || 'app', desc: c.desc || ''};
    });
    var edges = rawEdges.map(function(e) {
        return {source: e.from, target: e.to, label: e.label || ''};
    });

    // Layout using arch_topo's DAG layout
    var W = svg.clientWidth || 580;
    var H = Math.max(200, nodes.length * 40);
    var pos;
    if (typeof _archLayout === 'function') {
        pos = _archLayout(nodes, edges, W, H);
    } else {
        // Fallback: simple grid
        pos = {};
        nodes.forEach(function(n, i) {
            pos[n.name] = {x: 60 + (i % 4) * 140, y: 40 + Math.floor(i / 4) * 80};
        });
    }

    var NW = 100, NH = 40;
    var typeColor = {app: '#326CE5', infra: '#22c55e', aws: '#FF9900', agent: '#a855f7'};

    // Defs: arrow markers
    var defs = _svgE('defs');
    defs.innerHTML = '<marker id="scenArrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#475569"/></marker>'
        + '<marker id="scenArrowFault" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#ef4444"/></marker>';
    svg.appendChild(defs);

    // Render edges
    edges.forEach(function(e) {
        var sp = pos[e.source], tp = pos[e.target];
        if (!sp || !tp) return;
        var isFault = faultEdgeSet[e.source + '→' + e.target] || faultEdgeSet[e.target + '→' + e.source];
        var sx = sp.x + NW / 2, sy = sp.y + NH / 2;
        var tx = tp.x + NW / 2, ty = tp.y + NH / 2;
        // Offset to edge of node box
        var dx = tx - sx, dy = ty - sy, dist = Math.sqrt(dx * dx + dy * dy) || 1;
        sx += dx / dist * (NW / 2); sy += dy / dist * (NH / 2);
        tx -= dx / dist * (NW / 2); ty -= dy / dist * (NH / 2);

        var line = _svgE('line', {
            x1: sx, y1: sy, x2: tx, y2: ty,
            stroke: isFault ? '#ef4444' : '#475569',
            'stroke-width': isFault ? '2.5' : '1.2',
            'stroke-dasharray': isFault ? '' : '4,3',
            'marker-end': isFault ? 'url(#scenArrowFault)' : 'url(#scenArrow)',
            opacity: isFault ? '1' : '0.5'
        });
        if (isFault) {
            var anim = _svgE('animate', {attributeName: 'stroke-opacity', values: '1;0.4;1', dur: '1.5s', repeatCount: 'indefinite'});
            line.appendChild(anim);
        }
        svg.appendChild(line);

        // Edge label
        if (e.label) {
            var lx = (sx + tx) / 2, ly = (sy + ty) / 2 - 6;
            var lt = _svgE('text', {x: lx, y: ly, fill: '#64748b', 'font-size': '8', 'text-anchor': 'middle'});
            lt.textContent = e.label.length > 25 ? e.label.substring(0, 25) + '…' : e.label;
            svg.appendChild(lt);
        }
    });

    // Render nodes
    nodes.forEach(function(n) {
        var p = pos[n.name];
        if (!p) return;
        var isFault = faultSet[n.name];
        var color = isFault ? '#ef4444' : (typeColor[n.type] || '#326CE5');
        var g = _svgE('g', {transform: 'translate(' + p.x + ',' + p.y + ')', cursor: 'pointer', 'data-node': n.name});

        // Node box
        g.appendChild(_svgE('rect', {
            x: 0, y: 0, width: NW, height: NH, rx: 6,
            fill: isFault ? 'rgba(239,68,68,0.1)' : '#1e293b',
            stroke: color, 'stroke-width': isFault ? '2' : '1.2'
        }));

        // Fault pulse animation
        if (isFault) {
            var pulse = _svgE('rect', {
                x: -2, y: -2, width: NW + 4, height: NH + 4, rx: 8,
                fill: 'none', stroke: '#ef4444', 'stroke-width': '1'
            });
            var pulseAnim = _svgE('animate', {attributeName: 'stroke-opacity', values: '0.8;0;0.8', dur: '2s', repeatCount: 'indefinite'});
            pulse.appendChild(pulseAnim);
            g.appendChild(pulse);
        }

        // Icon (try arch_topo icon system)
        var iconKey = n.type === 'aws' ? 'aws-' + n.name : (n.type === 'agent' ? 'aws-bedrock' : 'k8s-deploy');
        if (typeof _ICON_CACHE !== 'undefined' && _ICON_CACHE[iconKey]) {
            var img = _svgE('image', {x: 4, y: 6, width: 28, height: 28, href: _ICON_CACHE[iconKey]});
            g.appendChild(img);
        }

        // Label
        var txt = _svgE('text', {x: 36, y: NH / 2 + 4, fill: isFault ? '#fca5a5' : '#e2e8f0', 'font-size': '11', 'font-weight': '600'});
        txt.textContent = n.label;
        g.appendChild(txt);

        // Tooltip
        var title = _svgE('title');
        title.textContent = n.label + (n.desc ? '\n' + n.desc : '');
        g.appendChild(title);

        svg.appendChild(g);
    });

    // Update viewBox
    var maxX = 0, maxY = 0;
    nodes.forEach(function(n) {
        var p = pos[n.name];
        if (p) { maxX = Math.max(maxX, p.x + NW + 20); maxY = Math.max(maxY, p.y + NH + 20); }
    });
    svg.setAttribute('viewBox', '0 0 ' + maxX + ' ' + maxY);
    svg.style.height = Math.max(180, maxY) + 'px';
}

// ── Update topology node states based on verification step results ──
function scenUpdateTopoStates(steps) {
    var svg = document.getElementById('scenDetailFaultSvg');
    if (!svg) return;

    // Build node→status map from steps
    var stateMap = {};
    (steps || []).forEach(function(st) {
        if (st.pod) stateMap[st.pod] = st.status;
    });
    if (!Object.keys(stateMap).length) return;

    // Color/style per status
    var STATE_STYLE = {
        checking: {stroke: '#3b82f6', fill: 'rgba(59,130,246,0.12)', pulseColor: '#3b82f6', textFill: '#93c5fd'},
        pass:     {stroke: '#22c55e', fill: 'rgba(34,197,94,0.10)',  pulseColor: null,      textFill: '#86efac'},
        fail:     {stroke: '#ef4444', fill: 'rgba(239,68,68,0.10)',  pulseColor: '#ef4444',  textFill: '#fca5a5'}
    };

    var groups = svg.querySelectorAll('g[data-node]');
    for (var i = 0; i < groups.length; i++) {
        var g = groups[i];
        var nodeId = g.getAttribute('data-node');
        var status = stateMap[nodeId];
        if (!status) continue;

        var style = STATE_STYLE[status];
        if (!style) continue;

        // Update main rect (first rect child)
        var rect = g.querySelector('rect');
        if (rect) {
            rect.setAttribute('stroke', style.stroke);
            rect.setAttribute('fill', style.fill);
            rect.setAttribute('stroke-width', '2');
        }

        // Update label text color
        var texts = g.querySelectorAll('text');
        if (texts.length) texts[0].setAttribute('fill', style.textFill);

        // Remove existing pulse rects (second rect if present)
        var rects = g.querySelectorAll('rect');
        for (var r = 1; r < rects.length; r++) rects[r].remove();

        // Add pulse animation for checking state
        if (style.pulseColor && status === 'checking') {
            var pulse = _svgE('rect', {
                x: -3, y: -3, width: 106, height: 46, rx: 9,
                fill: 'none', stroke: style.pulseColor, 'stroke-width': '1.5'
            });
            var anim = _svgE('animate', {
                attributeName: 'stroke-opacity', values: '0.9;0.1;0.9', dur: '1.2s', repeatCount: 'indefinite'
            });
            pulse.appendChild(anim);
            g.appendChild(pulse);
        }

        // Add status icon (checkmark or X) after the node
        var existing = g.querySelector('.scen-state-icon');
        if (existing) existing.remove();

        var iconG = _svgE('g', {'class': 'scen-state-icon'});
        if (status === 'pass') {
            var circle = _svgE('circle', {cx: 95, cy: 5, r: 7, fill: '#166534', stroke: '#22c55e', 'stroke-width': '1.5'});
            var check = _svgE('path', {d: 'M91,5 L94,8 L99,2', fill: 'none', stroke: '#22c55e', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
            iconG.appendChild(circle);
            iconG.appendChild(check);
        } else if (status === 'fail') {
            var circle = _svgE('circle', {cx: 95, cy: 5, r: 7, fill: '#7f1d1d', stroke: '#ef4444', 'stroke-width': '1.5'});
            var x1 = _svgE('line', {x1: 92, y1: 2, x2: 98, y2: 8, stroke: '#ef4444', 'stroke-width': '1.5', 'stroke-linecap': 'round'});
            var x2 = _svgE('line', {x1: 98, y1: 2, x2: 92, y2: 8, stroke: '#ef4444', 'stroke-width': '1.5', 'stroke-linecap': 'round'});
            iconG.appendChild(circle);
            iconG.appendChild(x1);
            iconG.appendChild(x2);
        } else if (status === 'checking') {
            var circle = _svgE('circle', {cx: 95, cy: 5, r: 7, fill: '#1e3a5f', stroke: '#3b82f6', 'stroke-width': '1.5'});
            var spinner = _svgE('path', {d: 'M95,0 A5,5 0 0,1 100,5', fill: 'none', stroke: '#60a5fa', 'stroke-width': '1.5', 'stroke-linecap': 'round'});
            var spinAnim = _svgE('animateTransform', {attributeName: 'transform', type: 'rotate', from: '0 95 5', to: '360 95 5', dur: '0.8s', repeatCount: 'indefinite'});
            spinner.appendChild(spinAnim);
            iconG.appendChild(circle);
            iconG.appendChild(spinner);
        }
        g.appendChild(iconG);
    }
}

// ── Retry from a failed step ──
function scenRetryFromStep(stepIndex) {
    if (!SCEN.runId) return;
    var btns = document.querySelectorAll('.scen-retry-btn');
    for (var i = 0; i < btns.length; i++) { btns[i].disabled = true; btns[i].textContent = '재시도 중...'; }

    _scenRunStart = Date.now();
    _scenTimerInterval = setInterval(function(){
        var sec = Math.floor((Date.now() - _scenRunStart) / 1000);
        $('scenRunTimer').textContent = sec + 's';
    }, 1000);
    $('scenBtnCancel').style.display = '';
    $('scenBtnRun').disabled = true;

    fetch('/api/scenario-run/' + SCEN.runId + '/retry/' + stepIndex, {method: 'POST'})
    .then(function(r){return r.json()})
    .then(function(data){
        if (data.action === 'new_run') {
            scenResumeScript(data.resume_from, data.scenario_id);
            return;
        }
        if (data.error) throw new Error(data.error);
        scenPollStatus();
    }).catch(function(e){
        alert('재시도 실패: ' + e.message);
        $('scenBtnRun').disabled = false;
        $('scenBtnCancel').style.display = 'none';
        scenStopTimer();
    });
}

function scenStopPoll() {
    if (SCEN.pollTimer) { clearTimeout(SCEN.pollTimer); SCEN.pollTimer = null; }
    scenStopTimer();
}
function scenStopTimer() {
    if (_scenTimerInterval) { clearInterval(_scenTimerInterval); _scenTimerInterval = null; }
}

function scenCancelRun() {
    if (!SCEN.runId) return;
    if (SCEN._sseSource) { SCEN._sseSource.close(); SCEN._sseSource = null; }
    fetch('/api/scenario-run/' + SCEN.runId + '/cancel', {method: 'POST'})
    .then(function(r){return r.json()})
    .then(function(){
        scenStopPoll();
        $('scenBtnRun').disabled = false;
        $('scenBtnCancel').style.display = 'none';
    }).catch(function(){});
}

function scenRestore() {
    if (!SCEN.runId) return;
    var btn = $('scenBtnRestore');
    btn.disabled = true;
    btn.textContent = '복원 중...';
    fetch('/api/scenario-run/' + SCEN.runId + '/restore', {method: 'POST'})
    .then(function(r){return r.json()})
    .then(function(data){
        btn.disabled = false; btn.textContent = '복원';
        if (data.error) alert('복원 실패: ' + data.error);
    }).catch(function(e){
        btn.disabled = false; btn.textContent = '복원';
        alert('복원 오류: ' + e.message);
    });
}

function scenDeleteScenario() {
    if (!SCEN.current) return;
    if (!confirm('Scenario "' + SCEN.current.name + '"을(를) 삭제하시겠습니까?')) return;
    var btn = $('scenBtnDelete');
    btn.disabled = true;
    btn.textContent = '삭제 중...';
    fetch('/api/scenarios/' + encodeURIComponent(SCEN.current.id) + '?space_id=' + encodeURIComponent(_scenSpaceId()), {method: 'DELETE'})
    .then(function(r){return r.json()})
    .then(function(data){
        if (data.error) throw new Error(data.error);
        scenBackToList();
        scenLoadList();
    }).catch(function(e){
        btn.disabled = false;
        btn.textContent = '삭제';
        alert('삭제 실패: ' + e.message);
    });
}

// ── Close Gen Panel ──
function scenCloseGenPanel() {
    $('scenChatSide').style.display = 'none';
    SCEN.chatOpen = false;
}

// ================================================================
// LIVE DAG — real-time rule-based investigation visualization
// ================================================================
SCEN._liveDagRecordCount = 0;
SCEN._liveDagFetching = false;

function scenUpdateLiveDag(taskId, runStatus) {
    if (SCEN._liveDagFetching) return;
    if (typeof DAG === 'undefined') return;
    var container = $('scenLiveDagCell');
    if (!container) return;

    var area = $('scenAnalysisArea');
    if (area) area.style.display = '';
    SCEN._liveDagFetching = true;

    fetch('/api/investigation-journal-raw?task_id=' + encodeURIComponent(taskId))
    .then(function(r){return r.json()})
    .then(function(d){
        SCEN._liveDagFetching = false;
        if (!d.ok || !d.records || !d.records.length) return;
        if (d.records.length === SCEN._liveDagRecordCount) return;
        SCEN._liveDagRecordCount = d.records.length;

        var svg = $('scenLiveDagSvg');
        if (!svg) return;
        var model = DAG.fromRecords(d.records);
        SCEN._liveDagModel = model;
        DAG.render(svg, model, {layout:'vertical'});
        svg.style.height = Math.min(600, Math.max(120, model.nodes.length * 50)) + 'px';

        var badge = $('scenLiveDagBadge');
        var countEl = $('scenLiveDagCount');
        if (badge) {
            var hasSummary = model.summary;
            badge.textContent = hasSummary ? 'COMPLETE' : 'LIVE';
            badge.style.background = hasSummary ? '#22c55e20' : '#f59e0b20';
            badge.style.color = hasSummary ? '#22c55e' : '#fbbf24';
        }
        if (countEl) {
            var obs = model.nodes.filter(function(n){return n.type==='observation'}).length;
            var fin = model.nodes.filter(function(n){return n.type==='finding'}).length;
            countEl.textContent = d.records.length + ' records | ' + obs + ' obs | ' + fin + ' findings';
        }
    })
    .catch(function(){SCEN._liveDagFetching = false;});
}

function scenFinalizeLiveDag() {
    var badge = $('scenLiveDagBadge');
    if (badge) {
        badge.textContent = 'COMPLETE';
        badge.style.background = '#22c55e20';
        badge.style.color = '#22c55e';
    }
}

function scenLoadHistory(scenarioId) {
    var el = $('scenHistoryArea');
    if (!el) return;
    fetch('/api/scenario-runs/' + encodeURIComponent(scenarioId))
    .then(function(r){return r.json()})
    .then(function(d){
        if (!d.ok || !d.runs || !d.runs.length) {
            el.innerHTML = '';
            return;
        }
        var h = '<div class="scen-analysis-title" style="margin-top:16px">실행 이력</div>';
        h += '<div class="scen-history-list">';
        d.runs.forEach(function(run) {
            var hasTask = run.investigation_task_id && run.investigation_task_id !== 'None';
            var status = run.status || '';
            var result = run.result || '';
            var sc = status === 'completed' ? (result === 'pass' ? '#22c55e' : result === 'fail' ? '#ef4444' : '#f59e0b') : '#64748b';
            var icon = result === 'pass' ? '&#9989;' : result === 'fail' ? '&#10060;' : status === 'cancelled' ? '&#9898;' : '&#9888;&#65039;';
            var dt = (run.started_at || '').replace('T', ' ').substring(0, 19);
            var elapsed = run.elapsed ? (Number(run.elapsed).toFixed(0) + 's') : '';
            var clickable = (status === 'completed' || status === 'done' || status === 'cancelled');
            h += '<div class="scen-history-item' + (clickable ? ' clickable' : '') + '"';
            if (clickable) h += ' onclick="scenShowRunDetail(\'' + esc(run.run_id) + '\'' + (hasTask ? ',\'' + esc(run.investigation_task_id) + '\',\'' + esc(run.scenario_id) + '\'' : '') + ')"';
            h += '>';
            h += '<span class="scen-history-icon">' + icon + '</span>';
            h += '<span class="scen-history-time">' + esc(dt) + '</span>';
            h += '<span class="scen-history-status" style="color:' + sc + '">' + esc(status) + (result && result !== status ? ' / ' + esc(result) : '') + '</span>';
            if (elapsed) h += '<span class="scen-history-elapsed">' + esc(elapsed) + '</span>';
            if (hasTask) h += '<span class="scen-history-badge">DAG</span>';
            if (status !== 'running' && result !== 'pass') {
                h += '<button class="scen-history-resume-btn" onclick="event.stopPropagation();scenResumeRun(\'' + esc(run.run_id) + '\')">재개</button>';
            }
            h += '</div>';
        });
        h += '</div>';
        el.innerHTML = h;
    }).catch(function(){});
}

function scenResumeRun(runId) {
    if (SCEN.runId) { alert('이미 실행 중인 시나리오가 있습니다.'); return; }
    fetch('/api/scenario-run/' + encodeURIComponent(runId) + '/resume', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({space_id:SCEN.spaceId})})
    .then(function(r){return r.json()})
    .then(function(d){
        if (!d.ok) { alert('재개 실패: ' + (d.error || 'unknown')); return; }
        SCEN.runId = d.run_id;
        scenPollStatus();
    }).catch(function(e){ alert('재개 오류: ' + e); });
}

function scenShowRunDetail(runId, taskId, scenarioId) {
    fetch('/api/scenario-run/' + encodeURIComponent(runId) + '/status')
    .then(function(r){return r.json()})
    .then(function(data){
        var area = $('scenRunArea');
        if (!area) return;
        area.style.display = '';
        scenRenderTimeline(data);
        if (taskId && scenarioId) {
            scenUpdateLiveDag(taskId, 'completed');
            scenFinalizeLiveDag();
            scenShowInvestigationLink({run_id: runId, investigation_task_id: taskId, scenario_id: scenarioId});
        }
    }).catch(function(e){ console.error('run detail error:', e); });
}

function scenLoadRun(runId, taskId, scenarioId) {
    if (SCEN.runId) return;
    SCEN._lastRunId = runId;
    var area = $('scenAnalysisArea');
    if (area) area.style.display = '';
    scenUpdateLiveDag(taskId, 'completed');
    scenFinalizeLiveDag();
    scenShowInvestigationLink({run_id: runId, investigation_task_id: taskId, scenario_id: scenarioId});

    var items = document.querySelectorAll('.scen-history-item');
    items.forEach(function(item) { item.classList.remove('active'); });
    var clicked = document.querySelector('.scen-history-item[onclick*="' + runId + '"]');
    if (clicked) clicked.classList.add('active');
}

// ================================================================
// Investigation Link (redirects to DAG page for analysis)
// ================================================================
function scenShowInvestigationLink(data) {
    var area = $('scenAnalysisArea');
    if (!area) return;
    area.style.display = '';
    var tid = data.investigation_task_id;
    var sid = _scenSpaceId();
    var rid = data.run_id || '';
    var scid = data.scenario_id || (SCEN.current ? SCEN.current.id : '');
    var url = '/dag?task_id=' + encodeURIComponent(tid) + '&space_id=' + encodeURIComponent(sid);
    if (rid) url += '&run_id=' + encodeURIComponent(rid);
    if (scid) url += '&scenario_id=' + encodeURIComponent(scid);
    area.innerHTML = '<div style="text-align:center;padding:20px">'
        + '<a href="' + url + '" target="_blank" style="display:inline-block;padding:10px 24px;background:#3b82f6;color:#fff;border-radius:8px;text-decoration:none;font-size:.75rem;font-weight:600">'
        + '&#128270; 조사 결과 보기 &rarr;</a>'
        + '<div style="color:#64748b;font-size:.58rem;margin-top:6px">task: ' + esc(tid.substring(0,12)) + '...</div>'
        + '</div>';
}

// ================================================================
// AUTO-ANALYSIS: DAG + Hypothesis + Rubric (reusable, app-agnostic)
// ================================================================

function scenRunAnalysis(runData) {
    var area = $('scenAnalysisArea');
    if (!area) return;
    area.style.display = '';
    var dagEl = $('scenDagSection');
    var hypEl = $('scenHypothesisSection');
    var rubEl = $('scenRubricSection');
    var spinner = '<span class="arch-spinner" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:6px"></span>';
    dagEl.innerHTML = '<div style="text-align:center;padding:16px;color:#94a3b8;font-size:.68rem">' + spinner + 'DAG 생성 중...</div>';
    hypEl.innerHTML = '<div style="text-align:center;padding:16px;color:#94a3b8;font-size:.68rem">' + spinner + '가설 분석 중...</div>';
    rubEl.innerHTML = '<div style="text-align:center;padding:16px;color:#94a3b8;font-size:.68rem">' + spinner + 'Rubric 평가 중...</div>';

    var taskId = runData.investigation_task_id;
    var runId = runData.run_id;
    var scenarioId = runData.scenario_id || (SCEN.current ? SCEN.current.id : '');

    Promise.all([
        fetch('/api/investigation-journal?task_id=' + encodeURIComponent(taskId))
            .then(function(r){return r.json()}).catch(function(e){return {error:String(e)}}),
        fetch('/api/evaluate/' + encodeURIComponent(runId), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({task_id: taskId, scenario_id: scenarioId, space_id: _scenSpaceId()})
        }).then(function(r){return r.json()}).catch(function(e){return {error:String(e)}})
    ]).then(function(results) {
        var hypResult = results[0];
        var rubResult = results[1];

        var hasHyp = hypResult.ok && hypResult.hypotheses && hypResult.hypotheses.length > 0;
        var hasRub = !!rubResult.criteria_results;

        if (hasHyp) {
            scenRenderDag(hypResult, dagEl);
            scenRenderHypotheses(hypResult, hypEl);
        } else {
            var hypErr = hypResult.error || '가설 데이터 없음';
            dagEl.innerHTML = '<div class="scen-analysis-title">Bedrock 가설 DAG</div><div style="color:#64748b;font-size:.62rem;padding:16px;text-align:center">' + esc(hypErr) + '</div>';
            hypEl.innerHTML = '';
        }

        if (hasRub) {
            scenRenderRubric(rubResult, rubEl);
        } else {
            var rubErr = rubResult.error || 'Rubric 평가 데이터 없음';
            rubEl.innerHTML = '<div class="scen-analysis-title">Rubric 평가</div><div style="color:#fca5a5;font-size:.62rem;padding:16px;text-align:center">' + esc(rubErr) + '</div>';
        }
    });
}


// ── DAG Rendering ──

function scenRenderDag(data, el) {
    var p1 = SCEN._liveDagModel || null;
    var model = DAG.fromBedrock(data, p1);
    el.innerHTML = '<div class="scen-analysis-title">Bedrock 가설 DAG</div>'
        + '<svg id="scenBedDagSvg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" '
        + 'style="display:block;width:100%;height:200px;background:#0a0f1a;border-radius:8px;border:1px solid #1e293b"></svg>';
    var svg = document.getElementById('scenBedDagSvg');
    DAG.render(svg, model, {layout:'vertical'});
    svg.style.height = Math.min(600, Math.max(120, model.nodes.length * 50)) + 'px';
}


// ── Hypothesis Detail Rendering ──

function scenRenderHypotheses(data, el) {
    var SC = {rejected: 'rgba(239,68,68,0.12)', partial: 'rgba(245,158,11,0.10)', confirmed: 'rgba(34,197,94,0.10)'};
    var SB = {rejected: '#ef4444', partial: '#f59e0b', confirmed: '#22c55e'};
    var SBD = {rejected: 'rgba(239,68,68,0.2)', partial: 'rgba(245,158,11,0.2)', confirmed: 'rgba(34,197,94,0.2)'};
    var ST = {rejected: '기각', partial: '부분 확인', confirmed: '확인'};
    var DI = {'metric': '&#128202;', 'log': '&#128221;', 'trace': '&#128269;', 'code_snippet': '&#128187;', 'change_event': '&#128640;',
              '메트릭': '&#128202;', '로그': '&#128221;', '트레이스': '&#128269;', 'K8s': '&#9784;&#65039;', '코드': '&#128187;', '배포이력': '&#128640;'};

    var h = '<div class="scen-analysis-title">가설 분석</div>';

    if (data.alarm) {
        h += '<div class="scen-hyp-alarm">';
        h += '&#128276; 알람 인지: ' + esc(data.alarm) + '</div>';
    }

    (data.hypotheses || []).forEach(function(hy, hi) {
        var s = hy.status || 'partial';
        var bg = SC[s] || 'rgba(30,41,59,0.5)';
        var bd = SB[s] || '#334155';
        var bdd = SBD[s] || 'rgba(51,65,85,0.3)';
        var lb = ST[s] || s;
        var uid = 'scenHyp-' + hi;
        var hypTitle = hy.label || hy.title || ('가설 ' + (hi + 1));

        h += '<div class="scen-hyp-card" style="border-color:' + bdd + '">';
        h += '<div class="scen-hyp-header" style="background:' + bg + '" onclick="var e=document.getElementById(\'' + uid + '\');e.style.display=e.style.display===\'none\'?\'block\':\'none\'">';
        h += '<span class="scen-hyp-num" style="background:' + bg + ';border-color:' + bdd + ';color:' + bd + '">' + (hi + 1) + '</span>';
        h += '<span class="scen-hyp-title">' + esc(hypTitle) + '</span>';
        if (hy.category) h += '<span class="scen-hyp-tag">' + esc(hy.category) + '</span>';
        h += '<span class="scen-hyp-status" style="color:' + bd + ';border-color:' + bdd + '">' + lb + '</span>';
        if (hy.leads_to) h += '<span class="scen-hyp-link">&rarr; 가설' + hy.leads_to + '</span>';
        h += '</div>';

        h += '<div id="' + uid + '">';
        (hy.steps || []).forEach(function(st) {
            var src = st.data_source || st.signal_type || '';
            var di = DI[src] || '&#128203;';
            var tm = (st.source_times || []).join(', ');
            var actionText = st.action || st.insight || '';
            h += '<div class="scen-hyp-step' + (st.is_key ? ' key' : '') + '">';
            h += '<div class="scen-hyp-step-meta">';
            if (tm) h += '<span class="scen-hyp-step-time">' + esc(tm) + '</span>';
            h += '<span class="scen-hyp-step-icon">' + di + '</span>';
            if (src) h += '<span class="scen-hyp-step-src">' + esc(src) + '</span>';
            if (st.is_key) h += '<span class="scen-hyp-step-key">&#11088; 핵심</span>';
            h += '</div>';
            if (actionText) h += '<div class="scen-hyp-step-text">' + esc(actionText) + '</div>';
            h += '</div>';
        });
        var reasonText = hy.status_reason || hy.reason || '';
        if (reasonText) h += '<div class="scen-hyp-reason" style="color:' + bd + '">' + esc(reasonText) + '</div>';
        h += '</div></div>';
    });

    if (data.root_cause) {
        var rc = data.root_cause;
        var rcTitle = rc.title || rc.summary || '';
        var rcDesc = rc.description || rc.summary || '';
        h += '<div class="scen-hyp-rootcause">';
        h += '<div class="scen-hyp-rootcause-title">&#127919; Root Cause</div>';
        if (rcTitle) h += '<div class="scen-hyp-rootcause-name">' + esc(rcTitle) + '</div>';
        if (rcDesc && rcDesc !== rcTitle) h += '<div class="scen-hyp-rootcause-desc">' + esc(rcDesc) + '</div>';
        h += '</div>';
    }

    h += '<div class="scen-hyp-footer">DevOps Agent API + Bedrock | 원본 ' + (data.raw_count || 0) + '건</div>';
    el.innerHTML = h;
}


// ── Rubric Evaluation Rendering ──

function scenRenderRubric(data, el) {
    var h = '<div class="scen-analysis-title">Rubric 평가</div>';
    h += '<div class="scen-rub-summary">';
    h += '<div class="scen-rub-score">' + data.overall_score + ' <span class="scen-rub-score-max">/ ' + data.max_score + '</span></div>';
    h += '<div class="scen-rub-meta">모델: ' + esc(data.model || '') + ' | 메시지: ' + (data.message_count || 0) + '건</div>';
    h += '</div>';

    var criteria = data.criteria_results || {};
    var keys = Object.keys(criteria);

    if (keys.length >= 3) {
        var n = keys.length, cx = 120, cy = 120, R = 90;
        var scores = keys.map(function(k){return Number(criteria[k].score) || 0});
        var labels = keys.map(function(k){return k.replace(/_/g, ' ')});
        function rpt(i, r){var a = (Math.PI * 2 * i / n) - Math.PI / 2; return [cx + r * Math.cos(a), cy + r * Math.sin(a)];}
        var svg = '<svg viewBox="0 0 240 240" style="width:100%;max-width:220px;height:220px">';
        [2, 4, 6, 8, 10].forEach(function(v){
            var pts = []; for (var i = 0; i < n; i++) pts.push(rpt(i, R * v / 10).join(','));
            svg += '<polygon points="' + pts.join(' ') + '" fill="none" stroke="#334155" stroke-width="0.5"/>';
        });
        for (var i = 0; i < n; i++){var p = rpt(i, R); svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + p[0] + '" y2="' + p[1] + '" stroke="#334155" stroke-width="0.5"/>';}
        var dp = []; for (var i = 0; i < n; i++) dp.push(rpt(i, R * scores[i] / 10).join(','));
        svg += '<polygon points="' + dp.join(' ') + '" fill="rgba(59,130,246,0.25)" stroke="#3b82f6" stroke-width="2"/>';
        for (var i = 0; i < n; i++){var p = rpt(i, R * scores[i] / 10); var c = scores[i] >= 7 ? '#22c55e' : scores[i] >= 4 ? '#fbbf24' : '#ef4444'; svg += '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="4" fill="' + c + '" stroke="#0f172a" stroke-width="1"/>';}
        for (var i = 0; i < n; i++){var p = rpt(i, R + 20); var anc = p[0] < cx - 10 ? 'end' : p[0] > cx + 10 ? 'start' : 'middle'; svg += '<text x="' + p[0] + '" y="' + p[1] + '" text-anchor="' + anc + '" fill="#94a3b8" font-size="7" dominant-baseline="middle">' + esc(labels[i]) + '</text>'; svg += '<text x="' + p[0] + '" y="' + (p[1] + 10) + '" text-anchor="' + anc + '" fill="' + (scores[i] >= 7 ? '#22c55e' : scores[i] >= 4 ? '#fbbf24' : '#ef4444') + '" font-size="8" font-weight="bold" dominant-baseline="middle">' + scores[i] + '</text>';}
        svg += '</svg>';
        h += '<div style="text-align:center;margin-bottom:10px">' + svg + '</div>';
    }

    for (var ki = 0; ki < keys.length; ki++) {
        var id = keys[ki];
        var c = criteria[id];
        var score = Number(c.score) || 0;
        var pct = Math.round(score * 10);
        var color = score >= 7 ? '#22c55e' : score >= 4 ? '#fbbf24' : '#ef4444';
        h += '<div class="scen-rub-item" style="border-left-color:' + color + '">';
        h += '<div class="scen-rub-item-hdr">';
        h += '<span class="scen-rub-item-name">' + esc(id) + ' <span class="scen-rub-item-weight">(가중치 ' + c.weight + ')</span></span>';
        h += '<span class="scen-rub-item-score" style="color:' + color + '">' + c.score + '/10</span>';
        h += '</div>';
        h += '<div class="scen-rub-bar"><div class="scen-rub-bar-fill" style="background:' + color + ';width:' + pct + '%"></div></div>';
        h += '<div class="scen-rub-item-criteria">' + esc(c.criteria || '') + '</div>';
        h += '<div class="scen-rub-item-reason">' + esc(c.reasoning || '') + '</div>';
        h += '</div>';
    }

    el.innerHTML = h;
}


// ══════════════════════════════════════════════════════════════════════════════
// Security Scenario: 공격 경로 SVG + 실행
// ══════════════════════════════════════════════════════════════════════════════

function scenFlushSecAttackPath() {
    if (!SCEN._pendingSecAttackPath) return;
    var edges = SCEN._pendingSecAttackPath;
    SCEN._pendingSecAttackPath = null;
    setTimeout(function() { _renderSecAttackPathSvg('scenSecAttackPathSvg', edges); }, 50);
}

function _renderSecAttackPathSvg(svgId, edges) {
    var svg = document.getElementById(svgId);
    if (!svg || !edges || !edges.length) return;

    var nodes = [];
    var nodeSet = {};
    edges.forEach(function(e) {
        if (!nodeSet[e.from]) { nodeSet[e.from] = true; nodes.push(e.from); }
        if (!nodeSet[e.to]) { nodeSet[e.to] = true; nodes.push(e.to); }
    });

    var W = svg.clientWidth || 400;
    var H = 70;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);

    var spacing = W / (nodes.length + 1);
    var posMap = {};
    nodes.forEach(function(n, i) { posMap[n] = {x: spacing * (i + 1), y: H / 2}; });

    var html = '';
    edges.forEach(function(e) {
        var p1 = posMap[e.from], p2 = posMap[e.to];
        if (!p1 || !p2) return;
        var mx = (p1.x + p2.x) / 2, my = p1.y - 12;
        html += '<line x1="' + p1.x + '" y1="' + p1.y + '" x2="' + p2.x + '" y2="' + p2.y + '" stroke="#dc2626" stroke-width="1.5" marker-end="url(#secArrow)"/>';
        if (e.label) html += '<text x="' + mx + '" y="' + my + '" text-anchor="middle" fill="#94a3b8" font-size="8">' + e.label + '</text>';
    });
    nodes.forEach(function(n) {
        var p = posMap[n];
        var isAttacker = n === 'attacker';
        var fill = isAttacker ? '#ef4444' : '#1e40af';
        html += '<circle cx="' + p.x + '" cy="' + p.y + '" r="14" fill="' + fill + '" fill-opacity="0.15" stroke="' + fill + '" stroke-width="1.5"/>';
        html += '<text x="' + p.x + '" y="' + (p.y + 3) + '" text-anchor="middle" fill="#e2e8f0" font-size="8" font-weight="600">' + n + '</text>';
    });
    html += '<defs><marker id="secArrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#dc2626"/></marker></defs>';

    svg.innerHTML = html;
}

function scenRunSecurityCheck(findingId, scenarioId) {
    var statusEl = document.getElementById('scenSecRunStatus');
    var runArea = document.getElementById('scenRunArea');
    if (!statusEl && !runArea) return;

    if (statusEl) { statusEl.textContent = '실행 중...'; statusEl.style.color = '#fbbf24'; }

    // 정적 Attack Steps 섹션을 실행 결과 패널로 교체
    var staticSection = document.getElementById('scenSecStepsSection');
    var targetEl = staticSection || runArea;
    if (targetEl) {
        targetEl.innerHTML = '<div id="secRunPanel" style="border:1px solid #1e293b;border-radius:8px;overflow:hidden">'
            + '<div style="padding:8px 12px;background:#1e293b;border-bottom:1px solid #334155;display:flex;align-items:center;gap:8px">'
            + '<div class="loading" style="width:10px;height:10px" id="secRunSpinner"></div>'
            + '<span style="font-size:.56rem;font-weight:600;color:#e2e8f0">Attack Replay 실행 중</span>'
            + '</div>'
            + '<div id="secRunSteps" style="padding:8px 12px;max-height:400px;overflow-y:auto"></div>'
            + '</div>';
        targetEl.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    fetch('/api/security/insights/scenarios/' + encodeURIComponent(scenarioId) + '/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({finding_id: findingId})
    }).then(function(r) { return r.json(); }).then(function(data) {
        if (!data.ok) throw new Error(data.error || 'execution failed');
        var res = data.result;
        var color = res.status === 'defended' ? '#4ade80' : res.status === 'vulnerable' ? '#ef4444' : '#f59e0b';
        if (statusEl) { statusEl.style.color = color; statusEl.textContent = res.status + ' — ' + (res.detail || '') + ' (' + (res.duration || 0).toFixed(1) + 's)'; }

        // Step 결과 순차 표시
        var stepsEl = document.getElementById('secRunSteps');
        var spinner = document.getElementById('secRunSpinner');
        if (spinner) spinner.style.display = 'none';

        var steps = res.steps || [];
        if (stepsEl && steps.length) {
            stepsEl.innerHTML = '';
            steps.forEach(function(s, i) {
                setTimeout(function() {
                    var sColor = s.error ? '#ef4444' : s.vuln_pattern_found ? '#ef4444' : (s.status_code >= 200 && s.status_code < 400) ? '#f59e0b' : '#4ade80';
                    var row = '<div style="padding:5px 0;border-bottom:1px solid #1e293b50;font-size:.5rem;animation:fadeIn .3s">';
                    row += '<div style="display:flex;align-items:center;gap:6px">';
                    row += '<span style="color:' + sColor + ';font-weight:700;min-width:14px;font-size:.6rem">' + (i + 1) + '</span>';
                    row += '<span style="color:#94a3b8;font-family:monospace;font-size:.48rem">' + (s.method || 'GET') + '</span>';
                    row += '<span style="color:#e2e8f0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.48rem">' + (s.path || '/') + '</span>';
                    row += '<span style="color:' + sColor + ';font-weight:600;font-size:.48rem">HTTP ' + (s.status_code || 0) + '</span>';
                    row += '<span style="color:#475569;font-size:.42rem">' + (s.duration || 0).toFixed(2) + 's</span>';
                    row += '</div>';
                    if (s.error) row += '<div style="color:#fca5a5;font-size:.42rem;margin:2px 0 0 20px">' + s.error + '</div>';
                    if (s.body_snippet) row += '<pre style="color:#64748b;font-size:.38rem;margin:2px 0 0 20px;max-height:40px;overflow:hidden;white-space:pre-wrap">' + s.body_snippet.substring(0, 150) + '</pre>';
                    row += '</div>';
                    stepsEl.insertAdjacentHTML('beforeend', row);
                    stepsEl.scrollTop = stepsEl.scrollHeight;
                }, i * 150);
            });

            // 최종 판정 표시
            setTimeout(function() {
                var panel = document.getElementById('secRunPanel');
                if (panel) {
                    var hdr = panel.querySelector('div');
                    if (hdr) { hdr.style.borderLeft = '3px solid ' + color; }
                    var label = panel.querySelector('span:nth-child(2)');
                    if (label) { label.textContent = res.status === 'defended' ? '방어 확인됨' : '취약점 존재'; label.style.color = color; }
                }
            }, steps.length * 150 + 100);
        } else if (stepsEl) {
            stepsEl.innerHTML = '<div style="font-size:.48rem;color:' + color + '">' + (res.detail || res.status) + '</div>';
        }

    }).catch(function(e) {
        if (statusEl) { statusEl.style.color = '#ef4444'; statusEl.textContent = '오류: ' + e.message; }
        var spinner = document.getElementById('secRunSpinner');
        if (spinner) spinner.style.display = 'none';
        var stepsEl = document.getElementById('secRunSteps');
        if (stepsEl) stepsEl.innerHTML = '<div style="font-size:.48rem;color:#ef4444">오류: ' + (e.message||'') + '</div>';
    });
}


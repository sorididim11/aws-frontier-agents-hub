// ================================================================
// SIMULATION PANEL — simulation_panel.js
// SSE-based Generate → Verify → Improve loop visualization
// ================================================================
/* global $ esc _scenSpaceId SCEN */

var SIM = {
    runId: null,
    evtSource: null,
    rounds: [],
    status: 'idle',
};

// ── Launch simulation from template picker ──
function simLaunch(templateId, targetService, namespace, appName) {
    var spaceId = _scenSpaceId();
    var body = {
        failure_mode_id: templateId,
        target_service: targetService,
        namespace: namespace || 'default',
        space_id: spaceId,
        max_rounds: 3,
    };
    if (appName) body.architecture_json = {app_name: appName};

    SIM.rounds = [];
    SIM.status = 'starting';
    _simRender();

    fetch('/api/simulation/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    }).then(function(r){ return r.json(); })
    .then(function(data) {
        if (data.error) throw new Error(data.error);
        SIM.runId = data.run_id;
        SIM.status = 'running';
        _simConnect(data.run_id);
    }).catch(function(e) {
        SIM.status = 'error';
        SIM.rounds = [{error: e.message}];
        _simRender();
    });
}

// ── Launch from existing scenario (re-run) ──
function simRerun(scenarioJson) {
    var spaceId = _scenSpaceId();
    SIM.rounds = [];
    SIM.status = 'starting';
    _simRender();

    fetch('/api/simulation/run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            existing_scenario: scenarioJson,
            target_service: scenarioJson.target_service || '',
            namespace: scenarioJson.namespace || 'default',
            space_id: spaceId,
            max_rounds: 3,
        })
    }).then(function(r){ return r.json(); })
    .then(function(data) {
        if (data.error) throw new Error(data.error);
        SIM.runId = data.run_id;
        SIM.status = 'running';
        _simConnect(data.run_id);
    }).catch(function(e) {
        SIM.status = 'error';
        SIM.rounds = [{error: e.message}];
        _simRender();
    });
}

// ── Cancel ──
function simCancel() {
    if (!SIM.runId) return;
    fetch('/api/simulation/' + SIM.runId + '/cancel', {method: 'POST'});
    _simDisconnect();
    SIM.status = 'cancelled';
    _simRender();
}

// ── SSE connection ──
function _simConnect(runId) {
    _simDisconnect();
    var es = new EventSource('/api/simulation/' + runId + '/stream');
    SIM.evtSource = es;

    es.addEventListener('round_start', function(e) {
        var d = JSON.parse(e.data);
        SIM.rounds.push({num: d.round, maxRounds: d.max_rounds, generator: [], verifier: [], verdict: null, phase: 'generate'});
        _simRender();
    });

    es.addEventListener('phase_change', function(e) {
        var d = JSON.parse(e.data);
        var cur = _simCurrentRound();
        if (cur) cur.phase = d.phase;
        _simRender();
    });

    es.addEventListener('agent_action', function(e) {
        var d = JSON.parse(e.data);
        var cur = _simCurrentRound();
        if (!cur) return;
        var entry = {tool: d.tool, input: d.input_summary, output: d.output_summary, ok: d.success !== false};
        if (d.agent === 'generator') cur.generator.push(entry);
        else cur.verifier.push(entry);
        _simRender();
    });

    es.addEventListener('validation', function(e) {
        var d = JSON.parse(e.data);
        var cur = _simCurrentRound();
        if (!cur) return;
        cur.generator.push({tool: 'submit_scenario', input: 'L' + d.layer, output: d.passed ? '통과' : (d.errors || []).join(', '), ok: d.passed});
        _simRender();
    });

    es.addEventListener('verdict', function(e) {
        var d = JSON.parse(e.data);
        var cur = _simCurrentRound();
        if (cur) cur.verdict = d;
        _simRender();
    });

    es.addEventListener('complete', function(e) {
        var d = JSON.parse(e.data);
        SIM.status = d.result === 'pass' ? 'success' : 'failed';
        SIM.finalScenario = d.final_scenario || null;
        _simDisconnect();
        _simRender();
    });

    es.addEventListener('error_event', function(e) {
        var d = JSON.parse(e.data);
        SIM.status = 'error';
        SIM.rounds.push({error: d.message});
        _simDisconnect();
        _simRender();
    });

    es.addEventListener('close', function() {
        _simDisconnect();
        if (SIM.status === 'running') SIM.status = 'completed';
        _simRender();
    });

    es.onerror = function() {
        _simDisconnect();
        if (SIM.status === 'running') {
            SIM.status = 'disconnected';
            _simRender();
        }
    };
}

function _simDisconnect() {
    if (SIM.evtSource) {
        SIM.evtSource.close();
        SIM.evtSource = null;
    }
}

function _simCurrentRound() {
    return SIM.rounds.length ? SIM.rounds[SIM.rounds.length - 1] : null;
}

// ── Render ──
function _simRender() {
    var el = $('simPanel');
    if (!el) return;

    var h = '';

    // Header
    h += '<div class="sim-header">';
    h += '<div class="sim-title">Simulation Engine v2</div>';
    h += '<div class="sim-status sim-status-' + SIM.status + '">' + _simStatusLabel(SIM.status) + '</div>';
    if (SIM.status === 'running') {
        h += '<button class="sim-cancel-btn" onclick="simCancel()">취소</button>';
    }
    h += '</div>';

    // Rounds
    SIM.rounds.forEach(function(round, idx) {
        if (round.error) {
            h += '<div class="sim-round sim-round-error"><div class="sim-round-hdr">오류</div><div class="sim-error-msg">' + esc(round.error) + '</div></div>';
            return;
        }
        h += '<div class="sim-round">';
        h += '<div class="sim-round-hdr" onclick="_simToggleRound(' + idx + ')">';
        h += '<span class="sim-round-num">Round ' + round.num + '/' + round.maxRounds + '</span>';
        if (round.verdict) {
            h += round.verdict.passed
                ? '<span class="sim-verdict-badge pass">PASS</span>'
                : '<span class="sim-verdict-badge fail">FAIL</span>';
        } else if (round.phase) {
            h += '<span class="sim-phase-badge">' + _simPhaseLabel(round.phase) + '</span>';
        }
        h += '</div>';

        h += '<div class="sim-round-body" id="simRoundBody_' + idx + '">';

        // Generator section
        if (round.generator.length) {
            h += '<div class="sim-agent-section">';
            h += '<div class="sim-agent-label">Generator</div>';
            round.generator.forEach(function(a) {
                var icon = a.ok ? '<span class="sim-icon-ok">&#10003;</span>' : '<span class="sim-icon-fail">&#10007;</span>';
                h += '<div class="sim-action-row">' + icon + '<span class="sim-tool">' + esc(a.tool) + '</span>';
                if (a.output) h += '<span class="sim-output">' + esc(_simTruncate(a.output, 80)) + '</span>';
                h += '</div>';
            });
            h += '</div>';
        }

        // Verifier section
        if (round.verifier.length) {
            h += '<div class="sim-agent-section">';
            h += '<div class="sim-agent-label">Verifier</div>';
            round.verifier.forEach(function(a) {
                var icon = a.ok ? '<span class="sim-icon-ok">&#10003;</span>' : '<span class="sim-icon-fail">&#10007;</span>';
                h += '<div class="sim-action-row">' + icon + '<span class="sim-tool">' + esc(a.tool) + '</span>';
                if (a.output) h += '<span class="sim-output">' + esc(_simTruncate(a.output, 80)) + '</span>';
                h += '</div>';
            });
            h += '</div>';
        }

        // Verdict
        if (round.verdict) {
            h += '<div class="sim-verdict-section ' + (round.verdict.passed ? 'pass' : 'fail') + '">';
            h += '<div class="sim-verdict-title">Verdict: ' + (round.verdict.passed ? 'PASS' : 'FAIL') + '</div>';
            if (round.verdict.failure_reason) h += '<div class="sim-verdict-reason">' + esc(round.verdict.failure_reason) + '</div>';
            if (round.verdict.fix_hint) h += '<div class="sim-verdict-hint">힌트: ' + esc(round.verdict.fix_hint) + '</div>';
            h += '</div>';
        }

        h += '</div></div>';
    });

    // Completion actions
    if (SIM.status === 'success') {
        h += '<div class="sim-complete-bar success">';
        h += '<span>시나리오 검증 완료 — 저장됨</span>';
        if (SIM.finalScenario) h += '<button class="sim-action-btn" onclick="_simShowScenario()">시나리오 보기</button>';
        h += '<button class="sim-action-btn" onclick="simLaunchPicker()">다른 FM 시도</button>';
        h += '</div>';
    } else if (SIM.status === 'failed') {
        h += '<div class="sim-complete-bar fail">';
        h += '<span>최대 라운드 도달 — 실패</span>';
        h += '<button class="sim-action-btn" onclick="simLaunchPicker()">다른 FM 시도</button>';
        h += '</div>';
    }

    el.innerHTML = h;
    _simScrollToBottom();
}

function _simToggleRound(idx) {
    var body = $('simRoundBody_' + idx);
    if (!body) return;
    body.classList.toggle('collapsed');
}

function _simShowScenario() {
    if (SIM.finalScenario && typeof scenShowGenResult === 'function') {
        scenShowGenResult('', SIM.finalScenario);
    }
}

function simLaunchPicker() {
    SIM.runId = null;
    SIM.rounds = [];
    SIM.status = 'idle';
    _simRender();
    if (typeof scenOpenPicker === 'function') scenOpenPicker();
}

function _simStatusLabel(s) {
    var map = {idle:'대기', starting:'시작 중...', running:'실행 중', success:'성공', failed:'실패', error:'오류', cancelled:'취소됨', disconnected:'연결 끊김', completed:'완료'};
    return map[s] || s;
}

function _simPhaseLabel(p) {
    var map = {generate:'생성 중', verify:'검증 중', improve:'개선 중'};
    return map[p] || p;
}

function _simTruncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '...' : str;
}

function _simScrollToBottom() {
    var el = $('simPanel');
    if (el) el.scrollTop = el.scrollHeight;
}

function scenCloseSimPanel() {
    $('scenSimSide').style.display = 'none';
    _simDisconnect();
}

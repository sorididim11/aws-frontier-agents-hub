let currentScenario = null;
let currentRunId = null;
let currentRunStartedTs = null;  // 실행 시작 Unix timestamp (Slack 필터링용)
let currentSlackThreadTs = null;  // Slack investigation thread ts (verifier가 기록)
let pollTimer = null;
let runStartTime = null;
let timerInterval = null;
let slackPollTimer = null;
const translationCache = {};

// ── Utility ──
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function getAlarmName(scenario) {
    const steps = scenario?.verification?.steps || [];
    const cwStep = steps.find(s => s.type === 'cw_alarm');
    return cwStep?.alarm || null;
}

function getMessageTypeIcon(text) {
    if (/\bFinding\b/i.test(text)) return { icon: '🔍', type: 'Finding' };
    if (/\bObservation\b/i.test(text)) return { icon: '📊', type: 'Observation' };
    if (/\bInvestigation\b/i.test(text)) return { icon: '🔬', type: 'Investigation' };
    if (/\bComplete\b/i.test(text)) return { icon: '✅', type: 'Complete' };
    return { icon: '💬', type: '' };
}

async function translateText(text) {
    if (!text) return null;
    const key = text.substring(0, 500);
    if (translationCache[key]) return translationCache[key];
    try {
        const res = await fetch('/api/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: key })
        });
        const data = await res.json();
        if (data.translated) {
            translationCache[key] = data.translated;
            return data.translated;
        }
    } catch (e) { }
    return null;
}

function renderMarkdown(text) {
    return text
        .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n/g, '<br>');
}

// ── Tab switching ──
function toggleLayer(id, header) {
    const body = document.getElementById(id);
    const chevron = header.querySelector('.layer-chevron');
    if (body.classList.contains('collapsed')) {
        body.classList.remove('collapsed');
        chevron.classList.add('open');
    } else {
        body.classList.add('collapsed');
        chevron.classList.remove('open');
    }
}
function switchTab(name) {
    document.querySelectorAll('.tabs .tab').forEach((t, i) => {
        const tabs = ['scenarios', 'history', 'environment'];
        t.classList.toggle('active', tabs[i] === name);
    });
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    if (name === 'history') loadHistory();
    if (name === 'environment') refreshEnv();
}

// ── Architecture & Flow Diagram ──
function renderArchDiagram(scenario) {
    const el = document.getElementById('archDiagram');
    const arch = scenario.architecture;
    if (!arch || !arch.components) { el.style.display = 'none'; return; }
    el.style.display = 'block';

    const comps = arch.components;
    const edges = arch.edges || [];
    const faultPath = arch.fault_path || [];

    // Layout: 2 rows. App services top, AWS/infra bottom
    const appNodes = comps.filter(c => c.type === 'app');
    const infraNodes = comps.filter(c => c.type !== 'app');

    const W = 110, H = 52, PAD = 20, GAP_X = 60, GAP_Y = 70;
    const row1Count = appNodes.length;
    const row2Count = infraNodes.length;
    const svgW = Math.max(row1Count, row2Count) * (W + GAP_X) + PAD * 2;
    const svgH = H * 2 + GAP_Y + PAD * 2 + 20;

    // Assign positions
    const pos = {};
    appNodes.forEach((c, i) => {
        pos[c.id] = { x: PAD + i * (W + GAP_X) + W / 2, y: PAD + H / 2 };
    });
    infraNodes.forEach((c, i) => {
        pos[c.id] = { x: PAD + i * (W + GAP_X) + W / 2, y: PAD + H + GAP_Y + H / 2 };
    });

    const typeIcon = { app: '⚙️', infra: '🗄️', aws: '☁️', agent: '🤖' };

    let svg = `<svg viewBox="0 0 ${svgW} ${svgH}" style="width:100%;max-width:${svgW}px;height:${svgH}px;" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#475569"/>
        </marker>
        <marker id="arrow-fault" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#ef4444"/>
        </marker>
    </defs>`;

    // Draw edges
    edges.forEach(e => {
        const from = pos[e.from], to = pos[e.to];
        if (!from || !to) return;
        const isFault = faultPath.includes(e.from) && faultPath.includes(e.to);
        const dx = to.x - from.x, dy = to.y - from.y;
        const len = Math.sqrt(dx * dx + dy * dy);
        const ex = from.x + dx * (W / 2 + 4) / len;
        const ey = from.y + dy * (H / 2 + 4) / len;
        const tx = to.x - dx * (W / 2 + 10) / len;
        const ty = to.y - dy * (H / 2 + 10) / len;
        const mx = (ex + tx) / 2, my = (ey + ty) / 2;
        const cls = isFault ? 'arch-edge fault' : 'arch-edge';
        const marker = isFault ? 'url(#arrow-fault)' : 'url(#arrow)';
        svg += `<line class="${cls}" x1="${ex}" y1="${ey}" x2="${tx}" y2="${ty}" marker-end="${marker}"/>`;
        if (e.label) {
            svg += `<text class="arch-edge-label" x="${mx}" y="${my - 4}" text-anchor="middle">${esc(e.label)}</text>`;
        }
    });

    // Draw nodes
    comps.forEach(c => {
        const p = pos[c.id];
        if (!p) return;
        const isFault = faultPath.includes(c.id);
        const cls = `arch-node ${c.type}${isFault ? ' fault' : ''}`;
        const icon = typeIcon[c.type] || '📦';
        svg += `<g class="${cls}" transform="translate(${p.x - W / 2},${p.y - H / 2})">
            <rect width="${W}" height="${H}"/>
            <text x="${W / 2}" y="20" text-anchor="middle">${icon} ${esc(c.label)}</text>
            <text class="subdesc" x="${W / 2}" y="36" text-anchor="middle">${esc(c.desc || '')}</text>
        </g>`;
    });

    svg += '</svg>';
    el.innerHTML = `<div style="font-size:0.72rem;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">🏗️ 아키텍처</div><div class="arch-diagram">${svg}</div>`;
}

function renderScenarioFlows(scenario) {
    // 정상 흐름 (아키텍처 아래)
    var nfEl = document.getElementById('normalFlowSection');
    var nf = scenario.normal_flow;
    if (nf && nf.length) {
        nfEl.style.display = 'block';
        var h = '<div style="font-size:0.72rem;color:#22c55e;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">✅ 정상 흐름</div>';
        h += '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:0;">';
        nf.forEach(function (s, i) {
            h += '<div class="flow-step"><div class="flow-node done" style="min-width:100px;max-width:160px;" title="' + esc(s.desc) + '">';
            h += '<div style="font-size:0.72rem;line-height:1.3;">' + esc(s.step) + '</div>';
            h += '<div style="font-size:0.6rem;color:#64748b;margin-top:2px;">' + esc(s.desc).substring(0, 40) + '</div>';
            h += '</div>' + (i < nf.length - 1 ? '<div class="flow-arrow">→</div>' : '') + '</div>';
        });
        h += '</div>';
        nfEl.innerHTML = h;
    } else { nfEl.style.display = 'none'; }

    // 검증 목표
    var goalEl = document.getElementById('investigationGoal');
    var goal = scenario.investigation_goal;
    if (goal) {
        goalEl.style.display = 'block';
        goalEl.innerHTML = '<div style="background:#1e3a5f;border:1px solid #3b82f6;border-radius:8px;padding:10px;font-size:0.8rem;color:#93c5fd;line-height:1.5;">🎯 <span style="font-weight:600;">검증 목표:</span> ' + esc(goal) + '</div>';
    } else { goalEl.style.display = 'none'; }
}

function renderFlowDiagram(scenario, runSteps) {
    var el = document.getElementById('flowDiagram');
    var stepsEl = document.getElementById('flowSteps');
    var ff = scenario.fault_flow;

    // fault_flow가 있으면 그걸 사용, 없으면 verification steps fallback
    if (ff && ff.length) {
        el.style.display = 'block';
        // verification step 상태를 fault_flow에 매핑 (인덱스 기반은 안 맞으니 키워드 매핑)
        var verSteps = runSteps || [];
        function getStepStatus(faultStep) {
            var text = (faultStep.step + ' ' + faultStep.desc).toLowerCase();
            for (var i = 0; i < verSteps.length; i++) {
                var vn = verSteps[i].name.toLowerCase();
                // 키워드 매칭
                if (text.indexOf('오염') >= 0 && vn.indexOf('검증 실패') >= 0) return verSteps[i].status;
                if (text.indexOf('oomkilled') >= 0 && vn.indexOf('oomkilled') >= 0) return verSteps[i].status;
                if (text.indexOf('알람') >= 0 && vn.indexOf('알람') >= 0) return verSteps[i].status;
                if (text.indexOf('lambda') >= 0 && vn.indexOf('lambda') >= 0) return verSteps[i].status;
                if (text.indexOf('조사 시작') >= 0 && vn.indexOf('조사 시작') >= 0) return verSteps[i].status;
                if (text.indexOf('조사') >= 0 && text.indexOf('시작') >= 0 && vn.indexOf('조사 시작') >= 0) return verSteps[i].status;
            }
            // 첫 번째 fault step은 trigger 성공이면 pass
            if (verSteps.length > 0 && verSteps[0].status === 'pass') return 'pass';
            return 'pending';
        }
        stepsEl.innerHTML = ff.map(function (f, i) {
            var status = getStepStatus(f);
            var cls = 'flow-node';
            if (status === 'pass') cls += ' done';
            else if (status === 'checking') cls += ' active';
            else if (status === 'fail') cls += ' fault-node';
            var isFault = f.step.indexOf('🔴') >= 0;
            var arrow = i < ff.length - 1 ? '<div class="flow-arrow' + (isFault ? ' fault' : '') + '">→</div>' : '';
            return '<div class="flow-step"><div class="' + cls + '" title="' + esc(f.desc) + '">' +
                '<div style="font-size:0.72rem;line-height:1.3;' + (isFault ? 'color:#fca5a5;' : '') + '">' + esc(f.step) + '</div>' +
                '<div style="font-size:0.6rem;color:#64748b;margin-top:2px;">' + esc(f.desc).substring(0, 50) + '</div>' +
                '</div>' + arrow + '</div>';
        }).join('');
    } else {
        // fallback: verification steps
        var steps = runSteps || (scenario.verification && scenario.verification.steps || []).map(function (s) { return { name: s.name, status: 'pending' }; });
        if (!steps.length) { el.style.display = 'none'; return; }
        el.style.display = 'block';
        stepsEl.innerHTML = steps.map(function (s, i) {
            var cls = 'flow-node';
            if (s.status === 'pass') cls += ' done';
            else if (s.status === 'checking') cls += ' active';
            else if (s.status === 'fail') cls += ' fault-node';
            var arrow = i < steps.length - 1 ? '<div class="flow-arrow">→</div>' : '';
            return '<div class="flow-step"><div class="' + cls + '"><div style="font-size:0.72rem;line-height:1.3;">' + esc(s.name) + '</div></div>' + arrow + '</div>';
        }).join('');
    }
}
// ── DevOps Agent 조사 흐름 (record_type 기반 트리) ──
// ── Investigation DAG ──
window._dagMessageCount = 0;
window._dagData = null;

function parseHypothesesToDag(data) { return HypothesisDag.parse(data); }

function renderInvestigationDag(data, isReadOnly) {
    var section = document.getElementById('investigationDagSection');
    var container = document.getElementById('dagContainer');
    var errorEl = document.getElementById('dagError');
    errorEl.style.display = 'none';

    if (!data || !data.hypotheses || data.hypotheses.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    HypothesisDag.render(container, data, {readOnly: isReadOnly, onClickTimes: 'showRawMsg'});
}

function openScenario(s, skipRestore) {
    currentScenario = s;
    currentRunId = null;
    currentRunStartedTs = null;
    currentSlackThreadTs = null;
    window._dagMessageCount = 0;
    window._dagData = null;
    stopPolling();
    document.getElementById('mainView').style.display = 'none';
    document.getElementById('scenarioPage').style.display = 'block';
    document.getElementById('scTitle').textContent = s.name;
    document.getElementById('scId').textContent = s.id;
    document.getElementById('scPurpose').textContent = s.purpose || '';
    document.getElementById('scExpected').textContent = s.expected_root_cause || '';
    document.getElementById('scCommand').textContent = s.trigger ? s.trigger.command : '';
    document.getElementById('btnRestore').style.display = (s.restore && s.restore.command) ? '' : 'none';
    document.getElementById('btnCancel').style.display = 'none';
    document.getElementById('btnRun').disabled = false;
    document.getElementById('runTimer').textContent = '';
    // 초기 상태
    document.getElementById('colRun').innerHTML = '<p style="color:#64748b;font-size:0.82rem;">실행 버튼을 눌러 시나리오를 시작하세요.</p>';
    document.getElementById('taskIdDisplay').textContent = '';
    // Evidence 초기화
    document.getElementById('evidenceContainer').innerHTML = '<div class="ai-placeholder" id="evidencePlaceholder">조사 완료 후 Evidence 증거가 여기에 표시됩니다.</div>';
    currentTaskId = null;
    // 섹션 초기화
    document.getElementById('colRubricEval').innerHTML = '<div class="slack-msg empty">조사 완료 후 자동 평가됩니다.</div>';
    document.getElementById('hypothesisContent').innerHTML = '<div class="slack-msg empty">조사 완료 후 자동 분석됩니다.</div>';
    // DAG 초기화
    document.getElementById('investigationDagSection').style.display = 'none';
    document.getElementById('dagContainer').innerHTML = '';
    document.getElementById('dagError').style.display = 'none';
    // 아키텍처 + 플로우 렌더링
    renderArchDiagram(s);
    renderFlowDiagram(s, null);
    renderScenarioFlows(s);
    loadScenarioHistory(s.id);
    // active run 복원 시도 (skipRestore가 아닌 경우만)
    if (!skipRestore) {
        _restoreActiveRun(s.id);
    }
}

async function openScenarioById(scenarioId, runId) {
    try {
        var spParam = (typeof SELECTED !== 'undefined' && SELECTED) ? '?space_id=' + encodeURIComponent(SELECTED) : '';
        const res = await fetch('/api/scenarios/' + scenarioId + spParam);
        if (!res.ok) { alert('시나리오를 찾을 수 없습니다: ' + scenarioId); return; }
        const s = await res.json();
        if (runId) {
            // 특정 run으로 열기: _restoreActiveRun 건너뛰고 직접 해당 run 로드
            openScenario(s, true);
            loadHistoryDetail(runId);
        } else {
            openScenario(s);
        }
    } catch (e) { alert('시나리오 로딩 실패: ' + e); }
}

async function _restoreActiveRun(scenarioId) {
    // 실행 중인 run만 복원. 완료된 이력은 이력 탭에서 확인.
    try {
        const res = await fetch('/api/runs');
        const runs = await res.json();
        for (const [rid, r] of Object.entries(runs)) {
            if (r.scenario_id === scenarioId && r.status === 'running') {
                currentRunId = rid;
                currentRunStartedTs = r.started_ts;
                runStartTime = new Date(r.started_at).getTime();
                document.getElementById('btnRun').disabled = true;
                document.getElementById('btnCancel').style.display = '';
                startTimer();
                renderTimeline(r);
                pollRunStatus();
                return;
            }
        }
    } catch (e) { }
}

function backToList() {
    stopPolling();
    document.getElementById('scenarioPage').style.display = 'none';
    document.getElementById('mainView').style.display = 'block';
}

// ── Run scenario ──
async function runScenario() {
    if (!currentScenario) return;
    document.getElementById('btnRun').disabled = true;
    document.getElementById('btnCancel').style.display = '';
    runStartTime = Date.now();
    currentRunStartedTs = Date.now() / 1000;  // Unix timestamp
    startTimer();
    // 실행 시작 시 조사 데이터 리셋
    lastSummaryHash = '';
    lastSummaryData = null;
    window._dagMessageCount = 0;
    window._dagData = null;
    window._currentRunSession = Date.now();  // 고유 세션 ID
    document.getElementById('colRun').innerHTML = '<div class="run-banner running"><span>🔄 실행 중...</span><span id="runTimerInner">0s</span></div><div id="timeline"><div class="tl-step checking"><div><div class="tl-name">🔄 트리거 실행 중...</div></div></div></div>';
    try {
        const res = await fetch('/api/run/' + currentScenario.id, { method: 'POST' });
        const data = await res.json();
        if (data.error) { alert('실행 실패: ' + data.error); document.getElementById('btnRun').disabled = false; stopTimer(); return; }
        currentRunId = data.run_id;
        pollRunStatus();
    } catch (e) { alert('실행 오류: ' + e); document.getElementById('btnRun').disabled = false; stopTimer(); }
}

function startTimer() {
    stopTimer();
    timerInterval = setInterval(() => {
        if (!runStartTime) return;
        const s = Math.floor((Date.now() - runStartTime) / 1000);
        document.getElementById('runTimer').textContent = s + 's';
        const inner = document.getElementById('runTimerInner');
        if (inner) inner.textContent = s + 's';
    }, 1000);
}
function stopTimer() { if (timerInterval) { clearInterval(timerInterval); timerInterval = null; } }
function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (slackPollTimer) { clearInterval(slackPollTimer); slackPollTimer = null; }
    stopTimer();
}

async function pollRunStatus() {
    if (!currentRunId) return;
    try {
        const res = await fetch('/api/run/' + currentRunId + '/status');
        const data = await res.json();
        renderTimeline(data);

        // investigation_task_id가 있으면 Agent Flow + DAG 실시간 업데이트
        if (data.investigation_task_id) {
            document.getElementById('taskIdDisplay').textContent = 'task: ' + data.investigation_task_id.substring(0, 12) + '...';
            try {
                var jRes = await fetch('/api/investigation-journal?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : '') + '&task_id=' + data.investigation_task_id + '&analyze=false&skip_classify=true');
                var jData = await jRes.json();
                if (jData.ok && jData.raw_messages && jData.raw_messages.length > 0) {
                    // DAG: compare message count with cache
                    var newCount = jData.raw_messages.length;
                    if (newCount > window._dagMessageCount) {
                        // Message count increased - call Bedrock for hypothesis structuring
                        var scenarioId = currentScenario ? currentScenario.id : '';
                        try {
                            var dagRes = await fetch('/api/investigation-journal?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : '') + '&task_id=' + data.investigation_task_id + '&scenario_id=' + scenarioId + '&analyze=true');
                            var dagData = await dagRes.json();
                            if (dagData.ok && dagData.hypotheses && dagData.hypotheses.length > 0) {
                                window._dagMessageCount = newCount;
                                window._dagData = dagData;
                                renderInvestigationDag(dagData, false);
                            }
                        } catch (dagErr) {
                            // Retain previous DAG, show non-blocking error
                            var dagErrorEl = document.getElementById('dagError');
                            if (dagErrorEl) {
                                dagErrorEl.textContent = '분석 업데이트 실패';
                                dagErrorEl.style.display = 'block';
                            }
                            if (window._dagData) {
                                renderInvestigationDag(window._dagData, false);
                            }
                        }
                    }
                    // else: message count unchanged, skip Bedrock call, retain current DAG
                }
            } catch (e) { }
        }

        if (data.status === 'running' || data.status === 'verifying') {
            pollTimer = setTimeout(pollRunStatus, 5000);
        } else {
            stopTimer();
            if (slackPollTimer) { clearInterval(slackPollTimer); slackPollTimer = null; }
            document.getElementById('btnCancel').style.display = 'none';
            document.getElementById('btnRun').disabled = false;
            if (data.status === 'completed') {
                loadScenarioHistory(currentScenario.id);

                // Auto-save DAG on completion (Task 7)
                if (window._dagData && currentRunId) {
                    var saveBody = {
                        hypotheses: window._dagData.hypotheses || [],
                        alarm: window._dagData.alarm || '',
                        root_cause: window._dagData.root_cause || null,
                        raw_count: window._dagData.raw_count || 0,
                        scenario_id: currentScenario ? currentScenario.id : ''
                    };
                    fetch('/api/run/' + currentRunId + '/dag', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(saveBody)
                    }).catch(function (e) { console.error('DAG auto-save failed:', e); });
                }

                // Re-render DAG in read-only mode on completion
                if (window._dagData) {
                    renderInvestigationDag(window._dagData, true);
                }

                const investigationDone = (data.steps || []).some(
                    s => s.name && s.name.includes('조사 완료') && s.status === 'pass'
                );
                if (investigationDone || data.result === 'pass') {
                    if (data.investigation_task_id) {
                        loadEvidence(data.investigation_task_id);
                        runAnalysisAndRubric();
                    }
                }
            }
        }
    } catch (e) { pollTimer = setTimeout(pollRunStatus, 5000); }
}

function renderTimeline(data) {
    const el = document.getElementById('colRun');
    // 플로우 다이어그램 실시간 업데이트
    if (currentScenario && data.steps) {
        renderFlowDiagram(currentScenario, data.steps);
    }
    if (!data.steps || !data.steps.length) {
        if (data.status === 'running') {
            el.innerHTML = '<div class="run-banner running"><span>🔄 트리거 실행 중...</span><span id="runTimerInner"></span></div>';
        }
        return;
    }
    const r = data.result || '';
    let bannerCls = 'running', bannerText = '🔄 실행 중...';
    if (data.status === 'completed') {
        bannerCls = r === 'pass' ? 'pass' : r === 'partial' ? 'partial' : 'fail';
        bannerText = r === 'pass' ? '✅ PASS' : r === 'partial' ? '⚠️ PARTIAL' : '❌ FAIL';
    } else if (data.status === 'cancelled') {
        bannerCls = 'partial'; bannerText = '🚫 취소됨';
    } else if (data.status === 'preflight_failed') {
        bannerCls = 'fail'; bannerText = '⛔ PRE-FLIGHT FAILED';
    }
    let html = '<div class="run-banner ' + bannerCls + '"><span>' + bannerText + '</span><span id="runTimerInner">' + (document.getElementById('runTimer')?.textContent || '') + '</span></div>';
    // incident_id, task_id 명시적 표시
    if (data.incident_id || data.investigation_task_id) {
        html += '<div style="font-size:0.72rem;color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:6px 10px;margin:6px 0;">';
        if (data.incident_id) html += '<div>📋 <b style="color:#60a5fa;">incident_id</b>: <code style="color:#fbbf24;">' + esc(data.incident_id) + '</code></div>';
        if (data.investigation_task_id) html += '<div>🔍 <b style="color:#60a5fa;">task_id</b>: <code style="color:#fbbf24;">' + esc(data.investigation_task_id) + '</code></div>';
        html += '</div>';
    }
    if (data.preflight && data.preflight.length) {
        html += data.preflight.map(function(pf) {
            var cls = pf.ok ? 'pass' : 'fail';
            var icon = pf.ok ? '✅' : '❌';
            return '<div class="tl-step ' + cls + '"><div>' +
                '<div class="tl-name">' + icon + ' [Pre-flight] ' + esc(pf.check) + '</div>' +
                '<div class="tl-detail">' + esc(pf.detail) + '</div>' +
                '</div></div>';
        }).join('');
    }
    const allPending = data.steps.every(s => s.status === 'pending');
    if (data.status === 'running' && allPending) {
        html += '<div class="tl-step checking"><div><div class="tl-name">🔄 ' + esc(data.trigger_output || '트리거 실행 중...') + '</div></div></div>';
    }
    html += data.steps.map(function(s, i) {
        const cls = s.status;
        const icon = s.status === 'pass' ? '✅' : s.status === 'fail' ? '❌' : s.status === 'checking' ? '🔄' : s.status === 'skipped' ? '⏭' : '⏳';
        let h = '<div class="tl-step ' + cls + '"><div><div class="tl-name">' + icon + ' ' + esc(s.name);
        if ((s.status === 'fail' || s.status === 'skipped') && currentRunId && data.status !== 'running') {
            h += ' <button onclick="retryFromStep(' + i + ')" style="margin-left:8px;font-size:0.65rem;padding:2px 8px;background:#1e40af;color:#93c5fd;border:1px solid #3b82f6;border-radius:4px;cursor:pointer;">↻ Retry</button>';
        }
        h += '</div>';
        if (s.detail) h += '<div class="tl-detail">' + esc(s.detail) + '</div>';
        if (s.elapsed !== null && s.elapsed !== undefined) h += '<div class="tl-elapsed">' + s.elapsed + 's</div>';
        h += '</div></div>';
        return h;
    }).join('');
    el.innerHTML = html;
}

async function retryFromStep(stepIndex) {
    if (!currentRunId) return;
    await fetch('/api/scenario-run/' + currentRunId + '/retry/' + stepIndex, { method: 'POST' });
    pollRunStatus();
}

async function cancelRun() {
    if (!currentRunId) return;
    await fetch('/api/run/' + currentRunId + '/cancel', { method: 'POST' });
}

async function restoreScenario() {
    if (!currentScenario || !currentScenario.restore) return;
    if (!confirm('복원 명령을 실행하시겠습니까?')) return;
    if (currentRunId) {
        const res = await fetch('/api/run/' + currentRunId + '/restore', { method: 'POST' });
        const data = await res.json();
        alert(data.success ? '복원 완료' : '복원 실패: ' + (data.error || ''));
    } else {
        alert('복원 명령:\n' + currentScenario.restore.command + '\n\n터미널에서 직접 실행하세요.');
    }
}

async function deleteScenario() {
    if (!currentScenario) return;
    if (!confirm(currentScenario.id + ' 삭제?')) return;
    const res = await fetch('/api/scenarios/' + currentScenario.id, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) { backToList(); location.reload(); }
    else alert('삭제 실패: ' + (data.error || ''));
}

// ── Investigation Summary (조사 메시지 요약 + 평가) ──
let lastSummaryHash = '';
let lastSummaryData = null;

function renderSummaryData(data, el) {
    let html = '';
    (data.phases || []).forEach(phase => {
        const assessCls = /정상|정확/.test(phase.phase_assessment) ? 'good' :
            /부분/.test(phase.phase_assessment) ? 'partial' : 'bad';
        html += `<div class="inv-phase">`;
        html += `<div class="inv-phase-header">`;
        html += `<span style="font-size:1.1rem;">${phase.icon || '💬'}</span>`;
        html += `<span class="inv-phase-name">${esc(phase.phase)}</span>`;
        if (phase.phase_assessment) {
            html += `<span class="inv-phase-assessment" style="background:${assessCls === 'good' ? '#14532d' : assessCls === 'partial' ? '#422006' : '#450a0a'};color:${assessCls === 'good' ? '#86efac' : assessCls === 'partial' ? '#fde68a' : '#fca5a5'};">${esc(phase.phase_assessment)}</span>`;
        }
        html += `</div>`;
        (phase.messages || []).forEach((m, mi) => {
            const uid = phase.phase + '-' + mi;
            html += `<div class="inv-msg">`;
            html += `<div class="inv-msg-summary">• ${esc(m.summary_ko)}</div>`;
            if (m.approach) html += `<div style="font-size:0.72rem;color:#64748b;margin-top:2px;">📐 ${esc(m.approach)}</div>`;
            if (m.original) {
                html += `<div class="inv-msg-toggle" onclick="document.getElementById('orig-${uid}').classList.toggle('show')">▶ 원본 보기</div>`;
                html += `<div class="inv-msg-original" id="orig-${uid}">${esc(m.original)}</div>`;
            }
            html += `</div>`;
        });
        html += `</div>`;
    });
    if (data.overall) {
        const score = data.overall.score || 0;
        const scoreCls = score >= 7 ? 'good' : score >= 4 ? 'mid' : 'bad';
        const matchIcon = data.overall.root_cause_match === '정확' ? '✅' : data.overall.root_cause_match === '부분적' ? '⚠️' : '❌';
        html += `<div class="inv-overall">`;
        html += `<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">`;
        html += `<span class="inv-score ${scoreCls}">${score}/10</span>`;
        html += `<span style="font-size:0.85rem;color:#94a3b8;">${matchIcon} 근본 원인: ${esc(data.overall.root_cause_match)}</span>`;
        html += `</div>`;
        const scores = data.overall.scores;
        if (scores) {
            const axes = [
                { key: 'root_cause_accuracy', label: '근본 원인 식별', icon: '🎯' },
                { key: 'fault_propagation', label: '장애 전파 추적', icon: '🔗' },
                { key: 'methodology', label: '조사 체계성', icon: '📋' },
                { key: 'data_utilization', label: '데이터소스 활용', icon: '📊' },
                { key: 'completeness', label: '조사 완결성', icon: '✅' }
            ];
            html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;">`;
            axes.forEach(a => {
                const v = scores[a.key] || 0;
                const c = v >= 7 ? '#22c55e' : v >= 4 ? '#f59e0b' : '#ef4444';
                html += `<div style="display:flex;align-items:center;gap:6px;font-size:0.78rem;">`;
                html += `<span>${a.icon}</span><span style="color:#94a3b8;min-width:90px;">${a.label}</span>`;
                html += `<div style="flex:1;height:6px;background:#1e293b;border-radius:3px;"><div style="width:${v * 10}%;height:100%;background:${c};border-radius:3px;"></div></div>`;
                html += `<span style="color:${c};font-weight:600;min-width:24px;">${v}</span>`;
                html += `</div>`;
            });
            html += `</div>`;
        }
        html += `<div style="font-size:0.85rem;color:#cbd5e1;line-height:1.5;">${esc(data.overall.summary_ko)}</div>`;
        html += `</div>`;
    }
    html += `<div style="font-size:0.68rem;color:#475569;margin-top:6px;text-align:right;">원본 메시지 ${data.raw_count || 0}건 분석</div>`;
    el.innerHTML = html;
}

function renderClassifiedMessages(classified, el) {
    var TYPE_COLORS = { Symptom: '#dc2626', Observation: '#2563eb', Finding: '#f59e0b', Conclusion: '#22c55e', System: '#64748b', Other: '#64748b' };
    var h = '';
    classified.forEach(function (group) {
        var color = TYPE_COLORS[group.type] || '#64748b';
        h += '<div style="margin-bottom:10px;">';
        h += '<div style="font-size:0.75rem;font-weight:600;color:' + color + ';margin-bottom:6px;display:flex;align-items:center;gap:6px;">';
        h += '<span>' + (group.icon || '') + '</span> ' + esc(group.type) + ' <span style="font-size:0.65rem;color:#64748b;">(' + group.count + ')</span></div>';
        group.messages.forEach(function (m, mi) {
            var uid = group.type + '-msg-' + mi;
            var isStructured = m.record_type && m.record_type !== 'message';
            var borderStyle = isStructured ? '3px solid ' + color : '2px solid ' + color + '40';
            h += '<div style="padding:6px 10px;margin-bottom:4px;border-left:' + borderStyle + ';font-size:0.78rem;' + (isStructured ? 'background:#0f172a;border-radius:0 4px 4px 0;' : '') + '">';
            if (m.source_icon) h += '<span style="font-size:0.65rem;padding:1px 5px;background:#1e293b;color:#94a3b8;border-radius:3px;margin-right:4px;">' + esc(m.source_icon + ' ' + (m.source || '')) + '</span>';
            if (isStructured) h += '<span style="font-size:0.6rem;padding:1px 4px;background:' + color + '30;color:' + color + ';border-radius:3px;margin-right:4px;">' + esc(m.record_type || '') + '</span>';
            if (m.code_ref) {
                var cr = m.code_ref;
                var refStr = (cr.file || '') + (cr.lines ? ':' + cr.lines : '');
                var codeId = 'code-' + group.type + '-' + mi;
                h += '<span style="font-size:0.65rem;padding:1px 5px;background:#1e3a5f;color:#60a5fa;border-radius:3px;margin-right:4px;font-family:monospace;cursor:pointer;" onclick="loadCodeSnippet(\'' + esc(cr.file || '') + '\',' + (parseInt(cr.lines) || 0) + ',' + (parseInt((cr.lines || '').split('-')[1]) || 0) + ',\'' + codeId + '\')">📄 ' + esc(refStr) + '</span>';
                if (cr.symbol) h += '<span style="font-size:0.65rem;color:#fbbf24;font-family:monospace;margin-right:4px;">' + esc(cr.symbol) + '</span>';
                h += '<pre class="inv-msg-original" id="' + codeId + '" style="font-size:0.7rem;background:#0a0a1a;color:#86efac;padding:8px;border-radius:4px;overflow-x:auto;"></pre>';
            }
            h += '<div style="color:#e2e8f0;">' + esc(m.summary) + '</div>';
            h += '<div style="font-size:0.65rem;color:#475569;margin-top:2px;">' + esc((m.time || '').substring(11, 19)) + '</div>';
            if (m.original && m.original.length > 50) {
                h += '<div class="inv-msg-toggle" onclick="document.getElementById(\'' + uid + '\').classList.toggle(\'show\')">▶ 원본 보기</div>';
                h += '<div class="inv-msg-original" id="' + uid + '">' + esc(m.original) + '</div>';
            }
            h += '</div>';
        });
        h += '</div>';
    });
    el.innerHTML = h;
}

function renderJournalData(data, el) {
    const SI = { rejected: '❌', partial: '⚠️', confirmed: '✅' };
    const SC = { rejected: '#450a0a', partial: '#422006', confirmed: '#14532d' };
    const SB = { rejected: '#ef4444', partial: '#f59e0b', confirmed: '#22c55e' };
    const ST = { rejected: '기각', partial: '부분 확인', confirmed: '확인' };
    const DI = { '메트릭': '📊', '로그': '📝', '트레이스': '🔍', 'K8s': '☸️', '코드': '💻', '배포이력': '🚀' };
    window._rawJournalMessages = data.raw_messages || [];
    let h = '';
    if (data.alarm) h += '<div class="tl-step pass"><div><div class="tl-name">🔔 알람 인지</div><div class="tl-detail">' + esc(data.alarm) + '</div></div></div>';
    if (data.linked_investigation_ids && data.linked_investigation_ids.length > 0) {
        h += '<div style="font-size:0.75rem;color:#a855f7;margin:6px 0 6px 20px;padding:8px 12px;background:#2d1a2d;border-radius:6px;border:1px solid #7c3aed;">';
        h += '🔗 Primary Investigation | LINKED ' + data.linked_investigation_ids.length + '건';
        h += '</div>';
    }
    (data.hypotheses || []).forEach(function (hy, hi) {
        var s = hy.status || 'partial', icon = SI[s] || '?', bg = SC[s] || '#1e293b', bd = SB[s] || '#334155', lb = ST[s] || s, uid = 'hyp-' + hi;
        h += '<div style="border:1px solid ' + bd + ';border-radius:8px;margin:8px 0 8px 20px;overflow:hidden;border-left:3px solid ' + bd + ';' + (s === 'confirmed' ? 'box-shadow:0 0 8px ' + bd + '40;' : '') + '">';
        h += '<div style="display:flex;align-items:center;gap:8px;padding:10px 14px;background:' + bg + ';cursor:pointer;" onclick="var e=document.getElementById(\'' + uid + '\');e.style.display=e.style.display===\'none\'?\'block\':\'none\'">';
        h += '<span>' + icon + '</span><span style="font-weight:600;font-size:0.85rem;color:#e2e8f0;">가설 ' + (hi + 1) + ': ' + esc(hy.title) + '</span>';
        if (hy.category) h += '<span style="font-size:0.65rem;padding:2px 6px;background:#334155;border-radius:4px;color:#94a3b8;">' + esc(hy.category) + '</span>';
        h += '<span style="margin-left:auto;font-size:0.75rem;color:' + bd + ';font-weight:600;">' + lb + '</span>';
        if (hy.leads_to) h += '<span style="font-size:0.65rem;color:#64748b;margin-left:4px;">→ 가설' + hy.leads_to + '</span>';
        h += '</div>';
        h += '<div id="' + uid + '">';
        (hy.steps || []).forEach(function (st) {
            var di = DI[st.data_source] || '📋', tm = (st.source_times || []).join(', ');
            h += '<div style="padding:8px 14px;border-bottom:1px solid #0f172a;' + (st.is_key ? 'background:#1a2e1a;' : '') + 'cursor:pointer;" onclick="showRawMsg(\'' + tm + '\')">';
            h += '<div style="display:flex;align-items:center;gap:6px;font-size:0.8rem;">';
            if (tm) h += '<span style="font-size:0.65rem;color:#475569;min-width:36px;">' + esc(tm) + '</span>';
            h += '<span>' + di + '</span><span style="color:#94a3b8;">' + esc(st.action) + '</span>';
            if (st.is_key) h += '<span style="font-size:0.65rem;padding:1px 5px;background:#22c55e30;color:#86efac;border-radius:3px;">⭐ 핵심</span>';
            h += '</div>';
            if (st.insight) h += '<div style="font-size:0.8rem;color:#e2e8f0;margin-top:4px;padding-left:' + (tm ? '58' : '22') + 'px;">💡 ' + esc(st.insight) + '</div>';
            h += '</div>';
        });
        if (hy.status_reason) h += '<div style="padding:8px 14px;font-size:0.78rem;color:' + bd + ';border-top:1px solid #334155;">' + icon + ' ' + esc(hy.status_reason) + '</div>';
        h += '</div></div>';
    });
    if (data.root_cause) {
        var rc = data.root_cause, mi = rc.matched ? '✅' : '❌';
        h += '<div class="tl-step pass" style="margin-top:8px;"><div><div class="tl-name">🎯 Root Cause ' + mi + '</div><div class="tl-detail" style="color:#86efac;">' + esc(rc.summary) + '</div></div></div>';
    }
    if (data.evaluation) {
        var ev = data.evaluation, sc = ev.score || 0, cc = sc >= 7 ? '#22c55e' : sc >= 4 ? '#f59e0b' : '#ef4444';
        h += '<div style="background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;margin-top:10px;">';
        h += '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;"><span style="font-size:1.3rem;font-weight:700;color:' + cc + ';">' + sc + '/10</span>';
        h += '<span style="font-size:0.82rem;color:#94a3b8;">가설 ' + (ev.total_hypotheses || 0) + '개: ' + (ev.rejected || 0) + ' 기각 / ' + (ev.confirmed || 0) + ' 확인 | 효율: ' + esc(ev.efficiency || '') + '</span></div>';
        if (ev.data_sources_used) h += '<div style="font-size:0.75rem;color:#64748b;margin-bottom:6px;">데이터소스: ' + ev.data_sources_used.map(function (d) { return esc(d) }).join(', ') + '</div>';
        h += '<div style="font-size:0.85rem;color:#cbd5e1;line-height:1.5;">' + esc(ev.summary || '') + '</div></div>';
    }
    h += '<div style="font-size:0.68rem;color:#475569;margin-top:6px;text-align:right;">DevOps Agent API + Bedrock | 원본 ' + (data.raw_count || 0) + '건</div>';
    el.innerHTML = h;
}
function showRawMsg(ts) {
    if (!ts || !window._rawJournalMessages) return;
    var p = document.getElementById('sidePanel'), c = document.getElementById('sidePanelContent');
    var times = ts.split(',').map(function (t) { return t.trim() });
    var matched = window._rawJournalMessages.filter(function (m) {
        var mt = (m.time || '').substring(11, 16);
        return times.some(function (t) { return mt.indexOf(t) === 0 });
    });
    if (!matched.length) c.textContent = '매칭 메시지 없음';
    else c.innerHTML = matched.map(function (m) { return '<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #1e293b;"><div style="font-size:0.68rem;color:#64748b;margin-bottom:4px;">' + esc(m.time || '') + '</div><div>' + esc(m.text) + '</div></div>' }).join('');
    p.style.display = 'block';
}

async function loadCodeSnippet(file, start, end, elementId) {
    var el = document.getElementById(elementId);
    if (el.classList.contains('show')) { el.classList.remove('show'); return; }
    el.innerHTML = 'Loading...';
    el.classList.add('show');
    try {
        var url = '/api/code/' + file + '?start=' + start;
        if (end) url += '&end=' + end;
        var res = await fetch(url);
        var data = await res.json();
        if (data.ok) {
            el.textContent = '// ' + file + ':' + (data.lines || '') + '\n' + data.snippet;
        } else {
            el.textContent = 'Error: ' + (data.error || '');
        }
    } catch (e) { el.textContent = 'Failed: ' + e; }
}

async function reanalyze() {
    lastSummaryHash = '';
    lastSummaryData = null;
    window._loadingInvestigation = false;
    var model = document.getElementById('modelSelect').value;
    var hypEl = document.getElementById('hypothesisContent');
    hypEl.innerHTML = '<div class="slack-msg empty"><span class="loading"></span> ' + model + ' 모델로 재분석 중...</div>';
    // task_id 확인
    var taskId = null;
    if (currentRunId) {
        try {
            var res = await fetch('/api/run/' + currentRunId + '/status');
            var data = await res.json();
            taskId = data.investigation_task_id;
        } catch (e) { }
    }
    if (!taskId) { hypEl.innerHTML = '<div class="slack-msg empty">task_id 없음</div>'; return; }
    var scenarioId = currentScenario ? currentScenario.id : '';
    // 가설 분석
    try {
        var anaRes = await fetch('/api/investigation-journal?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : '') + '&task_id=' + taskId + '&scenario_id=' + scenarioId + '&analyze=true&model=' + model);
        var anaData = await anaRes.json();
        if (anaData.ok && anaData.hypotheses && anaData.hypotheses.length > 0) {
            lastSummaryData = anaData;
            renderJournalData(anaData, hypEl);
        } else {
            hypEl.innerHTML = '<div class="slack-msg empty">가설 분석 결과 없음</div>';
        }
    } catch (e) {
        hypEl.innerHTML = '<div class="slack-msg empty">분석 실패: ' + String(e) + '</div>';
    }
}

// ── Scenario history ──
async function loadScenarioHistory(scenarioId) {
    const el = document.getElementById('colHistory');
    if (!el) return;  // 섹션 없으면 skip
    el.innerHTML = '<span class="loading"></span>';
    try {
        const res = await fetch('/api/history/' + scenarioId + '?limit=15');
        const data = await res.json();
        if (!data.length) { el.innerHTML = '<p style="color:#64748b;font-size:0.82rem;">실행 이력이 없습니다.</p>'; return; }
        let html = '<table class="history-table"><thead><tr><th>결과</th><th>시작</th><th>소요</th><th>단계</th></tr></thead><tbody>';
        data.forEach(r => {
            const badge = r.result === 'pass' ? 'pass' : r.result === 'partial' ? 'partial' : 'fail';
            const label = r.result === 'pass' ? 'PASS' : r.result === 'partial' ? 'PARTIAL' : 'FAIL';
            const started = r.started_at ? new Date(r.started_at).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-';
            let elapsed = '-';
            if (r.started_at && r.completed_at) elapsed = Math.round((new Date(r.completed_at) - new Date(r.started_at)) / 1000) + 's';
            const steps = (r.steps || []).map(s => s.status === 'pass' ? '✅' : s.status === 'fail' ? '❌' : '⏭').join('');
            const startedTs = r.started_at ? (new Date(r.started_at).getTime() / 1000).toFixed(0) : '';
            html += `<tr style="cursor:pointer;" onclick="loadHistoryDetail('${r.run_id}','${startedTs}')" title="클릭하여 조사 내용 보기">`;
            html += `<td><span class="badge ${badge}">${label}</span></td>`;
            html += `<td style="color:#94a3b8;font-size:0.75rem;">${started}</td>`;
            html += `<td style="color:#94a3b8;font-size:0.75rem;">${elapsed}</td>`;
            html += `<td>${steps}</td></tr>`;
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    } catch (e) { el.innerHTML = '<p style="color:#ef4444;">이력 로딩 실패</p>'; }
}

async function loadHistoryDetail(runId, startedTs) {
    currentRunId = runId;
    lastSummaryHash = '';
    lastSummaryData = null;
    // 해당 row 하이라이트 (이력 테이블에서 호출된 경우만)
    if (event && event.currentTarget) {
        document.querySelectorAll('.history-table tr').forEach(tr => tr.style.background = '');
        event.currentTarget.style.background = '#1e3a5f';
    }

    try {
        const res = await fetch('/api/run/' + runId + '/status');
        const data = await res.json();
        const taskId = data.investigation_task_id;
        // 실행 상태 표시
        if (data.steps) renderTimeline(data);
        if (taskId) {
            document.getElementById('taskIdDisplay').textContent = 'task: ' + taskId.substring(0, 12) + '...';
            // Evidence 로드
            loadEvidence(taskId);
            // 저장된 가설 분석 로드 (investigation_summary에서)
            try {
                var journalRes = await fetch('/api/investigation-journal?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : '') + '&task_id=' + taskId + '&analyze=false&skip_classify=true');
                var journalData = await journalRes.json();
                if (journalData.ok && journalData.raw_messages) {
                    // investigation_summary에서 가설 데이터 추출
                    var summaryMsg = journalData.raw_messages.find(function (m) { return m.record_type === 'investigation_summary' || m.record_type === 'investigation_summary_md'; });
                    if (summaryMsg) {
                        document.getElementById('hypothesisContent').innerHTML = '<div style="font-size:0.78rem;color:#e2e8f0;line-height:1.5;max-height:300px;overflow-y:auto;white-space:pre-wrap;">' + esc((summaryMsg.text || '').substring(0, 2000)) + '</div>';
                    }
                }
            } catch (e) { /* ignore */ }
        } else {
            document.getElementById('hypothesisContent').innerHTML = '<div class="slack-msg empty">조사 데이터 없음</div>';
        }
        // Rubric 평가 (DB에서 로드)
        loadSavedEvaluation(runId);

        // DAG 이력 로드 (Task 6)
        try {
            var dagRes = await fetch('/api/run/' + runId + '/dag');
            if (dagRes.ok) {
                var dagData = await dagRes.json();
                if (dagData && dagData.hypotheses && dagData.hypotheses.length > 0) {
                    renderInvestigationDag(dagData, true);
                }
            }
            // 404 = no DAG saved, keep section hidden
        } catch (dagErr) { /* ignore, DAG section stays hidden */ }
    } catch (e) {
        document.getElementById('hypothesisContent').innerHTML = '<div class="slack-msg empty">로딩 실패: ' + esc(String(e)) + '</div>';
    }
}

// ── Rubric Evaluation ──
async function loadSavedEvaluation(runId) {
    const el = document.getElementById('colRubricEval');
    try {
        const res = await fetch('/api/evaluate/' + runId);
        if (!res.ok) { console.log('loadSavedEvaluation: no saved eval, status=' + res.status); return; }
        const data = await res.json();
        if (!data.criteria_results) { console.log('loadSavedEvaluation: no criteria_results'); return; }
        renderRubricResults(data, el);
    } catch (e) { console.error('loadSavedEvaluation error:', e); }
}

function renderRubricResults(data, el) {
    let html = '<div style="margin-bottom:12px;">';
    html += '<div style="font-size:1.2rem;font-weight:bold;color:#fbbf24;">종합 점수: ' + data.overall_score + ' / ' + data.max_score + '</div>';
    html += '<div style="font-size:0.72rem;color:#64748b;">모델: ' + esc(data.model || '') + ' | 메시지: ' + (data.message_count || 0) + '건</div>';
    html += '</div>';
    // Radar Chart
    const criteria = data.criteria_results || {};
    const keys = Object.keys(criteria);
    if (keys.length >= 3) {
        var n = keys.length, cx = 120, cy = 120, R = 90;
        var scores = keys.map(function (k) { return Number(criteria[k].score) || 0; });
        var labels = keys.map(function (k) { return k.replace(/_/g, ' '); });
        function rpt(i, r) { var a = (Math.PI * 2 * i / n) - Math.PI / 2; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
        var svg = '<svg viewBox="0 0 240 240" style="width:100%;max-width:240px;height:240px;">';
        [2, 4, 6, 8, 10].forEach(function (v) {
            var pts = []; for (var i = 0; i < n; i++) pts.push(rpt(i, R * v / 10).join(','));
            svg += '<polygon points="' + pts.join(' ') + '" fill="none" stroke="#334155" stroke-width="0.5"/>';
        });
        for (var i = 0; i < n; i++) { var p = rpt(i, R); svg += '<line x1="' + cx + '" y1="' + cy + '" x2="' + p[0] + '" y2="' + p[1] + '" stroke="#334155" stroke-width="0.5"/>'; }
        var dp = []; for (var i = 0; i < n; i++) dp.push(rpt(i, R * scores[i] / 10).join(','));
        svg += '<polygon points="' + dp.join(' ') + '" fill="#3b82f640" stroke="#3b82f6" stroke-width="2"/>';
        for (var i = 0; i < n; i++) { var p = rpt(i, R * scores[i] / 10); var c = scores[i] >= 7 ? '#22c55e' : scores[i] >= 4 ? '#fbbf24' : '#ef4444'; svg += '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="4" fill="' + c + '" stroke="#0f172a" stroke-width="1"/>'; }
        for (var i = 0; i < n; i++) { var p = rpt(i, R + 20); var anc = p[0] < cx - 10 ? 'end' : p[0] > cx + 10 ? 'start' : 'middle'; svg += '<text x="' + p[0] + '" y="' + p[1] + '" text-anchor="' + anc + '" fill="#94a3b8" font-size="8" dominant-baseline="middle">' + esc(labels[i]) + '</text>'; svg += '<text x="' + p[0] + '" y="' + (p[1] + 10) + '" text-anchor="' + anc + '" fill="' + (scores[i] >= 7 ? '#22c55e' : scores[i] >= 4 ? '#fbbf24' : '#ef4444') + '" font-size="9" font-weight="bold" dominant-baseline="middle">' + scores[i] + '</text>'; }
        svg += '</svg>';
        html += '<div style="text-align:center;margin-bottom:12px;">' + svg + '</div>';
    }
    for (const [id, c] of Object.entries(criteria)) {
        const score = Number(c.score) || 0;
        const pct = Math.round(score * 10);
        const color = score >= 7 ? '#22c55e' : score >= 4 ? '#fbbf24' : '#ef4444';
        html += '<div style="margin-bottom:10px;padding:8px;background:#1e293b;border-radius:6px;border-left:3px solid ' + color + ';">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
        html += '<span style="font-size:0.8rem;color:#e2e8f0;">' + esc(id) + ' <span style="color:#64748b;">(가중치 ' + c.weight + ')</span></span>';
        html += '<span style="font-size:0.85rem;font-weight:bold;color:' + color + ';">' + c.score + '/10</span>';
        html += '</div>';
        html += '<div style="background:#334155;border-radius:3px;height:6px;margin:4px 0;"><div style="background:' + color + ';height:6px;border-radius:3px;width:' + pct + '%;"></div></div>';
        html += '<div style="font-size:0.72rem;color:#94a3b8;">' + esc(c.criteria || '') + '</div>';
        html += '<div style="font-size:0.72rem;color:#cbd5e1;margin-top:4px;">' + esc(c.reasoning || '') + '</div>';
        html += '</div>';
    }
    el.innerHTML = html;
}

// ── Analysis & Rubric (병렬 실행) ──
async function runAnalysisAndRubric() {
    if (!currentRunId) { alert('실행 중인 시나리오가 없습니다.'); return; }
    var hypEl = document.getElementById('hypothesisContent');
    var rubEl = document.getElementById('colRubricEval');
    hypEl.innerHTML = '<div style="text-align:center;padding:20px;"><span class="loading"></span> 가설 분석 중...</div>';
    rubEl.innerHTML = '<div style="text-align:center;padding:20px;"><span class="loading"></span> Rubric 평가 중...</div>';

    try {
        var statusRes = await fetch('/api/run/' + currentRunId + '/status');
        var statusData = await statusRes.json();
        var taskId = statusData.investigation_task_id;
        var scenarioId = statusData.scenario_id || (currentScenario ? currentScenario.id : '');

        if (!taskId) {
            hypEl.innerHTML = '<p style="color:#f87171;">task_id가 없습니다. 조사가 완료된 후 실행하세요.</p>';
            rubEl.innerHTML = '<p style="color:#f87171;">task_id가 없습니다.</p>';
            return;
        }

        document.getElementById('taskIdDisplay').textContent = 'task: ' + taskId.substring(0, 12) + '...';
        var model = document.getElementById('modelSelect').value;

        // 가설 분석 + Rubric 평가 병렬 실행
        var [hypResult, rubResult] = await Promise.all([
            // 가설 분석
            fetch('/api/investigation-journal?space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : '') + '&task_id=' + taskId + '&scenario_id=' + scenarioId + '&analyze=true&model=' + model)
                .then(function (r) { return r.json(); })
                .catch(function (e) { return { error: String(e) }; }),
            // Rubric 평가
            fetch('/api/evaluate/' + currentRunId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId, scenario_id: scenarioId })
            }).then(function (r) { return r.json(); })
                .catch(function (e) { return { error: String(e) }; })
        ]);

        // 가설 분석 결과 렌더링
        if (hypResult.ok && hypResult.hypotheses && hypResult.hypotheses.length > 0) {
            lastSummaryData = hypResult;
            renderJournalData(hypResult, hypEl);
        } else if (hypResult.error) {
            hypEl.innerHTML = '<p style="color:#f87171;">분석 실패: ' + esc(hypResult.error) + '</p>';
        } else {
            hypEl.innerHTML = '<div class="slack-msg empty">가설 분석 결과 없음</div>';
        }

        // Rubric 결과 렌더링
        if (rubResult.error) {
            rubEl.innerHTML = '<p style="color:#f87171;">평가 실패: ' + esc(rubResult.error) + '</p>';
        } else if (rubResult.criteria_results) {
            renderRubricResults(rubResult, rubEl);
        } else {
            rubEl.innerHTML = '<div class="slack-msg empty">평가 결과 없음</div>';
        }
    } catch (e) {
        hypEl.innerHTML = '<p style="color:#f87171;">오류: ' + e.message + '</p>';
        rubEl.innerHTML = '<p style="color:#f87171;">오류: ' + e.message + '</p>';
    }
}

async function runRubricEvaluation() {
    if (!currentRunId) { alert('실행 중인 시나리오가 없습니다.'); return; }
    const el = document.getElementById('colRubricEval');
    el.innerHTML = '<div style="text-align:center;padding:20px;"><span class="loading"></span> Rubric 평가 중... (기준별 독립 채점, 30초~1분 소요)</div>';

    try {
        const statusRes = await fetch('/api/run/' + currentRunId + '/status');
        const statusData = await statusRes.json();
        const taskId = statusData.investigation_task_id;
        const scenarioId = statusData.scenario_id;

        if (!taskId) {
            el.innerHTML = '<p style="color:#f87171;">task_id가 없습니다. 조사가 완료된 후 평가하세요.</p>';
            return;
        }

        const res = await fetch('/api/evaluate/' + currentRunId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId, scenario_id: scenarioId })
        });
        const data = await res.json();

        if (data.error) {
            el.innerHTML = '<p style="color:#f87171;">평가 실패: ' + esc(data.error) + '</p>';
            return;
        }

        // Render rubric results
        renderRubricResults(data, el);
    } catch (e) {
        el.innerHTML = '<p style="color:#f87171;">오류: ' + e.message + '</p>';
    }
}

// ── Evidence Dashboard ──
let currentTaskId = null;

async function loadEvidence(taskId) {
    currentTaskId = taskId;
    if (!taskId) return;
    const el = document.getElementById('evidenceContainer');
    el.innerHTML = '<div class="ai-placeholder"><span class="loading"></span> Evidence 로딩 중...</div>';
    try {
        const resp = await fetch('/api/evidence?task_id=' + encodeURIComponent(taskId) + '&space_id=' + encodeURIComponent(typeof SELECTED !== 'undefined' && SELECTED ? SELECTED : ''));
        const json = await resp.json();
        if (!json.ok) throw new Error(json.error || 'API error');
        renderRcaReportInline(json.evidence, el);
    } catch (e) {
        el.innerHTML = '<div class="ai-placeholder">Evidence 로딩 실패: ' + esc(e.message) + '</div>';
    }
}

// ── Main History tab ──
let _historyNextKey = null;
let _historyItems = [];

async function loadHistory(append) {
    const el = document.getElementById('historyContent');
    if (!append) {
        el.innerHTML = '<div class="loading"></div>';
        _historyNextKey = null;
        _historyItems = [];
    }
    const filter = document.getElementById('historyFilter').value;
    const scenFilter = document.getElementById('historyScenarioFilter').value;
    try {
        let url = scenFilter ? '/api/history/' + scenFilter + '?limit=10' : '/api/history?limit=10';
        if (typeof SELECTED !== 'undefined' && SELECTED) url += '&space_id=' + encodeURIComponent(SELECTED);
        if (_historyNextKey) url += '&last_key=' + encodeURIComponent(_historyNextKey);
        const res = await fetch(url);
        let resp = await res.json();
        let data = resp.items || resp;
        _historyNextKey = resp.next_key || null;
        if (filter === 'pass') data = data.filter(r => r.result === 'pass');
        if (filter === 'fail') data = data.filter(r => r.result === 'fail');
        _historyItems = _historyItems.concat(data);
        populateScenarioFilter(_historyItems);
        if (!_historyItems.length) { el.innerHTML = '<p style="color:#64748b;">실행 기록이 없습니다.</p>'; return; }
        let html = '<table class="history-table"><thead><tr><th>시나리오</th><th>결과</th><th>시작</th><th>소요</th><th>단계</th></tr></thead><tbody>';
        _historyItems.forEach(r => {
            const badge = r.result === 'pass' ? 'pass' : r.result === 'partial' ? 'partial' : 'fail';
            const label = r.result === 'pass' ? 'PASS' : r.result === 'partial' ? 'PARTIAL' : 'FAIL';
            const started = r.started_at ? new Date(r.started_at).toLocaleString('ko-KR') : '-';
            let elapsed = '-';
            if (r.started_at && r.completed_at) elapsed = Math.round((new Date(r.completed_at) - new Date(r.started_at)) / 1000) + 's';
            const steps = (r.steps || []).map(s => s.status === 'pass' ? '✅' : s.status === 'fail' ? '❌' : '⏭').join('');
            html += '<tr style="cursor:pointer;" onclick="openScenarioById(\'' + esc(r.scenario_id) + '\',\'' + esc(r.run_id) + '\')" title="시나리오 열기"><td>' + esc(r.scenario_name || r.scenario_id) + '</td><td><span class="badge ' + badge + '">' + label + '</span></td><td>' + started + '</td><td>' + elapsed + '</td><td>' + steps + '</td></tr>';
        });
        html += '</tbody></table>';
        if (_historyNextKey) {
            html += '<div style="text-align:center;margin:10px 0;"><button class="btn btn-secondary btn-sm" onclick="loadHistory(true)">더 보기</button></div>';
        }
        el.innerHTML = html;
    } catch (e) { el.innerHTML = '<p style="color:#ef4444;">로딩 실패: ' + e + '</p>'; }
}

function populateScenarioFilter(data) {
    const sel = document.getElementById('historyScenarioFilter');
    if (sel.options.length > 1) return;
    const ids = [...new Set(data.map(r => r.scenario_id))].sort();
    ids.forEach(id => { const o = document.createElement('option'); o.value = id; o.textContent = id; sel.appendChild(o); });
}

// ── Environment ──
async function refreshEnv() {
    const el = document.getElementById('envContent');
    el.innerHTML = '<div class="loading"></div> 로딩 중...';
    try {
        const res = await fetch('/api/environment');
        const data = await res.json();
        let html = '';
        html += '<div class="env-section"><h3 style="margin-bottom:10px;color:#94a3b8;font-size:0.82rem;">🖥 EKS 노드</h3><div class="env-grid">';
        (data.nodes || []).forEach(n => { const c = n.ready === 'True' ? 'ok' : 'error'; html += '<div class="env-item ' + c + '"><div class="label">' + esc(n.name) + '</div><div class="value">' + (n.ready === 'True' ? '✅ Ready' : '❌ NotReady') + '</div></div>'; });
        if (!data.nodes?.length) html += '<div class="env-item warn"><div class="value">노드 정보 없음</div></div>';
        html += '</div></div>';
        html += '<div class="env-section"><h3 style="margin-bottom:10px;color:#94a3b8;font-size:0.82rem;">📦 Pods</h3><div class="env-grid">';
        (data.pods || []).forEach(p => { const c = p.phase === 'Running' ? 'ok' : p.phase === 'Pending' ? 'warn' : 'error'; html += '<div class="env-item ' + c + '"><div class="label">' + esc(p.app || p.name) + '</div><div class="value">' + p.phase + (p.restarts > 0 ? ' (재시작:' + p.restarts + ')' : '') + '</div></div>'; });
        if (!data.pods?.length) html += '<div class="env-item warn"><div class="value">Pod 없음</div></div>';
        html += '</div></div>';
        html += '<div class="env-section"><h3 style="margin-bottom:10px;color:#94a3b8;font-size:0.82rem;">🔔 CloudWatch 알람</h3><div class="env-grid">';
        (data.alarms || []).forEach(a => { const c = a.State === 'OK' ? 'ok' : a.State === 'ALARM' ? 'error' : 'warn'; html += '<div class="env-item ' + c + '"><div class="label">' + esc(a.Name.replace('devops-agent-test-', '')) + '</div><div class="value">' + a.State + '</div></div>'; });
        if (!data.alarms?.length) html += '<div class="env-item warn"><div class="value">알람 없음</div></div>';
        html += '</div></div>';
        el.innerHTML = html;
    } catch (e) { el.innerHTML = '<p style="color:#ef4444;">로딩 실패: ' + e + '</p>'; }
}

// ── Create modal ──
function showCreateModal() { document.getElementById('createModal').classList.add('show'); }
function closeCreateModal() { document.getElementById('createModal').classList.remove('show'); }
async function createScenario() {
    const id = document.getElementById('cId').value.trim();
    const name = document.getElementById('cName').value.trim();
    if (!id || !name) { alert('ID와 이름은 필수입니다.'); return; }
    let verification = { steps: [] };
    const vText = document.getElementById('cVerification').value.trim();
    if (vText) { try { verification = { steps: JSON.parse(vText) }; } catch (e) { alert('검증 JSON 오류: ' + e.message); return; } }
    const flowText = document.getElementById('cFlow').value.trim();
    const flow = flowText ? flowText.split('\n').filter(l => l.trim()) : [];
    const restoreCmd = document.getElementById('cRestore').value.trim();
    const scenario = { id, name, category: document.getElementById('cCategory').value, layer: document.getElementById('cLayer').value.trim(), purpose: document.getElementById('cPurpose').value.trim(), expected_root_cause: document.getElementById('cExpected').value.trim(), flow, trigger: { type: 'kubectl', command: document.getElementById('cTrigger').value.trim() }, verification };
    if (restoreCmd) scenario.restore = { command: restoreCmd };
    try {
        const res = await fetch('/api/scenarios', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scenario) });
        const data = await res.json();
        if (data.success) { closeCreateModal(); location.reload(); } else alert('생성 실패: ' + (data.error || ''));
    } catch (e) { alert('생성 오류: ' + e); }
}

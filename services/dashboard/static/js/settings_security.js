// ================================================================
// SETTINGS SECURITY TAB — settings_security.js
// Pentest lifecycle: config display + run + poll + timeline + findings
// ================================================================

var SEC = { pollTimer: null, pentestJobId: null };

function _secUrl(path) {
    if (typeof SEC_SPACE_ID !== 'undefined' && SEC_SPACE_ID) {
        return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'sec_space_id=' + encodeURIComponent(SEC_SPACE_ID);
    }
    return path;
}

function switchSettingsTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
    });
    document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
    document.getElementById('tab-' + tabId).classList.add('active');
}

function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

// ================================================================
// Code Review (SAST) — 등록된 리포 상태
// ================================================================

function loadCodeReview() {
    var el = document.getElementById('sastFindings');
    if (!el) return;
    fetch(_secUrl('/api/settings/security/code-review'))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                el.innerHTML = '<p class="empty">코드 리뷰 설정을 불러올 수 없습니다: ' + esc(data.error || '') + '</p>';
                return;
            }
            if (!data.repos.length) {
                el.innerHTML = '<p class="empty">Code Review (SAST) 미설정 — PR 자동 리뷰를 사용하려면 리포지토리 연동 필요</p>';
                return;
            }
            var html = '<table class="sec-table"><thead><tr>';
            html += '<th>리포지토리</th><th>PR 코멘트</th><th>자동 수정</th>';
            html += '</tr></thead><tbody>';
            data.repos.forEach(function(r) {
                html += '<tr>';
                html += '<td><a class="pr-link" href="https://github.com/' + esc(r.owner) + '/' + esc(r.name) + '" target="_blank">' + esc(r.owner) + '/' + esc(r.name) + '</a></td>';
                html += '<td>' + (r.leaveComments ? '<span class="status-badge fixed">활성</span>' : '<span class="status-badge open">비활성</span>') + '</td>';
                html += '<td>' + (r.remediateCode ? '<span class="status-badge fixed">활성</span>' : '<span class="status-badge open">비활성</span>') + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table>';
            html += '<p style="font-size:.6rem;color:#475569;margin-top:8px;">PR 생성 시 Security Agent가 자동으로 코드 리뷰를 수행합니다.</p>';
            el.innerHTML = html;
        })
        .catch(function(e) {
            el.innerHTML = '<p class="empty">로딩 실패: ' + esc(String(e)) + '</p>';
        });
}

// ================================================================
// Target Domain
// ================================================================

function loadTargetDomain() {
    var el = document.getElementById('targetDomainStatus');
    var domEl = document.getElementById('domainConfig');
    fetch(_secUrl('/api/settings/security/target-domain'))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                if (el) el.innerHTML = '<p class="empty">Failed to load domain info: ' + esc(data.error || '') + '</p>';
                return;
            }
            if (el) {
                var html = '<h4 style="font-size:.66rem;color:#94a3b8;margin-bottom:10px;font-weight:600;">Target Domains (대상 도메인)</h4>';
                if (!data.domains.length) {
                    html += '<p class="empty">No target domains registered (등록된 도메인 없음)</p>';
                } else {
                    html += '<table class="sec-table"><thead><tr>';
                    html += '<th>Domain (도메인)</th><th>Verification (검증)</th><th>ID</th>';
                    html += '</tr></thead><tbody>';
                    data.domains.forEach(function(d) {
                        var statusCls = d.status === 'VERIFIED' ? 'fixed' : 'open';
                        html += '<tr>';
                        html += '<td class="mono">' + esc(d.domain) + '</td>';
                        html += '<td><span class="status-badge ' + statusCls + '">' + esc(d.status) + '</span></td>';
                        html += '<td class="mono">' + esc((d.id || '').substring(0, 16)) + '...</td>';
                        html += '</tr>';
                    });
                    html += '</tbody></table>';
                    html += '<p style="font-size:.56rem;color:#475569;margin-top:6px;">Private domains show UNREACHABLE — pentest uses VPC ENI for direct access (프라이빗 도메인은 VPC 직접 접근)</p>';
                }
                el.innerHTML = html;
            }
            if (domEl) {
                var html2 = '';
                if (data.zone) {
                    html2 += '<div class="gen-row"><span class="lb">Private Zone</span><span class="vl">' + esc(data.zone.name) + '</span></div>';
                    html2 += '<div class="gen-row"><span class="lb">Zone ID</span><span class="vl">' + esc(data.zone.zoneId) + '</span></div>';
                    html2 += '<div class="gen-row"><span class="lb">Associated VPCs (연결 VPC)</span><span class="vl">' + esc((data.zone.vpcs || []).join(', ')) + '</span></div>';
                }
                if (data.domains.length) {
                    data.domains.forEach(function(d) {
                        html2 += '<div class="gen-row"><span class="lb">Target Domain</span><span class="vl">' + esc(d.domain) + ' (' + esc(d.status) + ')</span></div>';
                    });
                }
                if (!html2) html2 = '<p class="empty">No private domain configured</p>';
                domEl.innerHTML = html2;
            }
        })
        .catch(function(e) {
            if (el) el.innerHTML = '<p class="empty">Loading failed: ' + esc(String(e)) + '</p>';
            if (domEl) domEl.innerHTML = '<p class="empty">Loading failed</p>';
        });
}

// ================================================================
// Pentest History
// ================================================================

function loadPentestHistory() {
    var el = document.getElementById('pentestHistory');
    var cardEl = document.getElementById('latestJobCard');
    if (!el) return;

    fetch(_secUrl('/api/settings/security/pentest'))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                el.innerHTML = '<p class="empty">' + esc(data.error || '이력 로드 실패') + '</p>';
                if (cardEl) cardEl.innerHTML = '';
                return;
            }
            var jobs = data.jobs || [];

            // 최신 완료 결과 카드
            var completedJob = jobs.find(function(j) { return j.status === 'COMPLETED'; });
            if (cardEl) {
                if (completedJob) {
                    var secInsLink = document.getElementById("secInsightsLink");
                    var insUrl = secInsLink ? secInsLink.href : '/security/insights';
                    if (completedJob.jobId) insUrl += (insUrl.indexOf('?') >= 0 ? '&' : '?') + 'job_id=' + encodeURIComponent(completedJob.jobId);
                    var cardHtml = '<a href="' + insUrl + '" style="display:block;text-decoration:none">';
                    cardHtml += '<div style="border:1px solid #334155;border-radius:8px;padding:14px 18px;background:#1e293b;cursor:pointer;transition:border-color .2s" onmouseover="this.style.borderColor=\'#38bdf8\'" onmouseout="this.style.borderColor=\'#334155\'">';
                    cardHtml += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">';
                    cardHtml += '<div style="width:10px;height:10px;border-radius:50%;background:#4ade80"></div>';
                    cardHtml += '<span style="font-size:.64rem;color:#e2e8f0;font-weight:600">최근 조사 완료</span>';
                    cardHtml += '<span style="font-size:.52rem;color:#64748b;margin-left:auto">' + esc((completedJob.startedAt || '').substring(0, 16)) + '</span>';
                    cardHtml += '</div>';
                    if (completedJob.findingsCount !== undefined) {
                        cardHtml += '<div style="font-size:.6rem;color:#fbbf24;font-weight:600;margin-bottom:4px">' + completedJob.findingsCount + '건 취약점 발견</div>';
                    }
                    cardHtml += '<div style="font-size:.54rem;color:#64748b">클릭하여 분석 결과 보기 →</div>';
                    cardHtml += '</div></a>';
                    cardEl.innerHTML = cardHtml;
                } else {
                    cardEl.innerHTML = '<p class="empty">아직 완료된 조사가 없습니다.</p>';
                }
            }
            if (!jobs.length) {
                el.innerHTML = '<p class="empty">실행 이력이 없습니다.</p>';
                return;
            }

            // 진행 중인 job이 있으면 버튼 비활성화 + 폴링 시작
            var activeJob = jobs.find(function(j) { return j.status === 'IN_PROGRESS'; });
            if (activeJob) {
                var btn = document.getElementById('btnRunPentest');
                if (btn) btn.disabled = true;
                var statusEl = document.getElementById('pentestStatus');
                if (statusEl) {
                    statusEl.className = 'pentest-status running';
                    statusEl.style.display = 'block';
                    statusEl.innerHTML = '<span class="loading"></span> 조사 진행 중...';
                }
                pollPentestJob(activeJob.jobId);
            }

            var html = '<table class="sec-table"><thead><tr><th>Job ID</th><th>상태</th><th>시작 시각</th><th>소요 시간</th><th>Findings</th></tr></thead><tbody>';
            jobs.forEach(function(j) {
                var statusCls = j.status === 'COMPLETED' ? 'fixed' : j.status === 'IN_PROGRESS' ? 'open' : 'open';
                var rowAction = j.status === 'COMPLETED' ? 'goToInsights(\'' + esc(j.jobId) + '\')' : 'loadJobDetail(\'' + esc(j.jobId) + '\')';
                var durationStr = '-';
                if (j.startedAt && j.completedAt && j.status === 'COMPLETED') {
                    var durSec = (new Date(j.completedAt).getTime() - new Date(j.startedAt).getTime()) / 1000;
                    if (durSec > 0) {
                        var durH = Math.floor(durSec / 3600);
                        var durM = Math.round((durSec % 3600) / 60);
                        durationStr = durH > 0 ? durH + 'h ' + durM + 'm' : durM + 'min';
                    }
                }
                html += '<tr style="cursor:pointer" onclick="' + rowAction + '">';
                html += '<td class="mono">' + esc((j.jobId || '').substring(0, 16)) + '...</td>';
                html += '<td><span class="status-badge ' + statusCls + '">' + esc(j.status) + '</span></td>';
                html += '<td class="mono">' + esc((j.startedAt || '').substring(0, 16)) + '</td>';
                html += '<td>' + durationStr + '</td>';
                html += '<td>' + (j.findingsCount !== undefined ? j.findingsCount + '건' : '-') + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table>';
            html += '<div id="pentestFindings"></div>';
            html += '<div id="pentestTimeline"></div>';
            el.innerHTML = html;

            // General tab info
            var cfg = data.config || {};
            var spaceEl = document.getElementById('genSpaceId');
            var pentestEl = document.getElementById('genPentestId');
            var intEl = document.getElementById('genIntegrationId');
            if (spaceEl) spaceEl.textContent = cfg.agent_space_id || '-';
            if (pentestEl) pentestEl.textContent = cfg.pentest_id || '-';
            if (intEl) intEl.textContent = cfg.integration_id || '-';
        })
        .catch(function(e) {
            el.innerHTML = '<p class="empty">이력 로딩 실패: ' + esc(String(e)) + '</p>';
        });
}

// ================================================================
// Job Detail — findings + timeline 로드
// ================================================================

function loadJobDetail(jobId) {
    var findingsEl = document.getElementById('pentestFindings');
    var timelineEl = document.getElementById('pentestTimeline');
    if (findingsEl) findingsEl.innerHTML = '<span class="loading"></span> 로딩...';
    if (timelineEl) timelineEl.innerHTML = '';

    fetch(_secUrl('/api/settings/security/pentest/job/' + encodeURIComponent(jobId)))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                if (findingsEl) findingsEl.innerHTML = '<p class="empty">' + esc(data.error || '') + '</p>';
                return;
            }
            var job = data.job || {};

            // Steps bar
            var statusEl = document.getElementById('pentestStatus');
            if (statusEl && job.steps && job.steps.length) {
                statusEl.style.display = 'block';
                statusEl.className = 'pentest-status ' + (job.status === 'COMPLETED' ? 'completed' : job.status === 'FAILED' ? 'failed' : 'running');
                var stepsHtml = renderStepsBar(job.steps);
                var errMsg = job.error ? ' — ' + esc(job.error.message) : '';
                statusEl.innerHTML = esc(job.status) + errMsg + stepsHtml;
            }

            // Findings
            if (data.findings && data.findings.length) {
                renderPentestFindings(data.findings);
            } else if (findingsEl) {
                findingsEl.innerHTML = job.status === 'COMPLETED' ? '<p class="empty" style="margin-top:12px">발견된 취약점 없음</p>' : '';
            }

            // Task timeline
            loadTaskTimeline(jobId);
        })
        .catch(function(e) {
            if (findingsEl) findingsEl.innerHTML = '<p class="empty">로딩 실패: ' + esc(String(e)) + '</p>';
        });
}

// ================================================================
// Steps Bar (PREFLIGHT → STATIC_ANALYSIS → PENTEST → FINALIZING)
// ================================================================

function renderStepsBar(steps) {
    if (!steps || !steps.length) return '';
    var html = '<div style="display:flex;gap:4px;margin-top:8px">';
    steps.forEach(function(s) {
        var bg = s.status === 'COMPLETED' ? '#4ade80' : s.status === 'IN_PROGRESS' ? '#38bdf8' : s.status === 'FAILED' ? '#f87171' : '#334155';
        var color = s.status === 'NOT_STARTED' ? '#64748b' : '#0f172a';
        html += '<div style="flex:1;text-align:center;padding:3px 6px;border-radius:4px;background:' + bg + ';color:' + color + ';font-size:.48rem;font-weight:600">' + esc(s.name) + '</div>';
    });
    html += '</div>';
    return html;
}

// ================================================================
// Run Pentest + Polling
// ================================================================

function runPentest() {
    var btn = document.getElementById('btnRunPentest');
    var statusEl = document.getElementById('pentestStatus');
    btn.disabled = true;

    fetch(_secUrl('/api/settings/security/pentest/run'), { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                if (statusEl) {
                    statusEl.className = 'pentest-status failed';
                    statusEl.style.display = 'block';
                    statusEl.textContent = '실행 실패: ' + (data.error || '');
                }
                btn.disabled = false;
                return;
            }
            SEC.pentestJobId = data.jobId;
            if (statusEl) {
                statusEl.className = 'pentest-status running';
                statusEl.style.display = 'block';
                statusEl.innerHTML = '<span class="loading"></span> 진행 중... (Job: ' + esc(data.jobId.substring(0, 12)) + '...)';
            }
            pollPentestJob(data.jobId);
        })
        .catch(function(e) {
            if (statusEl) {
                statusEl.className = 'pentest-status failed';
                statusEl.style.display = 'block';
                statusEl.textContent = '오류: ' + String(e);
            }
            btn.disabled = false;
        });
}

function pollPentestJob(jobId) {
    stopPollPentest();
    var elapsed = 0;

    SEC.pollTimer = setInterval(function() {
        elapsed += 10000;

        fetch(_secUrl('/api/settings/security/pentest/job/' + encodeURIComponent(jobId)))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) return;
                var statusEl = document.getElementById('pentestStatus');
                var job = data.job || {};

                var stepsHtml = renderStepsBar(job.steps || []);
                var elapsedMin = Math.floor(elapsed / 60000);
                var elapsedSec = Math.round((elapsed % 60000) / 1000);
                var elapsedStr = elapsedMin > 0 ? elapsedMin + '분 ' + elapsedSec + '초' : elapsedSec + '초';

                if (job.status === 'COMPLETED') {
                    stopPollPentest();
                    if (statusEl) {
                        statusEl.className = 'pentest-status completed';
                        statusEl.innerHTML = '완료 (' + esc((job.completedAt || '').substring(0, 19)) + ')' + stepsHtml;
                    }
                    var btn = document.getElementById('btnRunPentest');
                    if (btn) btn.disabled = false;
                    renderPentestFindings(data.findings || []);
                    loadTaskTimeline(jobId);
                } else if (job.status === 'FAILED' || job.status === 'CANCELLED') {
                    stopPollPentest();
                    if (statusEl) {
                        statusEl.className = 'pentest-status failed';
                        var errMsg = job.error ? job.error.message : job.status;
                        statusEl.innerHTML = '실패: ' + esc(errMsg) + stepsHtml;
                    }
                    var btn2 = document.getElementById('btnRunPentest');
                    if (btn2) btn2.disabled = false;
                    loadTaskTimeline(jobId);
                } else {
                    if (statusEl) {
                        statusEl.className = 'pentest-status running';
                        statusEl.style.display = 'block';
                        statusEl.innerHTML = '<span class="loading"></span> 진행 중... (' + elapsedStr + ')' + stepsHtml;
                    }
                    if (elapsed % 30000 === 0) loadTaskTimeline(jobId);
                }
            })
            .catch(function() {});
    }, 10000);
}

function stopPollPentest() {
    if (SEC.pollTimer) {
        clearInterval(SEC.pollTimer);
        SEC.pollTimer = null;
    }
}

// ================================================================
// Pentest Findings (결과 테이블 — 클릭 확장)
// ================================================================

function renderPentestFindings(findings) {
    var el = document.getElementById('pentestFindings');
    if (!el) return;
    if (!findings.length) {
        el.innerHTML = '<p class="empty" style="margin-top:12px;">발견된 취약점 없음</p>';
        return;
    }
    var html = '<h4 style="font-size:.7rem;color:#94a3b8;margin:14px 0 10px;font-weight:600;">Findings (발견 취약점) — ' + findings.length + '건</h4>';
    html += '<table class="sec-table"><thead><tr>';
    html += '<th>Risk Type</th><th>Confidence</th><th>Name (제목)</th><th>Status</th><th>Action</th>';
    html += '</tr></thead><tbody>';
    findings.forEach(function(f, idx) {
        var confCls = f.confidence === 'CONFIRMED' ? 'critical' : f.confidence === 'LIKELY' ? 'high' : f.confidence === 'POSSIBLE' ? 'medium' : 'low';
        var findingDetailId = 'finding-detail-' + idx;
        html += '<tr onclick="toggleTaskDetail(\'' + findingDetailId + '\')" style="cursor:pointer;">';
        html += '<td><span class="sev-badge medium">' + esc((f.riskType || '').replace(/_/g, ' ')) + '</span></td>';
        html += '<td><span class="sev-badge ' + confCls + '">' + esc(f.confidence || '-') + '</span></td>';
        html += '<td>' + esc(f.name || '-') + '</td>';
        html += '<td><span class="status-badge ' + (f.status === 'ACTIVE' ? 'open' : 'fixed') + '">' + esc(f.status) + '</span></td>';
        html += '<td><button class="remediate-btn" onclick="event.stopPropagation();startRemediation(\'' + esc(f.id) + '\')">Remediate</button></td>';
        html += '</tr>';
        html += '<tr id="' + findingDetailId + '" style="display:none;"><td colspan="5">';
        html += '<div class="task-detail" style="display:block;margin:0;">';
        html += '<div class="td-row"><span class="td-lb">Description</span><span class="td-vl">' + esc(f.description || '-') + '</span></div>';
        if (f.attackScript) {
            html += '<div class="td-row"><span class="td-lb">Attack Script</span><span class="td-vl" style="white-space:pre-wrap;font-family:\'SF Mono\',monospace;font-size:.52rem;">' + esc(f.attackScript) + '</span></div>';
        }
        if (f.prLink) {
            html += '<div class="td-row"><span class="td-lb">PR</span><span class="td-vl"><a href="' + esc(f.prLink) + '" target="_blank" class="pr-link">' + esc(f.prLink) + '</a></span></div>';
        }
        html += '</div></td></tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

// ================================================================
// Task Timeline (Gantt-style + 카테고리 컬러)
// ================================================================

var CATEGORY_COLORS = {
    'SETUP_INFRASTRUCTURE': '#64748b',
    'PREFLIGHT_VALIDATOR': '#06b6d4',
    'CODE_SCANNER': '#8b5cf6',
    'NETWORK_SCANNER': '#0ea5e9',
    'VALIDATOR': '#6366f1',
    'SQL_INJECTION': '#ef4444',
    'CROSS_SITE_SCRIPTING': '#f97316',
    'COMMAND_INJECTION': '#dc2626',
    'PATH_TRAVERSAL': '#f59e0b',
    'LOCAL_FILE_INCLUSION': '#eab308',
    'SERVER_SIDE_REQUEST_FORGERY': '#e11d48',
    'SERVER_SIDE_TEMPLATE_INJECTION': '#be185d',
    'INSECURE_DIRECT_OBJECT_REFERENCE': '#d946ef',
    'PRIVILEGE_ESCALATION': '#7c3aed',
    'CODE_INJECTION': '#b91c1c',
    'XML_EXTERNAL_ENTITY': '#ea580c',
    'JSON_WEB_TOKEN_VULNERABILITIES': '#0d9488',
    'ARBITRARY_FILE_UPLOAD': '#c2410c',
};

function loadTaskTimeline(jobId) {
    var el = document.getElementById('pentestTimeline');
    if (!el) return;
    el.innerHTML = '<span class="loading"></span> Loading tasks...';

    fetch(_secUrl('/api/settings/security/pentest/job/' + encodeURIComponent(jobId) + '/tasks'))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok || !data.tasks || !data.tasks.length) {
                el.innerHTML = data.tasks && !data.tasks.length ? '' : '<p class="empty">' + esc(data.error || '') + '</p>';
                return;
            }
            renderTaskTimeline(el, data.tasks);
        })
        .catch(function(e) {
            el.innerHTML = '<p class="empty">Tasks load failed: ' + esc(String(e)) + '</p>';
        });
}

function renderTaskTimeline(el, tasks) {
    var html = '<h4 style="font-size:.66rem;color:#94a3b8;margin:16px 0 10px;font-weight:600;">Task Timeline (실행 시나리오)</h4>';

    // Category summary cards
    var categories = {};
    var catOrder = [];
    tasks.forEach(function(t) {
        var cat = t.category || 'OTHER';
        if (!categories[cat]) { categories[cat] = { total: 0, completed: 0, inProgress: 0, failed: 0, aborted: 0 }; catOrder.push(cat); }
        categories[cat].total++;
        if (t.status === 'COMPLETED') categories[cat].completed++;
        else if (t.status === 'IN_PROGRESS') categories[cat].inProgress++;
        else if (t.status === 'FAILED') categories[cat].failed++;
        else if (t.status === 'ABORTED') categories[cat].aborted++;
    });

    html += '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">';
    catOrder.forEach(function(cat) {
        var c = categories[cat];
        var color = CATEGORY_COLORS[cat] || '#64748b';
        html += '<div style="background:' + color + '12;border:1px solid ' + color + '30;border-radius:6px;padding:5px 10px;">';
        html += '<div style="font-size:.54rem;color:' + color + ';font-weight:600;">' + esc(cat.replace(/_/g, ' ')) + '</div>';
        html += '<div style="font-size:.5rem;color:#94a3b8;margin-top:2px;">';
        html += c.completed + '/' + c.total + ' done';
        if (c.inProgress) html += ' · <span style="color:#7dd3fc;">' + c.inProgress + ' running</span>';
        if (c.failed) html += ' · <span style="color:#fca5a5;">' + c.failed + ' failed</span>';
        if (c.aborted) html += ' · ' + c.aborted + ' aborted';
        html += '</div></div>';
    });
    html += '</div>';

    // Gantt timeline
    var startTime = tasks.length ? new Date(tasks[0].createdAt).getTime() : 0;
    var endTime = startTime;
    tasks.forEach(function(t) {
        var end = new Date(t.updatedAt).getTime();
        if (end > endTime) endTime = end;
    });
    var totalDuration = endTime - startTime || 1;

    html += '<div class="task-timeline">';
    tasks.forEach(function(t, idx) {
        var tStart = new Date(t.createdAt).getTime();
        var tEnd = new Date(t.updatedAt).getTime();
        var left = ((tStart - startTime) / totalDuration) * 100;
        var width = Math.max(((tEnd - tStart) / totalDuration) * 100, 0.8);
        var color = CATEGORY_COLORS[t.category] || '#64748b';
        var statusIcon = t.status === 'COMPLETED' ? '✓' : t.status === 'IN_PROGRESS' ? '⟳' : t.status === 'FAILED' ? '✗' : t.status === 'ABORTED' ? '–' : '·';
        var opacity = t.status === 'COMPLETED' ? '1' : t.status === 'IN_PROGRESS' ? '0.8' : '0.3';
        var duration = Math.round((tEnd - tStart) / 1000);
        var taskId = 'task-detail-' + idx;

        html += '<div class="task-row" onclick="toggleTaskDetail(\'' + taskId + '\')" style="cursor:pointer;">';
        html += '<div class="task-label">';
        html += '<span style="color:' + color + ';">' + statusIcon + '</span> ';
        html += esc(t.title);
        html += '</div>';
        html += '<div class="task-bar-container">';
        html += '<div class="task-bar" style="left:' + left + '%;width:' + width + '%;background:' + color + ';opacity:' + opacity + ';"></div>';
        html += '</div>';
        html += '<div class="task-duration">' + (duration > 60 ? Math.round(duration/60) + 'm' : duration + 's') + '</div>';
        html += '</div>';

        // Hidden detail panel
        html += '<div id="' + taskId + '" class="task-detail" style="display:none;">';
        html += '<div class="td-row"><span class="td-lb">Description</span><span class="td-vl">' + esc(t.description || '-') + '</span></div>';
        if (t.riskType) html += '<div class="td-row"><span class="td-lb">Risk Type</span><span class="td-vl">' + esc(t.riskType) + '</span></div>';
        html += '<div class="td-row"><span class="td-lb">Endpoint</span><span class="td-vl mono">' + esc(t.endpoint || '-') + '</span></div>';
        html += '<div class="td-row"><span class="td-lb">Category</span><span class="td-vl">' + esc((t.category || '').replace(/_/g, ' ')) + '</span></div>';
        html += '<div class="td-row"><span class="td-lb">Duration</span><span class="td-vl">' + (duration > 60 ? Math.round(duration/60) + 'min ' + (duration%60) + 's' : duration + 's') + '</span></div>';
        html += '<div class="td-row"><span class="td-lb">Time</span><span class="td-vl mono">' + esc((t.createdAt||'').substring(11,19)) + ' → ' + esc((t.updatedAt||'').substring(11,19)) + '</span></div>';
        html += '</div>';
    });
    html += '</div>';

    // Total elapsed + Agent-minutes (parallel sum) + cost estimate
    var totalSec = Math.round(totalDuration / 1000);
    var totalMin = Math.floor(totalSec / 60);
    var agentSeconds = 0;
    var maxConcurrent = 0;
    tasks.forEach(function(t) {
        var tS = new Date(t.createdAt).getTime();
        var tE = new Date(t.updatedAt).getTime();
        agentSeconds += (tE - tS) / 1000;
        var concurrent = 0;
        tasks.forEach(function(o) {
            if (o.taskId === t.taskId) return;
            var oS = new Date(o.createdAt).getTime();
            var oE = new Date(o.updatedAt).getTime();
            if (oS < tE && oE > tS) concurrent++;
        });
        if (concurrent > maxConcurrent) maxConcurrent = concurrent;
    });
    var agentMin = Math.round(agentSeconds / 60);
    var agentHrs = (agentSeconds / 3600).toFixed(1);
    var costEstimate = (agentSeconds / 3600 * 50).toFixed(0);

    html += '<div style="margin-top:12px;padding:10px 14px;background:#1e293b;border:1px solid #334155;border-radius:8px;">';
    html += '<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;">';
    html += '<div><span style="font-size:.5rem;color:#64748b;">Wall-clock</span><div style="font-size:.7rem;color:#e2e8f0;font-weight:600;">' + totalMin + 'min ' + (totalSec%60) + 's</div></div>';
    html += '<div><span style="font-size:.5rem;color:#64748b;">Agent-minutes (병렬 합산)</span><div style="font-size:.7rem;color:#fbbf24;font-weight:600;">' + agentMin + ' min (' + agentHrs + ' hrs)</div></div>';
    html += '<div><span style="font-size:.5rem;color:#64748b;">예상 비용 ($50/hr)</span><div style="font-size:.7rem;color:#f87171;font-weight:600;">~$' + costEstimate + '</div></div>';
    html += '<div><span style="font-size:.5rem;color:#64748b;">최대 동시 실행</span><div style="font-size:.7rem;color:#7dd3fc;font-weight:600;">' + (maxConcurrent + 1) + ' tasks</div></div>';
    html += '<div><span style="font-size:.5rem;color:#64748b;">병렬 비율</span><div style="font-size:.7rem;color:#c4b5fd;font-weight:600;">' + (totalSec > 0 ? (agentSeconds / totalSec).toFixed(1) : '-') + 'x</div></div>';
    html += '</div></div>';

    el.innerHTML = html;
}

function toggleTaskDetail(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ================================================================
// Remediation
// ================================================================

function startRemediation(findingId) {
    if (!confirm('이 항목에 대한 코드 수정을 시작하시겠습니까?')) return;
    fetch(_secUrl('/api/settings/security/findings/' + encodeURIComponent(findingId) + '/remediate'), { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.ok) {
                alert('코드 수정 시작됨. GitHub PR이 자동 생성됩니다.');
            } else {
                alert('수정 시작 실패: ' + (data.error || ''));
            }
        })
        .catch(function(e) { alert('오류: ' + String(e)); });
}

// ================================================================
// Navigate to Insights
// ================================================================

function goToInsights(jobId) {
    var link = document.getElementById('secInsightsLink');
    var url = (link && link.href) ? link.href : '/security/insights';
    if (jobId) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'job_id=' + encodeURIComponent(jobId);
    window.location.href = url;
}

// ================================================================
// Init
// ================================================================

function settingsSecurityInit() {
    loadCodeReview();
    loadTargetDomain();
    loadPentestHistory();
    // Insights 캐시 프리웜
    var sil = document.getElementById("secInsightsLink");
    if (sil && sil.href) {
        var warmUrl = sil.href.replace('/security/insights', '/api/security/insights/enriched-findings');
        fetch(warmUrl).catch(function(){});
    }
}

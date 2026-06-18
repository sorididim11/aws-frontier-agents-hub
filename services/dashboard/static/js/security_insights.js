// ================================================================
// Security Insights — 서비스별 그룹핑 + Finding 드릴다운
// ================================================================

(function() {
    var _findings = [];
    var _stats = {};
    var _taskCounts = {};
    var _expandedFinding = null;

    var _spaceId = (typeof SPACE_ID !== 'undefined' && SPACE_ID) || '';
    var _secSpaceId = new URLSearchParams(location.search).get('sec_space_id') || '';
    var _jobId = new URLSearchParams(location.search).get('job_id') || '';
    var _devopsSpaceId = _spaceId;

    function _apiUrl(path) {
        var sep = path.indexOf('?') >= 0 ? '&' : '?';
        var url = path;
        if (_secSpaceId) { url += sep + 'sec_space_id=' + encodeURIComponent(_secSpaceId); sep = '&'; }
        else if (_spaceId) { url += sep + 'space_id=' + encodeURIComponent(_spaceId); sep = '&'; }
        if (_jobId) { url += sep + 'job_id=' + encodeURIComponent(_jobId); }
        return url;
    }

    window.securityInsightsInit = function() {
        _loadSpaceSelector();
        _loadData();
    };

    function _loadSpaceSelector() {
        var lsUrl = '/api/security/insights/linked-spaces';
        if (_secSpaceId) lsUrl += '?sec_space_id=' + encodeURIComponent(_secSpaceId);
        else if (_spaceId) lsUrl += '?space_id=' + encodeURIComponent(_spaceId);
        fetch(lsUrl)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.devops_space_id) _devopsSpaceId = data.devops_space_id;
                if (!data.ok || !data.spaces || data.spaces.length < 2) return;
                var spEl = document.getElementById('spaceInfo');
                if (!spEl) return;
                var html = '<select id="secSpaceSelect" style="font-size:.56rem;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:4px;padding:2px 6px;cursor:pointer">';
                data.spaces.forEach(function(s) {
                    var selected = (_secSpaceId === s.security_space_id) ? ' selected' : '';
                    if (!_secSpaceId && !selected && data.spaces.indexOf(s) === 0) selected = ' selected';
                    var label = s.target_domain || s.name || s.security_space_id;
                    html += '<option value="' + _esc(s.security_space_id) + '"' + selected + '>' + _esc(label) + '</option>';
                });
                html += '</select>';
                spEl.innerHTML = html;
                document.getElementById('secSpaceSelect').onchange = function() {
                    var val = this.value;
                    var params = new URLSearchParams(location.search);
                    params.set('sec_space_id', val);
                    location.search = params.toString();
                };
            })
            .catch(function() {});
    }

    function _loadData() {
        var el = document.getElementById('insightsContent');
        if (!el) return;
        el.innerHTML = '<div style="text-align:center;padding:40px"><span class="loading"></span> 분석 중...</div>';

        fetch(_apiUrl('/api/security/insights/enriched-findings'))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    el.innerHTML = '<div class="ins-empty">Error: ' + _esc(data.error || '') + '</div>';
                    return;
                }
                _findings = data.findings || [];
                _stats = data.stats || {};
                _taskCounts = data.task_counts || {};

                // space 정보 표시
                var si = data.space_info || {};
                var spEl = document.getElementById('spaceInfo');
                if (spEl && (si.target_domain || si.name)) {
                    var info = '';
                    if (si.target_domain) info += '<span style="color:#38bdf8;font-weight:600">' + _esc(si.target_domain) + '</span>';
                    if (si.name) info += ' <span style="color:#475569">(' + _esc(si.name) + ')</span>';
                    spEl.innerHTML = info;
                }

                if (!_findings.length && data.pentest_status && data.pentest_status.status === 'IN_PROGRESS') {
                    _renderInProgress(el, data.pentest_status);
                    return;
                }
                window._pentestStatus = data.pentest_status || null;
                _render(el);
            })
            .catch(function(e) {
                el.innerHTML = '<div class="ins-empty">Error: ' + _esc(String(e)) + '</div>';
            });
    }

    // ================================================================
    // IN_PROGRESS — Task Timeline 렌더링
    // ================================================================
    function _renderInProgress(el, status) {
        var tasks = status.tasks || [];
        var completed = tasks.filter(function(t) { return t.status === 'COMPLETED'; }).length;
        var inProgress = tasks.filter(function(t) { return t.status === 'IN_PROGRESS'; }).length;
        var failed = tasks.filter(function(t) { return t.status === 'FAILED'; }).length;
        var total = tasks.length;
        var pct = total > 0 ? Math.round((completed / total) * 100) : 0;

        var html = '<div style="padding:20px">';

        // 헤더
        html += '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">';
        html += '<span class="loading"></span>';
        html += '<span style="font-size:.72rem;color:#e2e8f0;font-weight:700">보안 조사 진행 중</span>';
        if (status.started_at) {
            html += '<span style="font-size:.52rem;color:#64748b;margin-left:auto">' + _esc(status.started_at.substring(0, 16)) + '</span>';
        }
        html += '</div>';

        // 진행률 요약
        html += '<div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">';
        html += '<span style="font-size:.6rem;color:#4ade80;font-weight:600">' + completed + ' 완료</span>';
        if (inProgress) html += '<span style="font-size:.6rem;color:#38bdf8;font-weight:600">' + inProgress + ' 진행 중</span>';
        if (failed) html += '<span style="font-size:.6rem;color:#fca5a5;font-weight:600">' + failed + ' 실패</span>';
        html += '<span style="font-size:.56rem;color:#64748b">' + total + ' 전체</span>';
        html += '<span style="font-size:.56rem;color:#94a3b8;margin-left:auto;font-weight:600">' + pct + '%</span>';
        html += '</div>';

        // 프로그레스 바
        html += '<div style="height:8px;background:#1e293b;border-radius:4px;margin-bottom:20px;overflow:hidden">';
        html += '<div style="height:100%;width:' + pct + '%;background:linear-gradient(90deg,#4ade80,#38bdf8);border-radius:4px;transition:width .3s"></div>';
        html += '</div>';

        // Task timeline
        html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;overflow:hidden">';
        html += '<div style="padding:12px 16px;border-bottom:1px solid #334155;font-size:.62rem;color:#94a3b8;font-weight:600">Task Timeline</div>';

        for (var i = 0; i < tasks.length; i++) {
            var t = tasks[i];
            var icon, color;
            if (t.status === 'COMPLETED') { icon = '&#10003;'; color = '#4ade80'; }
            else if (t.status === 'IN_PROGRESS') { icon = '&#9654;'; color = '#38bdf8'; }
            else if (t.status === 'FAILED') { icon = '&#10007;'; color = '#fca5a5'; }
            else { icon = '&#9679;'; color = '#64748b'; }

            html += '<div style="display:flex;align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid #0f172a">';
            html += '<span style="color:' + color + ';font-size:.64rem;width:18px;text-align:center">' + icon + '</span>';
            html += '<span style="font-size:.58rem;color:#e2e8f0;flex:1">' + _esc(t.title || t.riskType) + '</span>';
            if (t.endpoint) html += '<span style="font-size:.48rem;color:#64748b;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _esc(t.endpoint) + '</span>';
            html += '<span style="font-size:.48rem;padding:2px 6px;border-radius:3px;background:' + (t.status === 'COMPLETED' ? '#14532d' : t.status === 'IN_PROGRESS' ? '#0c4a6e' : '#1e293b') + ';color:' + color + '">' + _esc(t.status) + '</span>';
            html += '</div>';
        }

        if (!tasks.length) {
            html += '<div style="padding:16px;text-align:center;font-size:.56rem;color:#64748b">태스크 대기 중...</div>';
        }

        html += '</div>';

        // 자동 새로고침 안내
        html += '<div style="text-align:center;margin-top:16px;font-size:.52rem;color:#475569">30초 후 자동 새로고침</div>';
        html += '</div>';

        el.innerHTML = html;

        // 30초 후 자동 새로고침
        setTimeout(function() { _loadData(); }, 30000);
    }

    // ================================================================
    // Render: 서비스별 그룹핑 (Level 2)
    // ================================================================
    function _render(el) {
        var html = '';

        // IN_PROGRESS 배너 (findings가 있어도 새 조사 진행 중이면 표시)
        if (window._pentestStatus && window._pentestStatus.status === 'IN_PROGRESS') {
            var ps = window._pentestStatus;
            var pTasks = ps.tasks || [];
            var pDone = pTasks.filter(function(t) { return t.status === 'COMPLETED'; }).length;
            var pTotal = pTasks.length;
            var pPct = pTotal > 0 ? Math.round((pDone / pTotal) * 100) : 0;
            html += '<div style="margin:12px 20px;padding:12px 16px;background:#0c4a6e;border:1px solid #0ea5e9;border-radius:8px;display:flex;align-items:center;gap:12px">';
            html += '<span class="loading"></span>';
            html += '<span style="font-size:.6rem;color:#e0f2fe;font-weight:600">새 조사 진행 중</span>';
            html += '<div style="flex:1;height:6px;background:#1e3a5f;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + pPct + '%;background:#38bdf8;border-radius:3px"></div></div>';
            html += '<span style="font-size:.54rem;color:#7dd3fc">' + pDone + '/' + pTotal + ' (' + pPct + '%)</span>';
            html += '</div>';
            setTimeout(function() { _loadData(); }, 30000);
        }

        // 요약 카드
        html += '<div class="ins-stats">';
        html += _statCard('전체', _stats.total || 0, '#94a3b8');
        html += _statCard('위험도 하향', _stats.risk_reduced || 0, '#4ade80');
        html += _statCard('수정 완료', _stats.remediated || 0, '#38bdf8');
        html += _statCard('CRITICAL', _stats.critical || 0, '#ef4444');
        html += _statCard('HIGH', _stats.high || 0, '#f97316');
        html += _statCard('MEDIUM', _stats.medium || 0, '#fbbf24');
        html += _statCard('LOW', _stats.low || 0, '#4ade80');
        html += '</div>';

        // Pentest 비용 요약은 Task Timeline 내부에서 렌더링

        // 토폴로지 오버레이 다이어그램 (비동기 로드)
        html += '<div id="topoOverlayContainer" style="padding:16px 20px 8px">';
        html += '<div style="font-size:.62rem;color:#64748b;font-weight:600;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px">아키텍처 보안 오버레이</div>';
        html += '<div style="text-align:center;padding:20px;color:#475569;font-size:.55rem"><span class="loading"></span> 토폴로지 로딩...</div>';
        html += '</div>';

        // Endpoint Attack Graph (토폴로지 오버레이 하위에 통합)
        html += '<div id="endpointAttackGraph" style="padding:0 20px 12px"></div>';

        // 서비스별 그룹핑
        var groups = _groupByService(_findings);
        var svcNames = Object.keys(groups).sort(function(a, b) {
            if (a === 'unknown') return 1;
            if (b === 'unknown') return -1;
            return a.localeCompare(b);
        });

        html += '<div style="padding:0 20px 20px">';
        for (var s = 0; s < svcNames.length; s++) {
            var svc = svcNames[s];
            var findings = groups[svc];
            html += _renderServiceGroup(svc, findings);
        }
        html += '</div>';

        // Task Phase Timeline 섹션
        html += '<div id="taskPhaseTimeline" style="padding:0 20px 24px"><span class="loading"></span> Task Phase Timeline 로딩...</div>';

        el.innerHTML = html;

        // 비동기 로드
        _loadTopoOverlay();
        _loadEndpointAttackGraph();
        _loadTaskPhaseTimeline();
    }

    function _loadTopoOverlay() {
        var container = document.getElementById('topoOverlayContainer');
        if (!container) return;

        fetch(_apiUrl('/api/security/insights/topology-overlay'))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok || !(data.nodes || []).length) {
                    container.style.display = 'none';
                    return;
                }
                var headerHtml = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
                headerHtml += '<span style="font-size:.62rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.5px;flex:1">아키텍처 보안 오버레이</span>';
                headerHtml += '<button onclick="_topoZoom(-1)" style="width:22px;height:22px;border:1px solid #475569;background:#1e293b;color:#e2e8f0;border-radius:4px;cursor:pointer;font-size:.6rem;line-height:1">−</button>';
                headerHtml += '<button onclick="_topoZoom(0)" style="height:22px;border:1px solid #475569;background:#1e293b;color:#94a3b8;border-radius:4px;cursor:pointer;font-size:.48rem;padding:0 6px">Fit</button>';
                headerHtml += '<button onclick="_topoZoom(1)" style="width:22px;height:22px;border:1px solid #475569;background:#1e293b;color:#e2e8f0;border-radius:4px;cursor:pointer;font-size:.6rem;line-height:1">+</button>';
                headerHtml += '</div>';

                var svgHtml = _renderTopoOverlay(data);
                var wrapHtml = '<div id="topoSvgWrap" style="overflow:auto;border:1px solid #1e293b;border-radius:8px">';
                wrapHtml += '<div id="topoSvgInner" style="transform-origin:top left;transition:transform .2s">' + svgHtml + '</div>';
                wrapHtml += '</div>';

                container.innerHTML = headerHtml + wrapHtml;
            })
            .catch(function() {
                container.style.display = 'none';
            });
    }

    var _topoScale = 1;
    window._topoZoom = function(dir) {
        var inner = document.getElementById('topoSvgInner');
        if (!inner) return;
        if (dir === 0) { _topoScale = 1; }
        else if (dir > 0) { _topoScale = Math.min(3, _topoScale + 0.25); }
        else { _topoScale = Math.max(0.5, _topoScale - 0.25); }
        inner.style.transform = 'scale(' + _topoScale + ')';
    };

    // ================================================================
    // Task Timeline (Gantt-style, 카테고리 컬러)
    // ================================================================
    var _CAT_COLORS = {
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


    // ================================================================
    // Endpoint Attack Graph
    // ================================================================
    var _RISK_COLORS = {CRITICAL:'#ef4444', HIGH:'#f97316', MEDIUM:'#fbbf24', LOW:'#4ade80', INFO:'#64748b', UNKNOWN:'#64748b'};

    function _loadEndpointAttackGraph() {
        var el = document.getElementById('endpointAttackGraph');
        if (!el) return;
        fetch(_apiUrl('/api/security/insights/endpoint-attack-graph'))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok || (!(data.nodes||[]).length && !(data.chains||[]).length)) {
                    el.innerHTML = '';
                    return;
                }
                _renderEndpointAttackGraph(el, data);
            })
            .catch(function() { el.innerHTML = ''; });
    }

    function _renderEndpointAttackGraph(el, data) {
        var nodes = data.nodes || [];
        var chains = data.chains || [];
        var ro = {CRITICAL:4,HIGH:3,MEDIUM:2,LOW:1,INFO:0,UNKNOWN:0};

        var html = '<div style="border:1px solid #334155;border-radius:10px;overflow:hidden;background:#1e293b">';
        html += '<div style="padding:12px 16px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:12px">';
        html += '<span style="font-size:.66rem;color:#e2e8f0;font-weight:600">Endpoint Attack Graph</span>';
        html += '<span style="font-size:.52rem;color:#94a3b8">' + nodes.length + ' endpoints · ' + chains.length + ' chains</span>';
        html += '</div>';

        // Endpoint nodes (compact list with risk types)
        nodes.sort(function(a,b) { return (ro[b.max_risk]||0)-(ro[a.max_risk]||0); });
        html += '<div style="padding:8px 12px">';
        for (var ni = 0; ni < Math.min(nodes.length, 15); ni++) {
            var node = nodes[ni];
            var nc = _RISK_COLORS[node.max_risk] || '#64748b';
            var nodeId = 'ep-node-' + ni;

            html += '<div style="background:#0f172a;border:1px solid ' + nc + '20;border-radius:5px;margin-bottom:3px;overflow:hidden">';
            html += '<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;cursor:pointer" onclick="var d=document.getElementById(\'' + nodeId + '\');d.style.display=d.style.display===\'none\'?\'block\':\'none\'">';
            html += '<span style="width:6px;height:6px;border-radius:50%;background:' + nc + '"></span>';
            html += '<span style="font-size:.5rem;color:#e2e8f0;font-family:\'SF Mono\',monospace;flex:1">' + _esc(node.path) + '</span>';

            // Attack type badges inline
            var riskTypes = {};
            for (var fi = 0; fi < node.findings.length; fi++) {
                var rt = node.findings[fi].riskType || '';
                var rl = node.findings[fi].riskLevel || 'INFO';
                if (rt && !riskTypes[rt]) riskTypes[rt] = rl;
            }
            var keys = Object.keys(riskTypes);
            for (var ki = 0; ki < Math.min(keys.length, 3); ki++) {
                var rtLabel = keys[ki].replace(/_/g,' ').substring(0,14);
                var rtColor = _RISK_COLORS[riskTypes[keys[ki]]] || '#64748b';
                html += '<span style="font-size:.4rem;padding:1px 5px;border-radius:3px;background:' + rtColor + '15;color:' + rtColor + ';border:1px solid ' + rtColor + '30">' + _esc(rtLabel) + '</span>';
            }
            html += '<span style="font-size:.42rem;color:' + nc + ';font-weight:600">' + node.findings.length + '</span>';
            html += '</div>';

            // Expandable detail
            html += '<div id="' + nodeId + '" style="display:none;padding:3px 10px 6px 20px;border-top:1px solid #1e293b">';
            for (var fi2 = 0; fi2 < node.findings.length; fi2++) {
                var nf = node.findings[fi2];
                var fc = _RISK_COLORS[nf.riskLevel] || '#64748b';
                html += '<div style="display:flex;align-items:center;gap:5px;padding:2px 0">';
                html += '<span style="font-size:.38rem;color:#fff;background:' + fc + ';padding:1px 4px;border-radius:2px">' + _esc(nf.riskLevel || 'INFO') + '</span>';
                html += '<span style="font-size:.42rem;color:#94a3b8">' + _esc((nf.riskType||'').replace(/_/g,' ').substring(0,16)) + '</span>';
                html += '<span style="font-size:.44rem;color:#cbd5e1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _esc(nf.name) + '</span>';
                html += '</div>';
            }
            html += '</div></div>';
        }
        html += '</div>';

        // Chain attacks — compact clickable list (상세는 별도 페이지)
        if (chains.length > 0) {
            html += '<div style="padding:4px 12px 12px;border-top:1px solid #334155">';
            html += '<div style="font-size:.52rem;color:#ef4444;font-weight:600;padding:8px 0 4px;text-transform:uppercase;letter-spacing:.3px">Chain Attacks (' + chains.length + ')</div>';
            for (var ci = 0; ci < chains.length; ci++) {
                var chain = chains[ci];
                var cc = _RISK_COLORS[chain.riskLevel] || '#ef4444';
                var chainUrl = '/security/insights/chain/' + encodeURIComponent(chain.id) + (_secSpaceId ? '?sec_space_id=' + encodeURIComponent(_secSpaceId) : (_spaceId ? '?space_id=' + encodeURIComponent(_spaceId) : ''));
                var steps = chain.steps || [];

                html += '<a href="' + chainUrl + '" style="display:block;text-decoration:none;background:#0f172a;border:1px solid ' + cc + '30;border-radius:6px;padding:8px 10px;margin-bottom:4px;transition:border-color .15s"';
                html += ' onmouseover="this.style.borderColor=\'' + cc + '70\'" onmouseout="this.style.borderColor=\'' + cc + '30\'">';
                html += '<div style="display:flex;align-items:center;gap:8px">';
                html += '<span style="font-size:.42rem;color:#fff;background:' + cc + ';padding:1px 5px;border-radius:3px;font-weight:600">' + _esc(chain.riskLevel) + '</span>';
                html += '<span style="font-size:.48rem;color:#e2e8f0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _esc(chain.name) + '</span>';
                html += '<span style="font-size:.42rem;color:#94a3b8">' + steps.length + ' steps</span>';
                if (chain.escalation && chain.escalation.escalated) {
                    html += '<span style="font-size:.4rem;color:#fbbf24;font-weight:600">ESCALATION</span>';
                }
                html += '<span style="font-size:.46rem;color:' + cc + '">▸</span>';
                html += '</div>';

                // Step summary (one-line)
                if (steps.length > 0) {
                    var stepSum = steps.map(function(s) { return s.step + '.' + (s.action||s.method).substring(0,18); }).join(' → ');
                    html += '<div style="font-size:.42rem;color:#fbbf2480;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _esc(stepSum) + '</div>';
                }
                html += '</a>';
            }
            html += '</div>';
        }

        html += '</div>';
        el.innerHTML = html;
    }

    // ================================================================
    // Task Phase Timeline (stepName 기반)
    // ================================================================
    var _PHASE_COLORS = {
        PREFLIGHT: '#06b6d4',
        STATIC_ANALYSIS: '#8b5cf6',
        PENTEST: '#ef4444',
        FINALIZING: '#64748b'
    };
    var _EXEC_TYPE_COLORS = {
        PLAN_GENERATION: '#3b82f6',
        GUIDED_EXECUTION: '#f97316',
        MANAGED_VALIDATION: '#f59e0b',
        VALIDATION: '#6366f1',
        CHAIN_ATTACK: '#ef4444'
    };

    function _renderTaskRow(t, idSuffix, phaseColor, startTime, totalDur) {
        var tStart = new Date(t.createdAt).getTime();
        var tEnd = new Date(t.updatedAt).getTime();
        var left = ((tStart - startTime) / totalDur) * 100;
        var width = Math.max(((tEnd - tStart) / totalDur) * 100, 0.5);
        var durSec = Math.round((tEnd - tStart) / 1000);
        var durStr = durSec >= 60 ? Math.floor(durSec / 60) + 'm' + (durSec % 60) + 's' : durSec + 's';

        var execColor = _EXEC_TYPE_COLORS[t.execution_type] || phaseColor;
        var barColor = t.execution_type ? execColor : phaseColor;
        var isChain = t.execution_type === 'CHAIN_ATTACK';
        var statusIcon = t.status === 'COMPLETED' ? '✓' : t.status === 'FAILED' ? '✗' : '·';
        var statusColor = t.status === 'COMPLETED' ? '#4ade80' : t.status === 'FAILED' ? '#fca5a5' : '#64748b';
        var catColor = _CAT_COLORS[t.primary_category] || '#94a3b8';
        var taskDetailId = 'tpt-detail-' + idSuffix;

        var h = '';
        h += '<div style="cursor:pointer" onclick="var d=document.getElementById(\'' + taskDetailId + '\');d.style.display=d.style.display===\'none\'?\'block\':\'none\'">';
        h += '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid #0f172a40" onmouseenter="this.style.background=\'#263548\'" onmouseleave="this.style.background=\'transparent\'">';
        h += '<span style="font-size:.5rem;color:' + statusColor + ';width:12px;text-align:center">' + statusIcon + '</span>';
        var titleColor = isChain ? '#fbbf24' : '#cbd5e1';
        h += '<span style="font-size:.48rem;color:' + titleColor + ';min-width:150px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:' + (isChain ? '700' : '400') + '">' + _esc(t.title) + '</span>';
        h += '<div style="flex:1;height:5px;background:#0f172a;border-radius:3px;position:relative;overflow:hidden">';
        h += '<div style="position:absolute;top:0;height:100%;left:' + left + '%;width:' + width + '%;background:' + barColor + ';border-radius:3px;opacity:' + (t.status === 'COMPLETED' ? '1' : '0.4') + '"></div>';
        h += '</div>';
        if (t.execution_type) {
            var badge = {PLAN_GENERATION:'Plan',GUIDED_EXECUTION:'Exec',MANAGED_VALIDATION:'Managed',VALIDATION:'Valid',CHAIN_ATTACK:'Chain Attack'}[t.execution_type] || '';
            h += '<span style="font-size:.38rem;color:' + execColor + ';background:' + execColor + '15;border:1px solid ' + execColor + '40;padding:1px 4px;border-radius:3px;min-width:30px;text-align:center">' + badge + '</span>';
        } else {
            h += '<span style="min-width:30px"></span>';
        }
        h += '<span style="font-size:.4rem;color:#475569;min-width:30px;text-align:right;font-family:monospace">' + durStr + '</span>';
        h += '</div>';
        h += '<div id="' + taskDetailId + '" style="display:none;padding:6px 10px 8px 22px;background:#0f172a;border:1px solid #334155;border-top:none;border-radius:0 0 5px 5px;margin-bottom:3px">';
        if (t.riskType) h += '<div style="display:flex;gap:8px;padding:2px 0"><span style="font-size:.5rem;color:#64748b;min-width:70px">Risk Type</span><span style="font-size:.5rem;color:#cbd5e1">' + _esc(t.riskType) + '</span></div>';
        if (t.primary_category) h += '<div style="display:flex;gap:8px;padding:2px 0"><span style="font-size:.5rem;color:#64748b;min-width:70px">Category</span><span style="font-size:.5rem;color:' + catColor + '">' + _esc(t.primary_category.replace(/_/g,' ')) + '</span></div>';
        if (t.secondary_category) h += '<div style="display:flex;gap:8px;padding:2px 0"><span style="font-size:.5rem;color:#64748b;min-width:70px">Sub-category</span><span style="font-size:.5rem;color:#94a3b8">' + _esc(t.secondary_category.replace(/_/g,' ')) + '</span></div>';
        if (t.description) h += '<div style="display:flex;gap:8px;padding:2px 0"><span style="font-size:.5rem;color:#64748b;min-width:70px">Description</span><span style="font-size:.48rem;color:#94a3b8;word-break:break-word">' + _esc(t.description) + '</span></div>';
        h += '<div style="display:flex;gap:8px;padding:2px 0"><span style="font-size:.5rem;color:#64748b;min-width:70px">Time</span><span style="font-size:.5rem;color:#94a3b8;font-family:monospace">' + _esc((t.createdAt||'').substring(11,19)) + ' → ' + _esc((t.updatedAt||'').substring(11,19)) + '</span></div>';
        h += '</div>';
        h += '</div>';
        return h;
    }

    function _loadTaskPhaseTimeline() {
        var el = document.getElementById('taskPhaseTimeline');
        if (!el) return;
        fetch(_apiUrl('/api/security/insights/task-timeline'))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok || !(data.tasks||[]).length) {
                    el.innerHTML = '';
                    return;
                }
                _renderTaskPhaseTimeline(el, data);
            })
            .catch(function() { el.innerHTML = ''; });
    }

    function _renderTaskPhaseTimeline(el, data) {
        var tasks = data.tasks || [];

        var completedCount = tasks.filter(function(x) { return x.status === 'COMPLETED'; }).length;
        var failedCount = tasks.filter(function(x) { return x.status === 'FAILED'; }).length;

        var html = '<div style="border:1px solid #334155;border-radius:10px;overflow:hidden;background:#1e293b">';
        html += '<div style="padding:12px 16px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:12px">';
        html += '<span style="font-size:.66rem;color:#e2e8f0;font-weight:600">조사 타임라인</span>';
        html += '<span style="font-size:.52rem;color:#4ade80">' + completedCount + ' completed</span>';
        if (failedCount) html += '<span style="font-size:.52rem;color:#fca5a5">' + failedCount + ' failed</span>';
        html += '<span style="font-size:.52rem;color:#64748b;margin-left:auto">' + tasks.length + ' total</span>';
        html += '</div>';

        // Architecture phases (AWS Security Agent multi-agent flow)
        // Flow: Baseline → Managed → Guided(Plan→Exec) → Chain → Validation
        var archPhaseOrder = ['AUTH', 'BASELINE', 'MANAGED', 'GUIDED', 'CHAIN', 'VALIDATION', 'REPORT'];
        var archPhaseLabels = {AUTH:'Preflight', BASELINE:'Baseline Scanning', MANAGED:'Managed Execution', GUIDED:'Guided Exploration', CHAIN:'Chain Attack', VALIDATION:'Validation', REPORT:'Report'};
        var archPhaseColors = {AUTH:'#06b6d4', BASELINE:'#8b5cf6', MANAGED:'#f59e0b', GUIDED:'#f97316', CHAIN:'#ef4444', VALIDATION:'#6366f1', REPORT:'#64748b'};

        // Classify tasks into architecture phases (blog: Auth → Baseline → Managed+Guided concurrent → Validation → Report)
        function _classifyPhase(t) {
            var step = t.stepName || '';
            var etype = t.execution_type || '';
            if (step === 'PREFLIGHT') return 'AUTH';
            if (step === 'FINALIZING') return 'REPORT';
            if (step === 'STATIC_ANALYSIS') return 'BASELINE';
            if (etype === 'PLAN_GENERATION') return 'MANAGED';
            if (etype === 'GUIDED_EXECUTION') return 'GUIDED';
            if (etype === 'CHAIN_ATTACK') return 'CHAIN';
            if (etype === 'VALIDATION') return 'VALIDATION';
            return 'GUIDED';
        }

        var archGroups = {};
        for (var api = 0; api < archPhaseOrder.length; api++) archGroups[archPhaseOrder[api]] = [];
        tasks.forEach(function(t) { archGroups[_classifyPhase(t)].push(t); });

        // Phase summary bar
        var totalTasks = tasks.length || 1;
        html += '<div style="display:flex;height:24px;margin:12px 16px;border-radius:6px;overflow:hidden;border:1px solid #334155">';
        for (var pi = 0; pi < archPhaseOrder.length; pi++) {
            var pName = archPhaseOrder[pi];
            var pCount = archGroups[pName].length;
            var pPct = (pCount / totalTasks) * 100;
            if (pPct < 1) continue;
            var pColor = archPhaseColors[pName] || '#64748b';
            html += '<div style="width:' + pPct + '%;background:' + pColor + '20;display:flex;align-items:center;justify-content:center;border-right:1px solid #334155" title="' + archPhaseLabels[pName] + ': ' + pCount + '">';
            if (pPct > 6) html += '<span style="font-size:.4rem;color:' + pColor + ';font-weight:600">' + archPhaseLabels[pName].split(' ')[0] + ' ' + pCount + '</span>';
            html += '</div>';
        }
        html += '</div>';

        // Architecture flow indicator
        html += '<div style="display:flex;align-items:center;gap:4px;padding:4px 16px 8px;flex-wrap:wrap">';
        for (var fi = 0; fi < archPhaseOrder.length; fi++) {
            var fName = archPhaseOrder[fi];
            if (!archGroups[fName] || !archGroups[fName].length) continue;
            var fColor = archPhaseColors[fName] || '#64748b';
            if (fi > 0) html += '<span style="font-size:.42rem;color:#475569">→</span>';
            html += '<span style="font-size:.4rem;color:' + fColor + ';font-weight:600;background:' + fColor + '10;border:1px solid ' + fColor + '30;padding:1px 5px;border-radius:3px">' + archPhaseLabels[fName] + ' ' + archGroups[fName].length + '</span>';
        }
        html += '</div>';

        // Category summary badges (attack categories only)
        var categories = {};
        var catOrder = [];
        tasks.forEach(function(t) {
            var phase = _classifyPhase(t);
            if (phase !== 'MANAGED' && phase !== 'GUIDED' && phase !== 'CHAIN') return;
            var cat = t.primary_category || 'OTHER';
            if (!categories[cat]) { categories[cat] = {total:0, completed:0}; catOrder.push(cat); }
            categories[cat].total++;
            if (t.status === 'COMPLETED') categories[cat].completed++;
        });
        if (catOrder.length > 1) {
            html += '<div style="display:flex;gap:5px;flex-wrap:wrap;padding:6px 16px 10px;border-bottom:1px solid #334155">';
            for (var ci = 0; ci < catOrder.length; ci++) {
                var cat = catOrder[ci];
                var c = categories[cat];
                var catCol = _CAT_COLORS[cat] || '#64748b';
                html += '<div style="background:' + catCol + '10;border:1px solid ' + catCol + '25;border-radius:4px;padding:3px 6px">';
                html += '<span style="font-size:.4rem;color:' + catCol + ';font-weight:600">' + _esc(cat.replace(/_/g,' ')) + '</span>';
                html += ' <span style="font-size:.38rem;color:#94a3b8">' + c.completed + '/' + c.total + '</span>';
                html += '</div>';
            }
            html += '</div>';
        }

        // Global time range (for gantt positioning across all phases)
        var startTime = tasks.length ? new Date(tasks[0].createdAt).getTime() : 0;
        var endTime = startTime;
        tasks.forEach(function(t) {
            var e = new Date(t.updatedAt).getTime();
            if (e > endTime) endTime = e;
        });
        var totalDur = endTime - startTime || 1;

        // Render architecture phase groups
        html += '<div style="max-height:600px;overflow-y:auto">';
        for (var gi = 0; gi < archPhaseOrder.length; gi++) {
            var gName = archPhaseOrder[gi];
            var gTasks = archGroups[gName];
            if (!gTasks.length) continue;

            var gColor = archPhaseColors[gName] || '#64748b';
            var gLabel = archPhaseLabels[gName] || gName;

            // Phase duration
            var gStart = new Date(gTasks[0].createdAt).getTime();
            var gEnd = gStart;
            for (var gti = 0; gti < gTasks.length; gti++) {
                var ge = new Date(gTasks[gti].updatedAt).getTime();
                if (ge > gEnd) gEnd = ge;
            }
            var gDurSec = Math.round((gEnd - gStart) / 1000);
            var gDurStr = gDurSec >= 60 ? Math.floor(gDurSec / 60) + '분 ' + (gDurSec % 60) + '초' : gDurSec + '초';

            var gCompleted = gTasks.filter(function(x) { return x.status === 'COMPLETED'; }).length;

            // Phase header (collapsible)
            html += '<div class="tpt-phase" data-phase="' + gName + '">';
            html += '<div onclick="window._togglePhase(\'' + gName + '\')" style="padding:8px 16px;background:' + gColor + '08;border-top:1px solid #334155;cursor:pointer;display:flex;align-items:center;gap:8px" onmouseenter="this.style.background=\'' + gColor + '15\'" onmouseleave="this.style.background=\'' + gColor + '08\'">';
            html += '<span id="tpt-arrow-' + gName + '" style="font-size:.5rem;color:' + gColor + ';transition:transform .2s">▼</span>';
            html += '<span style="width:8px;height:8px;border-radius:50%;background:' + gColor + '"></span>';
            html += '<span style="font-size:.58rem;color:' + gColor + ';font-weight:700">' + gLabel + '</span>';
            html += '<span style="font-size:.46rem;color:#64748b">' + gCompleted + '/' + gTasks.length + '</span>';
            html += '<span style="font-size:.44rem;color:#475569;margin-left:auto">' + gDurStr + '</span>';
            html += '</div>';

            // Phase tasks
            html += '<div id="tpt-tasks-' + gName + '" style="padding:4px 12px 8px">';

            if ((gName === 'MANAGED' || gName === 'GUIDED') && gTasks.length > 5) {
                // Managed Execution: category 서브그룹핑
                var catGroups = {};
                var catGroupOrder = [];
                for (var cgi = 0; cgi < gTasks.length; cgi++) {
                    var cKey = gTasks[cgi].primary_category || 'OTHER';
                    if (!catGroups[cKey]) { catGroups[cKey] = []; catGroupOrder.push(cKey); }
                    catGroups[cKey].push(gTasks[cgi]);
                }
                for (var cgoi = 0; cgoi < catGroupOrder.length; cgoi++) {
                    var cgName = catGroupOrder[cgoi];
                    var cgTasks = catGroups[cgName];
                    var cgColor = _CAT_COLORS[cgName] || '#94a3b8';
                    var cgCompleted = cgTasks.filter(function(x) { return x.status === 'COMPLETED'; }).length;
                    var cgId = 'tpt-cat-' + gName + '-' + cgoi;

                    html += '<div style="margin-bottom:4px">';
                    html += '<div onclick="var d=document.getElementById(\'' + cgId + '\');var a=document.getElementById(\'' + cgId + '-arrow\');if(d.style.display===\'none\'){d.style.display=\'block\';a.style.transform=\'rotate(0deg)\'}else{d.style.display=\'none\';a.style.transform=\'rotate(-90deg)\'}" style="padding:5px 8px;background:' + cgColor + '08;border:1px solid ' + cgColor + '20;border-radius:5px;cursor:pointer;display:flex;align-items:center;gap:6px;margin-bottom:2px" onmouseenter="this.style.background=\'' + cgColor + '15\'" onmouseleave="this.style.background=\'' + cgColor + '08\'">';
                    html += '<span id="' + cgId + '-arrow" style="font-size:.42rem;color:' + cgColor + ';transition:transform .2s">▼</span>';
                    html += '<span style="font-size:.48rem;color:' + cgColor + ';font-weight:600">' + _esc(cgName.replace(/_/g, ' ')) + '</span>';
                    html += '<span style="font-size:.4rem;color:#64748b">' + cgCompleted + '/' + cgTasks.length + '</span>';
                    html += '</div>';
                    html += '<div id="' + cgId + '">';
                    for (var pti = 0; pti < cgTasks.length; pti++) {
                        html += _renderTaskRow(cgTasks[pti], gName + '-' + cgoi + '-' + pti, gColor, startTime, totalDur);
                    }
                    html += '</div></div>';
                }
            } else {
                for (var pti = 0; pti < gTasks.length; pti++) {
                    html += _renderTaskRow(gTasks[pti], gName + '-' + pti, gColor, startTime, totalDur);
                }
            }
            html += '</div>';
            html += '</div>';
        }
        html += '</div>';

        // Footer — 시간 요약 (wall-clock + agent-minutes + 병렬 비율)
        var totalSec = Math.round(totalDur / 1000);
        var totalMin = Math.floor(totalSec / 60);
        var totalHr = Math.floor(totalMin / 60);
        var elapsed = totalHr > 0 ? totalHr + '시간 ' + (totalMin % 60) + '분' : totalMin + '분 ' + (totalSec % 60) + '초';

        var agentSec = 0;
        tasks.forEach(function(t) {
            var tS = new Date(t.createdAt).getTime();
            var tE = new Date(t.updatedAt).getTime();
            agentSec += (tE - tS) / 1000;
        });
        var agentMin = Math.round(agentSec / 60);
        var agentHrs = (agentSec / 3600).toFixed(1);
        var wallSec = totalDur / 1000;
        var ratio = wallSec > 0 ? (agentSec / wallSec).toFixed(1) : '-';

        html += '<div style="padding:10px 16px;border-top:1px solid #334155;display:flex;align-items:center;gap:16px;flex-wrap:wrap">';
        html += '<div><span style="font-size:.44rem;color:#64748b">Wall-clock</span><div style="font-size:.58rem;color:#e2e8f0;font-weight:600">' + elapsed + '</div></div>';
        html += '<div><span style="font-size:.44rem;color:#64748b">Agent-minutes</span><div style="font-size:.58rem;color:#fbbf24;font-weight:600">' + agentMin + 'min (' + agentHrs + 'h)</div></div>';
        html += '<div><span style="font-size:.44rem;color:#64748b">병렬 비율</span><div style="font-size:.58rem;color:#c4b5fd;font-weight:600">' + ratio + 'x</div></div>';
        html += '<div><span style="font-size:.44rem;color:#64748b">Tasks</span><div style="font-size:.58rem;color:#7dd3fc;font-weight:600">' + tasks.length + '</div></div>';
        html += '</div>';
        html += '</div>';
        el.innerHTML = html;
    }

    window._togglePhase = function(phaseName) {
        var tasksEl = document.getElementById('tpt-tasks-' + phaseName);
        var arrowEl = document.getElementById('tpt-arrow-' + phaseName);
        if (!tasksEl) return;
        if (tasksEl.style.display === 'none') {
            tasksEl.style.display = 'block';
            if (arrowEl) arrowEl.style.transform = 'rotate(0deg)';
        } else {
            tasksEl.style.display = 'none';
            if (arrowEl) arrowEl.style.transform = 'rotate(-90deg)';
        }
    };

    function _groupByService(findings) {
        var groups = {};
        for (var i = 0; i < findings.length; i++) {
            var f = findings[i];
            var svc = (f.operational_context && f.operational_context.service_name) || '';
            if (!svc) svc = 'unknown';
            if (!groups[svc]) groups[svc] = [];
            groups[svc].push(f);
        }
        return groups;
    }

    function _renderServiceGroup(svcName, findings) {
        var maxTask = 1;
        for (var i = 0; i < findings.length; i++) {
            var tc = _taskCounts[(findings[i].riskType || '')] || 0;
            if (tc > maxTask) maxTask = tc;
        }
        // Store for row rendering
        window._maxTaskForGroup = maxTask;

        var html = '';
        html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;margin-bottom:12px;overflow:hidden">';

        // 서비스 헤더
        html += '<div style="padding:12px 16px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:8px">';
        html += '<div style="font-size:.72rem;font-weight:600;color:#e2e8f0;flex:1">' + _esc(svcName) + '</div>';
        html += '<span style="font-size:.55rem;color:#64748b">' + findings.length + '건</span>';
        html += '</div>';

        // Finding 행들
        for (var i = 0; i < findings.length; i++) {
            html += _renderFindingRow(findings[i]);
        }

        html += '</div>';
        return html;
    }

    function _renderFindingRow(f) {
        var fid = f.id;
        var statusInfo = _statusInfo(f);
        var taskCount = _taskCounts[f.riskType] || 0;

        var html = '';
        html += '<div id="finding-row-' + fid + '" style="border-bottom:1px solid #0f172a">';

        // 클릭 가능한 요약 행
        html += '<div onclick="window._toggleFinding(\'' + fid + '\')" style="padding:10px 16px;cursor:pointer;transition:background .1s" onmouseenter="this.style.background=\'#263548\'" onmouseleave="this.style.background=\'transparent\'">';
        html += '<div style="display:flex;align-items:center;gap:8px">';

        // Risk badge
        var risk = f.adjusted_risk || f.riskLevel || '';
        html += '<span style="' + _riskStyle(risk) + '">' + risk + '</span>';

        // Name
        html += '<span style="font-size:.62rem;color:#e2e8f0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _esc(f.name || f.riskType) + '</span>';

        // Status
        html += '<span style="font-size:.52rem;color:' + statusInfo.color + ';font-weight:500">' + statusInfo.label + '</span>';
        html += '</div>';

        // Depth bar row
        var taskCount = _taskCounts[(f.riskType || '')] || 0;
        html += '<div style="display:flex;align-items:center;gap:8px;margin-top:5px;padding-left:56px">';
        html += '<span style="font-size:.5rem;color:#64748b;min-width:70px">' + _esc(f.riskType || '') + '</span>';
        html += '<div style="flex:1">' + _renderDepthBar(taskCount, window._maxTaskForGroup || 1) + '</div>';
        html += '<span style="font-size:.5rem;color:#94a3b8;min-width:28px">' + (taskCount > 0 ? taskCount + '회' : '') + '</span>';
        html += '</div>';

        html += '</div>';

        // 확장 영역 (Level 3) — 초기 숨김
        html += '<div id="finding-detail-' + fid + '" style="display:none"></div>';

        html += '</div>';
        return html;
    }

    // ================================================================
    // Level 3: Finding 상세 (인라인 확장)
    // ================================================================
    window._toggleFinding = function(findingId) {
        var detailEl = document.getElementById('finding-detail-' + findingId);
        if (!detailEl) return;

        if (_expandedFinding === findingId) {
            detailEl.style.display = 'none';
            _expandedFinding = null;
            return;
        }

        // 이전 확장 닫기
        if (_expandedFinding) {
            var prev = document.getElementById('finding-detail-' + _expandedFinding);
            if (prev) prev.style.display = 'none';
        }

        _expandedFinding = findingId;
        detailEl.style.display = 'block';
        detailEl.innerHTML = '<div style="padding:16px 20px;color:#64748b;font-size:.6rem"><span class="loading"></span> 상세 정보 로딩 중...</div>';

        fetch(_apiUrl('/api/security/insights/finding-detail/' + encodeURIComponent(findingId)))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    detailEl.innerHTML = '<div style="padding:16px 20px;color:#ef4444;font-size:.6rem">' + _esc(data.error || 'Error') + '</div>';
                    return;
                }
                detailEl.innerHTML = _renderDetail(data);
            })
            .catch(function(e) {
                detailEl.innerHTML = '<div style="padding:16px 20px;color:#ef4444;font-size:.6rem">네트워크 오류</div>';
            });
    };

    function _renderDetail(data) {
        var f = data.finding;
        var inv = data.investigation || {};
        var pr = data.pr;
        var scenario = data.scenario;
        var scenarioInOtherSpace = data.scenario_in_other_space || false;
        var html = '';

        html += '<div style="padding:14px 20px;background:#0f172a;border-top:1px solid #334155">';

        // 리스크 재평가
        html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px">';
        html += '<span style="' + _riskStyle(f.riskLevel) + '">' + (f.riskLevel || '?') + '</span>';
        if (f.risk_changed && f.adjusted_risk !== f.riskLevel) {
            html += '<span style="color:#64748b;font-size:.6rem">→</span>';
            html += '<span style="' + _riskStyle(f.adjusted_risk) + '">' + f.adjusted_risk + '</span>';
            if (f.adjustment_reason) {
                html += '<span style="font-size:.5rem;color:#94a3b8;margin-left:6px">' + _esc(f.adjustment_reason) + '</span>';
            }
        }
        html += '</div>';

        // 조사 분석 (핵심 콘텐츠)
        html += '<div style="margin-bottom:14px">';
        html += '<div style="font-size:.58rem;color:#64748b;font-weight:600;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px">조사 분석</div>';

        if (inv.task_count > 0) {
            html += '<div style="font-size:.55rem;color:#94a3b8;margin-bottom:8px">조사 깊이: <b style="color:#38bdf8">' + inv.task_count + '회 수행</b></div>';

            if (inv.agent_conclusion) {
                html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:12px;font-size:.56rem;color:#cbd5e1;line-height:1.6;max-height:280px;overflow-y:auto;white-space:pre-wrap">';
                html += _esc(inv.agent_conclusion);
                html += '</div>';
            } else {
                html += '<div style="font-size:.54rem;color:#475569;font-style:italic">조사 로그에서 분석 결론을 추출할 수 없음</div>';
            }
        } else {
            html += '<div style="font-size:.54rem;color:#475569;font-style:italic">해당 유형 조사 미실행 (정적 분석)</div>';
        }
        html += '</div>';

        // PR (인라인)
        if (pr && pr.url) {
            html += '<div style="margin-bottom:14px">';
            html += '<div style="font-size:.58rem;color:#64748b;font-weight:600;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px">수정 PR</div>';

            var prState = (pr.state || '').toLowerCase();
            var prColor = prState === 'merged' ? '#a78bfa' : prState === 'open' ? '#4ade80' : '#94a3b8';
            html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">';
            html += '<span style="font-size:.52rem;color:' + prColor + ';font-weight:600">' + prState + '</span>';
            if (pr.title) html += '<span style="font-size:.56rem;color:#e2e8f0">' + _esc(pr.title) + '</span>';
            html += '</div>';

            if (pr.diff) {
                html += '<div style="background:#1e293b;border:1px solid #334155;border-radius:6px;padding:10px;font-size:.48rem;color:#94a3b8;line-height:1.7;max-height:160px;overflow-y:auto;font-family:\'SF Mono\',monospace;white-space:pre">';
                html += _colorDiff(pr.diff);
                html += '</div>';
            }

            html += '<a href="' + _esc(pr.url) + '" target="_blank" style="font-size:.5rem;color:#38bdf8;text-decoration:none;display:inline-block;margin-top:4px">GitHub에서 보기 →</a>';
            html += '</div>';
        }

        // 등록된 시나리오
        if (scenario && !scenarioInOtherSpace) {
            var _scenGoLink = '/?space_id=' + encodeURIComponent(scenario.devops_space_id || _devopsSpaceId) + '&tab=scenario&scenario=' + encodeURIComponent(scenario.id);
            html += '<div style="margin-bottom:14px">';
            html += '<div style="font-size:.58rem;color:#64748b;font-weight:600;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px">등록된 시나리오</div>';
            html += '<div style="display:flex;align-items:center;gap:8px">';
            html += '<div style="font-size:.56rem;color:#e2e8f0">' + _esc(scenario.name || scenario.id) + '</div>';
            html += '<a href="' + _scenGoLink + '" target="_top" style="font-size:.5rem;color:#38bdf8;text-decoration:none;border:1px solid #38bdf840;padding:2px 8px;border-radius:4px;white-space:nowrap">시나리오 보기 →</a>';
            html += '</div>';
            if (scenario.last_result) {
                var resColor = scenario.last_result.status === 'safe' ? '#4ade80' : '#ef4444';
                html += '<div style="font-size:.5rem;color:' + resColor + ';margin-top:2px">' + (scenario.last_result.status === 'safe' ? '방어 확인됨' : '취약') + '</div>';
            }
            html += '</div>';
        } else if (scenario && scenarioInOtherSpace) {
            html += '<div style="margin-bottom:14px">';
            html += '<div style="font-size:.58rem;color:#64748b;font-weight:600;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px">시나리오 (다른 Space)</div>';
            html += '<div style="font-size:.5rem;color:#94a3b8;margin-bottom:4px">' + _esc(scenario.app_name || '') + ' Space에 등록됨 — 이 Space에는 미등록</div>';
            html += '</div>';
        }

        // 액션 버튼
        html += '<div style="display:flex;gap:8px;padding-top:10px;border-top:1px solid #1e293b">';
        if (!pr) {
            html += '<button onclick="window._insightAction(\'remediate\',\'' + _esc(f.id) + '\')" style="' + _btnStyle('#0ea5e9', '#fff') + '">코드 수정 PR 생성</button>';
            html += '<button onclick="window._insightAction(\'defense\',\'' + _esc(f.id) + '\')" style="' + _btnStyle() + '">방어 확인</button>';
        }
        if (pr && pr.url && (pr.state || '').toLowerCase() === 'merged') {
            html += '<button onclick="window._insightAction(\'reverify\',\'' + _esc(f.id) + '\')" style="' + _btnStyle() + '">수정 재검증</button>';
        }
        if (!scenario || scenarioInOtherSpace) {
            html += '<button onclick="window._insightAction(\'register\',\'' + _esc(f.id) + '\')" style="' + _btnStyle() + '">시나리오 등록</button>';
        }
        html += '</div>';

        // 액션 결과 영역
        html += '<div id="insight-action-result-' + f.id + '" style="margin-top:8px"></div>';

        html += '</div>';
        return html;
    }

    // ================================================================
    // Actions
    // ================================================================
    window._insightAction = function(action, findingId) {
        var resultEl = document.getElementById('insight-action-result-' + findingId);
        if (!resultEl) return;

        if (action === 'reverify') {
            resultEl.innerHTML = '<div style="font-size:.52rem;color:#7dd3fc"><span class="loading"></span> 재검증 중...</div>';
            fetch(_apiUrl('/api/security/insights/reverify/' + encodeURIComponent(findingId)), {method: 'POST'})
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (!data.ok) { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">' + _esc(data.error) + '</div>'; return; }
                    var res = data.result;
                    var c = res.status === 'defended' ? '#4ade80' : '#fca5a5';
                    resultEl.innerHTML = '<div style="font-size:.54rem;color:' + c + ';font-weight:600">' + (res.status === 'defended' ? '수정 확인됨' : '여전히 취약') + ' — HTTP ' + (res.current_response || '') + '</div>';
                })
                .catch(function() { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">오류</div>'; });

        } else if (action === 'remediate') {
            resultEl.innerHTML = '<div style="font-size:.52rem;color:#7dd3fc"><span class="loading"></span> 코드 수정 PR 생성 중...</div>';
            fetch(_apiUrl('/api/settings/security/findings/' + encodeURIComponent(findingId) + '/remediate'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})})
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.ok) { resultEl.innerHTML = '<div style="font-size:.52rem;color:#4ade80">PR 생성이 시작되었습니다. 완료 시 GitHub PR 링크가 표시됩니다.</div>'; }
                    else { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">' + _esc(data.error || '실패') + '</div>'; }
                })
                .catch(function() { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">오류</div>'; });

        } else if (action === 'register') {
            resultEl.innerHTML = '<div style="font-size:.52rem;color:#7dd3fc"><span class="loading"></span> 등록 중...</div>';
            fetch(_apiUrl('/api/security/insights/register-scenario'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({finding_id: findingId, space_id: _spaceId, sec_space_id: _secSpaceId})})
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.ok) {
                        var scId = (data.scenario && data.scenario.id) || '';
                        var spId = data.space_id || _devopsSpaceId || '';
                        var scenLink = '/?space_id=' + encodeURIComponent(spId) + '&tab=scenario&scenario=' + encodeURIComponent(scId);
                        resultEl.innerHTML = '<div style="font-size:.52rem;color:#4ade80;display:flex;align-items:center;gap:8px">'
                            + (data.already_registered ? '이미 등록됨' : '시나리오 등록 완료')
                            + ' <a href="' + scenLink + '" target="_top" style="color:#38bdf8;font-weight:600;text-decoration:none;border:1px solid #38bdf840;padding:2px 8px;border-radius:4px">시나리오 보기 →</a></div>';
                    }
                    else { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">' + _esc(data.error || '실패') + '</div>'; }
                })
                .catch(function() { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">오류</div>'; });

        } else if (action === 'defense') {
            resultEl.innerHTML = '<div style="font-size:.52rem;color:#7dd3fc"><span class="loading"></span> 방어 확인 중...</div>';
            fetch(_apiUrl('/api/security/insights/scenarios/SEC-' + encodeURIComponent(findingId.substring(2, 10)) + '/run'), {method: 'POST'})
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (!data.ok) { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">' + _esc(data.error) + '</div>'; return; }
                    var res = data.result;
                    var c = res.status === 'defended' ? '#4ade80' : '#fca5a5';
                    resultEl.innerHTML = '<div style="font-size:.54rem;color:' + c + ';font-weight:600">' + (res.status === 'defended' ? '방어됨' : '취약') + ' — HTTP ' + (res.current_response || '') + '</div>';
                })
                .catch(function() { resultEl.innerHTML = '<div style="font-size:.52rem;color:#fca5a5">오류</div>'; });
        }
    };

    // ================================================================
    // Task count 로드 (서비스별 depth bar용)
    // ================================================================
    function _loadTaskCounts() {
        fetch(_apiUrl('/api/security/insights/service-summary/_all'))
            .catch(function() {});
    }

    // ================================================================
    // Utils
    // ================================================================
    // ================================================================
    // Topology Overlay — 실제 아키텍처 + 보안 결과 오버레이 SVG
    // ================================================================
    function _renderTopoOverlay(data) {
        var nodes = data.nodes || [];
        var edges = data.edges || [];
        var entryPoints = data.entry_points || [];
        var infraFindings = data.infra_findings || [];
        var chains = data.chains || [];

        if (!nodes.length) return '<div style="font-size:.55rem;color:#475569">토폴로지 데이터 없음</div>';

        var nodeW = 140, nodeH = 60;

        // 원래 레이아웃 (상→하) 복원
        var layoutEdges = edges.map(function(e) { return {source: e.source, target: e.target}; });
        var layoutNodes = nodes.map(function(n) { return {name: n.name, group: n.service_type || ''}; });
        var rawPos = _archLayout(layoutNodes, layoutEdges, 600, 400);

        var minX = Infinity, maxX = 0, minY = Infinity, maxY = 0;
        nodes.forEach(function(n) {
            var p = rawPos[n.name];
            if (!p) return;
            if (p.x < minX) minX = p.x;
            if (p.x > maxX) maxX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.y > maxY) maxY = p.y;
        });

        var PAD = 60;
        var rangeX = maxX - minX || 1;
        var rangeY = maxY - minY || 1;
        // 비율 클램프 — 극단적 종횡비 방지
        var ratio = Math.min(Math.max(rangeY / rangeX, 0.3), 3);
        var ratioInv = Math.min(Math.max(rangeX / rangeY, 0.3), 3);
        // 앱 영역 너비 (체인 레인 별도)
        var appAreaW = Math.max(ratio * 500 + PAD * 2, 500);
        var chainLaneW = chains.length > 0 ? 200 : 0;
        var svgW = appAreaW + chainLaneW;
        var svgH = Math.min(ratioInv * 300 + PAD * 2 + 80, 600);

        var positions = {};
        nodes.forEach(function(n) {
            var p = rawPos[n.name];
            if (!p) return;
            var nx = PAD + ((p.y - minY) / rangeY) * (appAreaW - PAD * 2 - nodeW);
            var ny = PAD + 60 + ((p.x - minX) / rangeX) * (svgH - PAD * 2 - 80 - nodeH);
            positions[n.name] = {x: nx, y: ny, cx: nx + nodeW / 2, cy: ny + nodeH / 2};
        });

        var internetX = appAreaW / 2;

        var svg = '<svg viewBox="0 0 ' + svgW + ' ' + svgH + '" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="background:#0f172a;display:block;width:100%;max-height:340px">';
        svg += '<defs>';
        svg += '<marker id="arrowRed" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#ef4444"/></marker>';
        svg += '<marker id="arrowOrange" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#f97316"/></marker>';
        svg += '</defs>';

        // Internet/Attacker node (위)
        var internetY = PAD;
        svg += '<rect x="' + (internetX - 50) + '" y="' + internetY + '" width="100" height="32" rx="4" fill="#7f1d1d" stroke="#ef4444" stroke-width="1.5"/>';
        svg += '<text x="' + internetX + '" y="' + (internetY + 20) + '" text-anchor="middle" font-size="10" fill="#fca5a5" font-weight="600">Internet (Attacker)</text>';

        // Entry point arrows
        for (var ei = 0; ei < entryPoints.length; ei++) {
            var ept = entryPoints[ei].target;
            if (!positions[ept]) continue;
            var targetNode = _findNode(nodes, ept);
            if (!targetNode || targetNode.findings_count === 0) continue;
            var ep = positions[ept];
            var ay1 = internetY + 32, ay2 = ep.y;
            var amx = (internetX + ep.cx) / 2, amy = (ay1 + ay2) / 2;
            svg += '<path d="M' + internetX + ',' + ay1 + ' C' + internetX + ',' + amy + ' ' + ep.cx + ',' + amy + ' ' + ep.cx + ',' + ay2 + '" fill="none" stroke="#ef4444" stroke-width="2" stroke-dasharray="6,3" opacity="0.8"/>';
            svg += '<circle cx="' + ep.cx + '" cy="' + ay2 + '" r="4" fill="#ef4444" opacity="0.8"/>';
        }

        // Edges (서비스 간)
        for (var i = 0; i < edges.length; i++) {
            var e = edges[i];
            var src = positions[e.source];
            var tgt = positions[e.target];
            if (!src || !tgt) continue;
            var ex1 = src.cx, ey1 = src.y + nodeH, ex2 = tgt.cx, ey2 = tgt.y;
            var ecx = (ex2 - ex1) * 0.4;
            svg += '<path d="M' + ex1 + ',' + ey1 + ' C' + (ex1+ecx) + ',' + ey1 + ' ' + (ex2-ecx) + ',' + ey2 + ' ' + ex2 + ',' + ey2 + '" fill="none" stroke="#334155" stroke-width="1.5" opacity="0.5"/>';
            svg += '<circle cx="' + ex2 + '" cy="' + ey2 + '" r="2.5" fill="#334155" opacity="0.5"/>';
        }

        // Nodes
        var allNodes = nodes;
        for (var i = 0; i < allNodes.length; i++) {
            var n = allNodes[i];
            var pos = positions[n.name];
            if (!pos) continue;

            var riskColor = _riskColorHex(n.max_risk || 'NONE');
            var borderColor = n.findings_count > 0 ? riskColor : '#334155';
            var borderWidth = n.findings_count > 0 ? '2' : '1';

            svg += '<rect x="' + pos.x + '" y="' + pos.y + '" width="' + nodeW + '" height="' + nodeH + '" rx="8" fill="#1e293b" stroke="' + borderColor + '" stroke-width="' + borderWidth + '"/>';
            if (n.findings_count > 0) {
                svg += '<rect x="' + pos.x + '" y="' + pos.y + '" width="' + nodeW + '" height="' + nodeH + '" rx="8" fill="' + riskColor + '" opacity="0.05"/>';
            }

            svg += '<text x="' + pos.cx + '" y="' + (pos.y + 18) + '" text-anchor="middle" font-size="12" fill="#e2e8f0" font-weight="700">' + _esc(n.name) + '</text>';
            var roleText = n.service_type === 'db' ? 'data store' : (n.role || '').substring(0, 25);
            svg += '<text x="' + pos.cx + '" y="' + (pos.y + 32) + '" text-anchor="middle" font-size="7" fill="#64748b">' + _esc(roleText) + '</text>';

            if (n.findings_count > 0) {
                var badgeX = pos.x + nodeW - 12, badgeY = pos.y - 6;
                svg += '<circle cx="' + badgeX + '" cy="' + badgeY + '" r="10" fill="' + riskColor + '"/>';
                svg += '<text x="' + badgeX + '" y="' + (badgeY + 4) + '" text-anchor="middle" font-size="9" fill="#fff" font-weight="700">' + n.findings_count + '</text>';

                var rtypes = {};
                var nFindings = n.findings || [];
                for (var fi = 0; fi < nFindings.length; fi++) { var rt = nFindings[fi].riskType || ''; if (rt) rtypes[rt] = true; }
                var rtList = Object.keys(rtypes);
                var rtText = rtList.slice(0,2).map(function(r) { return r.replace(/_/g,' ').substring(0,12); }).join(', ');
                if (rtList.length > 2) rtText += ' +' + (rtList.length - 2);
                svg += '<text x="' + pos.cx + '" y="' + (pos.y + 46) + '" text-anchor="middle" font-size="7" fill="' + riskColor + '">' + _esc(rtText) + '</text>';
            } else {
                svg += '<text x="' + pos.cx + '" y="' + (pos.y + 46) + '" text-anchor="middle" font-size="8" fill="#22c55e">● 취약점 없음</text>';
            }
        }

        // Lateral movement paths
        var lateralDrawn = {};
        for (var i = 0; i < allNodes.length; i++) {
            var n = allNodes[i];
            if (!n.findings_count || !n.findings) continue;
            var pos = positions[n.name];
            if (!pos) continue;
            for (var fi = 0; fi < n.findings.length; fi++) {
                var mentions = n.findings[fi].mentions_internal || [];
                for (var mi = 0; mi < mentions.length; mi++) {
                    var targetPos = positions[mentions[mi]];
                    var key = n.name + '->' + mentions[mi];
                    if (!targetPos || lateralDrawn[key]) continue;
                    lateralDrawn[key] = true;
                    var x1 = pos.cx, y1 = pos.y + nodeH, x2 = targetPos.cx, y2 = targetPos.y;
                    var mx = (x1+x2)/2, my = (y1+y2)/2;
                    svg += '<path d="M' + x1 + ',' + y1 + ' Q' + mx + ',' + (my-15) + ' ' + x2 + ',' + y2 + '" fill="none" stroke="#f97316" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.7"/>';
                    svg += '<circle cx="' + x2 + '" cy="' + y2 + '" r="3" fill="#f97316" opacity="0.7"/>';
                    svg += '<text x="' + mx + '" y="' + (my-18) + '" text-anchor="middle" font-size="7" fill="#f97316">도달 시도</text>';
                }
            }
        }

        // Infra findings
        if (infraFindings.length) {
            var infraX = appAreaW - 130, infraY = PAD + 60;
            svg += '<rect x="' + infraX + '" y="' + infraY + '" width="120" height="' + (20 + infraFindings.length * 14) + '" rx="6" fill="#1e293b" stroke="#475569" stroke-width="1" stroke-dasharray="3,2"/>';
            svg += '<text x="' + (infraX+60) + '" y="' + (infraY+14) + '" text-anchor="middle" font-size="8" fill="#94a3b8" font-weight="600">IaC / Infra</text>';
            for (var i = 0; i < infraFindings.length; i++) {
                var inf = infraFindings[i];
                var fc = _riskColorHex(inf.adjusted_risk);
                svg += '<text x="' + (infraX+10) + '" y="' + (infraY + 28 + i*14) + '" font-size="7" fill="' + fc + '">● ' + _esc((inf.riskType||'').replace(/_/g,' ').substring(0,16)) + '</text>';
            }
        }

        // ─── Chain Attack Flows (우측 레인) ───
        if (chains.length > 0) {
            var clX = appAreaW + 10;
            var clW = chainLaneW - 20;
            svg += '<rect x="' + clX + '" y="' + PAD + '" width="' + clW + '" height="' + (svgH - PAD*2) + '" rx="6" fill="#0f172a" stroke="#334155" stroke-width="0.5" stroke-dasharray="3,2"/>';
            svg += '<text x="' + (clX + clW/2) + '" y="' + (PAD + 14) + '" text-anchor="middle" fill="#ef4444" font-size="7.5" font-weight="700">Chain Attacks</text>';

            var cyy = PAD + 28;
            var maxChains = Math.min(chains.length, 5);
            for (var ci = 0; ci < maxChains; ci++) {
                var ch = chains[ci];
                var chColor = _riskColorHex(ch.riskLevel);
                var chSteps = ch.steps || [];
                if (chSteps.length < 2) continue;

                var chainUrl = '/security/insights/chain/' + encodeURIComponent(ch.id) + (_secSpaceId ? '?sec_space_id=' + encodeURIComponent(_secSpaceId) : (_spaceId ? '?space_id=' + encodeURIComponent(_spaceId) : ''));
                svg += '<a href="' + chainUrl + '">';
                var gH = 12 + Math.min(chSteps.length, 3) * 12 + 4;
                svg += '<rect x="' + (clX+2) + '" y="' + (cyy-2) + '" width="' + (clW-4) + '" height="' + gH + '" rx="4" fill="transparent" class="chain-hover"/>';

                svg += '<text x="' + (clX+6) + '" y="' + cyy + '" fill="' + chColor + '" font-size="6" font-weight="700">' + _esc((ch.riskType||'').substring(0,24)) + ' ▸</text>';
                cyy += 12;

                for (var csi = 0; csi < Math.min(chSteps.length, 3); csi++) {
                    var cs = chSteps[csi];
                    var csA = cs.action || (cs.method + ' ' + (cs.path||''));
                    svg += '<circle cx="' + (clX+10) + '" cy="' + (cyy+2) + '" r="3" fill="' + chColor + '40" stroke="' + chColor + '" stroke-width="0.6"/>';
                    svg += '<text x="' + (clX+10) + '" y="' + (cyy+2) + '" fill="' + chColor + '" font-size="4.5" text-anchor="middle" dominant-baseline="middle">' + cs.step + '</text>';
                    svg += '<text x="' + (clX+16) + '" y="' + (cyy+3) + '" fill="#fbbf24" font-size="5.5">' + _esc(csA.substring(0,26)) + '</text>';
                    if (csi < Math.min(chSteps.length,3)-1) {
                        svg += '<line x1="' + (clX+10) + '" y1="' + (cyy+5) + '" x2="' + (clX+10) + '" y2="' + (cyy+10) + '" stroke="' + chColor + '40" stroke-width="0.6"/>';
                    }
                    cyy += 12;
                }
                if (chSteps.length > 3) {
                    svg += '<text x="' + (clX+16) + '" y="' + (cyy+2) + '" fill="#64748b" font-size="5">+' + (chSteps.length-3) + ' more</text>';
                    cyy += 10;
                }
                svg += '</a>';

                // 서비스노드 → chain 연결
                var cmY = cyy - (Math.min(chSteps.length,3)*12)/2;
                var nearest = null, nDist = Infinity;
                for (var ni = 0; ni < allNodes.length; ni++) {
                    var np = positions[allNodes[ni].name];
                    if (!np || !allNodes[ni].findings_count) continue;
                    var d = Math.abs(np.cy - cmY);
                    if (d < nDist) { nDist = d; nearest = np; }
                }
                if (nearest) {
                    var lx1 = nearest.x + nodeW, ly1 = nearest.cy, lx2 = clX, ly2 = cmY;
                    var lm = (lx1+lx2)/2;
                    svg += '<path d="M' + lx1 + ',' + ly1 + ' C' + lm + ',' + ly1 + ' ' + lm + ',' + ly2 + ' ' + lx2 + ',' + ly2 + '" fill="none" stroke="' + chColor + '" stroke-width="1" stroke-dasharray="3,2" opacity="0.5" marker-end="url(#arrowOrange)"/>';
                }
                cyy += 8;
            }
        }

        svg += '<style>.chain-hover:hover{fill:#1e293b80}</style>';
        svg += '</svg>';
        return svg;
    }

    function _findNode(nodes, name) {
        for (var i = 0; i < nodes.length; i++) {
            if (nodes[i].name === name) return nodes[i];
        }
        return null;
    }

    function _riskColorHex(level) {
        var map = {CRITICAL: '#ef4444', HIGH: '#f97316', MEDIUM: '#f59e0b', LOW: '#22c55e', INFO: '#38bdf8'};
        return map[level] || '#64748b';
    }

    function _renderDepthBar(count, maxCount) {
        if (count === 0) {
            return '<span style="font-size:.48rem;color:#475569;font-style:italic">조사 미실행</span>';
        }
        var blocks = 10;
        var filled = Math.max(1, Math.round((count / maxCount) * blocks));
        var bar = '';
        for (var i = 0; i < blocks; i++) {
            var color = i < filled ? '#38bdf8' : '#334155';
            bar += '<span style="display:inline-block;width:8px;height:10px;background:' + color + ';margin-right:1px;border-radius:1px"></span>';
        }
        return bar;
    }

    function _statusInfo(f) {
        var rem = f.remediationStatus || '';
        if (rem === 'COMPLETED') return {label: 'PR 생성됨', color: '#a78bfa'};
        var adjusted = f.adjusted_risk || f.riskLevel || '';
        if (f.risk_changed && (adjusted === 'LOW' || adjusted === 'INFO')) return {label: '안전', color: '#4ade80'};
        if (f.confidence === 'FALSE_POSITIVE') return {label: '오탐', color: '#64748b'};
        return {label: '취약', color: '#f87171'};
    }

    function _riskStyle(level) {
        var colors = {
            CRITICAL: {bg: '#7f1d1d', fg: '#fca5a5', border: '#ef4444'},
            HIGH: {bg: '#7c2d12', fg: '#fdba74', border: '#f97316'},
            MEDIUM: {bg: '#78350f', fg: '#fcd34d', border: '#f59e0b'},
            LOW: {bg: '#14532d', fg: '#86efac', border: '#22c55e'},
            INFO: {bg: '#0c4a6e', fg: '#7dd3fc', border: '#38bdf8'},
            UNKNOWN: {bg: '#1e293b', fg: '#94a3b8', border: '#475569'}
        };
        var c = colors[level] || colors.UNKNOWN;
        return 'display:inline-block;padding:2px 6px;border-radius:4px;font-size:.5rem;font-weight:600;' +
               'background:' + c.bg + ';color:' + c.fg + ';border:1px solid ' + c.border;
    }

    function _btnStyle(bg, fg) {
        if (bg) return 'background:' + bg + ';border:1px solid ' + bg + ';color:' + (fg || '#fff') + ';padding:5px 10px;border-radius:5px;font-size:.54rem;cursor:pointer;font-weight:600';
        return 'background:transparent;border:1px solid #334155;color:#94a3b8;padding:5px 10px;border-radius:5px;font-size:.54rem;cursor:pointer';
    }

    function _colorDiff(diff) {
        if (!diff) return '';
        var lines = diff.split('\n');
        var out = '';
        var shown = 0;
        for (var i = 0; i < lines.length && shown < 30; i++) {
            var line = lines[i];
            var color = '#94a3b8';
            if (line.startsWith('+') && !line.startsWith('+++')) color = '#4ade80';
            else if (line.startsWith('-') && !line.startsWith('---')) color = '#f87171';
            else if (line.startsWith('@@')) color = '#38bdf8';
            out += '<span style="color:' + color + '">' + _esc(line) + '</span>\n';
            shown++;
        }
        if (lines.length > 30) out += '<span style="color:#475569">... (' + (lines.length - 30) + ' more)</span>\n';
        return out;
    }

    function _statCard(label, value, color) {
        return '<div class="ins-stat-card">' +
            '<div class="ins-stat-value" style="color:' + color + '">' + value + '</div>' +
            '<div class="ins-stat-label">' + label + '</div></div>';
    }

    function _esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
})();

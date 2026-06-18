// ================================================================
// Security Agent — Agent Space 내 연결 섹션 + 토폴로지 시각화
// _onShowOverview hook: detail panel에 Security Agent 섹션 추가
// _onRenderTopo hook: SVG에 Security Agent 노드 + 라벨 패치
// ================================================================

(function() {
    var _secMatchCache = null;

    // ================================================================
    // Hook: renderTopo 완료 후
    // ================================================================
    window._onRenderTopo = function() {
        _patchTopoLabels();
        _addSecurityNodeToTopo();
    };

    // ================================================================
    // Hook: showOverview 완료 후
    // ================================================================
    window._onShowOverview = function() {
        _renderSecuritySection();
    };

    // ================================================================
    // Topo: "Agent Space" → "DevOps Agent" 라벨 변경
    // ================================================================
    function _patchTopoLabels() {
        var svg = document.getElementById('topoSvg');
        if (!svg) return;
        var texts = svg.querySelectorAll('text');
        for (var i = 0; i < texts.length; i++) {
            if (texts[i].textContent === 'Agent Space') {
                texts[i].textContent = 'DevOps Agent';
            }
        }
        var dTitle = document.getElementById('dTitle');
        if (dTitle && dTitle.textContent === 'Agent Space') {
            dTitle.textContent = 'DevOps Agent';
        }
    }

    // ================================================================
    // Topo: Security Agent 노드 추가 (연결된 경우만)
    // ================================================================
    function _addSecurityNodeToTopo() {
        var svg = document.getElementById('topoSvg');
        if (!svg || !SELECTED) return;

        var existing = document.getElementById('secAgentTopoNode');
        if (existing) existing.remove();

        if (_secMatchCache && _secMatchCache.match) {
            _drawSecNode(svg, _secMatchCache);
        } else {
            fetch('/api/settings/security/match/' + encodeURIComponent(SELECTED))
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.ok) {
                        _secMatchCache = data;
                        if (data.match) _drawSecNode(svg, data);
                    }
                })
                .catch(function() {});
        }
    }

    function _drawSecNode(svg, data) {
        if (!data.match) return;
        var vb = svg.getAttribute('viewBox');
        if (!vb) return;
        var parts = vb.split(' ');
        var W = parseFloat(parts[2]);
        var H = parseFloat(parts[3]);
        var centerX = W / 2;

        var nW = 140, nH = 50;
        var nX = centerX - nW / 2;
        var nY = 20;
        var cCenterY = H / 2;

        var g = _svgE('g', {id: 'secAgentTopoNode', style: 'cursor:pointer'});

        // Edge: Security Agent → DevOps Agent
        var x1 = centerX, y1 = nY + nH;
        var x2 = centerX, y2 = cCenterY - 35;
        var my = (y1 + y2) / 2;
        g.appendChild(_svgE('path', {
            d: 'M ' + x1 + ' ' + y1 + ' C ' + x1 + ' ' + my + ', ' + x2 + ' ' + my + ', ' + x2 + ' ' + y2,
            fill: 'none', stroke: '#f43f5e', 'stroke-width': '2', 'stroke-opacity': '0.6', 'stroke-dasharray': '4,3'
        }));

        // Box
        g.appendChild(_svgE('rect', {
            x: nX, y: nY, width: nW, height: nH, rx: '10',
            fill: '#1c1017', stroke: '#f43f5e', 'stroke-width': '2'
        }));

        // Icon
        var fo = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
        fo.setAttribute('x', nX + 8); fo.setAttribute('y', nY + 8);
        fo.setAttribute('width', '16'); fo.setAttribute('height', '16');
        var img = document.createElement('img');
        img.src = '/static/icons/aws/security-hub.svg';
        img.style.cssText = 'width:16px;height:16px;border-radius:50%';
        fo.appendChild(img);
        g.appendChild(fo);

        // Title
        var t1 = _svgE('text', {x: nX + 28, y: nY + 19, 'font-size': '11', fill: '#fca5a5', 'font-weight': '600'});
        t1.textContent = 'Security Agent';
        g.appendChild(t1);

        // Space name
        var t2 = _svgE('text', {x: nX + 10, y: nY + 34, 'font-size': '8', fill: '#94a3b8'});
        t2.textContent = _trun(data.match.security_space_name, 22);
        g.appendChild(t2);

        // SAST status
        var sastActive = data.match.repo && data.match.repo.leave_comments;
        var sast = sastActive ? 'SAST Active' : 'SAST Inactive';
        var sastColor = sastActive ? '#4ade80' : '#fbbf24';
        var t3 = _svgE('text', {x: nX + 10, y: nY + 45, 'font-size': '7', fill: sastColor});
        t3.textContent = sast;
        g.appendChild(t3);

        // Click → Security Agent 페이지로 이동
        g.addEventListener('click', function() {
            window.location.href = '/security?sec_space_id=' + encodeURIComponent(data.match.security_space_id);
        });

        // Tooltip
        g.addEventListener('mouseenter', function(ev) {
            showTip(ev, '<div class="tt">Security Agent</div>' +
                _esc(data.match.security_space_name) + '<br>' +
                'Repo: ' + _esc((data.devops_repo || {}).owner || '') + '/' + _esc((data.devops_repo || {}).name || '') + '<br>' +
                sast + '<br><span style="font-size:.5rem;color:#64748b">클릭하여 보안 조사 페이지로 이동</span>');
        });
        g.addEventListener('mousemove', moveTip);
        g.addEventListener('mouseleave', hideTip);

        svg.appendChild(g);
    }

    // ================================================================
    // Detail panel: Security Agent 섹션
    // ================================================================
    function _renderSecuritySection() {
        var dBody = document.getElementById('dBody');
        if (!dBody || !SELECTED) return;

        var existing = document.getElementById('securityAgentSection');
        if (existing) existing.remove();

        var sec = document.createElement('div');
        sec.className = 'dsec';
        sec.id = 'securityAgentSection';
        sec.style.border = '1px solid #1e3a5f';
        sec.style.borderRadius = '8px';
        sec.style.padding = '10px';
        sec.innerHTML =
            '<h3><img src="/static/icons/aws/security-hub.svg" style="width:16px;height:16px;vertical-align:middle;border-radius:50%"> Security Agent</h3>' +
            '<p style="font-size:.58rem;color:#94a3b8;margin:4px 0 8px">SAST 코드 리뷰 — GitHub PR 자동 분석 및 취약점 탐지</p>' +
            '<div id="secAgentStatus"><span class="loading" style="display:inline-block;width:12px;height:12px;border:2px solid #334155;border-top-color:#38bdf8;border-radius:50%;animation:spin .6s linear infinite"></span> <span style="font-size:.58rem;color:#64748b">연결 상태 확인 중...</span></div>';

        var simSection = dBody.querySelector('[onclick*="openSimulator"]');
        if (simSection && simSection.parentElement) {
            dBody.insertBefore(sec, simSection.parentElement);
        } else {
            dBody.appendChild(sec);
        }

        _checkSecurityMatch(SELECTED);
    }

    function _checkSecurityMatch(spaceId) {
        var statusEl = document.getElementById('secAgentStatus');
        if (!statusEl) return;

        fetch('/api/settings/security/match/' + encodeURIComponent(spaceId))
            .then(function(r) { return r.json(); })
            .then(function(data) {
                _secMatchCache = data;

                if (!data.ok) {
                    statusEl.innerHTML = '<span style="font-size:.58rem;color:#f87171">Error: ' + _esc(data.error || '') + '</span>';
                    return;
                }

                var repo = data.devops_repo;
                var repoHtml = repo ? '<div style="font-size:.58rem;color:#cbd5e1;margin-bottom:8px">' +
                    '<img src="/static/icons/github.svg" style="width:14px;height:14px;vertical-align:middle;border-radius:50%"> ' +
                    _esc(repo.owner) + '/' + _esc(repo.name) +
                    '</div>' : '';

                if (data.match) {
                    window._securityTabSpaceId = data.match.security_space_id;
                    if(typeof _showSecurityFrame==='function'){var _secTab=document.querySelector('.tab-btn.active');if(_secTab&&_secTab.getAttribute('data-tab')==='security')_showSecurityFrame(data.match.security_space_id);}
                    var allMatches = data.matches || [data.match];
                    var html = repoHtml;
                    for (var mi = 0; mi < allMatches.length; mi++) {
                        var m = allMatches[mi];
                        var sid = m.security_space_id;
                        var label = m.target_domain || m.name || sid;
                        html += '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.2);border-radius:6px;margin-bottom:6px;cursor:pointer" onclick="window.location.href=\'/security?sec_space_id=' + encodeURIComponent(sid) + '\'">' +
                            '<div style="width:8px;height:8px;border-radius:50%;background:#4ade80;flex-shrink:0"></div>' +
                            '<div style="flex:1">' +
                            '<div style="font-size:.6rem;color:#4ade80;font-weight:600">' + _esc(label) + '</div>' +
                            '<div style="font-size:.52rem;color:#94a3b8">' + _esc(m.name || '') + ' (' + _esc(sid.substring(0, 12)) + '...)</div>' +
                            '</div>' +
                            '<button onclick="event.stopPropagation();window._secDisconnect(\'' + _esc(spaceId) + '\',\'' + _esc(sid) + '\')" style="padding:3px 10px;font-size:.52rem;background:transparent;color:#f87171;border:1px solid #f8717140;border-radius:4px;cursor:pointer;flex-shrink:0">Disconnect</button>' +
                            '</div>';
                    }
                    statusEl.innerHTML = html;
                    _refreshTopoSecNode(data);
                } else if (!repo) {
                    window._securityTabSpaceId = null;
                    statusEl.innerHTML =
                        '<div style="font-size:.58rem;color:#94a3b8;padding:6px 0">' +
                        'GitHub repo가 연결되어 있지 않아 Security Agent를 추가할 수 없습니다.</div>';
                    _removeTopoSecNode();
                } else {
                    window._securityTabSpaceId = null;
                    statusEl.innerHTML = repoHtml +
                        '<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2);border-radius:6px;margin-bottom:8px">' +
                        '<div style="width:8px;height:8px;border-radius:50%;background:#fbbf24;flex-shrink:0"></div>' +
                        '<div style="flex:1">' +
                        '<div style="font-size:.6rem;color:#fbbf24;font-weight:600">미연결</div>' +
                        '<div style="font-size:.52rem;color:#94a3b8">' + _esc(data.message || '연결 가능한 Security Agent Space 없음') + '</div>' +
                        '</div></div>' +
                        '<div style="margin-bottom:8px">' +
                        '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">' +
                        '<label style="font-size:.54rem;color:#94a3b8;min-width:50px">Account</label>' +
                        '<select id="secAcctSelect" style="padding:4px 8px;font-size:.56rem;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#e2e8f0;min-width:140px"><option value="">계정 선택...</option></select>' +
                        '</div>' +
                        '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
                        '<label style="font-size:.54rem;color:#94a3b8;min-width:50px">Space</label>' +
                        '<select id="secLinkSelect" style="padding:4px 8px;font-size:.56rem;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#e2e8f0;min-width:200px" disabled><option value="">계정을 먼저 선택하세요</option></select>' +
                        '<button id="secLinkBtn" onclick="window._secLinkExisting(\'' + _esc(spaceId) + '\')" style="padding:5px 12px;font-size:.58rem;font-weight:600;background:#334155;color:#e2e8f0;border:1px solid #475569;border-radius:5px;cursor:pointer" disabled>연결</button>' +
                        '</div>' +
                        '</div>' +
                        '<div style="display:flex;gap:8px;align-items:center">' +
                        '<button onclick="window._secCreateNew(\'' + _esc(spaceId) + '\')" style="padding:5px 12px;font-size:.58rem;font-weight:600;background:#0ea5e9;color:#fff;border:none;border-radius:5px;cursor:pointer">신규 생성</button>' +
                        '</div>';
                    _loadAccounts();
                    _removeTopoSecNode();
                }
            })
            .catch(function(e) {
                statusEl.innerHTML = '<span style="font-size:.58rem;color:#f87171">Error: ' + _esc(String(e)) + '</span>';
            });
    }

    function _refreshTopoSecNode(data) {
        var svg = document.getElementById('topoSvg');
        if (!svg) return;
        var existing = document.getElementById('secAgentTopoNode');
        if (existing) existing.remove();
        _drawSecNode(svg, data);
    }

    function _removeTopoSecNode() {
        var existing = document.getElementById('secAgentTopoNode');
        if (existing) existing.remove();
    }

    // ================================================================
    // Actions: connect / disconnect
    // ================================================================
    window._secDisconnect = function(devopsSpaceId, securitySpaceId) {
        if (!confirm('Security Agent 연결을 해제하시겠습니까?\nSAST 코드 리뷰가 비활성화됩니다.')) return;

        var statusEl = document.getElementById('secAgentStatus');
        if (!statusEl) return;
        var btn = statusEl.querySelector('button[onclick*="secDisconnect"]');
        if (btn) { btn.disabled = true; btn.textContent = 'Disconnecting...'; }

        fetch('/api/settings/security/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ devops_space_id: devopsSpaceId, security_space_id: securitySpaceId }),
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    alert('Error: ' + (data.error || ''));
                    if (btn) { btn.disabled = false; btn.textContent = 'Disconnect'; }
                    return;
                }
                _secMatchCache = null;
                _checkSecurityMatch(devopsSpaceId);
            })
            .catch(function(e) {
                alert('Error: ' + String(e));
                if (btn) { btn.disabled = false; btn.textContent = 'Disconnect'; }
            });
    };

    window._secCreateNew = function(devopsSpaceId) {
        var statusEl = document.getElementById('secAgentStatus');
        if (!statusEl) return;

        var btn = statusEl.querySelector('button');
        if (btn) { btn.disabled = true; btn.textContent = 'Creating...'; }

        fetch('/api/settings/security/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ devops_space_id: devopsSpaceId }),
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    alert('Error: ' + (data.error || ''));
                    if (btn) { btn.disabled = false; btn.textContent = 'Security Agent 생성 (SAST 활성화)'; }
                    return;
                }
                _secMatchCache = null;
                _checkSecurityMatch(devopsSpaceId);
            })
            .catch(function(e) {
                alert('Error: ' + String(e));
                if (btn) { btn.disabled = false; btn.textContent = 'Security Agent 생성 (SAST 활성화)'; }
            });
    };

    function _loadAccounts() {
        fetch('/api/accounts')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var sel = document.getElementById('secAcctSelect');
                if (!sel || !data.ok) return;
                (data.accounts || []).forEach(function(a) {
                    var opt = document.createElement('option');
                    opt.value = a.account_id;
                    opt.textContent = a.profile + ' (' + a.account_id + ')';
                    sel.appendChild(opt);
                });
                sel.onchange = function() {
                    if (sel.value) {
                        _loadSecSpaceOptions(sel.value);
                    } else {
                        var spaceSel = document.getElementById('secLinkSelect');
                        if (spaceSel) {
                            spaceSel.innerHTML = '<option value="">계정을 먼저 선택하세요</option>';
                            spaceSel.disabled = true;
                        }
                        var btn = document.getElementById('secLinkBtn');
                        if (btn) btn.disabled = true;
                    }
                };
            }).catch(function() {});
    }

    function _loadSecSpaceOptions(accountId) {
        var spaceSel = document.getElementById('secLinkSelect');
        if (!spaceSel) return;
        spaceSel.innerHTML = '<option value="">로딩 중...</option>';
        spaceSel.disabled = true;

        var url = '/api/settings/security/agent-spaces';
        if (accountId) url += '?account_id=' + encodeURIComponent(accountId);

        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    spaceSel.innerHTML = '<option value="">Error: ' + (data.error || '') + '</option>';
                    return;
                }
                var spaces = data.spaces || [];
                var devopsRepo = _secMatchCache && _secMatchCache.devops_repo;
                var matched = [];
                var others = [];
                spaces.forEach(function(s) {
                    if (devopsRepo && s.repo && s.repo.owner && s.repo.name &&
                        s.repo.owner === devopsRepo.owner && s.repo.name === devopsRepo.name) {
                        matched.push(s);
                    } else {
                        others.push(s);
                    }
                });
                spaceSel.innerHTML = '<option value="">Security Space 선택...</option>';
                if (matched.length > 0) {
                    var grp1 = document.createElement('optgroup');
                    grp1.label = '★ 동일 repo (' + matched.length + ')';
                    matched.forEach(function(s) {
                        var opt = document.createElement('option');
                        opt.value = s.id;
                        opt.textContent = s.name || s.id.substring(0, 12);
                        grp1.appendChild(opt);
                    });
                    spaceSel.appendChild(grp1);
                }
                if (others.length > 0) {
                    var grp2 = document.createElement('optgroup');
                    grp2.label = '기타 (' + others.length + ')';
                    others.forEach(function(s) {
                        var opt = document.createElement('option');
                        opt.value = s.id;
                        var repoLabel = s.repo ? (s.repo.owner + '/' + s.repo.name) : 'no repo';
                        opt.textContent = (s.name || s.id.substring(0, 12)) + ' — ' + repoLabel;
                        grp2.appendChild(opt);
                    });
                    spaceSel.appendChild(grp2);
                }
                spaceSel.disabled = false;
                spaceSel.onchange = function() {
                    var btn = document.getElementById('secLinkBtn');
                    if (btn) btn.disabled = !spaceSel.value;
                };
            }).catch(function() {
                spaceSel.innerHTML = '<option value="">로드 실패</option>';
            });
    }

    window._secLinkExisting = function(devopsSpaceId) {
        var sel = document.getElementById('secLinkSelect');
        var secSpaceId = sel ? sel.value : '';
        if (!secSpaceId) return;

        var acctSel = document.getElementById('secAcctSelect');
        var accountId = acctSel ? acctSel.value : '';

        var btn = document.getElementById('secLinkBtn');
        if (btn) { btn.disabled = true; btn.textContent = '연결 중...'; }

        fetch('/api/settings/security/link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ devops_space_id: devopsSpaceId, security_space_id: secSpaceId, account_id: accountId }),
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) {
                    alert('Error: ' + (data.error || ''));
                    if (btn) { btn.disabled = false; btn.textContent = '연결'; }
                    return;
                }
                _secMatchCache = null;
                _checkSecurityMatch(devopsSpaceId);
            })
            .catch(function(e) {
                alert('Error: ' + String(e));
                if (btn) { btn.disabled = false; btn.textContent = '연결'; }
            });
    };

    // ================================================================
    // Utilities
    // ================================================================
    function _esc(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function _trun(s, n) {
        if (!s) return '';
        return s.length > n ? s.slice(0, n) + '…' : s;
    }

    function _svgE(t, a) {
        var e = document.createElementNS('http://www.w3.org/2000/svg', t);
        if (a) for (var k in a) e.setAttribute(k, a[k]);
        return e;
    }
})();

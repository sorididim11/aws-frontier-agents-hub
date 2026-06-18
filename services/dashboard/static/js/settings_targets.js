// ================================================================
// SETTINGS TARGETS — Security Agent Space 연결
// ================================================================

var ST = { selectedSpaceId: '', matchData: null };

function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function initTargetsTab() {
    loadSpaces();
    loadLinks();
}

// ================================================================
// Step 1: DevOps Agent Space 목록
// ================================================================

function loadSpaces() {
    fetch('/api/settings/security/spaces')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var sel = document.getElementById('spaceSelect');
            if (!data.ok || !data.spaces.length) {
                sel.innerHTML = '<option value="">No spaces found</option>';
                return;
            }
            var html = '<option value="">-- Select DevOps Agent Space --</option>';
            data.spaces.forEach(function(s) {
                var repoLabel = s.repo ? ' (' + s.repo.owner + '/' + s.repo.name + ')' : '';
                html += '<option value="' + esc(s.id) + '">' + esc(s.name) + repoLabel + '</option>';
            });
            sel.innerHTML = html;
        })
        .catch(function(e) {
            document.getElementById('spaceSelect').innerHTML = '<option value="">Error</option>';
        });
}

function onSpaceSelected() {
    var spaceId = document.getElementById('spaceSelect').value;
    var infoEl = document.getElementById('spaceInfo');
    var matchSec = document.getElementById('matchSection');

    if (!spaceId) {
        infoEl.innerHTML = '';
        matchSec.style.display = 'none';
        return;
    }

    ST.selectedSpaceId = spaceId;
    infoEl.innerHTML = '<span class="loading"></span> Repo 정보 확인 중...';
    matchSec.style.display = 'block';
    document.getElementById('matchResult').innerHTML = '<span class="loading"></span> 매칭 확인 중...';
    document.getElementById('actionButtons').innerHTML = '';

    fetch('/api/settings/security/match/' + spaceId)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) {
                infoEl.innerHTML = '<p class="empty">Error: ' + esc(data.error || '') + '</p>';
                matchSec.style.display = 'none';
                return;
            }

            ST.matchData = data;

            // Show repo info
            if (data.devops_repo) {
                var repo = data.devops_repo;
                infoEl.innerHTML = '<div class="info-box">' +
                    '<div class="info-row"><span class="lb">Repository</span><span class="vl">' + esc(repo.owner) + '/' + esc(repo.name) + '</span></div>' +
                    '<div class="info-row"><span class="lb">Repo ID</span><span class="vl">' + esc(repo.repo_id) + '</span></div>' +
                    '</div>';
            } else {
                infoEl.innerHTML = '<p class="empty">이 Space에 GitHub repo가 연결되어 있지 않습니다.</p>';
                matchSec.style.display = 'none';
                return;
            }

            // Show match result
            renderMatchResult(data);
        })
        .catch(function(e) {
            infoEl.innerHTML = '<p class="empty">Error: ' + esc(String(e)) + '</p>';
        });
}

// ================================================================
// Step 2: Match Result + Actions
// ================================================================

function renderMatchResult(data) {
    var resultEl = document.getElementById('matchResult');
    var actionsEl = document.getElementById('actionButtons');

    if (data.match) {
        // 기존 Security Space 발견 — 연결 버튼
        var m = data.match;
        resultEl.innerHTML = '<div class="match-result found">' +
            '<strong>동일 repo로 연결된 Security Agent Space 발견</strong><br>' +
            '<span style="font-size:.58rem;">' + esc(m.security_space_name) + ' (' + esc(m.security_space_id.substring(0, 12)) + '...)</span><br>' +
            '<span style="font-size:.54rem;color:#94a3b8;">SAST: ' +
            (m.repo.leave_comments ? 'PR 코멘트 활성' : '비활성') + ' · ' +
            (m.repo.remediate_code ? '자동 수정 활성' : '비활성') +
            '</span></div>';
        actionsEl.innerHTML = '<button class="btn btn-success" onclick="linkExisting(\'' + esc(m.security_space_id) + '\')">Connect (연결)</button>';
    } else {
        // 매칭 없음 — 신규 생성 버튼
        resultEl.innerHTML = '<div class="match-result not-found">' +
            '<strong>' + esc(data.message || '연결 가능한 Security Agent Space 없음') + '</strong><br>' +
            '<span style="font-size:.54rem;">신규 생성하면 이 repo에 대해 SAST(코드 리뷰)가 자동 활성화됩니다.</span></div>';
        actionsEl.innerHTML = '<button class="btn btn-primary" onclick="createNew()">Create Security Agent (신규 생성)</button>';
    }
}

// ================================================================
// Actions
// ================================================================

function linkExisting(securitySpaceId) {
    fetch('/api/settings/security/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            devops_space_id: ST.selectedSpaceId,
            security_space_id: securitySpaceId,
        }),
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) { alert('Error: ' + (data.error || '')); return; }
            document.getElementById('actionButtons').innerHTML = '<span style="font-size:.62rem;color:#4ade80;">연결 완료</span>';
            loadLinks();
        })
        .catch(function(e) { alert('Error: ' + String(e)); });
}

function createNew() {
    var btn = document.querySelector('#actionButtons button');
    if (btn) { btn.disabled = true; btn.textContent = 'Creating...'; }

    fetch('/api/settings/security/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ devops_space_id: ST.selectedSpaceId }),
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) { alert('Error: ' + (data.error || '')); if (btn) { btn.disabled = false; btn.textContent = 'Create Security Agent'; } return; }
            var html = '<span style="font-size:.62rem;color:#4ade80;">생성 완료: ' + esc(data.space_name) + ' (' + esc(data.security_space_id.substring(0, 12)) + '...)</span>';
            if (data.repo_registered) {
                html += '<br><span style="font-size:.54rem;color:#94a3b8;">SAST 코드 리뷰 활성화됨</span>';
            }
            document.getElementById('actionButtons').innerHTML = html;
            loadLinks();
        })
        .catch(function(e) { alert('Error: ' + String(e)); if (btn) { btn.disabled = false; btn.textContent = 'Create Security Agent'; } });
}

// ================================================================
// Current Links
// ================================================================

function loadLinks() {
    var el = document.getElementById('currentLinks');
    fetch('/api/settings/security/links')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok || !data.links.length) {
                el.innerHTML = '<p class="empty">연결된 Security Agent Space가 없습니다.</p>';
                return;
            }
            var html = '';
            data.links.forEach(function(link) {
                var repo = link.repo || {};
                html += '<div class="link-card">';
                html += '<div class="info">';
                html += '<div class="title">' + esc(repo.owner || '') + '/' + esc(repo.name || '') + '</div>';
                html += '<div class="sub">DevOps: ' + esc((link.devops_space_id || '').substring(0, 12)) + '... → Security: ' + esc((link.security_space_id || '').substring(0, 12)) + '...</div>';
                html += '</div>';
                html += '<span class="badge">SAST Active</span>';
                html += '</div>';
            });
            el.innerHTML = html;
        })
        .catch(function(e) {
            el.innerHTML = '<p class="empty">Error: ' + esc(String(e)) + '</p>';
        });
}

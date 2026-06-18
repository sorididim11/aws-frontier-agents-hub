/* skills_tab.js — Unified Skill Library (Space Skills + Library: AWS catalog + recommendations) */
var SKILLS = {deployed: [], selected: null, _loaded: false, _deployedLoaded: false};
var SKILL_LIB = {catalog: null, recommendations: [], _catalogLoaded: false, _recLoaded: false};

function _skillSpaceId() {
    return (typeof SELECTED !== 'undefined' && SELECTED) ? SELECTED : '';
}

function skillsInit() {
    var spaceId = _skillSpaceId();
    if (SKILLS._loaded && SKILLS._spaceId === spaceId) return;
    SKILLS._loaded = true;
    SKILLS._spaceId = spaceId;
    skillRefresh();
    _skillLoadCatalog();
    _skillLoadRecommendations();
}

function skillRefresh() {
    var spaceId = _skillSpaceId();
    if (!spaceId) return;
    fetch('/api/skills?space_id=' + spaceId).then(function(r){return r.json()}).then(function(data){
        if (!data.ok) return;
        SKILLS.deployed = data.skills || [];
        SKILLS._deployedLoaded = true;
        _skillRenderDeployed();
        skillLibRender();
    });
}

function skillForceRefresh() {
    var spaceId = _skillSpaceId();
    if (!spaceId) { _skillToast('Select a Space first', true); return; }
    var btn = document.getElementById('btnSkillRefresh');
    btn.disabled = true; btn.textContent = 'Syncing...';
    fetch('/api/skills/refresh', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({space_id: spaceId})
    }).then(function(r){return r.json()}).then(function(data){
        btn.disabled = false; btn.textContent = 'Refresh';
        if (data.ok) {
            SKILLS.deployed = data.skills || [];
            _skillRenderDeployed();
            skillLibRender();
            _skillToast('새로고침 완료 (' + SKILLS.deployed.length + '개)');
        } else { _skillToast('Sync failed: ' + (data.error||''), true); }
    }).catch(function(){ btn.disabled = false; btn.textContent = 'Refresh'; });
}

/* ─── Catalog & Recommendations loading ─── */

function _skillLoadCatalog() {
    if (SKILL_LIB._catalogLoaded) return;
    fetch('/api/skills/catalog').then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            SKILL_LIB.catalog = data;
            SKILL_LIB._catalogLoaded = true;
            _skillPopulateCategoryFilter();
            skillLibRender();
        }
    }).catch(function(){});
}

function _skillPopulateCategoryFilter() {
    var sel = document.getElementById('skillLibCategory');
    if (!sel) return;
    var cats = SKILL_LIB.catalog ? Object.keys(SKILL_LIB.catalog.categories || {}).sort() : [];
    sel.innerHTML = '<option value="all">전체 카테고리</option>';
    cats.forEach(function(cat){
        var count = (SKILL_LIB.catalog.categories[cat] || []).length;
        sel.innerHTML += '<option value="' + _escHtml(cat) + '">' + _escHtml(cat) + ' (' + count + ')</option>';
    });
}

function _skillLoadRecommendations() {
    var spaceId = _skillSpaceId();
    if (!spaceId) return;
    fetch('/api/skills/recommend?space_id=' + spaceId).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            SKILL_LIB.recommendations = data.recommendations || [];
            SKILL_LIB._recLoaded = true;
            skillLibRender();
        }
    }).catch(function(){});
}

function skillCatalogRefresh() {
    SKILL_LIB._catalogLoaded = false;
    SKILL_LIB.catalog = null;
    fetch('/api/skills/catalog/refresh', {method:'POST'}).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            SKILL_LIB.catalog = data;
            SKILL_LIB._catalogLoaded = true;
            skillLibRender();
            _skillToast('카탈로그 갱신 완료');
        }
    }).catch(function(){ _skillToast('카탈로그 갱신 실패', true); });
}

/* ─── Unified Skill Library rendering ─── */

function skillLibRender() {
    var el = document.getElementById('skillPoolList');
    var countEl = document.getElementById('skillPoolCount');
    if (!el) return;

    var query = (document.getElementById('skillLibSearch') || {}).value || '';
    query = query.toLowerCase().trim();
    var filter = (document.getElementById('skillLibFilter') || {}).value || 'all';
    var catFilter = (document.getElementById('skillLibCategory') || {}).value || 'all';

    var items = _skillBuildUnifiedList();

    if (filter === 'recommend') {
        items = items.filter(function(i){ return i._recommended; });
    } else if (filter === 'user') {
        items = items.filter(function(i){ return i._source === 'user'; });
    } else if (filter === 'catalog') {
        items = items.filter(function(i){ return i._source === 'catalog'; });
    }

    if (catFilter !== 'all') {
        items = items.filter(function(i){
            return (i.category || '') === catFilter;
        });
    }

    if (query) {
        items = items.filter(function(i){
            return i.name.toLowerCase().indexOf(query) >= 0 ||
                   (i.description||'').toLowerCase().indexOf(query) >= 0 ||
                   (i.service_name||'').toLowerCase().indexOf(query) >= 0 ||
                   (i.category||'').toLowerCase().indexOf(query) >= 0 ||
                   (i.folder_name||'').toLowerCase().indexOf(query) >= 0;
        });
    }

    items.sort(function(a, b){
        if (a._recommended && !b._recommended) return -1;
        if (!a._recommended && b._recommended) return 1;
        if (a._source === 'user' && b._source !== 'user') return -1;
        if (a._source !== 'user' && b._source === 'user') return 1;
        return (a.name||a.folder_name||'').localeCompare(b.name||b.folder_name||'');
    });

    countEl.textContent = '(' + items.length + ')';

    if (!items.length) {
        var msg;
        if (query) {
            msg = '검색 결과 없음';
        } else if (filter === 'user') {
            if (!SKILLS._deployedLoaded) {
                msg = '스킬 로딩 중...';
            } else {
                msg = '사용자 정의 스킬 없음 — 상단 \'+ 새 스킬\' 버튼으로 생성하세요';
            }
        } else if (!SKILL_LIB._catalogLoaded) {
            msg = '스킬 라이브러리 로딩 중...';
        } else {
            msg = '등록 가능한 스킬 없음';
        }
        el.innerHTML = '<div style="text-align:center;padding:40px;color:#475569;font-size:.68rem">' + msg + '</div>';
        return;
    }

    var html = '';
    items.forEach(function(item){ html += _skillLibCard(item); });
    el.innerHTML = html;
}

function _skillBuildUnifiedList() {
    var items = [];
    var deployedNames = {};
    SKILLS.deployed.forEach(function(d){ deployedNames[d.name] = true; });

    var recFolders = {};
    SKILL_LIB.recommendations.forEach(function(r){
        recFolders[r.folder_name] = r;
    });

    // User-created skills (skill_type USER, deployed with valid asset ID)
    SKILLS.deployed.forEach(function(sk){
        if ((sk.skill_type || 'USER') === 'USER' && sk.knowledge_item_id) {
            items.push({
                name: sk.name,
                description: sk.description || '',
                agent_types: sk.agent_types || [],
                knowledge_item_id: sk.knowledge_item_id,
                category: '사용자 정의',
                _source: 'user',
                _deployed: true,
                _recommended: false
            });
        }
    });

    // Catalog skills (excluding already deployed)
    if (SKILL_LIB.catalog && SKILL_LIB.catalog.skills) {
        var skills = SKILL_LIB.catalog.skills;
        Object.keys(skills).forEach(function(folder){
            var sk = skills[folder];
            if (deployedNames[folder]) return;

            var rec = recFolders[folder];
            items.push({
                name: sk.service_name || folder,
                folder_name: folder,
                description: sk.description || '',
                service_name: sk.service_name || '',
                category: sk.category || '',
                reference_count: sk.reference_count || 0,
                domains: sk.domains || [],
                _source: 'catalog',
                _deployed: false,
                _recommended: !!rec,
                _matchReason: rec ? rec.match_reason : ''
            });
        });
    }

    return items;
}

function _skillLibCard(item) {
    var badges = '';
    if (item._recommended) {
        badges += '<span class="skill-badge recommend">★ 추천</span>';
    }
    if (item._source === 'user') {
        badges += '<span class="skill-badge source-local">사용자 정의</span>';
    } else {
        badges += '<span class="skill-badge source-aws">AWS</span>';
    }
    if (item._deployed) {
        badges += '<span class="skill-badge deployed">등록됨</span>';
    }
    if (item.category && item.category !== '사용자 정의') {
        badges += '<span class="skill-badge category">' + _escHtml(item.category) + '</span>';
    }
    if (item.reference_count) {
        badges += '<span class="skill-agent-type">' + item.reference_count + ' runbooks</span>';
    }

    var buttons = '';
    if (item._source === 'user') {
        buttons += '<button class="arch-btn" style="font-size:.5rem;padding:3px 8px;background:#1e3a5f;color:#38bdf8" onclick="event.stopPropagation();skillEditDeployed(\'' + _escHtml(item.knowledge_item_id) + '\')">Edit</button>';
    } else if (!item._deployed) {
        buttons += '<button class="arch-btn arch-btn-primary" style="font-size:.5rem;padding:3px 10px" onclick="event.stopPropagation();catalogDeploy(\'' + _escHtml(item.folder_name) + '\')">Register</button>';
        buttons += '<button class="arch-btn" style="font-size:.5rem;padding:3px 8px;background:#334155;color:#94a3b8" onclick="event.stopPropagation();catalogShowDetail(\'' + _escHtml(item.folder_name) + '\')">상세</button>';
    }

    var matchLine = '';
    if (item._matchReason) {
        matchLine = '<div style="font-size:.5rem;color:#38bdf8;margin-top:2px">' + _escHtml(item._matchReason) + '</div>';
    }

    var clickFn = item._source === 'user'
        ? 'skillSelectDeployed(\'' + _escHtml(item.name) + '\')'
        : 'catalogShowDetail(\'' + _escHtml(item.folder_name) + '\')';

    return '<div class="skill-card" onclick="' + clickFn + '">' +
        '<div class="skill-card-info">' +
            '<div class="skill-card-name">' + _escHtml(item.name) + '</div>' +
            '<div class="skill-card-desc">' + _escHtml(item.description || '').substring(0, 120) + '</div>' +
            matchLine +
            '<div class="skill-card-meta">' + badges + '</div>' +
        '</div>' +
        '<div style="display:flex;gap:4px;flex-shrink:0;align-items:center">' + buttons + '</div>' +
    '</div>';
}

/* ─── Left panel: Space Skills (deployed) ─── */
function _skillRenderDeployed() {
    var el = document.getElementById('skillDeployedList');
    var cnt = document.getElementById('skillDeployedCount');
    cnt.textContent = '(' + SKILLS.deployed.length + ')';

    if (!SKILLS.deployed.length) {
        el.innerHTML = '<div class="skill-empty">No skills registered in this Space.<br><span style="font-size:.56rem">Register from Skill Library on the right</span></div>';
        return;
    }

    var html = '';
    SKILLS.deployed.forEach(function(sk) {
        if (!sk.knowledge_item_id) return;
        html += _skillDeployedCard(sk);
    });
    el.innerHTML = html;
}

function _skillDeployedCard(sk) {
    var badges = '';
    badges += sk.status === 'ACTIVE' ? '<span class="skill-badge active">ACTIVE</span>' :
              '<span class="skill-badge inactive">INACTIVE</span>';
    if (sk.skill_type === 'USER') {
        badges += '<span class="skill-badge source-local">사용자</span>';
    }

    var types = (sk.agent_types || []).map(function(t){ return '<span class="skill-agent-type">' + _escHtml(t) + '</span>'; }).join('');

    return '<div class="skill-card" onclick="skillSelectDeployed(\'' + _escHtml(sk.name) + '\')">' +
        '<div class="skill-card-info">' +
            '<div class="skill-card-name">' + _escHtml(sk.name) + '</div>' +
            '<div class="skill-card-desc">' + _escHtml(sk.description || '') + '</div>' +
            '<div class="skill-card-meta">' + badges + types + '</div>' +
        '</div>' +
    '</div>';
}

/* ─── Detail modal ─── */
function skillSelectDeployed(name) {
    var sk = SKILLS.deployed.find(function(s){ return s.name === name; });
    if (!sk || !sk.knowledge_item_id) return;
    _skillOpenDetail(sk);
}

function skillEditDeployed(kid) {
    var sk = SKILLS.deployed.find(function(s){ return s.knowledge_item_id === kid; });
    if (sk) {
        _skillOpenDetail(sk);
        setTimeout(function(){ _skillEnterEditMode(); }, 100);
    }
}

function _skillOpenDetail(sk) {
    SKILLS.selected = sk;
    SKILLS._editMode = false;
    var overlay = document.getElementById('skillDetailOverlay');
    overlay.style.display = 'flex';
    _skillRenderViewMode();
}

function _skillRenderViewMode() {
    var sk = SKILLS.selected;
    SKILLS._editMode = false;
    document.getElementById('skillDetailTitle').textContent = sk.name;

    var body = document.getElementById('skillDetailBody');
    body.innerHTML = '<div id="skillContentArea" class="skill-content-area" style="min-height:300px">Loading...</div>';

    if (sk.knowledge_item_id) {
        fetch('/api/skills/' + sk.knowledge_item_id + '?space_id=' + _skillSpaceId())
            .then(function(r){return r.json()}).then(function(data){
                var el = document.getElementById('skillContentArea');
                if (!el) return;
                if (!data.ok) { el.textContent = 'Error: ' + (data.error||''); return; }
                var content = data.content || '';
                var mdBody = content.replace(/^---[\s\S]*?---\s*/, '');
                if (typeof marked !== 'undefined') {
                    el.innerHTML = marked.parse(mdBody);
                    el.classList.add('skill-md-rendered');
                } else {
                    el.textContent = mdBody;
                }
                SKILLS._currentContent = content;
            }).catch(function(err){
                var el = document.getElementById('skillContentArea');
                if (el) el.textContent = 'Error loading content: ' + err.message;
            });
    } else {
        document.getElementById('skillContentArea').textContent = '(content not available — no asset ID)';
    }

    var agentTypes = '';
    if (sk.knowledge_item_id) {
        agentTypes = '<div class="skill-agent-types-row" style="margin-top:10px">Agent Types: ' + _skillAgentTypeCheckboxes(sk) + '</div>';
        body.innerHTML += agentTypes;
    }
    _skillRenderModalFooter();
}

function _skillEnterEditMode() {
    var sk = SKILLS.selected;
    if (!sk || !sk.knowledge_item_id) return;
    SKILLS._editMode = true;
    document.getElementById('skillDetailTitle').textContent = sk.name + ' — Edit';

    var body = document.getElementById('skillDetailBody');
    body.innerHTML = '<textarea id="skillEditor" class="skill-content-area" style="min-height:360px;font-family:monospace;font-size:.52rem">Loading...</textarea>';

    fetch('/api/skills/' + sk.knowledge_item_id + '?space_id=' + _skillSpaceId())
        .then(function(r){return r.json()}).then(function(data){
            var ed = document.getElementById('skillEditor');
            if (ed) ed.value = data.ok ? data.content : 'Error loading content';
        });
    _skillRenderEditFooter();
}

function _skillExitEditMode() {
    SKILLS._editMode = false;
    _skillRenderViewMode();
}

function _skillRenderModalFooter() {
    var sk = SKILLS.selected;
    var el = document.getElementById('skillDetailActions');
    var btns = '';

    if (sk.knowledge_item_id) {
        btns += '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;background:#1e3a5f;color:#38bdf8;font-weight:600" onclick="_skillEnterEditMode()">Edit</button>';
    }

    btns += '<span style="flex:1"></span>';

    if (sk.knowledge_item_id) {
        var toggleLabel = sk.status === 'ACTIVE' ? 'Deactivate' : 'Activate';
        var toggleStyle = sk.status === 'ACTIVE' ? 'background:#334155;color:#94a3b8' : 'background:#064e3b;color:#6ee7b7';
        btns += '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;' + toggleStyle + '" onclick="skillToggle(\'' + sk.knowledge_item_id + '\',' + (sk.status !== 'ACTIVE') + ')">' + toggleLabel + '</button>';
        btns += '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;background:#451a1a;color:#fca5a5;border-color:#7f1d1d" onclick="skillDelete(\'' + sk.knowledge_item_id + '\',\'' + _escHtml(sk.name) + '\')">Delete</button>';
    }

    el.innerHTML = btns;
}

function _skillRenderEditFooter() {
    var sk = SKILLS.selected;
    var el = document.getElementById('skillDetailActions');
    el.innerHTML = '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;background:#334155;color:#94a3b8" onclick="_skillExitEditMode()">Cancel</button>' +
        '<span style="flex:1"></span>' +
        '<button class="arch-btn arch-btn-primary" style="font-size:.58rem;padding:5px 14px;font-weight:600" onclick="skillSaveContent()">Save</button>';
}

function _skillAgentTypeCheckboxes(sk) {
    var allTypes = ['GENERIC', 'INCIDENT_TRIAGE', 'INCIDENT_RCA'];
    var current = (sk.agent_types || []).map(function(t){
        var map = {Generic:'GENERIC',Triage:'INCIDENT_TRIAGE',RootCauseAnalysis:'INCIDENT_RCA','Incident RCA':'INCIDENT_RCA'};
        return map[t] || t;
    });
    return allTypes.map(function(t){
        var checked = current.indexOf(t) >= 0 ? ' checked' : '';
        var extra = t === 'GENERIC' ? ' checked onclick="return false" title="Required" style="opacity:.7"' : ' onchange="skillAgentTypeChanged()"';
        return '<label style="font-size:.5rem;color:#cbd5e1;margin-right:8px;cursor:pointer">' +
            '<input type="checkbox" class="skill-type-cb" value="' + t + '"' + (t === 'GENERIC' ? extra : checked + extra) + '> ' + t +
        '</label>';
    }).join('');
}

function skillAgentTypeChanged() {
    var sk = SKILLS.selected;
    if (!sk || !sk.knowledge_item_id) return;
    var cbs = document.querySelectorAll('.skill-type-cb:checked');
    var types = [];
    cbs.forEach(function(cb){ types.push(cb.value); });
    if (types.indexOf('GENERIC') < 0) types.unshift('GENERIC');

    fetch('/api/skills/update-agent-types', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: _skillSpaceId(), knowledge_item_id: sk.knowledge_item_id, agent_types: types})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) { _skillToast('Agent types updated'); }
        else { _skillToast('Failed: ' + (data.error||''), true); }
    });
}

function skillCloseDetail() {
    document.getElementById('skillDetailOverlay').style.display = 'none';
    SKILLS.selected = null;
    SKILLS._editMode = false;
}

/* ─── Actions ─── */

function skillSaveContent() {
    var sk = SKILLS.selected;
    if (!sk || !sk.knowledge_item_id) return;
    var ed = document.getElementById('skillEditor');
    if (!ed) return;
    var content = ed.value.trim();
    if (!content) { _skillToast('Content is empty', true); return; }

    fetch('/api/skills/' + sk.knowledge_item_id, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: _skillSpaceId(), content: content})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            _skillToast(sk.name + ' 업데이트 완료');
            skillCloseDetail();
            skillRefresh();
        } else { _skillToast('Save failed: ' + (data.error||''), true); }
    });
}

function skillCreate() {
    var spaceId = _skillSpaceId();
    if (!spaceId) { _skillToast('Space를 먼저 선택하세요.', true); return; }

    var overlay = document.getElementById('skillDetailOverlay');
    overlay.style.display = 'flex';
    document.getElementById('skillDetailTitle').textContent = '새 스킬 작성';
    var body = document.getElementById('skillDetailBody');
    body.innerHTML = '<textarea id="skillEditor" class="skill-content-area" style="min-height:360px;font-family:monospace;font-size:.52rem" placeholder="---\nname: my-skill\ndescription: 설명\nagent_types:\n  - Generic\n---\n\n# 스킬 내용\n"></textarea>';

    var el = document.getElementById('skillDetailActions');
    el.innerHTML = '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;background:#334155;color:#94a3b8" onclick="skillCloseDetail()">Cancel</button>' +
        '<span style="flex:1"></span>' +
        '<button class="arch-btn" style="font-size:.58rem;padding:5px 14px;background:#1e3a5f;color:#38bdf8" onclick="skillGenerate()">AI 생성</button>' +
        '<button class="arch-btn arch-btn-primary" style="font-size:.58rem;padding:5px 14px;font-weight:600" onclick="skillCreateSubmit()">등록</button>';
}

function skillCreateSubmit() {
    var ed = document.getElementById('skillEditor');
    if (!ed) return;
    var content = ed.value.trim();
    if (!content) { _skillToast('Content is empty', true); return; }

    fetch('/api/skills/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: _skillSpaceId(), content: content})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            _skillToast(data.name + ' 등록 완료');
            skillCloseDetail();
            skillRefresh();
        } else { _skillToast('등록 실패: ' + (data.error||''), true); }
    });
}

function skillGenerate() {
    var prompt = window.prompt('스킬 용도를 설명하세요:');
    if (!prompt) return;
    _skillToast('AI 생성 중...');

    fetch('/api/skills/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt: prompt})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            var ed = document.getElementById('skillEditor');
            if (ed) ed.value = data.content;
            _skillToast('AI 초안 생성 완료 — 수정 후 등록하세요');
        } else { _skillToast('생성 실패: ' + (data.error||''), true); }
    });
}

function skillToggle(kid, enabled) {
    if (!kid) return;
    var skName = '';
    SKILLS.deployed.forEach(function(s){
        if (s.knowledge_item_id === kid) { s.status = enabled ? 'ACTIVE' : 'INACTIVE'; skName = s.name; }
    });
    _skillRenderDeployed();
    skillCloseDetail();
    fetch('/api/skills/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({space_id: _skillSpaceId(), knowledge_item_id: kid, enabled: enabled})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) { _skillToast(skName + ' ' + (enabled ? 'activated' : 'deactivated')); }
        else { _skillToast('Toggle failed', true); skillRefresh(); }
    });
}

function skillDelete(kid, name) {
    if (!kid) return;
    if (!confirm(name + '을(를) 삭제하시겠습니까?')) return;
    skillCloseDetail();
    fetch('/api/skills/' + kid + '?space_id=' + _skillSpaceId(), {method: 'DELETE'})
        .then(function(r){return r.json()}).then(function(data){
            if (data.ok) {
                _skillToast(name + ' 삭제 완료');
                SKILLS.deployed = SKILLS.deployed.filter(function(s){ return s.knowledge_item_id !== kid; });
            } else {
                _skillToast('삭제 실패: ' + (data.error||''), true);
            }
            _skillRenderDeployed();
            skillLibRender();
        });
}

/* ─── Catalog detail & deploy ─── */

function catalogShowDetail(folder) {
    var overlay = document.getElementById('skillDetailOverlay');
    var title = document.getElementById('skillDetailTitle');
    var body = document.getElementById('skillDetailBody');
    var actions = document.getElementById('skillDetailActions');

    title.textContent = folder;
    body.innerHTML = '<div style="text-align:center;padding:20px;color:#64748b">로딩 중...</div>';
    actions.innerHTML = '';
    overlay.style.display = 'flex';

    fetch('/api/skills/catalog/' + folder).then(function(r){return r.json()}).then(function(data){
        if (!data.ok) { body.innerHTML = '<div style="color:#ef4444">' + _escHtml(data.error||'Error') + '</div>'; return; }

        var html = '';
        if (typeof marked !== 'undefined') {
            var content = (data.skill_md || '').replace(/^---[\s\S]*?---\s*/, '');
            html = '<div class="skill-md-rendered" style="font-size:.64rem;max-height:400px;overflow-y:auto;background:#0f172a;padding:12px;border-radius:8px">' + marked.parse(content) + '</div>';
        } else {
            html = '<pre style="font-size:.52rem;white-space:pre-wrap;max-height:400px;overflow-y:auto;background:#0f172a;padding:12px;border-radius:8px;color:#e2e8f0">' + _escHtml(data.skill_md || '') + '</pre>';
        }

        if (data.references && data.references.length) {
            html += '<div style="margin-top:12px;font-size:.64rem;color:#94a3b8;font-weight:600">References (' + data.references.length + ')</div>';
            html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">';
            data.references.forEach(function(ref){
                html += '<span style="font-size:.54rem;background:#334155;color:#cbd5e1;padding:3px 8px;border-radius:4px">' + _escHtml(ref) + '</span>';
            });
            html += '</div>';
        }
        body.innerHTML = html;

        var isDeployed = SKILLS.deployed.some(function(d){ return d.name === folder; });
        if (isDeployed) {
            actions.innerHTML = '<span style="font-size:.62rem;color:#6ee7b7">이미 등록됨</span><span style="flex:1"></span>' +
                '<button class="arch-btn" onclick="skillCloseDetail()" style="font-size:.64rem;padding:6px 18px;background:#334155;color:#94a3b8">닫기</button>';
        } else {
            actions.innerHTML = '<button class="arch-btn arch-btn-primary" onclick="catalogDeploy(\'' + _escHtml(folder) + '\')" style="font-size:.64rem;padding:6px 18px">등록</button>' +
                '<span style="flex:1"></span>' +
                '<button class="arch-btn" onclick="skillCloseDetail()" style="font-size:.64rem;padding:6px 18px;background:#334155;color:#94a3b8">닫기</button>';
        }
    });
}

function catalogDeploy(folder) {
    var spaceId = _skillSpaceId();
    if (!spaceId) { _skillToast('Space를 먼저 선택하세요.', true); return; }

    var actions = document.getElementById('skillDetailActions');
    actions.innerHTML = '<span style="font-size:.62rem;color:#64748b;animation:pulse 1.5s infinite">등록 중...</span>';

    fetch('/api/skills/catalog/' + folder + '/deploy', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({space_id: spaceId})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            actions.innerHTML = '<span style="font-size:.62rem;color:#22c55e">등록 완료</span>';
            _skillToast(folder + ' 등록 완료');
            skillRefresh();
            SKILL_LIB._recLoaded = false;
            _skillLoadRecommendations();
        } else {
            actions.innerHTML = '<span style="font-size:.62rem;color:#ef4444">실패: ' + _escHtml(data.error||'') + '</span>';
        }
    });
}

/* ─── Recommend batch deploy ─── */

function recommendApplyAll() {
    var spaceId = _skillSpaceId();
    if (!spaceId) { _skillToast('Space를 먼저 선택하세요.', true); return; }
    var folders = SKILL_LIB.recommendations.map(function(i){ return i.folder_name; });
    if (!folders.length) { _skillToast('추천 스킬 없음'); return; }
    if (!confirm(folders.length + '개 추천 스킬을 모두 등록하시겠습니까?')) return;

    _skillToast('추천 스킬 일괄 등록 중...');
    fetch('/api/skills/recommend/apply', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({space_id: spaceId, folder_names: folders})
    }).then(function(r){return r.json()}).then(function(data){
        if (data.ok) {
            _skillToast('추천 스킬 등록 완료');
            skillRefresh();
            SKILL_LIB._recLoaded = false;
            _skillLoadRecommendations();
        } else {
            var fails = Object.keys(data.results||{}).filter(function(k){ return !data.results[k].ok; });
            _skillToast('일부 실패: ' + fails.join(', '), true);
        }
    });
}

/* ─── Helpers ─── */
function _skillToast(msg, isErr) {
    var el = document.getElementById('tooltip');
    if (!el) return;
    el.textContent = msg;
    el.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:10px 24px;border-radius:8px;font-size:.7rem;z-index:99999;pointer-events:none;font-weight:500;' +
        (isErr ? 'background:#7f1d1d;color:#fca5a5;border:1px solid #991b1b' : 'background:#1e3a5f;color:#38bdf8;border:1px solid #1e40af');
    el.style.display = 'block';
    if (SKILLS._toastTimer) clearTimeout(SKILLS._toastTimer);
    SKILLS._toastTimer = setTimeout(function(){ el.style.display = 'none'; }, 4000);
}

function _escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

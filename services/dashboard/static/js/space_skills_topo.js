// ================================================================
// Skills — 토폴로지 다이어그램에 Skills 노드 추가
// DevOps Agent 박스 아래에 배치 (Security Agent는 위)
// ================================================================

(function() {
    var _skillsCache = null;

    var _prevOnRenderTopo = window._onRenderTopo;
    window._onRenderTopo = function() {
        if (_prevOnRenderTopo) _prevOnRenderTopo();
        _addSkillsNode();
    };

    function _addSkillsNode() {
        var svg = document.getElementById('topoSvg');
        if (!svg || !SELECTED) return;

        var existing = document.getElementById('skillsTopoNode');
        if (existing) existing.remove();

        if (_skillsCache) {
            _drawSkillsNode(svg, _skillsCache);
        } else {
            fetch('/api/skills?space_id=' + encodeURIComponent(SELECTED))
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (!data.ok) return;
                    var deployed = (data.skills || []).filter(function(s) { return s.sync_status !== 'local-only'; });
                    if (!deployed.length && !data.cached) {
                        fetch('/api/skills/refresh', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({space_id: SELECTED})})
                            .then(function(r) { return r.json(); })
                            .then(function(d2) {
                                if (!d2.ok) return;
                                var dep2 = (d2.skills || []).filter(function(s) { return s.sync_status !== 'local-only'; });
                                _skillsCache = dep2;
                                _drawSkillsNode(svg, dep2);
                            }).catch(function() {});
                        return;
                    }
                    _skillsCache = deployed;
                    _drawSkillsNode(svg, deployed);
                }).catch(function() {});
        }
    }

    function _drawSkillsNode(svg, skills) {
        if (!skills || !skills.length) return;
        var vb = svg.getAttribute('viewBox');
        if (!vb) return;
        var parts = vb.split(' ');
        var W = parseFloat(parts[2]);
        var H = parseFloat(parts[3]);
        var centerX = W / 2;
        var cCenterY = H / 2;

        var nW = 160, nH = 50;
        var nX = centerX - nW / 2;
        var nY = cCenterY + 55;

        var g = _svgE('g', {id: 'skillsTopoNode', style: 'cursor:pointer'});

        // Edge: DevOps Agent → Skills
        var x1 = centerX, y1 = cCenterY + 35;
        var x2 = centerX, y2 = nY;
        var my = (y1 + y2) / 2;
        g.appendChild(_svgE('path', {
            d: 'M ' + x1 + ' ' + y1 + ' C ' + x1 + ' ' + my + ', ' + x2 + ' ' + my + ', ' + x2 + ' ' + y2,
            fill: 'none', stroke: '#8b5cf6', 'stroke-width': '2', 'stroke-opacity': '0.6', 'stroke-dasharray': '4,3'
        }));

        // Box
        g.appendChild(_svgE('rect', {
            x: nX, y: nY, width: nW, height: nH, rx: '10',
            fill: '#1a1025', stroke: '#8b5cf6', 'stroke-width': '2'
        }));

        // Title
        var activeCount = skills.filter(function(s) { return s.status === 'ACTIVE'; }).length;
        var t1 = _svgE('text', {x: nX + 10, y: nY + 18, 'font-size': '11', fill: '#c4b5fd', 'font-weight': '600'});
        t1.textContent = 'Skills (' + skills.length + ')';
        g.appendChild(t1);

        // Skill names (max 3)
        var show = skills.slice(0, 3);
        var nameStr = show.map(function(s) { return s.name; }).join(', ');
        if (skills.length > 3) nameStr += ' +' + (skills.length - 3);
        var t2 = _svgE('text', {x: nX + 10, y: nY + 32, 'font-size': '7.5', fill: '#94a3b8'});
        t2.textContent = nameStr.length > 28 ? nameStr.slice(0, 28) + '…' : nameStr;
        g.appendChild(t2);

        // Active status
        var statusColor = activeCount > 0 ? '#4ade80' : '#fbbf24';
        var t3 = _svgE('text', {x: nX + 10, y: nY + 44, 'font-size': '7', fill: statusColor});
        t3.textContent = activeCount + ' Active / ' + (skills.length - activeCount) + ' Inactive';
        g.appendChild(t3);

        // Click → Skills 탭으로 이동
        g.addEventListener('click', function() {
            switchTab('skills');
        });

        // Tooltip
        g.addEventListener('mouseenter', function(ev) {
            var tip = '<div class="tt">Skills</div>';
            skills.forEach(function(s) {
                var c = s.status === 'ACTIVE' ? '#4ade80' : '#fbbf24';
                tip += '<span style="color:' + c + '">' + _esc(s.name) + '</span><br>';
            });
            tip += '<span style="font-size:.5rem;color:#64748b">클릭하여 Skills 탭으로 이동</span>';
            showTip(ev, tip);
        });
        g.addEventListener('mousemove', moveTip);
        g.addEventListener('mouseleave', hideTip);

        svg.appendChild(g);
    }

    function _esc(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function _svgE(t, a) {
        var e = document.createElementNS('http://www.w3.org/2000/svg', t);
        if (a) for (var k in a) e.setAttribute(k, a[k]);
        return e;
    }

    // Space 변경 시 캐시 초기화
    var _origSelectSpace = window.selectSpace;
    if (_origSelectSpace) {
        window.selectSpace = function() {
            _skillsCache = null;
            return _origSelectSpace.apply(this, arguments);
        };
    }
})();

// ================================================================
// Security Badges — 토폴로지 SVG에 finding count badge 추가
// _onRenderTopo를 체이닝하여 기존 space_security.js 미수정으로 동작
// ================================================================

(function() {
    var _origOnRenderTopo = window._onRenderTopo;
    var _origOnShowOverview = window._onShowOverview;
    var _attackPathCache = null;

    window._onRenderTopo = function() {
        if (_origOnRenderTopo) _origOnRenderTopo();
        _addFindingBadges();
    };

    window._onShowOverview = function() {
        if (_origOnShowOverview) _origOnShowOverview();
        _addInsightsLink();
    };

    // ================================================================
    // Topology: 서비스 노드에 finding badge 추가
    // ================================================================
    function _addFindingBadges() {
        var svg = document.getElementById('topoSvg');
        if (!svg) return;

        var existing = svg.querySelectorAll('.sec-finding-badge');
        for (var i = 0; i < existing.length; i++) existing[i].remove();

        if (_attackPathCache) {
            _drawBadges(svg, _attackPathCache);
        } else {
            fetch('/api/security/insights/attack-paths')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.ok) {
                        _attackPathCache = data.services || [];
                        _drawBadges(svg, _attackPathCache);
                    }
                })
                .catch(function() {});
        }
    }

    function _drawBadges(svg, services) {
        if (!services || !services.length) return;

        var texts = svg.querySelectorAll('text');
        var serviceNodeMap = {};
        for (var i = 0; i < texts.length; i++) {
            var t = texts[i];
            var name = (t.textContent || '').toLowerCase().trim();
            if (name && t.parentElement) {
                serviceNodeMap[name] = t;
            }
        }

        for (var s = 0; s < services.length; s++) {
            var svc = services[s];
            var svcName = (svc.service_name || '').toLowerCase();
            var textEl = serviceNodeMap[svcName];
            if (!textEl || !svc.findings_count) continue;

            var x = parseFloat(textEl.getAttribute('x') || 0);
            var y = parseFloat(textEl.getAttribute('y') || 0);

            var rects = textEl.parentElement.querySelectorAll('rect');
            var nodeRect = rects.length ? rects[0] : null;
            var bx = x, by = y - 20;
            if (nodeRect) {
                bx = parseFloat(nodeRect.getAttribute('x') || 0) + parseFloat(nodeRect.getAttribute('width') || 0) - 8;
                by = parseFloat(nodeRect.getAttribute('y') || 0) - 4;
            }

            var g = _svgEl('g', {'class': 'sec-finding-badge', style: 'cursor:pointer'});

            var riskColor = _riskColor(svc.max_risk);
            g.appendChild(_svgEl('circle', {
                cx: bx, cy: by, r: '8',
                fill: riskColor, 'fill-opacity': '0.9', stroke: '#0f172a', 'stroke-width': '1.5'
            }));

            var countText = _svgEl('text', {
                x: bx, y: by + 3.5,
                'font-size': '8', fill: '#fff', 'font-weight': '700',
                'text-anchor': 'middle', 'dominant-baseline': 'middle'
            });
            countText.textContent = svc.findings_count;
            g.appendChild(countText);

            g.addEventListener('click', (function(svcData) {
                return function() {
                    window.open('/security/insights/' + encodeURIComponent(SELECTED), '_blank');
                };
            })(svc));

            g.addEventListener('mouseenter', (function(svcData) {
                return function(ev) {
                    if (typeof showTip === 'function') {
                        var tip = '<div class="tt">' + _esc(svcData.service_name) + '</div>';
                        tip += '취약점 ' + svcData.findings_count + '개';
                        tip += '<br>최고 위험: ' + svcData.max_risk;
                        for (var f = 0; f < Math.min(svcData.findings.length, 3); f++) {
                            tip += '<br>· ' + _esc(svcData.findings[f].name || svcData.findings[f].risk_type);
                        }
                        tip += '<br><span style="font-size:.5rem;color:#64748b">클릭하여 인사이트 보기</span>';
                        showTip(ev, tip);
                    }
                };
            })(svc));
            if (typeof moveTip === 'function') g.addEventListener('mousemove', moveTip);
            if (typeof hideTip === 'function') g.addEventListener('mouseleave', hideTip);

            svg.appendChild(g);
        }
    }

    // ================================================================
    // Detail panel: Security Insights 링크 추가
    // ================================================================
    function _addInsightsLink() {
        var dBody = document.getElementById('dBody');
        if (!dBody) return;

        var existing = document.getElementById('secInsightsLink');
        if (existing) return;

        var secSection = document.getElementById('securityAgentSection');
        if (!secSection) return;

        var link = document.createElement('div');
        link.id = 'secInsightsLink';
        link.style.cssText = 'padding:6px 0 0;border-top:1px solid #1e293b;margin-top:8px';
        link.innerHTML =
            '<a href="/security/insights/' + encodeURIComponent(SELECTED) + '" target="_blank" style="font-size:.56rem;color:#38bdf8;text-decoration:none">' +
            'Security Insights 대시보드 →</a>';
        secSection.appendChild(link);
    }

    // ================================================================
    // Utils
    // ================================================================
    function _riskColor(level) {
        var map = {CRITICAL: '#ef4444', HIGH: '#f97316', MEDIUM: '#f59e0b', LOW: '#22c55e', INFO: '#38bdf8'};
        return map[level] || '#64748b';
    }

    function _svgEl(tag, attrs) {
        var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
        if (attrs) for (var k in attrs) el.setAttribute(k, attrs[k]);
        return el;
    }

    function _esc(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }
})();

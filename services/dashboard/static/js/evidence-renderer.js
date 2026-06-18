// ══════════════════════════════════════════════════════════
// Evidence Renderer — Shared JS
// Used by both evidence.html (standalone) and index.html (embedded)
// Requires: esc() function defined by host page
// ══════════════════════════════════════════════════════════

// ── Constants ──
const EV_TYPE_ICONS = {metric:'📊', change_event:'☸️', trace:'🔍', code_snippet:'💻', log:'📝'};
const EV_TYPE_LABELS = {metric:'Metrics', change_event:'K8s Events', trace:'Traces', code_snippet:'Code', log:'Logs'};
const EV_TAB_ORDER = ['metric','change_event','trace','code_snippet','log'];
const EV_REASON_ICONS = {
    ScalingReplicaSet:'⚡', Scheduled:'📋', Killing:'💀',
    Unhealthy:'⚠️', Started:'✅', Pulling:'📦', default:'🔄'
};

// Back-compat aliases (evidence.html uses these names)
if (typeof TYPE_ICONS === 'undefined') { var TYPE_ICONS = EV_TYPE_ICONS; }
if (typeof TYPE_LABELS === 'undefined') { var TYPE_LABELS = EV_TYPE_LABELS; }
if (typeof TAB_ORDER === 'undefined') { var TAB_ORDER = EV_TAB_ORDER; }
if (typeof REASON_ICONS === 'undefined') { var REASON_ICONS = EV_REASON_ICONS; }

// ── Utilities ──
function evFmtTime(epoch) {
    const d = new Date(epoch * 1000);
    return d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false});
}
if (typeof fmtTime === 'undefined') { var fmtTime = evFmtTime; }

function evCopyCmd(cmd) {
    navigator.clipboard.writeText(cmd).then(() => evShowToast('클립보드에 복사됨'));
}
if (typeof copyCmd === 'undefined') { var copyCmd = evCopyCmd; }

function evShowToast(msg) {
    const el = document.getElementById('toast') || document.getElementById('ev-toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    setTimeout(() => el.classList.remove('show'), 2000);
}
if (typeof showToast === 'undefined') { var showToast = evShowToast; }

// ══════════════════════════════════════════════════════════
// Cascade Chain
// ══════════════════════════════════════════════════════════

function buildCascadeChain(findings, summary) {
    const sf = summary && summary.findings ? summary.findings : [];
    if (sf.length && sf.some(f => f.cascades_to && f.cascades_to.length)) {
        const allTargets = new Set();
        sf.forEach(f => (f.cascades_to || []).forEach(t => allTargets.add(t)));
        const roots = sf.filter(f => !allTargets.has(f.id));

        const visited = new Set();
        const chain = [];

        function walk(id) {
            if (visited.has(id)) return;
            visited.add(id);
            const node = sf.find(f => f.id === id);
            if (!node) return;
            const detail = findings.find(f => f.id === id);
            chain.push({
                id: node.id,
                type: node.type || (detail && detail.finding_type) || 'cause',
                title: (detail && detail.title) || node.title || node.id,
                description: (detail && detail.description) || node.description || '',
                related_resources: (detail && detail.related_resources) || node.related_resources || [],
            });
            (node.cascades_to || []).forEach(walk);
        }

        (roots.length ? roots : [sf[0]]).forEach(r => walk(r.id));
        sf.filter(f => !visited.has(f.id)).forEach(f => walk(f.id));
        return chain;
    }

    const typeOrder = {root_cause: 0, cause: 1, impact: 2};
    const sorted = [...findings].sort((a, b) =>
        (typeOrder[a.finding_type] ?? 1) - (typeOrder[b.finding_type] ?? 1)
    );
    return sorted.map(f => ({
        id: f.id,
        type: f.finding_type || 'cause',
        title: f.title,
        description: f.description || '',
        related_resources: f.related_resources || [],
    }));
}

function renderCascadeChain(chain, symptom) {
    const el = document.getElementById('rcaChain');
    if (!el) return;
    if (!chain.length) {
        el.innerHTML = '<div class="ev-empty">인과관계 정보 없음</div>';
        return;
    }

    let html = '';
    chain.forEach((node, i) => {
        const typeCls = node.type === 'root_cause' ? 'root-cause' : 'cause';
        const typeLabel = node.type === 'root_cause' ? '근본 원인' : '원인';
        const typeBadgeCls = node.type === 'root_cause' ? 'root-cause' : 'cause';
        const res = node.related_resources.length ? node.related_resources.join(', ') : '';

        html += `<div class="rca-node ${typeCls}" onclick="highlightFindingEvidence('${esc(node.id)}')">
            <span class="rca-node-type ${typeBadgeCls}">${typeLabel}</span>
            <div class="rca-node-title">${esc(node.title).substring(0,120)}</div>
            ${res ? `<div class="rca-node-res">${esc(res)}</div>` : ''}
        </div>`;
        if (i < chain.length - 1 || symptom) {
            html += `<div class="rca-arrow">↓</div>`;
        }
    });

    if (symptom) {
        html += `<div class="rca-node symptom-node">
            <span class="rca-node-type symptom">증상</span>
            <div class="rca-node-title">${esc(symptom.title)}</div>
            ${symptom.related_resources ? `<div class="rca-node-res">${esc(symptom.related_resources.join(', '))}</div>` : ''}
        </div>`;
    }

    el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════
// Finding-driven Evidence Rendering
// ══════════════════════════════════════════════════════════

function renderFindingEvidence(chain, data) {
    const el = document.getElementById('rcaEvidence');
    if (!el) return;
    const {findings, observations} = data;

    if (!chain.length) {
        el.innerHTML = '<div class="ev-empty">연결된 핵심 증거가 없습니다</div>';
        return;
    }

    let html = '';
    const metricSignals = [];

    for (const node of chain) {
        const finding = findings.find(f => f.id === node.id);
        if (!finding) continue;

        const obsIds = finding.supporting_observations || [];
        let signalCount = 0;
        let obsHtml = '';

        for (const obsId of obsIds) {
            const obs = observations[obsId];
            if (!obs) continue;
            const signals = obs.signals || [];
            if (!signals.length) continue;
            signalCount += signals.length;

            obsHtml += `<div class="rca-finding-obs">
                <div class="rca-finding-obs-title">${esc(obs.title)}</div>`;

            for (const sig of signals) {
                const deepLink = sig._deep_link
                    ? (sig._deep_link.startsWith('kubectl')
                        ? `<span class="ev-link-cmd" onclick="event.stopPropagation();evCopyCmd('${esc(sig._deep_link)}')" title="클릭하여 복사" style="font-size:0.58rem">$ ${esc(sig._deep_link)}</span>`
                        : `<a class="ev-link" href="${esc(sig._deep_link)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="font-size:0.58rem">🔗 Open</a>`)
                    : '';

                if (sig.type === 'metric') metricSignals.push(sig);

                obsHtml += `<div class="rca-ev-card">
                    <div class="rca-ev-card-title">${EV_TYPE_ICONS[sig.type]||'📎'} ${esc(sig.title || sig.id)}</div>
                    ${deepLink}
                    ${rcaRenderByType(sig)}
                </div>`;
            }
            obsHtml += '</div>';
        }

        if (!obsHtml) continue;

        const typeCls = node.type === 'root_cause' ? 'root-cause' : (node.type || 'cause');
        const typeLabel = node.type === 'root_cause' ? '근본 원인' : (node.type === 'impact' ? '영향' : '원인');

        html += `<div class="rca-finding-block" id="rca-ev-${esc(node.id)}">
            <div class="rca-finding-header ${typeCls}">
                <span class="rca-node-type ${typeCls}">${typeLabel}</span>
                <span class="rca-finding-header-title">${esc(node.title)}</span>
                <span class="rca-finding-header-count">${signalCount}건 증거</span>
            </div>
            ${obsHtml}
        </div>`;
    }

    if (!html) {
        el.innerHTML = '<div class="ev-empty">연결된 핵심 증거가 없습니다</div>';
        return;
    }

    el.innerHTML = html;

    requestAnimationFrame(() => {
        metricSignals.forEach(sig => {
            const canvas = document.getElementById(`rca-spark-${sig.id}`);
            if (canvas) drawSparkline(canvas, sig);
        });
    });
}

function highlightFindingEvidence(findingId) {
    const el = document.getElementById(`rca-ev-${findingId}`);
    if (el) {
        el.scrollIntoView({behavior:'smooth', block:'start'});
        el.classList.add('highlighted');
        setTimeout(() => el.classList.remove('highlighted'), 3000);
    }
    // Also highlight in findings panel if it exists
    const fp = document.getElementById(`finding-${findingId}`);
    if (fp) {
        fp.scrollIntoView({behavior:'smooth', block:'center'});
        fp.classList.add('highlighted');
        setTimeout(() => fp.classList.remove('highlighted'), 3000);
    }
}

// ══════════════════════════════════════════════════════════
// Per-type compact renderer (RCA cards)
// ══════════════════════════════════════════════════════════

function rcaRenderByType(sig) {
    switch (sig.type) {
        case 'metric': {
            const ds = sig.datasets?.metricDataset?.[0];
            if (!ds) return '';
            const data = ds.data || [];
            const values = data.map(d => d.y);
            const min = values.length ? Math.min(...values).toFixed(1) : '-';
            const max = values.length ? Math.max(...values).toFixed(1) : '-';
            const avg = values.length ? (values.reduce((a,b)=>a+b,0)/values.length).toFixed(1) : '-';
            const unit = ds.unit === 'milliseconds' ? 'ms' : (ds.unit || '');
            return `<canvas class="rca-sparkline-lg" id="rca-spark-${esc(sig.id)}" width="800" height="100"></canvas>
                <div class="rca-ev-stats">min: ${min} | max: ${max} | avg: ${avg} ${unit}</div>`;
        }
        case 'trace': {
            const records = sig.traces?.records || [];
            if (!records.length) return '';
            return records.slice(0, 3).map(r => {
                const statusCls = r.status === 'error' ? 'err' : 'ok';
                const icon = r.status === 'error' ? '🔴' : '🟢';
                const spans = (r.spans || []).map(sp =>
                    `${esc(sp.service)} → ${esc(sp.operation)} ${sp.duration_ms}ms${sp.error_message ? ' <span class="err">'+esc(sp.error_message)+'</span>' : ''}`
                ).join(' → ');
                return `<div class="rca-ev-trace"><span class="${statusCls}">${icon} ${r.duration_ms}ms</span> ${spans}</div>`;
            }).join('');
        }
        case 'log': {
            const msgs = sig.logs?.messages || [];
            if (!msgs.length) return '';
            const lines = msgs.slice(0, 4).map(m => {
                const ts = m.timestamp ? m.timestamp.split('T')[1]?.substring(0,8) || '' : '';
                const cls = (m.message||'').includes('ERROR') ? ' class="err"' : '';
                return `<div${cls}>${ts ? '<span style="color:#475569">'+ts+'</span> ' : ''}${esc((m.message||'').substring(0,120))}</div>`;
            }).join('');
            return `<div class="rca-ev-log">${lines}${msgs.length > 4 ? '<div style="color:#475569">... +'+(msgs.length-4)+'건</div>' : ''}</div>`;
        }
        case 'code_snippet': {
            const cs = sig.code_snippet;
            if (!cs) return '';
            const diffs = cs.code_diffs || [];
            return diffs.slice(0, 1).map(diff => {
                const fp = diff.file_path?.new || diff.file_path?.old || '';
                const lines = (diff.content || '').split('\n').map(line => {
                    if (line.startsWith('+')) return `<span class="add">${esc(line)}</span>`;
                    if (line.startsWith('-')) return `<span class="del">${esc(line)}</span>`;
                    return `<span class="ctx">${esc(line)}</span>`;
                }).join('\n');
                return `<div style="font-size:0.6rem;color:#475569;margin-bottom:2px">${esc(fp)}</div><div class="rca-ev-diff">${lines}</div>`;
            }).join('');
        }
        case 'change_event': {
            const ce = sig.change_event;
            if (!ce) return '';
            const isSuspicious = ce.details?.suspicious_env_var || ce.details?.new_env;
            const reason = ce.details?.reason || '';
            const icon = EV_REASON_ICONS[reason] || EV_REASON_ICONS.default;
            return `<div class="rca-ev-event${isSuspicious?' suspicious':''}">${icon} ${esc(reason)} — ${esc(ce.resource||'')}${ce.details?.new_env ? '<br><strong>'+esc(ce.details.new_env)+'</strong>' : ''}${ce.details?.message ? '<br>'+esc(ce.details.message).substring(0,100) : ''}</div>`;
        }
        default:
            return '';
    }
}

// ══════════════════════════════════════════════════════════
// Sparkline (Canvas 2D)
// ══════════════════════════════════════════════════════════

function drawSparkline(canvas, sig) {
    const ds = sig.datasets?.metricDataset?.[0];
    if (!ds) return;

    const data = ds.data || [];
    if (data.length < 2) return;

    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    const padL = 40, padR = 10, padT = 8, padB = 20;
    const chartW = w - padL - padR;
    const chartH = h - padT - padB;

    const xs = data.map(d => d.x);
    const ys = data.map(d => d.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const rangeX = maxX - minX || 1;
    const rangeY = maxY - minY || 1;

    function toCanvasX(x) { return padL + ((x - minX) / rangeX) * chartW; }
    function toCanvasY(y) { return padT + chartH - ((y - minY) / rangeY) * chartH; }

    ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
        const y = padT + (chartH / 4) * i;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    }

    ctx.fillStyle = '#475569'; ctx.font = '9px sans-serif'; ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const val = maxY - (rangeY / 4) * i;
        const y = padT + (chartH / 4) * i;
        ctx.fillText(val.toFixed(0), padL - 4, y + 3);
    }

    ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(data.length / 5));
    for (let i = 0; i < data.length; i += step) {
        const px = toCanvasX(data[i].x);
        ctx.fillText(evFmtTime(data[i].x), px, h - 4);
    }

    ctx.beginPath(); ctx.strokeStyle = '#38bdf8'; ctx.lineWidth = 1.5;
    data.forEach((d, i) => {
        const px = toCanvasX(d.x), py = toCanvasY(d.y);
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    });
    ctx.stroke();

    ctx.lineTo(toCanvasX(data[data.length-1].x), padT + chartH);
    ctx.lineTo(toCanvasX(data[0].x), padT + chartH);
    ctx.closePath();
    ctx.fillStyle = 'rgba(56,189,248,0.08)';
    ctx.fill();

    const mean = ys.reduce((a,b) => a+b, 0) / ys.length;
    const std = Math.sqrt(ys.reduce((a,b) => a + (b-mean)**2, 0) / ys.length);
    data.forEach(d => {
        if (Math.abs(d.y - mean) > 2 * std) {
            const px = toCanvasX(d.x), py = toCanvasY(d.y);
            ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#ef4444'; ctx.fill();
        }
    });

    data.forEach(d => {
        if (Math.abs(d.y - mean) <= 2 * std) {
            const px = toCanvasX(d.x), py = toCanvasY(d.y);
            ctx.beginPath(); ctx.arc(px, py, 2, 0, Math.PI * 2);
            ctx.fillStyle = '#38bdf8'; ctx.fill();
        }
    });
}

// ══════════════════════════════════════════════════════════
// Inline RCA Report Renderer (for index.html embedding)
// ══════════════════════════════════════════════════════════

function renderRcaReportInline(data, containerEl) {
    const {findings, symptom, summary} = data;

    if (!findings.length && !symptom) {
        containerEl.innerHTML = '<div class="ev-empty">Evidence 데이터가 없습니다</div>';
        return;
    }

    let html = '';

    // Symptom banner
    if (symptom) {
        const time = symptom.start_time ? symptom.start_time.replace('T',' ').replace('Z','') : '';
        html += `<div class="rca-symptom-banner">
            <span class="rca-sym-title">🚨 ${esc(symptom.title)}</span>
            <span class="rca-sym-desc">${esc(symptom.description).substring(0,200)}</span>
            ${time ? `<span class="rca-sym-time">${time}</span>` : ''}
        </div>`;
    }

    // Two-column layout
    const chain = buildCascadeChain(findings, summary);
    html += '<div class="rca-columns">';
    html += '<div class="rca-chain-col"><div class="rca-section-label">인과관계 체인</div><div class="rca-chain" id="rcaChain"></div></div>';
    html += '<div class="rca-metrics-col"><div class="rca-section-label">핵심 증거</div><div id="rcaEvidence"></div></div>';
    html += '</div>';

    containerEl.innerHTML = html;

    // Render into containers
    renderCascadeChain(chain, symptom);
    renderFindingEvidence(chain, data);
}

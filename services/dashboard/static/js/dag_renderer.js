// ================================================================
// DAG RENDERER — Reusable investigation DAG (full-featured)
// Matches dag.html renderDAG() exactly: modal, drag, edge tooltips,
// layout toggle, 220x64 nodes, 11px/9px fonts
// ================================================================
/* global trun */

var DAG = (function(){

var ICO={metric:'📊',trace:'🔗',log:'📋',code_snippet:'📝',change_event:'🔧'};
var SRC={metric:'Metric',trace:'X-Ray',log:'Log',code_snippet:'Code',change_event:'K8s Event'};
var C={NW:220,NH:64,colGap:16,rowGap:50,pad:24,groupPad:10,groupLabelH:16,legendH:80};

var _layout='horizontal';
function setLayout(l){_layout=l;}
function getLayout(){return _layout;}
function toggleLayout(){_layout=_layout==='vertical'?'horizontal':'vertical';return _layout;}

function _trun(s,n){if(!s)return'';return s.length>n?s.slice(0,n)+'…':s;}
function _esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');}
function _svgE(t,a){var e=document.createElementNS('http://www.w3.org/2000/svg',t);if(a)for(var k in a)e.setAttribute(k,a[k]);return e;}
function _wrap(text,maxC,maxL){
    if(!text)return[''];
    text=String(text).replace(/\n/g,' ').trim();
    if(text.length<=maxC)return[text];
    var result=[],rem=text;
    for(var i=0;i<maxL&&rem.length>0;i++){
        if(rem.length<=maxC){result.push(rem);break;}
        var cut=rem.lastIndexOf(' ',maxC);if(cut<maxC*0.3)cut=maxC;
        var line=rem.slice(0,cut);rem=rem.slice(cut).trim();
        if(i===maxL-1&&rem.length>0)line=line.slice(0,maxC-1)+'…';
        result.push(line);
    }
    return result.length?result:[''];
}
function _unique(arr){var s={};return arr.filter(function(v){if(s[v])return false;s[v]=true;return true;});}
function _tryJ(t){try{return JSON.parse(t);}catch(e){return null;}}
function _isCause(f){return f.finding_type==='root_cause'||f.finding_type==='cause';}

// ── Tooltip ──
var _tipEl=null;
function _ensureTip(){
    if(_tipEl)return;
    _tipEl=document.createElement('div');
    _tipEl.id='dagTip';
    _tipEl.style.cssText='position:fixed;display:none;background:#1e293b;border:1px solid #475569;border-radius:8px;padding:10px 14px;font-size:.72rem;max-width:400px;pointer-events:none;z-index:9999;line-height:1.5;color:#cbd5e1;box-shadow:0 4px 12px rgba(0,0,0,.5)';
    document.body.appendChild(_tipEl);
}
function _showTip(ev,html){_ensureTip();_tipEl.innerHTML=html;_tipEl.style.display='';_tipEl.style.left=(ev.clientX+14)+'px';_tipEl.style.top=(ev.clientY-10)+'px';}
function _moveTip(ev){if(_tipEl){_tipEl.style.left=(ev.clientX+14)+'px';_tipEl.style.top=(ev.clientY-10)+'px';}}
function _hideTip(){if(_tipEl)_tipEl.style.display='none';}
function _nodeTipHtml(n){
    var h='<div style="color:#38bdf8;font-weight:600;margin-bottom:3px">'+_esc(n.evidence||n.label||'')+'</div>';
    if(n.description)h+='<div>'+_esc(n.description)+'</div>';
    if(n.target)h+='<div style="color:#64748b;font-size:.6rem;border-top:1px solid #334155;padding-top:3px;margin-top:3px">Target: '+_esc(n.target)+'</div>';
    if(n.resource)h+='<div style="color:#64748b;font-size:.6rem">Resource: '+_esc(n.resource)+'</div>';
    if(n.activity)h+='<div style="color:#64748b;font-size:.6rem">Activity: '+_esc(n.activity)+'</div>';
    if(n.signals&&n.signals.length){h+='<div style="color:#64748b;font-size:.6rem">Signals: '+n.signals.map(function(s){return(ICO[s.type]||'')+' '+(s.title||s.type);}).join(', ')+'</div>';}
    if(n.terminated)h+='<div style="color:#ef4444;font-size:.6rem">조사했으나 Finding에 연결되지 않음 → 종료</div>';
    if(n.inChain&&n.chainIndex===0)h+='<div style="color:#22c55e;font-size:.6rem">Root Cause</div>';
    if(n.time)h+='<div style="color:#64748b;font-size:.6rem">'+n.time+'</div>';
    return h;
}

function _sigInsight(sig){
    if(!sig)return'';
    if(sig.type==='metric')return sig.title||sig.summary||'metric';
    if(sig.type==='trace'&&sig.traces){var rec=(sig.traces.records||[])[0];if(rec&&rec.spans){var e=rec.spans.find(function(s){return s.error_message;});if(e)return e.service+'→'+e.operation+': '+e.error_message;}return sig.summary||sig.title||'trace';}
    if(sig.type==='log'&&sig.logs){var msgs=sig.logs.messages||[];if(msgs.length&&msgs[0].message)return msgs[0].message.replace(/^\[.*?\]\s*/,'');return sig.title||'log';}
    if(sig.type==='change_event'&&sig.change_event){var d=sig.change_event.details||{};return d.new_env||(d.change_type||'')+' '+(sig.change_event.resource||'');}
    if(sig.type==='code_snippet'&&sig.code_snippet){var diffs=sig.code_snippet.code_diffs||[];if(diffs.length){var add=(diffs[0].content||'').split('\n').find(function(l){return l.charAt(0)==='+';});return(diffs[0].file_path?(diffs[0].file_path.new||diffs[0].file_path):'')+': '+(add?add.slice(1,60):'');}return sig.title||'code';}
    return sig.title||sig.summary||'';
}

// ── Modal ──
var _modalEl=null;
function _ensureModal(){
    if(_modalEl)return;
    var ov=document.createElement('div');
    ov.id='dagModalOverlay';
    ov.style.cssText='display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:200;justify-content:center;align-items:center';
    ov.addEventListener('click',function(e){if(e.target===ov){ov.style.display='none';}});
    var m=document.createElement('div');
    m.style.cssText='background:#1e293b;border:1px solid #334155;border-radius:12px;width:640px;max-height:80vh;overflow-y:auto;padding:20px';
    m.addEventListener('click',function(e){e.stopPropagation();});
    m.innerHTML='<h3 id="dagModalTitle" style="font-size:.85rem;color:#38bdf8;margin-bottom:10px"></h3><div id="dagModalBody"></div>';
    ov.appendChild(m);
    document.body.appendChild(ov);
    _modalEl=ov;
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&_modalEl)_modalEl.style.display='none';});
}
function _showModal(n){
    _ensureModal();
    document.getElementById('dagModalTitle').textContent=n.label||n.evidence||'Detail';
    var h='<div style="font-size:.7rem;color:#94a3b8;margin-bottom:12px;line-height:1.6">';
    if(n.time)h+='<strong style="color:#e2e8f0">Time:</strong> '+n.time+'<br>';
    if(n.type)h+='<strong style="color:#e2e8f0">Type:</strong> '+n.type+'<br>';
    if(n.findingType)h+='<strong style="color:#e2e8f0">Finding:</strong> '+n.findingType+'<br>';
    h+='</div>';
    if(n.description)h+='<div style="background:#0f172a;border-left:3px solid #38bdf8;padding:8px 12px;margin-bottom:12px;font-size:.73rem;color:#cbd5e1;line-height:1.5">'+_esc(n.description)+'</div>';
    var data=n.data;
    if(data){
        if(data.analysis)h+='<div style="background:#0f172a;border-left:3px solid #38bdf8;padding:8px 12px;margin-bottom:12px;font-size:.73rem;color:#cbd5e1;line-height:1.5">'+_esc(data.analysis)+'</div>';
        if(data.signals)(data.signals||[]).forEach(function(sig){
            h+='<div style="background:#0f172a;border:1px solid #334155;border-radius:6px;padding:10px;margin-bottom:8px;font-size:.68rem">';
            h+='<div style="color:#f59e0b;font-weight:600;font-size:.63rem;text-transform:uppercase;margin-bottom:4px">'+(ICO[sig.type]||'')+' '+sig.type+(sig.title?' — '+sig.title:'')+'</div>';
            h+='<div style="color:#cbd5e1;line-height:1.5;white-space:pre-wrap;font-family:\'SF Mono\',monospace;font-size:.65rem;max-height:200px;overflow-y:auto">'+_esc(_fmtSig(sig))+'</div>';
            h+='</div>';
        });
    }
    document.getElementById('dagModalBody').innerHTML=h;
    _modalEl.style.display='flex';
}
function _fmtSig(sig){
    if(sig.type==='metric'&&sig.datasets){var b='';(sig.datasets.metricDataset||[]).forEach(function(d){b+=d.label+':\n';(d.data||[]).forEach(function(p){b+='  '+(p.x?new Date(p.x*1000).toISOString().slice(11,16):'?')+' → '+p.y+'\n';});});return b;}
    if(sig.type==='trace'&&sig.traces){var b='';(sig.traces.records||[]).forEach(function(t){b+='Trace: '+t.trace_id+' ('+t.duration_ms+'ms '+t.status+')\n';(t.spans||[]).forEach(function(s){b+='  '+s.service+' → '+s.operation+' '+s.duration_ms+'ms'+(s.error_message?' ❌ '+s.error_message:'')+'\n';});});return b;}
    if(sig.type==='log'&&sig.logs)return(sig.logs.messages||[]).map(function(m){return(m.timestamp||'')+' '+(m.message||'');}).join('\n');
    if(sig.type==='change_event'&&sig.change_event){var c=sig.change_event,b='Resource: '+(c.resource||'')+'\n';if(c.details)Object.keys(c.details).forEach(function(k){b+=k+': '+c.details[k]+'\n';});return b;}
    if(sig.type==='code_snippet'&&sig.code_snippet){var cs=sig.code_snippet,b='';(cs.code_diffs||[]).forEach(function(d){b+=(d.file_path?(d.file_path.new||d.file_path):'')+'\n'+(d.content||'')+'\n';});return b;}
    return JSON.stringify(sig,null,2).slice(0,400);
}

// ── Parse raw journal records into categorized lists ──
function parseRecords(records){
    var alarm=null, observations=[], findings=[], summaryFindings=[], summary=null, messages=[];
    var TYPE_RANK={unknown:0,hypothesis:1,impact:2,cause:3,root_cause:4};

    records.forEach(function(r){
        var p=r.parsed||_tryJ(r.raw_text)||_tryJ(r.content);
        if(!p)return;
        var type=r.record_type||r.recordType||(p.type)||'';
        var time=(r.created_at||r.createdAt||'').slice(11,16);
        var ts=r.created_at||r.createdAt||'';

        if(type==='symptom') alarm={id:p.id||'symptom',title:p.title||'Alarm',time:time,ts:ts,data:p};
        else if(type==='observation') observations.push({
            id:p.id||'obs-'+observations.length, title:p.title||'', time:time, ts:ts,
            activity:p.activity_id||'', signals:p.signals||[], analysis:p.analysis||'', data:p
        });
        else if(type==='finding') findings.push({
            id:p.id||'fin-'+findings.length, title:p.title||'', time:time, ts:ts,
            finding_type:p.finding_type||'unknown', supporting:p.supporting_observations||[],
            description:p.description||'', data:p
        });
        else if(type==='investigation_summary'){
            summary={id:'summary',title:'Summary',time:time,ts:ts,data:p,gaps:p.investigation_gaps||[]};
            summaryFindings=p.findings||[];
        }
        else if(type==='message'){
            var txt='';
            if(p.content&&Array.isArray(p.content)&&p.content[0]) txt=p.content[0].text||'';
            else if(typeof p.content==='string') txt=p.content;
            if(txt) messages.push({time:time,ts:ts,text:txt});
        }
    });

    var deduped={};
    findings.forEach(function(f){
        var fid=f.id;
        if(deduped[fid]){
            var ex=deduped[fid];
            if((TYPE_RANK[f.finding_type]||0)>(TYPE_RANK[ex.finding_type]||0)){
                f.supporting=_unique(ex.supporting.concat(f.supporting));
                deduped[fid]=f;
            }else{ex.supporting=_unique(ex.supporting.concat(f.supporting));}
        }else{deduped[fid]=f;}
    });
    findings=Object.values(deduped);

    return {alarm:alarm,observations:observations,findings:findings,summaryFindings:summaryFindings,summary:summary,messages:messages};
}

// ── Build DAG model from parsed data ──
function buildModel(alarm, observations, findings, summaryFindings, summary, messages){
    var nodes=[], edges=[];

    var alarmNode = alarm ? {id:'alarm',type:'alarm',label:alarm.title,time:alarm.time,data:alarm.data} : null;
    if(alarmNode) nodes.push(alarmNode);

    var sfMap={};
    summaryFindings.forEach(function(sf){sfMap[sf.id]=sf;});
    var cascadeGraph={};
    summaryFindings.forEach(function(sf){cascadeGraph[sf.id]=sf.cascades_to||[];});

    var rootCauseId=null;
    summaryFindings.forEach(function(sf){if(sf.type==='root_cause')rootCauseId=sf.id;});

    var chainOrder=[], chainEdges=[];
    if(rootCauseId){
        var cur=rootCauseId, visited={};
        while(cur&&!visited[cur]){
            visited[cur]=true;
            if(sfMap[cur]){
                chainOrder.push(cur);
                var targets=cascadeGraph[cur]||[];
                if(targets.length){
                    var nxt=targets[0];
                    if(sfMap[nxt]){chainEdges.push({from:cur,to:nxt});cur=nxt;}
                    else{chainEdges.push({from:cur,to:'alarm'});break;}
                }else break;
            }else break;
        }
    }
    var chainSet={};
    chainOrder.forEach(function(id){chainSet[id]=true;});

    var activityGroups={};
    var obsNodes=[];
    observations.forEach(function(obs){
        var obsId='obs_'+obs.id;
        var target=obs.id.split('-')[0]||'';
        if(!target||target==='obs') target='데이터 수집';
        var sigTypes={};
        (obs.signals||[]).forEach(function(s){sigTypes[s.type]=true;});
        var resource=Object.keys(sigTypes).map(function(t){return SRC[t]||t;}).join(', ')||'';
        var evidence=obs.title||obs.analysis||'';
        if(!evidence&&obs.signals.length>0) evidence=_sigInsight(obs.signals[0]);

        var node={id:obsId,type:'observation',target:target,resource:resource,evidence:evidence,
            time:obs.time,obsId:obs.id,data:obs.data,signals:obs.signals,activity:obs.activity};
        obsNodes.push(node);

        var act=obs.activity||'unknown';
        if(!activityGroups[act])activityGroups[act]=[];
        activityGroups[act].push(node);
    });
    nodes=nodes.concat(obsNodes);

    if(alarmNode){
        obsNodes.forEach(function(o){edges.push({from:'alarm',to:o.id,edgeType:'alarm-obs'});});
    }

    var connectedObs={};
    findings.forEach(function(fin){
        var inChain=!!chainSet[fin.id];
        var res=(fin.data&&fin.data.related_resources)||[];
        var fTarget=res.length?res.join(', '):(fin.id.split('-')[0]||'');
        var fEvidence=fin.title||fin.description||'';
        nodes.push({id:'fin_'+fin.id,type:'finding',label:fin.title,time:fin.time,findingType:fin.finding_type,data:fin.data,description:fin.description,inChain:inChain,chainIndex:inChain?chainOrder.indexOf(fin.id):-1,target:fTarget,evidence:fEvidence});
        (fin.supporting||[]).forEach(function(oid){
            obsNodes.forEach(function(o){
                if(o.obsId===oid){
                    edges.push({from:o.id,to:'fin_'+fin.id,edgeType:inChain?'obs-chain-finding':'obs-finding'});
                    connectedObs[o.id]=true;
                }
            });
        });
    });

    chainEdges.forEach(function(ce){
        if(ce.to==='alarm') edges.push({from:'fin_'+ce.from,to:'alarm',edgeType:'chain-to-alarm'});
        else edges.push({from:'fin_'+ce.from,to:'fin_'+ce.to,edgeType:'chain'});
    });

    var orphans=obsNodes.filter(function(o){return !connectedObs[o.id];});
    orphans.forEach(function(o){
        var tId='term_'+o.id;
        var reason=o.evidence||o.target||'무관';
        nodes.push({id:tId,type:'terminated',label:'종료: '+_trun(reason,20),time:'',description:'조사했으나 Finding에 연결되지 않음'});
        o.terminated=true;
        edges.push({from:o.id,to:tId,edgeType:'orphan'});
    });

    var msgMap={};
    messages.forEach(function(m){
        observations.forEach(function(obs){
            if(obs.ts&&m.ts&&Math.abs(new Date(obs.ts)-new Date(m.ts))<120000){
                if(!msgMap[obs.id])msgMap[obs.id]=[];
                msgMap[obs.id].push(m.text);
            }
        });
    });

    return {nodes:nodes,edges:edges,msgMap:msgMap,alarm:alarm,findings:findings,summary:summary,
        chainOrder:chainOrder,chainSet:chainSet,rootCauseId:rootCauseId,
        activityGroups:activityGroups,summaryFindings:summaryFindings};
}

// ── Convenience: records → model ──
function fromRecords(records){
    var p = parseRecords(records);
    return buildModel(p.alarm, p.observations, p.findings, p.summaryFindings, p.summary, p.messages);
}

// ── Node style ──
function _nodeStyle(n){
    switch(n.type){
        case 'alarm': return{bg:'#172554',border:'#38bdf8',sw:2.5,color:'#7dd3fc',fontSize:'12'};
        case 'observation':
            if(n.terminated) return{bg:'#1c1917',border:'#a8a29e',sw:1.2,dash:'4,3',color:'#d6d3d1',badgeColor:'#a8a29e'};
            return{bg:'#0c1a2e',border:'#60a5fa',sw:1.5,color:'#e2e8f0',badgeColor:'#60a5fa'};
        case 'finding':
            if(n.inChain&&n.chainIndex===0) return{bg:'#14532d',border:'#4ade80',sw:3,color:'#bbf7d0',badgeColor:'#4ade80',fontSize:'11'};
            if(n.inChain) return{bg:'#0a2e1a',border:'#22c55e',sw:2,color:'#86efac',badgeColor:'#22c55e'};
            return{bg:'#1e1b4b',border:'#818cf8',sw:1.5,color:'#c7d2fe',badgeColor:'#818cf8'};
        case 'terminated': return{bg:'#1c1917',border:'#ef4444',sw:1.5,dash:'5,3',color:'#fca5a5',badgeColor:'#ef4444',fontSize:'11'};
        default: return{bg:'#1e293b',border:'#475569',sw:1,color:'#e2e8f0'};
    }
}

// ── Drag cleanup registry ──
var _dragCleanup={};

// ── Render DAG into an SVG element ──
function render(svgEl, model, opts){
    var svgId=svgEl.id||('dagSvg_'+Math.random().toString(36).slice(2,8));
    if(!svgEl.id) svgEl.id=svgId;
    svgEl.innerHTML='';
    if(_dragCleanup[svgId]){_dragCleanup[svgId]();_dragCleanup[svgId]=null;}
    if(!model||!model.nodes.length)return;

    opts=opts||{};
    var isV=(opts.layout||_layout)==='vertical';

    var pos={};
    var actGroups=model.activityGroups||{};
    var chainOrder=model.chainOrder||[];
    var alarmNode=model.nodes.find(function(n){return n.type==='alarm';});
    var chainFindings=model.nodes.filter(function(n){return n.type==='finding'&&n.inChain;});
    chainFindings.sort(function(a,b){return a.chainIndex-b.chainIndex;});
    var nonChainFindings=model.nodes.filter(function(n){return n.type==='finding'&&!n.inChain;});
    var termNodes=model.nodes.filter(function(n){return n.type==='terminated';});
    var actKeys=Object.keys(actGroups);
    var groupRects=[];

    var orphanTermMap={};
    termNodes.forEach(function(tn){
        var parentEdge=model.edges.find(function(e){return e.to===tn.id&&e.edgeType==='orphan';});
        if(parentEdge) orphanTermMap[parentEdge.from]=tn.id;
    });

    if(isV){
        var curY=C.pad;
        var obsStartY=curY+C.NH+C.rowGap;
        var obsX=C.pad;
        var maxObsBottomY=obsStartY;
        actKeys.forEach(function(act){
            var obsInGroup=actGroups[act]; if(!obsInGroup||!obsInGroup.length)return;
            var gy=obsStartY;
            obsInGroup.forEach(function(o){pos[o.id]={x:obsX,y:gy};gy+=C.NH+6;});
            if(gy>maxObsBottomY) maxObsBottomY=gy;
            var gey=gy-6;
            if(obsInGroup.length>=2) groupRects.push({x:obsX-C.groupPad,y:obsStartY-C.groupLabelH-C.groupPad,w:C.NW+C.groupPad*2,h:gey-obsStartY+C.groupLabelH+C.groupPad*2,label:act.replace(/-/g,' ')});
            obsX+=C.NW+C.colGap+10;
        });
        var totalObsW=obsX-C.colGap-10;
        var centerX=Math.max(totalObsW/2,C.pad+C.NW/2);
        var termRowY=maxObsBottomY+10;
        var anyTerm=false;
        termNodes.forEach(function(tn){
            var parentId=null;
            for(var k in orphanTermMap){if(orphanTermMap[k]===tn.id){parentId=k;break;}}
            var px=parentId&&pos[parentId]?pos[parentId].x:centerX;
            pos[tn.id]={x:px,y:termRowY};anyTerm=true;
        });
        var findStartY=anyTerm?termRowY+C.NH+C.rowGap:maxObsBottomY+C.rowGap;
        var findY=findStartY;
        for(var ci=chainFindings.length-1;ci>=0;ci--){pos[chainFindings[ci].id]={x:centerX-C.NW/2,y:findY};findY+=C.NH+C.rowGap;}
        nonChainFindings.forEach(function(fn,i){pos[fn.id]={x:centerX+C.NW+40,y:findStartY+i*(C.NH+C.rowGap)};});
        if(alarmNode) pos['alarm']={x:centerX-C.NW/2,y:curY};
    }else{
        var obsStartX=C.pad+C.NW+C.rowGap;
        var obsY=C.pad;
        var maxObsBottomY=obsY;
        actKeys.forEach(function(act){
            var obsInGroup=actGroups[act]; if(!obsInGroup||!obsInGroup.length)return;
            var gy=obsY;
            obsInGroup.forEach(function(o){pos[o.id]={x:obsStartX,y:gy};gy+=C.NH+6;});
            if(gy>maxObsBottomY) maxObsBottomY=gy;
            var gey=gy-6;
            if(obsInGroup.length>=2) groupRects.push({x:obsStartX-C.groupPad,y:obsY-C.groupLabelH-C.groupPad,w:C.NW+C.groupPad*2,h:gey-obsY+C.groupLabelH+C.groupPad*2,label:act.replace(/-/g,' ')});
            obsY=gy+10;
        });
        var termRowY=maxObsBottomY+10;
        var anyTerm=false;
        termNodes.forEach(function(tn){pos[tn.id]={x:obsStartX,y:termRowY};termRowY+=C.NH+8;anyTerm=true;});
        var totalObsH=Math.max(maxObsBottomY,anyTerm?termRowY:0);
        var centerY=Math.max(totalObsH/2,C.pad+C.NH/2);
        var findStartX=obsStartX+C.NW+C.rowGap;
        var findY=C.pad;
        for(var ci=chainFindings.length-1;ci>=0;ci--){pos[chainFindings[ci].id]={x:findStartX,y:findY};findY+=C.NH+C.rowGap;}
        nonChainFindings.forEach(function(fn,i){pos[fn.id]={x:findStartX+C.NW+40,y:C.pad+i*(C.NH+C.rowGap)};});
        if(alarmNode) pos['alarm']={x:C.pad,y:centerY-C.NH/2};
    }

    var maxX=0, maxY=0;
    model.nodes.forEach(function(n){var p=pos[n.id];if(!p)return;if(p.x+C.NW>maxX)maxX=p.x+C.NW;if(p.y+C.NH>maxY)maxY=p.y+C.NH;});
    var totalW=maxX+C.pad;
    var totalH=maxY+C.pad+C.legendH;
    svgEl.setAttribute('viewBox','0 0 '+totalW+' '+totalH);

    var defs=_svgE('defs');
    [['arr','#475569'],['arr-chain','#22c55e'],['arr-obs','#60a5fa'],['arr-orphan','#ef4444'],['arr-alarm','#f59e0b']].forEach(function(m){
        var mk=_svgE('marker',{id:m[0]+'-'+svgId,markerWidth:'6',markerHeight:'6',refX:'5',refY:'3',orient:'auto'});
        mk.appendChild(_svgE('path',{d:'M0,0.5 L0,5.5 L6,3 z',fill:m[1]}));defs.appendChild(mk);
    });
    svgEl.appendChild(defs);

    var bgG=_svgE('g'); svgEl.appendChild(bgG);
    var groupColors=['#1e3a5f','#2d1b4e','#1a3636'];
    groupRects.forEach(function(gr,gi){
        var gc=groupColors[gi%groupColors.length];
        bgG.appendChild(_svgE('rect',{x:gr.x,y:gr.y,width:gr.w,height:gr.h,rx:'8',fill:gc,stroke:'#60a5fa','stroke-width':'1','stroke-dasharray':'6,3',opacity:'0.5'}));
        var lt=_svgE('text',{x:gr.x+8,y:gr.y+12,'font-size':'9',fill:'#93c5fd','font-weight':'600'});
        lt.textContent=gr.label; bgG.appendChild(lt);
    });

    var nG=_svgE('g'); svgEl.appendChild(nG);
    var eG=_svgE('g'); svgEl.appendChild(eG);

    var edgePaths=[];

    function calcEdgePath(e){
        var fp=pos[e.from], tp=pos[e.to]; if(!fp||!tp)return null;
        var et=e.edgeType||'';
        var x1,y1,x2,y2,d;
        if(isV){
            x1=fp.x+C.NW/2; y1=fp.y+C.NH; x2=tp.x+C.NW/2; y2=tp.y;
            if(et==='chain-to-alarm'){x1=fp.x+C.NW;y1=fp.y+C.NH/2;x2=tp.x+C.NW;y2=tp.y+C.NH/2;var cx=Math.max(x1,x2)+60;d='M '+x1+' '+y1+' C '+cx+' '+y1+', '+cx+' '+y2+', '+x2+' '+y2;}
            else if(et==='orphan'){d='M '+x1+' '+y1+' L '+x2+' '+y2;}
            else{var my=(y1+y2)/2;d='M '+x1+' '+y1+' C '+x1+' '+my+', '+x2+' '+my+', '+x2+' '+y2;}
        }else{
            x1=fp.x+C.NW; y1=fp.y+C.NH/2; x2=tp.x; y2=tp.y+C.NH/2;
            if(et==='chain-to-alarm'){y1=fp.y+C.NH;y2=tp.y+C.NH;var cy=Math.max(y1,y2)+60;x1=fp.x+C.NW/2;x2=tp.x+C.NW/2;d='M '+x1+' '+y1+' C '+x1+' '+cy+', '+x2+' '+cy+', '+x2+' '+y2;}
            else if(et==='orphan'){d='M '+x1+' '+y1+' L '+x2+' '+y2;}
            else{var mx=(x1+x2)/2;d='M '+x1+' '+y1+' C '+mx+' '+y1+', '+mx+' '+y2+', '+x2+' '+y2;}
        }
        return d;
    }

    function redrawEdges(){
        edgePaths.forEach(function(ep){
            var d=calcEdgePath(ep.edge);
            if(d) ep.path.setAttribute('d',d);
        });
    }

    // Edges
    model.edges.forEach(function(e){
        var d=calcEdgePath(e); if(!d)return;
        var et=e.edgeType||'';
        var color='#475569', sw=1, dash='', markId='arr';
        if(et==='chain'){color='#22c55e';sw=2.5;markId='arr-chain';}
        else if(et==='chain-to-alarm'){color='#f59e0b';sw=2;dash='6,3';markId='arr-alarm';}
        else if(et==='obs-chain-finding'){color='#22c55e';sw=1.2;markId='arr-chain';}
        else if(et==='obs-finding'){color='#60a5fa';sw=1;markId='arr-obs';}
        else if(et==='orphan'){color='#ef4444';sw=1.2;dash='5,3';markId='arr-orphan';}
        else if(et==='alarm-obs'){color='#334155';sw=0.6;}

        var path=_svgE('path',{d:d,fill:'none',stroke:color,'stroke-width':sw,'stroke-dasharray':dash,'marker-end':'url(#'+markId+'-'+svgId+')'});

        // Edge tooltips: Agent reasoning messages
        var fromNode=model.nodes.find(function(n){return n.id===e.from;});
        if(fromNode&&fromNode.obsId&&model.msgMap[fromNode.obsId]){
            var msgs=model.msgMap[fromNode.obsId];
            path.style.cursor='help';
            path.addEventListener('mouseenter',function(ev){_showTip(ev,'<div style="color:#38bdf8;font-weight:600;margin-bottom:3px">Agent reasoning</div>'+msgs.map(function(m){return _esc(_trun(m,120));}).join('<br>'));});
            path.addEventListener('mousemove',_moveTip);
            path.addEventListener('mouseleave',_hideTip);
        }
        eG.appendChild(path);
        edgePaths.push({edge:e,path:path});
    });

    // Nodes
    model.nodes.forEach(function(n){
        var p=pos[n.id]; if(!p)return;
        var g=_svgE('g',{class:'node','data-id':n.id});
        var style=_nodeStyle(n);
        g.appendChild(_svgE('rect',{x:p.x,y:p.y,width:C.NW,height:C.NH,rx:'6',fill:style.bg,stroke:style.border,'stroke-width':style.sw,'stroke-dasharray':style.dash||''}));
        if(n.time){var tbg=_svgE('text',{x:p.x+C.NW-4,y:p.y+10,'text-anchor':'end','font-size':'7',fill:'#64748b'});tbg.textContent=n.time;g.appendChild(tbg);}

        if(n.type==='observation'){
            var t1=_svgE('text',{x:p.x+8,y:p.y+16,'font-size':'11',fill:'#94a3b8','font-weight':'600'});
            t1.textContent=_trun(n.target||'',26); g.appendChild(t1);
            var t2=_svgE('text',{x:p.x+8,y:p.y+30,'font-size':'9',fill:'#64748b'});
            t2.textContent=_trun(n.resource||'',30); g.appendChild(t2);
            var t3=_svgE('text',{x:p.x+8,y:p.y+44,'font-size':'9',fill:n.terminated?'#64748b':'#e2e8f0'});
            t3.textContent=_trun(n.evidence||'',30); g.appendChild(t3);
            var badge=n.terminated?'TERMINATED':'OBS';
            var bt=_svgE('text',{x:p.x+8,y:p.y+C.NH-4,'font-size':'7',fill:style.badgeColor||'#64748b','font-weight':'600'});
            bt.textContent=badge; g.appendChild(bt);
        }else if(n.type==='finding'){
            var ft=_svgE('text',{x:p.x+8,y:p.y+16,'font-size':'11',fill:style.color,'font-weight':'600'});
            ft.textContent=_trun(n.target||'',26); g.appendChild(ft);
            var fbadge='';
            if(n.inChain&&n.chainIndex===0) fbadge='ROOT CAUSE';
            else if(n.inChain) fbadge='CAUSE';
            else fbadge=(n.findingType||'').toUpperCase();
            var fbt=_svgE('text',{x:p.x+8,y:p.y+30,'font-size':'9',fill:style.badgeColor||'#64748b','font-weight':'600'});
            fbt.textContent=fbadge; g.appendChild(fbt);
            var fe=_svgE('text',{x:p.x+8,y:p.y+44,'font-size':'9',fill:'#e2e8f0'});
            fe.textContent=_trun(n.evidence||'',30); g.appendChild(fe);
            if(n.inChain){
                g.appendChild(_svgE('circle',{cx:p.x+C.NW-12,cy:p.y+C.NH-12,r:'10',fill:'#22c55e',opacity:'0.8'}));
                var ct=_svgE('text',{x:p.x+C.NW-12,y:p.y+C.NH-8,'text-anchor':'middle','font-size':'11',fill:'#fff','font-weight':'700'});
                ct.textContent=String(n.chainIndex+1); g.appendChild(ct);
            }
        }else{
            var lines=_wrap(n.label||'',28,2);
            lines.forEach(function(ln,i){var t=_svgE('text',{x:p.x+8,y:p.y+24+i*16,'font-size':'11',fill:style.color,'font-weight':i===0?'600':'400'});t.textContent=ln;g.appendChild(t);});
            if(n.type==='terminated'){var dbt=_svgE('text',{x:p.x+8,y:p.y+C.NH-6,'font-size':'8',fill:'#64748b','font-weight':'600'});dbt.textContent='DISMISSED';g.appendChild(dbt);}
        }

        // Tooltip
        g.addEventListener('mouseenter',function(ev){_showTip(ev,_nodeTipHtml(n));});
        g.addEventListener('mousemove',_moveTip);
        g.addEventListener('mouseleave',_hideTip);

        // Click → modal
        if(n.data) g.addEventListener('click',function(){_showModal(n);});

        // Drag setup
        g.style.cursor='grab';
        g.setAttribute('data-drag-id',n.id);
        g.setAttribute('data-base-x',String(pos[n.id].x));
        g.setAttribute('data-base-y',String(pos[n.id].y));
        g.setAttribute('data-cum-dx','0');
        g.setAttribute('data-cum-dy','0');

        nG.appendChild(g);
    });

    // Drag controller
    (function(){
        var active=null, startPt=null, sessDx=0, sessDy=0;
        function onDown(ev){
            var g=ev.target.closest('g.node[data-drag-id]');
            if(!g||ev.button!==0)return;
            ev.preventDefault();
            active={
                group:g,
                nodeId:g.getAttribute('data-drag-id'),
                baseX:parseFloat(g.getAttribute('data-base-x')),
                baseY:parseFloat(g.getAttribute('data-base-y')),
                cumDx:parseFloat(g.getAttribute('data-cum-dx')),
                cumDy:parseFloat(g.getAttribute('data-cum-dy'))
            };
            sessDx=0; sessDy=0;
            var pt=svgEl.createSVGPoint();
            pt.x=ev.clientX; pt.y=ev.clientY;
            startPt=pt.matrixTransform(svgEl.getScreenCTM().inverse());
            g.style.cursor='grabbing';
        }
        function onMove(ev){
            if(!active)return;
            _hideTip();
            var pt=svgEl.createSVGPoint();
            pt.x=ev.clientX; pt.y=ev.clientY;
            var cur=pt.matrixTransform(svgEl.getScreenCTM().inverse());
            sessDx=cur.x-startPt.x; sessDy=cur.y-startPt.y;
            var tx=active.cumDx+sessDx, ty=active.cumDy+sessDy;
            active.group.setAttribute('transform','translate('+tx+','+ty+')');
            pos[active.nodeId]={x:active.baseX+tx, y:active.baseY+ty};
            redrawEdges();
        }
        function onUp(){
            if(!active)return;
            active.group.style.cursor='grab';
            var newCumDx=active.cumDx+sessDx, newCumDy=active.cumDy+sessDy;
            active.group.setAttribute('data-cum-dx',String(newCumDx));
            active.group.setAttribute('data-cum-dy',String(newCumDy));
            active=null; startPt=null; sessDx=0; sessDy=0;
        }
        svgEl.addEventListener('mousedown',onDown);
        document.addEventListener('mousemove',onMove);
        document.addEventListener('mouseup',onUp);
        _dragCleanup[svgId]=function(){
            svgEl.removeEventListener('mousedown',onDown);
            document.removeEventListener('mousemove',onMove);
            document.removeEventListener('mouseup',onUp);
        };
    })();

    // Legend
    _drawLegend(svgEl, C.pad, totalH-C.legendH+5, totalW);
}

function _drawLegend(svg, x, y, totalW){
    var lg=_svgE('g');
    lg.appendChild(_svgE('line',{x1:x,y1:y-4,x2:totalW-C.pad,y2:y-4,stroke:'#334155','stroke-width':'0.5'}));
    var items=[
        {color:'#22c55e',dash:'',label:'인과 체인 (Finding→Finding)',sw:2.5},
        {color:'#22c55e',dash:'',label:'근거 연결 (Obs→Finding)',sw:1.2},
        {color:'#f59e0b',dash:'6,3',label:'체인→알람 귀결',sw:2},
        {color:'#ef4444',dash:'5,3',label:'종료 (기각/무관)',sw:1.2},
        {color:'#60a5fa',dash:'',label:'비체인 연결',sw:1}
    ];
    var cx=x;
    items.forEach(function(it){
        lg.appendChild(_svgE('line',{x1:cx,y1:y+10,x2:cx+28,y2:y+10,stroke:it.color,'stroke-width':it.sw,'stroke-dasharray':it.dash||'none'}));
        var t=_svgE('text',{x:cx+32,y:y+14,'font-size':'9',fill:'#94a3b8'});t.textContent=it.label;lg.appendChild(t);
        cx+=120;
    });
    var ny=y+30;
    var nodeItems=[
        {bg:'#172554',border:'#38bdf8',sw:2.5,label:'Alarm'},
        {bg:'#0c1a2e',border:'#60a5fa',sw:1.5,label:'Observation'},
        {bg:'#14532d',border:'#4ade80',sw:3,label:'Root Cause'},
        {bg:'#0a2e1a',border:'#22c55e',sw:2,label:'Cause'},
        {bg:'#1c1917',border:'#ef4444',sw:1.5,dash:'5,3',label:'종료'}
    ];
    var nx=x;
    nodeItems.forEach(function(it){
        lg.appendChild(_svgE('rect',{x:nx,y:ny,width:16,height:12,rx:2,fill:it.bg,stroke:it.border,'stroke-width':it.sw,'stroke-dasharray':it.dash||''}));
        var t=_svgE('text',{x:nx+20,y:ny+10,'font-size':'9',fill:'#94a3b8'});t.textContent=it.label;lg.appendChild(t);
        nx+=110;
    });
    svg.appendChild(lg);
}

// ── Build DAG model from Bedrock hypothesis response ──
function fromBedrock(br, p1Model){
    var alarm = p1Model ? p1Model.alarm : null;
    var nodes=[], edges=[];

    var alarmNode=alarm?{id:'alarm',type:'alarm',label:alarm.title,time:alarm.time,data:alarm.data}:null;
    if(alarmNode) nodes.push(alarmNode);

    var brChain=br.causal_chain||[];
    var chainOrder=[], chainSet={};
    brChain.forEach(function(c,i){
        var fid='brchain_'+i;
        chainOrder.push(fid); chainSet[fid]=true;
        nodes.push({id:fid,type:'finding',label:c.event,time:'',findingType:i===0?'root_cause':'cause',
            inChain:true,chainIndex:i,data:c,description:'['+c.service+'] '+c.event,
            target:c.service||'',evidence:c.event||''});
    });
    for(var ci=0;ci<chainOrder.length-1;ci++){
        edges.push({from:chainOrder[ci],to:chainOrder[ci+1],edgeType:'chain'});
    }
    if(chainOrder.length&&alarmNode){
        edges.push({from:chainOrder[chainOrder.length-1],to:'alarm',edgeType:'chain-to-alarm'});
    }

    var obsNodes=[], seenObs={}, connectedObs={}, activityGroups={};
    (br.hypotheses||[]).forEach(function(hyp){
        var groupKey=hyp.label||hyp.id;
        var groupNodes=[];
        (hyp.steps||[]).forEach(function(step,si){
            var oid=step.obs_id||('step_'+hyp.id+'_'+si);
            var nodeId='obs_'+oid;
            if(!seenObs[oid]){
                seenObs[oid]=true;
                var stepTarget=oid.split('-')[0]||'';
                if(!stepTarget||stepTarget==='obs'||stepTarget==='step') stepTarget=step.service||'데이터 수집';
                var stepResource=SRC[step.signal_type]||step.signal_type||'';
                var stepEvidence=step.insight||oid;
                var obsNode={id:nodeId,type:'observation',target:stepTarget,resource:stepResource,evidence:stepEvidence,
                    time:'',obsId:oid,data:null,activity:groupKey,signals:[],hypStatus:hyp.status};
                if(hyp.status==='rejected') obsNode.terminated=true;
                obsNodes.push(obsNode); groupNodes.push(obsNode);
                if(alarmNode) edges.push({from:'alarm',to:nodeId,edgeType:'alarm-obs'});
            }
        });

        if(hyp.findings&&hyp.findings.length&&hyp.status==='confirmed'){
            (hyp.steps||[]).forEach(function(step,si){
                var oid=step.obs_id||('step_'+hyp.id+'_'+si);
                var nodeId='obs_'+oid;
                (hyp.findings||[]).forEach(function(fid){
                    var target=null;
                    chainOrder.forEach(function(cid){
                        var cn=nodes.find(function(n){return n.id===cid;});
                        if(cn&&cn.description&&cn.description.indexOf(fid)>=0) target=cid;
                    });
                    if(!target&&p1Model){
                        var p1fins=p1Model.findings||[];
                        p1fins.forEach(function(pf){
                            if(pf.id===fid){
                                chainOrder.forEach(function(cid){
                                    var cn=nodes.find(function(n){return n.id===cid;});
                                    if(cn&&pf.supporting){
                                        pf.supporting.forEach(function(soid){
                                            if(oid===soid&&!target) target=cid;
                                        });
                                    }
                                });
                            }
                        });
                    }
                    if(target){edges.push({from:nodeId,to:target,edgeType:'obs-chain-finding'});connectedObs[nodeId]=true;}
                });
            });
        }
        if(groupNodes.length) activityGroups[groupKey]=groupNodes;
    });
    nodes=nodes.concat(obsNodes);

    var orphans=obsNodes.filter(function(o){return !connectedObs[o.id];});
    orphans.forEach(function(o){
        var tId='term_'+o.id;
        var reason=o.label||o.activity||'기각';
        nodes.push({id:tId,type:'terminated',label:'종료: '+_trun(reason,20),time:'',description:'기각된 가설의 관찰'});
        o.terminated=true;
        edges.push({from:o.id,to:tId,edgeType:'orphan'});
    });

    return {nodes:nodes,edges:edges,msgMap:{},alarm:alarm,
        findings:p1Model?p1Model.findings:[],summary:p1Model?p1Model.summary:null,
        chainOrder:chainOrder,chainSet:chainSet,rootCauseId:chainOrder[0]||null,
        terminatesAtAlarm:true,activityGroups:activityGroups,summaryFindings:[]};
}

return {
    parseRecords: parseRecords,
    buildModel: buildModel,
    fromRecords: fromRecords,
    fromBedrock: fromBedrock,
    render: render,
    setLayout: setLayout,
    getLayout: getLayout,
    toggleLayout: toggleLayout,
    C: C
};

})();

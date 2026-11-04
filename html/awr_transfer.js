(function () {
  'use strict';
  const payload = JSON.parse(document.getElementById('payload').textContent);
  const data = payload.dataset;
  const $ = id => document.getElementById(id);
  const fmt = value => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 2}) : '—';
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = value => Date.parse(value + 'Z'); // Frame bounds retain source wall-clock fields.
  const colors = {low:'#50d3c5',mid:'#f5bc69',high:'#ff7c88',blue:'#7eb8fc'};
  const color = route => route.congPct >= 5 ? colors.high : route.congPct >= 2 ? colors.mid : colors.low;
  const charts = {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let index = 0, timer = null, focus = null, summaries = [];
  let matrixColumns = 0, matrixAxes = '';

  function select(next) {
    pause();
    focus = focus && focus.type === next.type && focus.key === next.key ? null : next;
    refresh();
  }
  function matches(route) {
    return !focus || (focus.type === 'node' ? route.src === focus.key || route.dst === focus.key : route.key === focus.key);
  }
  function summarize(frame) {
    const classes = $('blockClass').value ? [$('blockClass').value] : data.classes;
    const groups = new Map();
    frame.routes.filter(r => classes.includes(r.class)).forEach(row => {
      const key = `${row.src} → ${row.dst}`;
      if (!groups.has(key)) groups.set(key, {key,src:row.src,dst:row.dst,rows:[]});
      groups.get(key).rows.push(row);
    });
    const routes = [...groups.values()].filter(matches).map(route => {
      const valid = route.rows.length === classes.length && route.rows.every(row => row.valid);
      const result = {...route,valid,reason: valid ? '' : route.rows.find(row=>!row.valid)?.reason || 'Missing selected block class'};
      for (const key of ['cr','current','congested','crRate','currentRate','congestedRate','totalRate']) {
        result[key] = valid ? route.rows.reduce((sum,row)=>sum+row[key],0) : null;
      }
      result.total = valid ? result.cr + result.current : null;
      result.timeUs = valid && route.rows.every(row=>row.timeUs !== null) ? route.rows.reduce((sum,row)=>sum+row.timeUs,0) : null;
      result.avgUs = result.total > 0 && result.timeUs !== null ? result.timeUs/result.total : null;
      result.congPct = result.total > 0 ? result.congested/result.total*100 : null;
      result.begin = route.rows[0].begin; result.end = route.rows[0].end;
      return result;
    });
    const valid = routes.filter(route=>route.valid);
    const total = key => valid.length ? valid.reduce((sum,route)=>sum+route[key],0) : null;
    const received = total('total');
    const nodes = frame.nodes.filter(node=>!focus || (focus.type==='node' ? node.id===focus.key : focus.key.split(' → ').map(Number).includes(node.id)));
    const allocated = nodes.filter(node=>node.cacheGiB!==null);
    const activity = nodes.filter(node=>node.activity.hits!==null);
    const reads = nodes.filter(node=>node.activity.missRate!==null);
    const logical = activity.reduce((sum,node)=>sum+node.activity.logical,0);
    let expected = 0;
    frame.nodes.forEach(src=>frame.nodes.forEach(dst=>{
      if(src.id!==dst.id && matches({src:src.id,dst:dst.id,key:`${src.id} → ${dst.id}`})) expected++;
    }));
    return {frame,routes,valid,nodes,expected,totalRate:total('totalRate'),currentRate:total('currentRate'),crRate:total('crRate'),congestedRate:total('congestedRate'),
      congPct:received>0?total('congested')/received*100:null,
      avgUs:received>0 && valid.every(route=>route.timeUs!==null)?total('timeUs')/received:null,
      cacheGiB:allocated.length?allocated.reduce((sum,node)=>sum+node.cacheGiB,0):null,cacheCount:allocated.length,
      missRate:reads.length?reads.reduce((sum,node)=>sum+node.activity.missRate,0):null,
      hitPct:logical>0?activity.reduce((sum,node)=>sum+node.activity.hits,0)/logical*100:null,
      activityCount:activity.length,readsCount:reads.length};
  }
  function base(name) {
    return {backgroundColor:'transparent',animation:!reduced.matches,animationDurationUpdate:150,
      textStyle:{color:'#bdd2df',fontFamily:'system-ui, sans-serif'},aria:{enabled:true},
      tooltip:{confine:true,extraCssText:'max-width:min(440px,calc(100vw - 64px));white-space:normal;overflow-wrap:anywhere',backgroundColor:'#102536',borderColor:'#35556b',textStyle:{color:'#eaf5fb'}},
      toolbox:{right:0,top:0,iconStyle:{borderColor:'#94b3c7'},feature:{saveAsImage:{title:'Save chart image',name:`AWR-${name}`,backgroundColor:'#102131',pixelRatio:2}}}};
  }
  function chart(name) {
    if (!charts[name]) {
      charts[name] = echarts.init($(name),null,{renderer:'canvas'});
      charts[name].on('click',params=>{
        if(params.data?.route) select({type:'route',key:params.data.route.key});
        else if(params.data?.node) select({type:'node',key:params.data.node.id});
        else if(Number.isInteger(params.data?.frameIndex)) {pause();seek(params.data.frameIndex);}
      });
    }
    return charts[name];
  }
  function tooltip(route) {
    if(!route.valid) return `<strong>Instance ${esc(route.key)}</strong><br>Unavailable: ${esc(route.reason)}`;
    return `<strong>Instance ${esc(route.key)}</strong><br>Current: ${fmt(route.currentRate)} blocks/s<br>CR: ${fmt(route.crRate)} blocks/s<br>Congested: ${fmt(route.congestedRate)} blocks/s (${fmt(route.congPct)}%)<br>Mean transfer: ${fmt(route.avgUs)} µs<br>Receiver interval: ${esc(route.begin)} → ${esc(route.end)}<br>Selected classes: ${route.rows.length}`;
  }
  function renderNetwork(summary) {
    const selected = [...summary.valid].sort((a,b)=>b[$('metric').value]-a[$('metric').value]);
    const limit = Number($('limit').value);
    const routes = (limit?selected.slice(0,limit):selected).filter(route=>route[$('metric').value]>0);
    const nodes = summary.frame.nodes;
    const compact = $('network').clientWidth < 500;
    const positions = new Map(nodes.map((node,i)=>{const angle=Math.PI/2-2*Math.PI*i/nodes.length;return[node.id,[50+31*Math.cos(angle),50+34*Math.sin(angle)]];}));
    const max = Math.max(1,...routes.map(route=>route[$('metric').value]));
    const option = base('network');
    $('networkCaption').textContent=`${routes.length} nonzero routes drawn · ${summary.valid.length}/${summary.expected} routes measured for the selected classes`;
    chart('network').setOption({...option,grid:{left:0,right:0,top:28,bottom:15},
      xAxis:{type:'value',show:false,min:0,max:100},yAxis:{type:'value',show:false,min:0,max:100},
      tooltip:{...option.tooltip,formatter:params=>params.data.route?tooltip(params.data.route):`<strong>${esc(params.data.node.name)}</strong><br>${esc(params.data.node.host)}<br>Allocated cache: ${fmt(params.data.node.cacheGiB)} GiB<br>Startup: ${esc(params.data.node.startup)}<br>${esc(params.data.node.reason||'Snapshot present')}`},
      series:[{type:'lines',coordinateSystem:'cartesian2d',z:2,symbol:['none','arrow'],symbolSize:7,
        effect:{show:!reduced.matches,period:4,trailLength:.1,symbol:'arrow',symbolSize:6},
        lineStyle:{curveness:.17,opacity:.7},emphasis:{lineStyle:{opacity:1,width:6}},
        data:routes.map(route=>({coords:[positions.get(route.src),positions.get(route.dst)],route,
          lineStyle:{color:color(route),width:1+5*Math.sqrt(route[$('metric').value]/max)}}))},
        {type:'scatter',coordinateSystem:'cartesian2d',z:3,symbolSize:compact?32:42,
          data:nodes.map(node=>({value:positions.get(node.id),node,
            itemStyle:{color:'#24435c',borderColor:focus?.type==='node'&&focus.key===node.id?'#fff':'#79b9d8',borderWidth:2},
            label:{show:true,formatter:`${node.id}`,position:'inside',color:'#fff',fontWeight:700,fontSize:15}}))},
        {type:'scatter',coordinateSystem:'cartesian2d',silent:true,z:4,symbolSize:0,labelLayout:{hideOverlap:true},
          data:nodes.map(node=>({value:positions.get(node.id),label:{show:true,formatter:compact&&nodes.length>8?node.name:`${node.name}\n${fmt(node.cacheGiB)} GiB`,position:'bottom',distance:compact?21:27,color:'#bbd8e8',fontSize:compact?9:10,lineHeight:14}}))}]
    },{notMerge:true});
  }
  function render() {
    const summary = summaries[index];
    if(!summary)return;
    const frame=summary.frame;
    $('frame').value=index;
    $('frameLabel').textContent=`${frame.begin.replace('T',' ')} → ${frame.end.replace('T',' ')} · SNAP ${frame.snap} · ${index+1}/${data.frames.length}${frame.boundary?' · overlaps window boundary':''}`;
    $('frameLabel').className=frame.boundary?'boundary':'';
    $('previous').disabled=index===0;$('next').disabled=index===data.frames.length-1;$('play').disabled=data.frames.length<2;$('clear').disabled=!focus;
    $('focus').textContent=focus?`${focus.type==='node'?'Instance':'Route'} ${focus.key}`:'Instances with snapshots';
    $('focusHint').textContent=focus?summary.nodes.map(node=>`${node.name} · ${node.host} · allocated ${fmt(node.cacheGiB)} GiB`).join(' / '):'Click a node, route, matrix cell, or table row to focus.';
    const label=summary.valid.length===summary.expected?'Measured':'Observed subset';
    const values=[['Received blocks / sec',fmt(summary.totalRate),label],['Current / CR per sec',`${fmt(summary.currentRate)} / ${fmt(summary.crRate)}`,'Selected block classes'],
      ['Mean transfer time',`${fmt(summary.avgUs)} µs`,data.timeUnit?'Count-weighted total time':'Timing units unverified'],
      ['Congested share',`${fmt(summary.congPct)}%`,'Of measured received blocks'],['Measured routes',`${summary.valid.length} / ${summary.expected}`,'Source → destination'],
      ['Allocated buffer cache',`${fmt(summary.cacheGiB)} GiB`,`${summary.cacheCount}/${summary.nodes.length} selected instances`]];
    $('stats').innerHTML=values.map(([name,value,detail])=>`<div class="stat"><span>${esc(name)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`).join('');
    const problems=frame.nodes.filter(node=>node.reason).map(node=>`instance ${node.id}: ${node.reason}`);
    if(frame.missingInstances?.length) problems.push(`Snapshots absent for instance(s) ${frame.missingInstances.join(', ')}; membership is unknown`);
    $('coverage').textContent=`${label}: ${summary.valid.length}/${summary.expected} directed routes among instances with snapshots have valid deltas for every selected class. Cache activity: ${summary.activityCount}/${summary.nodes.length} instances. ${problems.join('; ')}`;
    const routes=[...summary.routes].sort((a,b)=>(b[$('metric').value]??-1)-(a[$('metric').value]??-1));
    const max=Math.max(1,...routes.map(route=>route[$('metric').value]||0));
    $('routes').innerHTML=routes.length?routes.map(route=>`<button class="route" data-route="${esc(route.key)}"><span class="route-head"><span>Instance ${esc(route.key)}</span><strong style="color:${route.valid?color(route):'#9ab2c5'}">${fmt(route[$('metric').value])}/s</strong></span><small>${route.valid?`Mean ${fmt(route.avgUs)} µs · Congested ${fmt(route.congPct)}%`:esc(route.reason)}</small><span class="track"><i style="width:${(route[$('metric').value]||0)/max*100}%;background:${color(route)}"></i></span></button>`).join(''):'<p class="note">No route measurements for this selection.</p>';
    $('cacheTable').innerHTML='<table><thead><tr><th>Inst.</th><th>GiB</th><th>Est. hits/s</th><th>Miss proxy/s</th><th>Hit %</th></tr></thead><tbody>'+frame.nodes.map(node=>`<tr><td><button data-node="${node.id}" title="${esc(node.name)} · ${esc(node.host)}">${node.id}</button></td><td>${fmt(node.cacheGiB)}</td><td title="${esc(node.activity.reason||'Estimated cache hits')}">${fmt(node.activity.hitRate)}</td><td>${fmt(node.activity.missRate)}</td><td>${fmt(node.activity.hitPct)}</td></tr>`).join('')+'</tbody></table>';
    renderNetwork(summary);renderMatrix(summary);markTime();
  }
  function renderTimeline(name, firstKey, secondKey, firstName, secondName) {
    const first=[],second=[];
    summaries.forEach((summary,i)=>{
      if(i && clock(summary.frame.begin)>clock(summaries[i-1].frame.end)+1000){
        const gap=(clock(summary.frame.begin)+clock(summaries[i-1].frame.end))/2;
        first.push({value:[gap,null]});second.push({value:[gap,null]});
      }
      first.push({value:[clock(summary.frame.end),summary[firstKey]],frameIndex:i});
      second.push({value:[clock(summary.frame.end),summary[secondKey]],frameIndex:i});
    });
    const option=base(name);
    chart(name).setOption({...option,useUTC:true,grid:{left:64,right:52,top:60,bottom:72},
      legend:{data:[firstName,secondName],top:5,textStyle:{color:'#bad2e1'}},
      tooltip:{...option.tooltip,trigger:'axis',formatter:params=>{
        const point=params.find(p=>Number.isInteger(p.data.frameIndex));
        if(!point)return 'No snapshot in this gap';
        const s=summaries[point.data.frameIndex];
        return `SNAP ${s.frame.snap} · ${esc(s.frame.end.replace('T',' '))}<br>${esc(firstName)}: ${fmt(s[firstKey])}/s<br>${esc(secondName)}: ${fmt(s[secondKey])}%<br>${name==='activity'?`${s.activityCount}/${s.nodes.length} instances with hit estimates`:`${s.valid.length}/${s.expected} routes measured`}${s.frame.boundary?'<br>Full interval overlaps requested boundary':''}`;
      }},xAxis:{type:'time',axisLabel:{color:'#9eb7c9',hideOverlap:true}},
      yAxis:[{type:'value',min:0,name:'per sec',axisLabel:{color:'#9eb7c9'},splitLine:{lineStyle:{color:'#294153'}}},
        {type:'value',min:0,max:100,name:'%',axisLabel:{color:'#9eb7c9'},splitLine:{show:false}}],
      dataZoom:[{type:'inside',filterMode:'none'},{type:'slider',bottom:8,height:25,filterMode:'none',textStyle:{color:'#9eb7c9'}}],
      series:[{id:'rate',name:firstName,type:'line',data:first,connectNulls:false,showSymbol:summaries.length<100,symbolSize:7,lineStyle:{width:2,color:colors.low},itemStyle:{color:colors.low},areaStyle:{color:colors.low,opacity:.08}},
        {id:'percent',name:secondName,type:'line',yAxisIndex:1,data:second,connectNulls:false,showSymbol:summaries.length<100,symbolSize:6,lineStyle:{width:1.5,color:name==='activity'?colors.blue:colors.high},itemStyle:{color:name==='activity'?colors.blue:colors.high}}]
    },{notMerge:true});
  }
  function markTime(){
    for(const name of ['timeline','activity']) if(charts[name]) charts[name].setOption({series:[{id:'rate',markLine:{symbol:'none',silent:true,animation:false,data:[{xAxis:clock(data.frames[index].end),label:{show:false},lineStyle:{type:'dashed',color:'#b9d8e8'}}]}}]});
  }
  function renderMatrix(summary){
    const ids=summary.frame.nodes.map(node=>node.id);
    const option=base('matrix');
    $('matrix').style.height=`${Math.max(280,Math.min(580,ids.length*30+140))}px`;
    const instance=chart('matrix');
    const columns=Math.max(3,Math.min(18,Math.floor(($('matrix').clientWidth-100)/48)));
    const axes=ids.join(',');
    const zoom=matrixColumns===columns&&matrixAxes===axes?instance.getOption().dataZoom||[]:[];
    matrixColumns=columns;matrixAxes=axes;
    const range=i=>zoom[i]?{start:zoom[i].start,end:zoom[i].end}:{startValue:0,endValue:Math.max(0,Math.min(ids.length-1,columns-1))};
    instance.setOption({...option,grid:{left:80,right:35,top:40,bottom:70},
      tooltip:{...option.tooltip,formatter:params=>tooltip(params.data.route)},
      xAxis:{type:'category',data:ids.map(id=>`Inst ${id}`),axisLabel:{color:'#aec7d8',fontSize:10},splitArea:{show:true,areaStyle:{color:['#193043','#14293a']}}},
      yAxis:{type:'category',data:ids.map(id=>`Inst ${id}`),inverse:true,axisLabel:{color:'#aec7d8',fontSize:10},splitArea:{show:true}},
      dataZoom:[{type:'slider',xAxisIndex:0,bottom:8,height:20,...range(0)},...(ids.length>15?[{type:'slider',yAxisIndex:0,right:0,width:16,...range(1)}]:[])],
      series:[{type:'heatmap',data:summary.valid.map(route=>({value:[ids.indexOf(route.dst),ids.indexOf(route.src),route[$('metric').value]],route,itemStyle:{color:color(route)}})),
        itemStyle:{borderColor:'#102131',borderWidth:3,borderRadius:4},label:{show:true,fontSize:10,color:'#102131',formatter:p=>p.value[2]>=10000?p.value[2].toLocaleString('en',{notation:'compact',maximumFractionDigits:1}):fmt(p.value[2])},emphasis:{itemStyle:{borderColor:'#fff',borderWidth:2}}}]
    },{notMerge:true});instance.resize();
  }
  function refresh(){
    summaries=data.frames.map(summarize);
    renderTimeline('timeline',$('metric').value,'congPct',$('metric').selectedOptions[0].text+' blocks','Congested %');
    renderTimeline('activity','missRate','hitPct','Cache physical reads','Estimated hit %');
    $('activityCaption').textContent=focus?`Selected ${focus.type}: ${focus.key}. Physical reads are a miss proxy; percentages are weighted estimates.`:'Instances with snapshots. Physical reads are a miss proxy; percentages are weighted estimates.';
    render();
  }
  function seek(next){index=Math.max(0,Math.min(data.frames.length-1,next));render();}
  function pause(){clearInterval(timer);timer=null;$('play').textContent='▶ Play';$('play').setAttribute('aria-pressed','false');}
  function play(){if(data.frames.length<2)return;if(index===data.frames.length-1)seek(0);$('play').textContent='Ⅱ Pause';$('play').setAttribute('aria-pressed','true');timer=setInterval(()=>index===data.frames.length-1?pause():seek(index+1),Number($('speed').value));}
  function download(){
    const rows=[['dbid','con_dbid','con_id','snap','begin','end','boundary','source','destination','classes','valid','reason','current_per_sec','cr_per_sec','congested_per_sec','total_per_sec','congested_pct','mean_us']];
    summaries.forEach(s=>s.routes.forEach(r=>rows.push([data.dbid,data.scope.con_dbid,data.scope.con_id,s.frame.snap,r.begin,r.end,s.frame.boundary,r.src,r.dst,$('blockClass').value||'All',r.valid,r.reason,r.currentRate,r.crRate,r.congestedRate,r.totalRate,r.congPct,r.avgUs])));
    const cell=value=>'"'+String(value??'').replace(/^[=+@\-]/,"'$&").replace(/"/g,'""')+'"';
    const url=URL.createObjectURL(new Blob([rows.map(row=>row.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='awr-filtered-routes.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('scope').textContent=`DBID ${data.dbid} · CON_DBID ${data.scope.con_dbid??'NULL'} / CON_ID ${data.scope.con_id??'NULL'} · ${data.requested[0].replace('T',' ')} to ${data.requested[1].replace('T',' ')} · source clock`;
  $('warnings').innerHTML=data.warnings.map(message=>`<div class="warning">${esc(message)}</div>`).join('');
  $('blockClass').insertAdjacentHTML('beforeend',data.classes.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join(''));
  $('frame').max=data.frames.length-1;
  $('provenance').textContent=`Generated ${data.generatedUtc}. ${data.frames.length} snapshot frames. Requested source-clock range: ${data.requested.join(' → ')}. Snapshot context CON_ID: ${data.snapshotContext??'NULL'}. ${data.issues.length} instance intervals flagged. Per-instance offsets are retained in the cache CSV begin/end fields when supplied by AWR.`;
  $('coverageDetails').textContent=JSON.stringify({snapshotCoverage:data.coverage,flaggedIntervals:data.issues},null,2);
  $('timingNote').textContent=data.timeUnit?`Verified input timing unit: ${data.timeUnit}. Verification reference: ${data.timeEvidence}. Mean transfer time uses total BLOCK_TIME deltas divided by received blocks; busy/congested times are not added twice.`:'Transfer timing is unavailable because its units have not been verified. Counts and blocks/sec remain available.';
  $('validation').textContent=data.validation;$('sql').textContent=payload.sql;
  $('previous').onclick=()=>{pause();seek(index-1);};$('next').onclick=()=>{pause();seek(index+1);};$('play').onclick=()=>timer?pause():play();
  $('frame').oninput=event=>{pause();seek(Number(event.target.value));};$('speed').onchange=()=>{if(timer){pause();play();}};
  ['blockClass','metric','limit'].forEach(id=>$(id).onchange=()=>{pause();refresh();});
  $('clear').onclick=()=>{pause();focus=null;refresh();};$('export').onclick=download;
  $('routes').onclick=event=>{const row=event.target.closest('[data-route]');if(row)select({type:'route',key:row.dataset.route});};
  $('cacheTable').onclick=event=>{const row=event.target.closest('[data-node]');if(row)select({type:'node',key:Number(row.dataset.node)});};
  document.addEventListener('keydown',event=>{if(/^(INPUT|SELECT|BUTTON|SUMMARY)$/.test(event.target.tagName))return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();pause();seek(index+(event.key==='ArrowRight'?1:-1));}else if(event.code==='Space'){event.preventDefault();timer?pause():play();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});window.addEventListener('pagehide',pause);
  let networkCompact=null;
  new ResizeObserver(()=>{Object.values(charts).forEach(c=>c.resize());const columns=Math.max(3,Math.min(18,Math.floor(($('matrix').clientWidth-100)/48)));if(columns!==matrixColumns&&summaries[index])renderMatrix(summaries[index]);const compact=$('network').clientWidth<500;if(compact!==networkCompact&&summaries[index]){networkCompact=compact;renderNetwork(summaries[index]);}}).observe(document.querySelector('main'));
  reduced.addEventListener('change',()=>render());
  // Read-only inspection surface for browser validation; does not expose credentials.
  window.awrTransfer={charts,get summaries(){return summaries;},get index(){return index;},get focus(){return focus;}};
  refresh();
})();

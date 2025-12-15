// Worker: aggregate listener CONNECT_DATA by minute/hour/service combinations
self.onmessage = e => {
  const {lines, mode, groupByService} = e.data;
  const total = lines.length;

  const MON={JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11,
             FEV:1,ABR:3,MAI:4,AGO:7,SET:8,OUT:9,DEZ:11};
  const TS=/^(\d{2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2})(?::\d{2}(?:[.,]\d+)?)?/;
  const counts={}, keys=new Set();
  let processed=0;

  for(const line of lines){
    if(!/CONNECT_DATA/i.test(line)){processed++;continue;}

    const svcMatch=line.match(/SERVICE_NAME\s*=\s*([\w.$-]+)/i);
    const svc=svcMatch?svcMatch[1]:"(unknown)";

    const m=line.match(TS);
    if(!m){processed++;continue;}
    let [_,dd,monStr,yyyy,hh,mm]=m;
    monStr=monStr.toUpperCase();
    const monIdx=MON[monStr];
    if(monIdx===undefined){processed++;continue;}

    const year=+yyyy, month=monIdx, day=+dd, hour=+hh, minute=+mm;
    const aggMinute=(mode==='hour')?0:minute;
    const baseKey=`${year}-${String(month+1).padStart(2,'0')}-${String(day).padStart(2,'0')} ${String(hour).padStart(2,'0')}:${String(aggMinute).padStart(2,'0')}`;

    const key = groupByService ? `${baseKey}|${svc}` : baseKey;
    counts[key]=(counts[key]||0)+1;
    keys.add(baseKey);

    processed++;
    if(processed%20000===0){
      postMessage({type:'progress',progress:(processed/total)*100});
    }
  }

  postMessage({type:'done',data:{counts,keys:Array.from(keys)}});
  close();
};


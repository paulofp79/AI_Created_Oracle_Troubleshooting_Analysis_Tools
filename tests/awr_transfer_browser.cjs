/* Run with Playwright available through NODE_PATH and a Python 3.10+ PYTHON_BIN.
 * Synthetic only: no repository connection, customer data, or deployed service.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'awr-transfer-browser-'));
const fixture = spawnSync(process.env.PYTHON_BIN || 'python3', ['-c', `
import json, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from test_awr_transfer import fixture, COUNTS, TIMES, build_dataset, render_report
f = fixture(instances=12, frame_count=9)
weights = [0,1,4,12,28,32,34,41,53,58]
for row in f['transfers']:
    n = row['snap_id']-100
    for key in COUNTS:
        increment = (row[key]-10000)/n if n else 0
        row[key] = 10000 + weights[n]*increment
    for key in TIMES:
        increment = (row[key]-1000000)/n if n else 0
        row[key] = 1000000 + weights[n]*increment*row['instance']
data = build_dataset(**f)
data['warnings'].append('Synthetic validation fixture. No customer database was queried.')
data['snapshotContext'] = 0
out = Path(sys.argv[1])
(out/'report.html').write_text(render_report(data, []))
(out/'dataset.json').write_text(json.dumps(data))
f = fixture(frame_count=4)
f['sga'] = []; f['sysstat'] = []
for row in f['transfers']:
    if row['instance_number']==1 and row['instance']==2:
        for key in COUNTS: row[key] = 10000
        for key in TIMES: row[key] = 1000000
f['transfers'] = [r for r in f['transfers'] if not(r['snap_id']==101 and r['instance_number']==2 and r['instance']==1)]
f['snapshots'] = [r for r in f['snapshots'] if r['snap_id']!=103]
(out/'gaps.html').write_text(render_report(build_dataset(**f), []))
`, output], {cwd:root, encoding:'utf8'});
assert.equal(fixture.status, 0, fixture.stderr);
const expected = JSON.parse(fs.readFileSync(path.join(output, 'dataset.json')));
const approximately = (actual, expected) => assert.ok(Math.abs(actual-expected) < 1e-6, `${actual} != ${expected}`);

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000},acceptDownloads:true});
    const errors = [], requests = [];
    page.on('pageerror', error=>errors.push(error.message));
    page.on('request', request=>{if(/^https?:/.test(request.url())) requests.push(request.url());});
    await page.goto('file://' + path.join(output, 'report.html'));
    await page.waitForFunction(()=>window.awrTransfer && Object.keys(window.awrTransfer.charts).length===4);
    const summary = await page.evaluate(()=>{
      const s = awrTransfer.summaries[0]; return {rate:s.totalRate,avg:s.avgUs,pct:s.congPct,valid:s.valid.length};
    });
    const rows=expected.frames[0].routes;
    const received=rows.reduce((sum,r)=>sum+r.cr+r.current,0);
    approximately(summary.rate,rows.reduce((sum,r)=>sum+r.totalRate,0));
    approximately(summary.avg,rows.reduce((sum,r)=>sum+r.timeUs,0)/received);
    approximately(summary.pct,rows.reduce((sum,r)=>sum+r.congested,0)/received*100);
    assert.equal(summary.valid,132);
    const arithmetic=rows.reduce((sum,r)=>sum+r.avgUs,0)/rows.length;
    assert.ok(Math.abs(summary.avg-arithmetic)>1,'Fixture must distinguish weighted from arithmetic mean');

    await page.locator('#next').click();
    assert.equal(await page.evaluate(()=>awrTransfer.index),1);
    await page.locator('#previous').click();
    await page.locator('#frame').fill('4');
    assert.equal(await page.evaluate(()=>awrTransfer.index),4);
    await page.locator('#speed').selectOption('250');
    await page.locator('#play').click();
    await page.waitForFunction(()=>awrTransfer.index>4);
    await page.locator('#play').click();
    assert.equal(await page.locator('#play').getAttribute('aria-pressed'),'false');
    await page.locator('#frame').fill('0');
    await page.locator('#blockClass').selectOption('data block');
    approximately(await page.evaluate(()=>awrTransfer.summaries[0].totalRate),summary.rate/3);
    await page.locator('#metric').selectOption('congestedRate');
    await page.locator('#limit').selectOption('12');
    assert.equal(await page.evaluate(()=>awrTransfer.charts.network.getOption().series[0].data.length),12);
    await page.locator('#routes [data-route]').first().click();
    assert.equal(await page.evaluate(()=>awrTransfer.summaries[0].valid.length),1);
    await page.locator('#clear').click();
    await page.locator('#cacheTable [data-node="2"]').click();
    assert.equal(await page.evaluate(()=>awrTransfer.focus.key),2);
    assert.equal(await page.evaluate(()=>awrTransfer.summaries[0].valid.length),22);
    await page.locator('#clear').click();
    await page.locator('#blockClass').selectOption('');
    await page.locator('#metric').selectOption('totalRate');

    // Real canvas clicks: instance drilldown, matrix drilldown, timeline scrub.
    async function canvasClick(name, series, value) {
      const locator=page.locator('#'+name);
      await locator.scrollIntoViewIfNeeded();
      const coords=await page.evaluate(({name,series,value})=>awrTransfer.charts[name].convertToPixel({seriesIndex:series},value),{name,series,value});
      const box=await locator.boundingBox();
      await page.mouse.click(box.x+coords[0],box.y+coords[1]);
    }
    const nodePoint=await page.evaluate(()=>awrTransfer.charts.network.getOption().series[1].data[0].value);
    await canvasClick('network',1,nodePoint);
    assert.equal(await page.evaluate(()=>awrTransfer.focus.type),'node');
    await page.locator('#clear').click();
    await canvasClick('matrix',0,[1,0]);
    assert.equal(await page.evaluate(()=>awrTransfer.focus.key),'1 → 2');
    await page.locator('#clear').click();
    const timePoint=await page.evaluate(()=>awrTransfer.charts.timeline.getOption().series[0].data[3].value);
    await canvasClick('timeline',0,timePoint);
    assert.equal(await page.evaluate(()=>awrTransfer.index),3);
    await page.evaluate(()=>awrTransfer.charts.matrix.dispatchAction({type:'dataZoom',dataZoomIndex:0,start:20,end:70}));
    const zoom=await page.evaluate(()=>awrTransfer.charts.matrix.getOption().dataZoom[0].start);
    await page.locator('#next').click();
    assert.equal(await page.evaluate(()=>awrTransfer.charts.matrix.getOption().dataZoom[0].start),zoom);

    const downloadPromise=page.waitForEvent('download');
    await page.locator('#export').click();
    const csv=await downloadPromise;
    await csv.saveAs(path.join(output,'routes.csv'));
    assert.equal(csv.suggestedFilename(),'awr-filtered-routes.csv');
    assert.ok(fs.readFileSync(path.join(output,'routes.csv'),'utf8').includes('-03:00'));
    // Each toolbox export uses this same ECharts PNG path.
    for(const name of ['network','timeline','activity','matrix']) {
      const image=await page.evaluate(name=>awrTransfer.charts[name].getDataURL({type:'png',pixelRatio:1}),name);
      assert.ok(image.startsWith('data:image/png;base64,'));
      assert.ok(image.length>5000);
    }
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
    await page.locator('#matrix').scrollIntoViewIfNeeded();
    const mobile=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,end:awrTransfer.charts.matrix.getOption().dataZoom[0].end}));
    assert.ok(mobile.scroll<=mobile.width);
    assert.ok(mobile.end<100,'Mobile matrix should allow scrolling across columns');
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.waitForFunction(()=>awrTransfer.charts.network.getOption().series[0].effect.show===false);
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    await page.goto('file://'+path.join(output,'gaps.html'));
    await page.waitForFunction(()=>window.awrTransfer);
    const gaps=await page.evaluate(()=>({
      zero:awrTransfer.summaries[0].routes.find(r=>r.key==='2 → 1').totalRate,
      missing:awrTransfer.summaries[0].routes.some(r=>r.key==='1 → 2'),
      cache:awrTransfer.summaries[0].cacheGiB,
      activity:awrTransfer.summaries[0].missRate,
      nulls:awrTransfer.charts.timeline.getOption().series[0].data.filter(p=>p.value[1]===null).length,
      last:awrTransfer.summaries.at(-1).totalRate
    }));
    assert.equal(gaps.zero,0);
    assert.equal(gaps.missing,false);
    assert.equal(gaps.cache,null);
    assert.equal(gaps.activity,null);
    assert.ok(gaps.nulls>=2);
    assert.equal(gaps.last,null);
    assert.deepEqual(errors,[]);
    assert.deepEqual(requests,[],'Standalone exports must be fully offline');
    console.log('PASS: offline charts, weighted math, playback, filters, canvas drilldown, zoom, CSV/PNG, mobile, reduced motion, zero/missing/gap cases.');
    console.log('Artifacts: '+output);
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});

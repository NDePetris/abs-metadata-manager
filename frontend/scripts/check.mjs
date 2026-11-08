import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {writeFile, mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const server = spawn('../.venv/Scripts/python.exe', ['../scripts/serve_bulk_fixture.py'], {stdio: 'inherit'});
let browser;
try {
  for (let i=0; i<100; i++) {try {if ((await fetch('http://127.0.0.1:8517')).ok) break;} catch {} await new Promise(r=>setTimeout(r,100));}
  browser = await chromium.launch({channel:'msedge', headless:true});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[]; page.on('pageerror', e=>errors.push(String(e)));
  await page.route('**/api/connect', r=>r.fulfill({json:{started:true}}));
  await page.route('**/api/job', r=>r.fulfill({json:{running:false,result:{libraries:[{id:'library-a',name:'Synthetic library',mediaType:'book'}],warnings:[]}}}), {times:1});
  await page.goto('http://127.0.0.1:8517');
  await page.getByLabel('ABS server URL').fill('http://synthetic.invalid');
  await page.getByLabel('API key', {exact:true}).fill('synthetic-only');
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByRole('option',{name:'Synthetic library'}).waitFor({state:'attached'});
  const downloaded=page.waitForEvent('download'); await page.getByRole('button',{name:'Export JSON',exact:true}).click();
  const file=await downloaded; const {readFile}=await import('node:fs/promises');
  const exported=JSON.parse(await readFile(await file.path(),'utf8')); assert.equal(exported.book_count,500);
  const proposals={schema_version:1,snapshot_id:exported.snapshot_id,scope:exported.scope,items:exported.books.map(b=>({item_id:b.item_id,identity:b.identity,changes:{title:{before:b.current.title,after:b.current.title+' corrected'}}}))};
  await page.getByLabel('Import corrections JSON').setInputFiles({name:'corrections.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(proposals))});
  await page.getByText('500 field differences imported',{exact:true}).waitFor();
  const timings=[];
  async function measure(action) {const start=performance.now(); await action(); await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))); timings.push(performance.now()-start);}
  let requests=0; page.on('request',r=>{if(r.url().includes('/api/')) requests++;});
  for (let i=0;i<10;i++) {
    await measure(()=>page.getByLabel('Filter books or fields').fill(i%2?'':'Synthetic Book 01'));
    await measure(()=>page.getByRole('button',{name:'Select eligible filtered fields',exact:true}).click());
    await measure(()=>page.getByRole('button',{name:'Clear selection',exact:true}).click());
  }
  assert.equal(requests,0,'Local interactions must not call server');
  await page.getByLabel('Filter books or fields').fill('');
  await measure(()=>page.getByRole('checkbox').first().check());
  await measure(()=>page.getByRole('checkbox').first().uncheck());
  await measure(()=>page.getByRole('button',{name:'Next',exact:true}).click());
  await measure(()=>page.getByRole('button',{name:'Previous',exact:true}).click());
  await page.getByText('Edit value',{exact:true}).first().click();
  await measure(()=>page.getByLabel('JSON value',{exact:true}).first().fill('"Edited synthetic title"'));
  await measure(()=>page.getByRole('button',{name:'Save value',exact:true}).first().click());
  assert.equal(requests,0,'Editing and exclusions must remain local');
  await page.getByLabel('Filter books or fields').fill('Synthetic Book 000');
  await page.getByRole('button',{name:'Select eligible filtered fields',exact:true}).click();
  await page.getByRole('button',{name:'Review audiobook update',exact:true}).click();
  const phrase=(await page.getByText(/^Type APPLY /).textContent()).replace('Type ','');
  await page.getByLabel(/^Type APPLY /).fill(phrase);
  await page.getByRole('button',{name:'Apply confirmed changes',exact:true}).click();
  await page.getByText('Readback verified',{exact:false}).waitFor();
  assert.equal(await page.getByRole('checkbox').first().isDisabled(),true);
  await page.getByLabel('Filter books or fields').fill('Synthetic Book 499');
  await page.getByRole('button',{name:'Select eligible filtered fields',exact:true}).click();
  const ebook=page.waitForEvent('download'); await page.getByRole('button',{name:'Download Calibre report',exact:true}).click();
  const ebookFile=await ebook; const report=JSON.parse(await readFile(await ebookFile.path(),'utf8')); assert.equal(report.corrections[0].kind,'ebook');
  await page.getByLabel('Filter books or fields').fill('');
  await mkdir('test-results',{recursive:true}); await page.screenshot({path:'test-results/bulk-desktop.png',fullPage:true});
  assert.deepEqual(errors,[]);
  timings.sort((a,b)=>a-b); const p95=timings[Math.ceil(timings.length*.95)-1];
  const result={books:500,samples:timings.length,p95_ms:Math.round(p95*10)/10,max_ms:Math.round(timings.at(-1)*10)/10,api_requests_during_local_actions:0,browser:await browser.version(),platform:process.platform,passed:p95<200};
  await writeFile('test-results/performance.json',JSON.stringify(result,null,2)); console.log(JSON.stringify(result)); assert.ok(p95<200,'p95 local interaction target is 200ms');
} finally {await browser?.close(); server.kill();}

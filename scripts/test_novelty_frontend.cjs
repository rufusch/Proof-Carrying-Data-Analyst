const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8010/');
  await page.waitForFunction(()=>document.querySelector('#connection').textContent==='Workspace ready',null,{timeout:90000});
  async function upload(content){
   await page.locator('#files').setInputFiles({name:'demo.csv',mimeType:'text/csv',buffer:Buffer.from(content)});
   await page.locator('#upload').click();
   await page.waitForFunction(()=>!document.querySelector('#analyze').disabled,null,{timeout:120000});
  }
  async function ask(question){await page.locator('#question').fill(question);await page.locator('#analyze').click();await page.locator('#review-panel').waitFor({state:'visible',timeout:180000});await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);}
  await upload('Month,Revenue\nMarch,EUR 100\nMarch,EUR 50\nApril,EUR 900\n');
  await ask('total revenue for March in USD');
  await page.locator('#recovery-form').waitFor({state:'visible'});
  assert.match(await page.locator('#recovery-code').textContent(),/def answer/);
  assert.match(await page.locator('#recovery-requirements').textContent(),/EUR/);
  await page.locator('#recovery-rate').fill('1.1');await page.locator('#recovery-source').fill('March rate supplied for this demo');
  await page.locator('#recover').click();
  await page.locator('#calculation-evidence').waitFor({state:'visible',timeout:180000});
  assert.equal(await page.locator('#metrics strong').textContent(),'USD 165');
  assert.match(await page.locator('#two-analyst-status').textContent(),/same result/ );
  assert.match(await page.locator('#skeptic-status').textContent(),/no conflicting answer/);
  assert.equal(await page.locator('#heatmap').count(),0); assert.match(await page.locator('#audit-code').textContent(),/connection.execute/); assert.equal(await page.title(),'SureCount — Make sense of your data'); assert.equal(await page.locator('#audit-panel').evaluate(el=>el.open),false); await page.locator('#audit-panel summary').click(); await page.locator('#copy-audit').click(); await page.waitForFunction(()=>document.querySelector('#copy-audit').textContent==='Copied');
  await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
  await page.screenshot({path:path.join(__dirname,'../.test-env/novelty-recovery.png'),fullPage:true});
  await upload('amount\n100\n100\n50\n');await ask('sum amount');
  assert.match(await page.locator('#verification').textContent(),/Outcome: refused/);
  assert.equal(await page.locator('#metrics strong').count(),0);
  assert.match(await page.locator('#sensitivity').textContent(),/-40%/);
  assert.equal(await page.locator('#heatmap').count(),0);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.screenshot({path:path.join(__dirname,'../.test-env/novelty-skeptic.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: parameterized EUR/USD refusal and recovery, two analysts, skeptic gating, -40% duplicate impact, copyable AuditCode, simplified review, mobile layout.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

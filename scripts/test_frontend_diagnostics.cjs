/* Optional real browser check; requires playwright and installed Edge on Windows. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
  try {
    const page = await browser.newPage();
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.TEST_FRONTEND_URL || 'http://127.0.0.1:8010/');
    await page.waitForFunction(()=>document.querySelector('#connection').textContent==='Workspace ready',null,{timeout:90000});
    await page.locator('#files').setInputFiles({name:'invalid.csv',mimeType:'text/csv',buffer:Buffer.from('amount,\n10,20\n')});
    await page.locator('#upload').click();
    await page.locator('#error').waitFor({state:'visible',timeout:120000});
    assert.match(await page.locator('#error').textContent(),/Invalid headers in columns 2/);
    assert.match(await page.locator('#dataset-status').textContent(),/Each column needs/);
    await page.waitForFunction(()=>!document.querySelector('#upload').disabled);
    await page.locator('#files').setInputFiles({name:'money.csv',mimeType:'text/csv',buffer:Buffer.from('Revenue\n"$1,000 USD"\n')});
    await page.locator('#upload').click();
    await page.waitForFunction(()=>!document.querySelector('#analyze').disabled,null,{timeout:120000});
    assert.equal(await page.locator('#error').isVisible(),false);
    await page.locator('#question').fill('sum Revenue');
    await page.locator('#analyze').click();
    await page.locator('#output').waitFor({state:'visible',timeout:120000});
    const reason=await page.locator('#narrative').textContent();
    assert.match(reason,/Revenue/); assert.match(reason,/numeric/); assert.match(reason,/Convert a copy/);
    assert.match(await page.locator('#verification').textContent(),/Outcome: refused/);
    assert.deepEqual(errors,[]);
    console.log('PASS: actual upload failure reason, recovery on next upload, and actionable nonnumeric-question refusal displayed in the browser.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

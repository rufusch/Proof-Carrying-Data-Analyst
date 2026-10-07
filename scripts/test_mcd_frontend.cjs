const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  try {
    const page=await browser.newPage();
    await page.goto('http://127.0.0.1:8010/');
    await page.waitForFunction(()=>document.querySelector('#connection').textContent==='Workspace ready',null,{timeout:90000});
    await page.locator('#files').setInputFiles(process.argv[2]);
    await page.locator('#upload').click();
    await page.waitForFunction(()=>!document.querySelector('#analyze').disabled,null,{timeout:120000});
    for(const [question,expected] of [
      ['What is the difference between Total Assets between 2021 and 2022?','-3.17 billion dollars'],
      ['Rate of growth for Total Assets', '-5.91418%'],
      ['Rate of growth for Revenue ($B) between 2002 and 2022','50.5195%']
    ]) {
      await page.locator('#question').fill(question);
      await page.locator('#analyze').click();
      if(question==='Rate of growth for Total Assets') {
        await page.locator('#clarification-form').waitFor({state:'visible',timeout:120000});
        await page.locator('input[name="choice"]').first().check();
        await page.locator('#answer').click();
      }
      await page.locator('#output').waitFor({state:'visible',timeout:180000});
      await page.locator('#calculation-evidence').waitFor({state:'visible',timeout:30000});
      assert.equal((await page.locator('#metrics strong').allTextContents()).at(-1),expected);
      const explanation=await page.locator('#narrative').textContent();
      assert.match(explanation,question.includes('2002')?/7.78 ÷ 15.4 × 100 = 50.5195%/:/50.43.*−.*53.6/);
      assert.equal(await page.locator('#result-json, #evidence-json').count(),0);
      assert.match(await page.locator('#calculation-evidence').textContent(),/independent recalculation matched/);
      await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
    }
    console.log('PASS: actual McDonalds CSV, exact user question, growth-period clarification, arithmetic explanations and units in browser.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

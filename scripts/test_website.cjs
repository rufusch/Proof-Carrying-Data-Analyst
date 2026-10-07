const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.platform==='win32'?{channel:'msedge'}:{})});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8010/');
  await page.waitForFunction(()=>document.querySelector('#connection').textContent==='Workspace ready',null,{timeout:90000});
  await page.getByRole('link',{name:'Explore your data'}).click();
  assert.equal(await page.evaluate(()=>location.hash),'#workspace');
  await page.getByRole('button',{name:'Total revenue'}).click();
  assert.equal(await page.locator('#question').inputValue(),'What is the total revenue?');
  await page.locator('#files').setInputFiles({name:'demo.csv',mimeType:'text/csv',buffer:Buffer.from('Revenue,Region\n10,West\n20,East\n')});
  assert.match(await page.locator('#file-selection').textContent(),/demo.csv/);
  await page.locator('#clear-files').click();
  assert.equal(await page.locator('#files').evaluate(el=>el.files.length),0);
  await page.evaluate(()=>{
   const transfer=new DataTransfer();transfer.items.add(new File(['Revenue,Region\n10,West\n20,East\n'],'demo.csv',{type:'text/csv'}));
   document.querySelector('#drop-zone').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer}));
  });
  assert.equal(await page.locator('#files').evaluate(el=>el.files.length),1);
  await page.locator('#upload').click();
  await page.waitForFunction(()=>!document.querySelector('#analyze').disabled,null,{timeout:120000});
  await page.locator('#analyze').click();
  await page.locator('#calculation-evidence').waitFor({state:'visible',timeout:180000});
  assert.equal(await page.locator('#metrics strong').textContent(),'30');
  assert.equal(await page.locator('#result-json,#evidence-json').count(),0);
  const download=page.waitForEvent('download');await page.locator('#download').click();
  assert.match((await download).suggestedFilename(),/analysis-.*\.json/);
  await page.screenshot({path:path.join(__dirname,'../.test-env/website-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(__dirname,'../.test-env/website-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.getByRole('link',{name:'How it works',exact:true}).click();
  assert.equal(await page.evaluate(()=>location.hash),'#guide');
  assert.deepEqual(errors,[]);
  console.log('PASS: navigation, suggestions, clear selection, drag/drop, upload, answer, download, mobile layout, no raw panels or browser errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

// Exercise the actual vendored WebAssembly build and Worker implementation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
let reply;
const self = {postMessage(value) {reply = value;}};
const sandbox = {self, Uint8Array, Math, Promise, importScripts() {self.cv = require('./web/vendor/opencv.js');}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'web/local-worker.js'), 'utf8'), sandbox);
let id = 0;
async function request(data) {reply = null; await self.onmessage({data:{...data,id:++id}}); assert(reply); assert(!reply.error, reply.error); return reply;}
function pixels(shift = 0, blank = false) {
  const result = new Uint8Array(400 * 300 * 4); let seed = 7;
  const pattern = new Uint8Array(400 * 300);
  for (let i=0;i<pattern.length;i++) {seed = (Math.imul(seed,1664525)+1013904223)|0;pattern[i] = seed>>>24;}
  for (let y=0;y<300;y++) for (let x=0;x<400;x++) {
    const i=(y*400+x)*4, value=blank?128:x>=shift?pattern[y*400+x-shift]:0;
    result[i]=result[i+1]=result[i+2]=value;result[i+3]=255;
  }
  return result.buffer;
}
(async()=>{
  await request({type:'reset'});
  let result = await request({width:400,height:300,pixels:pixels()});
  assert(result.metrics.sharpness > 40); assert.equal(result.motion,null);
  result = await request({width:400,height:300,pixels:pixels()});
  assert.equal(result.motion.status,'stationary');
  result = await request({width:400,height:300,pixels:pixels(5),remember:false});
  assert.equal(result.motion.status,'homography_dominant'); assert(result.motion.motion_percent>.5);
  await request({type:'commit'});
  result = await request({width:400,height:300,pixels:pixels(5)});
  assert.equal(result.motion.status,'stationary');
  await request({type:'reset'});
  result = await request({width:400,height:300,pixels:pixels(0,true)});
  assert.equal(result.metrics.sharpness,0);
  result = await request({width:400,height:300,pixels:pixels(0,true)});
  assert.equal(result.motion.status,'insufficient_features');
  const fflate = require('./web/vendor/fflate.js'); const chunks=[];
  const zip = new fflate.Zip((error,chunk)=>{assert.ifError(error);chunks.push(chunk);});
  const item=new fflate.ZipPassThrough('images/photo.jpg');zip.add(item);item.push(new Uint8Array([255,216,255]),true);zip.end();
  const archive = Buffer.concat(chunks.map(Buffer.from));
  assert.deepEqual([...fflate.unzipSync(archive)['images/photo.jpg']],[255,216,255]);
  console.log('Local WASM: quality, stationary/translation ambiguity, commit/reset, insufficient features and ZIP passed.');
  process.exit(0);
})().catch(error=>{console.error(error);process.exit(1);});

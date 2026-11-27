'use strict';
let engine, previous = null, pending = null;
const ready = new Promise((resolve, reject) => {
  try {
    importScripts('/vendor/opencv.js');
    self.cv.then(module => {engine = module; resolve(true);}, reject);
  } catch (error) {reject(error);}
});
function median(values) {values.sort((a,b)=>a-b); return values[Math.floor(values.length / 2)] || 0;}
function resize(gray, width) {
  const result = new engine.Mat(), scale = Math.min(1, width / gray.cols);
  engine.resize(gray,result,new engine.Size(Math.max(1,Math.round(gray.cols*scale)),Math.max(1,Math.round(gray.rows*scale))));
  return result;
}
function quality(gray) {
  const cv=engine, small=resize(gray,640), lap=new cv.Mat(), mean=new cv.Mat(), std=new cv.Mat();
  try {
    cv.Laplacian(small,lap,cv.CV_64F);cv.meanStdDev(lap,mean,std);const sharpness=std.data64F[0]**2;
    cv.meanStdDev(small,mean,std);const texture=std.data64F[0];let dark=0,bright=0;
    for (const x of small.data) {if(x<10)dark++;if(x>245)bright++;}
    return {sharpness,dark_fraction:dark/small.data.length,bright_fraction:bright/small.data.length,texture};
  } finally {[small,lap,mean,std].forEach(x=>x.delete());}
}
function motion(first,second) {
  const cv=engine, owned=[];const own=x=>(owned.push(x),x);
  const result={status:'insufficient_features',matches:0,inliers:0,motion_percent:null,homography_ratio:null,residual_percent:null};
  if(first.rows!==second.rows||first.cols!==second.cols)return {...result,status:'different_dimensions'};
  try {
    const detector=own(new cv.ORB()),ka=own(new cv.KeyPointVector()),kb=own(new cv.KeyPointVector());
    const da=own(new cv.Mat()),db=own(new cv.Mat()),mask=own(new cv.Mat());
    detector.detectAndCompute(first,mask,ka,da);detector.detectAndCompute(second,mask,kb,db);
    if(da.rows<2||db.rows<2)return result;
    const matcher=own(new cv.BFMatcher(cv.NORM_HAMMING,false));
    function match(a,b) {
      const matches=own(new cv.DMatchVectorVector());matcher.knnMatch(a,b,matches,2);
      const good=new Map();
      for(let i=0;i<matches.size();i++) {const pair=matches.get(i);try {if(pair.size()===2) {const m=pair.get(0),n=pair.get(1);if(m.distance<.75*n.distance)good.set(m.queryIdx,m.trainIdx);}} finally {pair.delete();}}
      return good;
    }
    const forward=match(da,db),backward=match(db,da),a=[],b=[];
    for(const [i,j] of forward)if(backward.get(j)===i) {const p=ka.get(i).pt,q=kb.get(j).pt;a.push(p.x,p.y);b.push(q.x,q.y);}
    result.matches=a.length/2;if(result.matches<20)return result;
    const pa=own(cv.matFromArray(result.matches,1,cv.CV_32FC2,a)),pb=own(cv.matFromArray(result.matches,1,cv.CV_32FC2,b));
    const inliers=own(new cv.Mat()),h=own(cv.findHomography(pa,pb,cv.RANSAC,3,inliers));
    if(h.empty())return {...result,status:'low_planar_consistency'};
    const projected=own(new cv.Mat());cv.perspectiveTransform(pa,projected,h);
    const diagonal=Math.hypot(first.cols,first.rows),movement=[],residual=[];
    for(let i=0;i<result.matches;i++) {
      if(inliers.data[i])movement.push(Math.hypot(b[2*i]-a[2*i],b[2*i+1]-a[2*i+1])/diagonal*100);
      residual.push(Math.hypot(b[2*i]-projected.data32F[2*i],b[2*i+1]-projected.data32F[2*i+1])/diagonal*100);
    }
    result.inliers=movement.length;result.motion_percent=median(movement);result.residual_percent=median(residual);result.homography_ratio=movement.length/result.matches;
    result.status=movement.length<15||result.homography_ratio<.25?'low_planar_consistency':result.motion_percent<.3&&result.residual_percent<.15?'stationary':result.homography_ratio>.9&&result.residual_percent<.15?'homography_dominant':'tracked';
    return result;
  } finally {owned.reverse().forEach(x=>x.delete());}
}
self.onmessage=async ({data})=>{
  try {
    await ready;
    if(data.type==='reset') {previous?.delete();pending?.delete();previous=pending=null;self.postMessage({id:data.id,ok:true});return;}
    if(data.type==='commit') {previous?.delete();previous=pending;pending=null;self.postMessage({id:data.id,ok:true});return;}
    const cv=engine,rgba=cv.matFromArray(data.height,data.width,cv.CV_8UC4,new Uint8Array(data.pixels)),gray=new cv.Mat();
    try {
      cv.cvtColor(rgba,gray,cv.COLOR_RGBA2GRAY);
      const metrics=quality(gray),current=resize(gray,800);
      try {
        const pair=previous?motion(previous,current):null;
        pending?.delete();pending=current.clone();
        if(data.remember!==false){previous?.delete();previous=pending;pending=null;}
        self.postMessage({id:data.id,metrics,motion:pair,engine:'OpenCV.js 4.13.0 / WebAssembly'});
      } finally {current.delete();}
    } finally {rgba.delete();gray.delete();}
  } catch(error) {self.postMessage({id:data.id,error:String(error.message||error)});}
};

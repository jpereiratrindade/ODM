'use strict';
let calculate;
let engine;
const ready = (async () => {
  try {
    const response = await fetch('/wasm/gsd.wasm');
    if (!response.ok) throw new Error('Módulo indisponível');
    const {instance} = await WebAssembly.instantiate(await response.arrayBuffer());
    calculate = instance.exports.gsd;
    engine = 'WebAssembly';
  } catch {
    calculate = (altitude, sensor, focal, pixels) => altitude * sensor / focal / pixels * 100;
    engine = 'JavaScript (fallback)';
  }
  self.postMessage({type: 'ready', engine});
})();
self.onmessage = async ({data}) => {
  await ready;
  const values = data.values;
  if (!Array.isArray(values) || values.length !== 4 || values.some(v => !Number.isFinite(v) || v <= 0)) {
    self.postMessage({type: 'error', error: 'Informe quatro valores positivos.'});
    return;
  }
  self.postMessage({type: 'result', value: calculate(...values), engine});
};

/* In-place radix-2 FFT, shared by browser worker and numerical tests. */
(function (root) {
  'use strict';
  function fft(re, im) {
    const n = re.length;
    if (!n || (n & (n - 1)) || im.length !== n) throw Error('Invalid FFT size');
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const angle = -2 * Math.PI / len, cr = Math.cos(angle), ci = Math.sin(angle);
      for (let i = 0; i < n; i += len) {
        let wr = 1, wi = 0;
        for (let j = 0; j < len / 2; j++) {
          const a = i + j, b = a + len / 2;
          const tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
          re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
          const next = wr * cr - wi * ci; wi = wr * ci + wi * cr; wr = next;
        }
      }
    }
  }
  function spectrum(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < 32 || view.getUint32(0, true) !== 0x3151495a) throw Error('Invalid IQ header');
    const n = view.getUint32(16, true);
    if (n < 1024 || n > 16384 || (n & (n - 1)) || view.byteLength !== 32 + n * 4) throw Error('Invalid IQ length');
    const re = new Float64Array(n), im = new Float64Array(n);
    let sum = 0;
    for (let i = 0; i < n; i++) {
      const w = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1)); sum += w;
      re[i] = view.getInt16(32 + 4 * i, true) * w / 32768;
      im[i] = view.getInt16(34 + 4 * i, true) * w / 32768;
    }
    fft(re, im);
    const db = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const k = (i + n / 2) % n;
      db[i] = 20 * Math.log10(Math.max(1e-7, Math.hypot(re[k], im[k]) / sum));
    }
    return {db, sequence: view.getUint32(4, true), frequency: view.getUint32(8, true),
      rate: view.getUint32(12, true), gain: view.getUint32(20, true),
      dropped: Number(view.getBigUint64(24, true))};
  }
  root.ZeroFFT = {fft, spectrum};
  if (typeof module !== 'undefined') module.exports = root.ZeroFFT;
})(globalThis);

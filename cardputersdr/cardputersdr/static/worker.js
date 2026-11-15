importScripts('/fft.js');
onmessage = ({data}) => {
  try {
    const result = ZeroFFT.spectrum(data);
    postMessage(result, [result.db.buffer]);
  } catch (e) { postMessage({error: e.message}); }
};

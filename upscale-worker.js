// 3DRBXMT Upscale Worker - High-quality texture resampling
// Same-origin worker loaded from /upscale-worker.js (precached by service worker)

const KERNELS = {
  lanczos3: { type: 'lanczos', lobes: 3, radius: 3 },
  robidouxsharp: { type: 'mitchell', B: 0.375, C: 0.375, radius: 2 },
  catmullrom: { type: 'mitchell', B: 0, C: 0.5, radius: 2 },
  spline64: { type: 'spline', lobes: 4, radius: 4 },
  nearest: { type: 'nearest', radius: 0.5 }
};

function mitchellWeight(x, B, C) {
  const absX = Math.abs(x);
  if (absX < 1) {
    return ((12 - 9 * B - 6 * C) * absX * absX * absX + (-18 + 12 * B + 6 * C) * absX * absX + (6 - 2 * B)) / 6;
  } else if (absX < 2) {
    return ((-B - 6 * C) * absX * absX * absX + (6 * B + 30 * C) * absX * absX + (-12 * B - 48 * C) * absX + (8 * B + 24 * C)) / 6;
  }
  return 0;
}

function lanczosWeight(x, lobes) {
  const absX = Math.abs(x);
  if (absX < 1e-10) return 1; // handle x=0 limit
  if (absX >= lobes) return 0;
  const piX = Math.PI * absX;
  return (lobes * Math.sin(piX) * Math.sin(piX / lobes)) / (piX * piX);
}

// Spline64 = 4-lobe B-spline (Keys spline with B=0, C=0.5, but 4 lobes)
// Using the standard cubic B-spline basis scaled to 4 lobes
function splineWeight(x, lobes) {
  const absX = Math.abs(x);
  if (absX >= lobes) return 0;
  // Uniform B-spline basis function of degree 3 (cubic), 4 lobes
  // B3(x) = 1/6 * (|x|^3 - 4|x|^2 + 6|x| - 4) for 1 <= |x| < 2
  // B3(x) = 1/6 * (4 - 6|x|^2 + 3|x|^3) for 0 <= |x| < 1
  // B3(x) = 1/6 * (2 - |x|)^3 for 2 <= |x| < 3
  // B3(x) = 1/6 * (4 - |x|)^3 for 3 <= |x| < 4
  // Normalized to sum to 1
  if (absX < 1) {
    return (4 - 6 * absX * absX + 3 * absX * absX * absX) / 6;
  } else if (absX < 2) {
    return (4 - 8 * absX + 5 * absX * absX - absX * absX * absX) / 6;
  } else if (absX < 3) {
    return Math.pow(2 - absX, 3) / 6;
  } else if (absX < 4) {
    return Math.pow(4 - absX, 3) / 6;
  }
  return 0;
}

function buildKernelWeights(kernelName, scale) {
  const kernel = KERNELS[kernelName];
  if (!kernel) return { weights: new Float32Array([1]), radius: 0, isNearest: true };

  if (kernel.type === 'nearest') {
    return { weights: new Float32Array([1]), radius: 0, isNearest: true };
  }

  const radius = kernel.radius;
  const support = Math.ceil(radius * (scale < 1 ? 1 / scale : 1));
  const size = support * 2 + 1;
  const weights = new Float32Array(size);
  let sum = 0;

  for (let i = 0; i < size; i++) {
    const x = (i - support) * (scale < 1 ? scale : 1);
    let w = 0;
    if (kernel.type === 'mitchell') {
      w = mitchellWeight(x, kernel.B, kernel.C);
    } else if (kernel.type === 'lanczos') {
      w = lanczosWeight(x, kernel.lobes);
    } else if (kernel.type === 'spline') {
      w = splineWeight(x, kernel.lobes);
    }
    weights[i] = w;
    sum += w;
  }

  for (let i = 0; i < size; i++) weights[i] /= sum;

  return { weights, radius: support, isNearest: false };
}

// Single resample pass (horizontal then vertical)
function resamplePass(srcData, srcW, srcH, dstW, dstH, kernelName) {
  const hKernel = buildKernelWeights(kernelName, srcW / dstW);
  const vKernel = buildKernelWeights(kernelName, srcH / dstH);

  // Horizontal pass
  const hData = resampleHorizontal(srcData, srcW, srcH, dstW, hKernel.weights, hKernel.radius, hKernel.isNearest);

  // Vertical pass
  const vData = resampleVertical(hData, dstW, srcH, dstH, vKernel.weights, vKernel.radius, vKernel.isNearest);

  return vData;
}

function resampleHorizontal(srcData, srcW, srcH, dstW, kernelWeights, radius, isNearest) {
  const dstData = new Float32Array(dstW * srcH * 4);
  const scaleX = srcW / dstW;

  for (let y = 0; y < srcH; y++) {
    for (let x = 0; x < dstW; x++) {
      const srcX = (x + 0.5) * scaleX - 0.5;
      const srcXi = Math.floor(srcX);

      if (isNearest) {
        const xi = Math.min(Math.max(Math.round(srcX), 0), srcW - 1);
        const si = (y * srcW + xi) * 4;
        const di = (y * dstW + x) * 4;
        dstData[di] = srcData[si];
        dstData[di + 1] = srcData[si + 1];
        dstData[di + 2] = srcData[si + 2];
        dstData[di + 3] = srcData[si + 3];
      } else {
        let r = 0, g = 0, b = 0, a = 0, wsum = 0;
        for (let k = -radius; k <= radius; k++) {
          const xi = srcXi + k;
          if (xi < 0 || xi >= srcW) continue;
          const si = (y * srcW + xi) * 4;
          const w = kernelWeights[k + radius];
          // Premultiply alpha
          const pa = srcData[si + 3] / 255;
          r += srcData[si] * pa * w;
          g += srcData[si + 1] * pa * w;
          b += srcData[si + 2] * pa * w;
          a += srcData[si + 3] * w;
          wsum += w;
        }
        const di = (y * dstW + x) * 4;
        if (a > 0 && wsum > 0) {
          const ia = 255 / a;
          dstData[di] = Math.min(255, Math.max(0, r * ia));
          dstData[di + 1] = Math.min(255, Math.max(0, g * ia));
          dstData[di + 2] = Math.min(255, Math.max(0, b * ia));
          dstData[di + 3] = Math.min(255, Math.max(0, a));
        } else {
          dstData[di] = dstData[di + 1] = dstData[di + 2] = dstData[di + 3] = 0;
        }
      }
    }
  }
  return dstData;
}

function resampleVertical(srcData, srcW, srcH, dstH, kernelWeights, radius, isNearest) {
  const dstData = new Float32Array(srcW * dstH * 4);
  const scaleY = srcH / dstH;

  for (let y = 0; y < dstH; y++) {
    const srcY = (y + 0.5) * scaleY - 0.5;
    const srcYi = Math.floor(srcY);

    for (let x = 0; x < srcW; x++) {
      if (isNearest) {
        const yi = Math.min(Math.max(Math.round(srcY), 0), srcH - 1);
        const si = (yi * srcW + x) * 4;
        const di = (y * srcW + x) * 4;
        dstData[di] = srcData[si];
        dstData[di + 1] = srcData[si + 1];
        dstData[di + 2] = srcData[si + 2];
        dstData[di + 3] = srcData[si + 3];
      } else {
        let r = 0, g = 0, b = 0, a = 0, wsum = 0;
        for (let k = -radius; k <= radius; k++) {
          const yi = srcYi + k;
          if (yi < 0 || yi >= srcH) continue;
          const si = (yi * srcW + x) * 4;
          const w = kernelWeights[k + radius];
          const pa = srcData[si + 3] / 255;
          r += srcData[si] * pa * w;
          g += srcData[si + 1] * pa * w;
          b += srcData[si + 2] * pa * w;
          a += srcData[si + 3] * w;
          wsum += w;
        }
        const di = (y * srcW + x) * 4;
        if (a > 0 && wsum > 0) {
          const ia = 255 / a;
          dstData[di] = Math.min(255, Math.max(0, r * ia));
          dstData[di + 1] = Math.min(255, Math.max(0, g * ia));
          dstData[di + 2] = Math.min(255, Math.max(0, b * ia));
          dstData[di + 3] = Math.min(255, Math.max(0, a));
        } else {
          dstData[di] = dstData[di + 1] = dstData[di + 2] = dstData[di + 3] = 0;
        }
      }
    }
  }
  return dstData;
}

self.onmessage = function(e) {
  const { srcData, srcW, srcH, dstW, dstH, kernel, cancelToken } = e.data;

  try {
    // Check if this is the 2xSSAA Spline64 special mode
    const isSpline64_2xSSAA = (kernel === 'spline64-2xssaa');
    const scaleX = srcW / dstW;
    const scaleY = srcH / dstH;
    const avgScale = (scaleX + scaleY) / 2; // approximate uniform scale

    let finalData;

    if (isSpline64_2xSSAA && Math.abs(avgScale - 2) < 0.1) {
      // 2xSSAA Spline64: 2-pass supersampling (4x up → 2x down with Spline64)
      // This is specifically for ~2x upscale
      const midW = srcW * 2; // 2x intermediate (4x relative to final 2x output = wait...)
      // Actually: target is 2x. 2xSSAA means:
      // Pass 1: upscale 4x (2x the target scale) → intermediate at 4x original
      // Pass 2: downscale 2x → final at 2x original
      // So if src=100, dst=200 (2x), mid=400 (4x), then downscale to 200

      const intermediateW = srcW * 4;
      const intermediateH = srcH * 4;

      // Progress: starting first pass (4x up)
      self.postMessage({ type: 'progress', progress: 0.1 });

      // Pass 1: 4x upscale with Spline64
      const midData = resamplePass(srcData, srcW, srcH, intermediateW, intermediateH, 'spline64');

      // Progress: first pass done
      self.postMessage({ type: 'progress', progress: 0.5 });

      // Check cancel
      if (cancelToken && cancelToken.cancelled) return;

      // Pass 2: 2x downscale with Spline64 (from 4x to 2x)
      finalData = resamplePass(midData, intermediateW, intermediateH, dstW, dstH, 'spline64');

      // Progress: second pass done
      self.postMessage({ type: 'progress', progress: 0.9 });

    } else {
      // Single-pass for all other kernels (including spline64 non-SSAA)
      const effectiveKernel = isSpline64_2xSSAA ? 'spline64' : kernel;
      const hKernel = buildKernelWeights(effectiveKernel, scaleX);
      const vKernel = buildKernelWeights(effectiveKernel, scaleY);

      // Horizontal pass
      const hData = resampleHorizontal(srcData, srcW, srcH, dstW, hKernel.weights, hKernel.radius, hKernel.isNearest);

      // Progress
      self.postMessage({ type: 'progress', progress: 0.5 });

      // Check cancel
      if (cancelToken && cancelToken.cancelled) return;

      // Vertical pass
      finalData = resampleVertical(hData, dstW, srcH, dstH, vKernel.weights, vKernel.radius, vKernel.isNearest);
    }

    // Convert to Uint8ClampedArray
    const result = new Uint8ClampedArray(finalData.length);
    for (let i = 0; i < finalData.length; i++) {
      result[i] = Math.min(255, Math.max(0, Math.round(finalData[i])));
    }

    self.postMessage({ type: 'done', data: result, dstW, dstH }, [result.buffer]);
  } catch (err) {
    self.postMessage({ type: 'error', error: err.message });
  }
};
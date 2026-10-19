// Pre-cache renderer retained as a pixel-equivalence oracle.
function referenceDrawTexture(out, w, h, cx, cy, r, tex, opacity, metadata) {
  const minX = Math.max(0, Math.floor(cx - r));
  const maxX = Math.min(w - 1, Math.ceil(cx + r));
  const minY = Math.max(0, Math.floor(cy - r));
  const maxY = Math.min(h - 1, Math.ceil(cy + r));
  const alphaScale = clamp(opacity, 0, 1);
  const srcW = tex.sourceWidth || tex.width;
  const srcH = tex.sourceHeight || tex.height;
  const textureCenter = metadata.texture_center || [srcW / 2, srcH / 2];
  const textureRadius = Number(metadata.texture_radius || Math.min(srcW, srcH) / 2);
  const tcx = Number(textureCenter[0]);
  const tcy = Number(textureCenter[1]);
  const mapping = textureMapping(metadata, tcx, tcy, textureRadius, texScale(tex));
  const mCx = mapping.centerX;
  const mCy = mapping.centerY;
  const mRx = mapping.radiusX;
  const mRy = mapping.radiusY;
  const mCrX = mapping.crvalX;
  const mCrY = mapping.crvalY;
  const mC = mapping.c;
  const mS = mapping.s;
  const tw = tex.width;
  const th = tex.height;
  const td = tex.data;
  // Inverse view rotation inlined: quatRotate(conj(viewQuat), v) without per-pixel allocation.
  const qw = viewQuat.w;
  const qx = -viewQuat.x;
  const qy = -viewQuat.y;
  const qz = -viewQuat.z;
  // Multi-epoch alignment: transform the base-frame vector into this layer's
  // own observer frame before sampling (identity for the reference layer).
  const frame = layerObserverFrame(metadata);
  const fSbR = frame ? frame.sbR : 0;
  const fCbR = frame ? frame.cbR : 1;
  const fSD = frame ? frame.sD : 0;
  const fCD = frame ? frame.cD : 1;
  const fSbI = frame ? frame.sbI : 0;
  const fCbI = frame ? frame.cbI : 1;

  for (let py = minY; py <= maxY; py++) {
    const dy = (py - cy) / r;
    const rrY = dy * dy;
    for (let px = minX; px <= maxX; px++) {
      const dx = (px - cx) / r;
      const rr = dx * dx + rrY;
      if (rr > 1) continue;
      const vz = Math.sqrt(1 - rr);
      const vx = dx;
      const vy = -dy;
      const t2x = 2 * (qy * vz - qz * vy);
      const t2y = 2 * (qz * vx - qx * vz);
      const t2z = 2 * (qx * vy - qy * vx);
      const wx = vx + qw * t2x + (qy * t2z - qz * t2y);
      const wy = vy + qw * t2y + (qz * t2x - qx * t2z);
      const wz = vz + qw * t2z + (qx * t2y - qy * t2x);
      let sx = wx;
      let sy = wy;
      let sz = wz;
      if (frame) {
        const hy = fCbR * wy + fSbR * wz;
        const hz = -fSbR * wy + fCbR * wz;
        const hx = wx * fCD - hz * fSD;
        const hz2 = wx * fSD + hz * fCD;
        sx = hx;
        sy = fCbI * hy - fSbI * hz2;
        sz = fSbI * hy + fCbI * hz2;
      }
      if (sz < 0 || sx * sx + sy * sy > 1.02) continue;

      const sampleX = mC * sx + mS * sy;
      const sampleY = -mS * sx + mC * sy;
      const tx = Math.max(0, Math.min(tw - 1, Math.floor(mCx + sampleX * mRx - mCrX)));
      const ty = Math.max(0, Math.min(th - 1, Math.floor(mCy - sampleY * mRy + mCrY)));
      const si = (ty * tw + tx) * 4;
      const a = (td[si + 3] / 255) * alphaScale;
      if (a <= 0) continue;
      const di = (py * w + px) * 4;
      out[di] = out[di] * (1 - a) + td[si] * a;
      out[di + 1] = out[di + 1] * (1 - a) + td[si + 1] * a;
      out[di + 2] = out[di + 2] * (1 - a) + td[si + 2] * a;
      out[di + 3] = 255;
    }
  }
}

function referenceSampleVisibleSurfaceLayers(basePoint) {
  if (basePoint.z < 0) return [5, 7, 10, 255];
  let out = [5, 7, 10, 255];
  const visibleLayers = layers.filter((layer) => layer.visible && layerMode(layer) === panelMode && layer.kind === "image");
  for (const layer of visibleLayers) {
    const tex = textureGetPreferFull(layer);
    if (!tex) continue;
    const metadata = layer.metadata || {};
    if (metadata.render_mode === "corona") continue;
    const frame = layerObserverFrame(metadata);
    const point = frame ? applyObserverFrame(frame, basePoint) : basePoint;
    if (point.z < 0) continue;  // this spot was on the far side for that epoch
    const srcW = tex.sourceWidth || tex.width;
    const srcH = tex.sourceHeight || tex.height;
    const textureCenter = metadata.texture_center || [srcW / 2, srcH / 2];
    const textureRadius = Number(metadata.texture_radius || Math.min(srcW, srcH) / 2);
    const mapping = textureMapping(metadata, Number(textureCenter[0]), Number(textureCenter[1]), textureRadius, texScale(tex));
    const sample = planeToTexture(point.x, point.y, mapping);
    if (sample.tx < 0 || sample.tx >= tex.width || sample.ty < 0 || sample.ty >= tex.height) continue;
    const si = (sample.ty * tex.width + sample.tx) * 4;
    const a = (tex.data[si + 3] / 255) * clamp(layer.opacity, 0, 1);
    if (a <= 0) continue;
    out[0] = out[0] * (1 - a) + tex.data[si] * a;
    out[1] = out[1] * (1 - a) + tex.data[si + 1] * a;
    out[2] = out[2] * (1 - a) + tex.data[si + 2] * a;
  }
  return out;
}

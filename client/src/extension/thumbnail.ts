// JPEG copies of screenshots, made in the worker with OffscreenCanvas: small thumbnails beside a screenshot row of the
// activity block and on the chip of a "Screenshot of this tab", and the full-size copy a vision model gets.

/** The image redrawn as JPEG, at most `max` pixels on its longer side; base64 without a data: prefix. */
async function toJpegBase64(
  base64: string,
  mimeType: string,
  max: number,
  quality: number,
): Promise<string | undefined> {
  if (typeof OffscreenCanvas === 'undefined' || typeof createImageBitmap === 'undefined') return undefined;
  try {
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: mimeType }));
    const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = new OffscreenCanvas(width, height);
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();
    const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
    const out = new Uint8Array(await blob.arrayBuffer());
    let binary = '';
    for (let i = 0; i < out.length; i += 0x8000) binary += String.fromCharCode(...out.subarray(i, i + 0x8000));
    return btoa(binary);
  } catch {
    return undefined;
  }
}

export async function thumbnail(base64: string, mimeType: string, max = 240): Promise<string | undefined> {
  const jpeg = await toJpegBase64(base64, mimeType, max, 0.7);
  return jpeg ? `data:image/jpeg;base64,${jpeg}` : undefined;
}

/**
 * A screenshot for the model as JPEG at full size: several times smaller than the layer's PNG, so a request crosses a
 * slow link (a tunnel to a remote server) much faster. Quality 0.85 keeps the labels' small text readable.
 */
export async function modelImage(
  base64: string,
  mimeType: string,
): Promise<{ data: string; mediaType: string } | undefined> {
  if (mimeType === 'image/jpeg') return undefined;
  const jpeg = await toJpegBase64(base64, mimeType, Infinity, 0.85);
  return jpeg ? { data: jpeg, mediaType: 'image/jpeg' } : undefined;
}

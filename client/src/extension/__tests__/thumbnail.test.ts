import { afterEach, describe, expect, it, vi } from 'vitest';
import { modelImage } from '../thumbnail';
import { privacy } from '../privacy';
import { withholdUnknownImages } from '@src/privacy/net-wrapper';

afterEach(() => vi.unstubAllGlobals());

function converter(jpeg: string) {
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 1328, height: 813, close: vi.fn() }));
  vi.stubGlobal(
    'OffscreenCanvas',
    class {
      getContext() {
        return { drawImage: vi.fn() };
      }
      async convertToBlob() {
        return new Blob([Uint8Array.from(atob(jpeg), c => c.charCodeAt(0))], { type: 'image/jpeg' });
      }
    },
  );
}

describe('model screenshot conversion', () => {
  it('keeps the converted masked screenshot in the actual outgoing image content', async () => {
    const png = btoa('masked screenshot original');
    const jpeg = btoa('converted masked screenshot');
    converter(jpeg);
    privacy.images.allow(png, 'masked-screenshot');
    const converted = await modelImage(png, 'image/png');
    expect(converted).toEqual({ data: jpeg, mediaType: 'image/jpeg' });
    const body = {
      messages: [
        { role: 'user', content: [{ type: 'image_url', image_url: { url: `data:image/jpeg;base64,${jpeg}` } }] },
      ],
    };
    expect(withholdUnknownImages(body, privacy.images)).toEqual({ body, withheld: 0 });
    expect(privacy.images.sourceOf(jpeg)).toBe('masked-screenshot');
  });

  it('does not allow conversion to approve an image that was never masked or attached', async () => {
    const png = btoa('unapproved original');
    const jpeg = btoa('converted unapproved image');
    converter(jpeg);
    expect(await modelImage(png, 'image/png')).toBeUndefined();
    expect(privacy.images.sourceOf(jpeg)).toBeUndefined();
  });

  it('falls back to the original approved image when conversion fails', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('decode failed')));
    vi.stubGlobal('OffscreenCanvas', class {});
    const png = btoa('approved original with failed conversion');
    privacy.images.allow(png, 'masked-screenshot');
    expect(await modelImage(png, 'image/png')).toBeUndefined();
    expect(privacy.images.sourceOf(png)).toBe('masked-screenshot');
  });
});

import { describe, expect, it } from 'vitest';
import { createPrivacyContext } from '../context';
import { detect } from '../detectors';
import { ImageRegistry } from '../image-registry';
import { findLeaks } from '../leak-check';
import { privacyFetch, REDACTION_HEADER, WITHHELD_IMAGE_TEXT, withholdUnknownImages } from '../net-wrapper';
import { redactBody, redactText } from '../redactor';
import { swapIn } from '../swap-back';
import { Vault } from '../vault';

const IMAGE = 'iVBORw0KGgo' + 'A'.repeat(600);

describe('detectors', () => {
  it('finds email, phone with separators, a valid card and an SSN', () => {
    const kinds = detect('mail a@b.co, call +1 415 555 0134, card 4111 1111 1111 1111, ssn 123-45-6789').map(
      h => h.kind,
    );
    expect(kinds).toEqual(['EMAIL', 'PHONE', 'CARD', 'ID']);
  });

  it('leaves tab ids, refs and plain digit runs alone', () => {
    expect(detect('active tab: [1400559681] «New Tab» [e12] button «Next» 4111111111111112')).toEqual([]);
  });

  it('reads the real snapshot format (JSON-style quotes, from perception/outline.ts)', () => {
    const outline =
      '[bo60] textbox "Full name" (focused) = "Jane Doe"\n[bo61] textbox "Coupon" = "SAVE10"\n[bo62] button "Add question"';
    expect(detect(outline)).toEqual([expect.objectContaining({ value: 'Jane Doe', kind: 'NAME' })]);
    const vault = new Vault();
    expect(redactText(outline, vault)).toContain('textbox "Full name" (focused) = "[[NAME_1]]"');
    expect(detect('[a1] textbox "Your \\"nick\\" name" = "Ada"').map(h => h.value)).toEqual(['Ada']);
  });

  it('tags the value of a field whose label names personal data', () => {
    const hits = detect('[e3] textbox «Full name» (focused) = «Jane Doe»\n[e4] textbox «Coupon» = «SAVE10»');
    expect(hits).toEqual([expect.objectContaining({ value: 'Jane Doe', kind: 'NAME' })]);
  });

  it('finds a name the user states in plain text', () => {
    expect(detect('Fill it in (my name is Jane Doe, email x@y.org)').map(h => h.value)).toEqual([
      'Jane Doe',
      'x@y.org',
    ]);
    expect(detect('Name: Ada Lovelace').map(h => h.value)).toEqual(['Ada Lovelace']);
    expect(detect('the name of the form')).toEqual([]);
  });

  it('applies learned literal and label rules', () => {
    const hits = detect('Hello Jayant, [e5] textbox «Employee code» = «X-77»', [
      { id: '1', kind: 'NAME', literal: 'jayant' },
      { id: '2', kind: 'ID', label: 'employee code' },
    ]);
    expect(hits.map(h => h.value)).toEqual(['Jayant', 'X-77']);
  });
});

describe('redactText', () => {
  it('replaces vault values first, so a value no pattern finds is still tagged', () => {
    const vault = new Vault();
    vault.tagFor('Jane Doe', 'NAME');
    expect(redactText('text «Welcome back, jane   doe»', vault)).toBe('text «Welcome back, [[NAME_1]]»');
  });

  it('keeps one tag per value across calls and does not re-tag a tag', () => {
    const vault = new Vault();
    const first = redactText('[e3] textbox «Email» = «jane@x.org»', vault);
    const second = redactText(first + ' and jane@x.org', vault);
    expect(second).toBe('[e3] textbox «Email» = «[[EMAIL_1]]» and [[EMAIL_1]]');
    expect(vault.size).toBe(1);
  });
});

describe('the end-to-end run (2026-10-18): a page that echoes the submitted form', () => {
  const echoed = '<pre>kind=contact&name=Jane+Doe&email=jane.doe%40example.com&phone=%2B1+415+555+0134</pre>';

  it('redacts URL- and form-encoded values, known or not', () => {
    const known = new Vault();
    known.tagFor('Jane Doe', 'NAME');
    known.tagFor('jane.doe@example.com', 'EMAIL');
    known.tagFor('+1 415 555 0134', 'PHONE');
    expect(redactText(echoed, known)).toBe(
      '<pre>kind=contact&name=[[NAME_1]]&email=[[EMAIL_1]]&phone=[[PHONE_1]]</pre>',
    );

    const fresh = new Vault();
    const out = redactText(echoed, fresh);
    expect(out).not.toMatch(/jane\.doe%40|415\+555/i);
    expect(findLeaks(out, fresh, new ImageRegistry())).toEqual([]);
  });

  it('applies a tag issued late in the body to strings read before it, so the request is not blocked', () => {
    const vault = new Vault();
    const body = { messages: [{ content: 'Thanks, Jane Doe!' }, { content: 'My name is Jane Doe' }] };
    const { body: out } = redactBody(body, vault);
    expect(JSON.stringify(out)).not.toContain('Jane');
    expect(findLeaks(out, vault, new ImageRegistry())).toEqual([]);
  });

  it('the leak check sees an encoded value the redactor was not given', () => {
    const leaks = findLeaks('email=jane.doe%40example.com', new Vault(), new ImageRegistry());
    expect(leaks.map(l => l.reason)).toEqual(['detector-hit']);
  });
});

describe('swapIn', () => {
  const vault = new Vault();
  vault.tagFor('Jane Doe', 'NAME');

  it('puts real values into nested arguments, mixed text included', () => {
    const r = swapIn({ fields: [{ target: { ref: 'e3' }, value: 'Hi [[NAME_1]]!' }] }, vault);
    expect(r).toEqual({ ok: true, swapped: 1, args: { fields: [{ target: { ref: 'e3' }, value: 'Hi Jane Doe!' }] } });
  });

  it('refuses a tag the vault never issued', () => {
    expect(swapIn({ value: '[[NAME_9]]' }, vault)).toMatchObject({ ok: false, reason: 'unknown-tag' });
  });

  it.each(['[NAME_1]', '[[name_1]]', '⟦NAME_1⟧', 'NAME_1', '[[NAME]]'])('refuses the bent tag %s', bent => {
    expect(swapIn({ value: bent }, vault)).toMatchObject({ ok: false, reason: 'near-miss' });
  });

  it('passes ordinary arguments through', () => {
    expect(swapIn({ target: { ref: 'e1' }, text: 'hello' }, vault)).toMatchObject({ ok: true, swapped: 0 });
  });
});

describe('images', () => {
  it('withholds a content part with an unregistered image and keeps a registered one', () => {
    const registry = new ImageRegistry();
    registry.allow(IMAGE, 'masked-screenshot');
    const body = {
      messages: [
        {
          role: 'user',
          content: [
            { type: 'image_url', image_url: { url: `data:image/png;base64,${IMAGE}` } },
            { type: 'image_url', image_url: { url: `data:image/png;base64,${IMAGE}B` } },
          ],
        },
      ],
    };
    const { body: out, withheld } = withholdUnknownImages(body, registry);
    expect(withheld).toBe(1);
    const content = (out as typeof body).messages[0].content as unknown[];
    expect(content[1]).toEqual({ type: 'text', text: WITHHELD_IMAGE_TEXT });
    expect(findLeaks(out, new Vault(), registry)).toEqual([]);
  });
});

describe('privacyFetch', () => {
  it('sends only tags, with the redaction header', async () => {
    let sent: { body: string; headers: Headers } | null = null;
    const base = (async (_url: unknown, init?: RequestInit) => {
      sent = { body: String(init?.body), headers: new Headers(init?.headers) };
      return new Response('{}');
    }) as typeof fetch;
    const ctx = createPrivacyContext({ images: new ImageRegistry() });
    const wrapped = privacyFetch(base, ctx);
    await wrapped('http://server/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({
        messages: [
          { role: 'user', content: 'Fill the form. My email is jane@x.org' },
          { role: 'tool', content: '[e3] textbox «Your name» = «Jane Doe»' },
        ],
      }),
    });
    expect(sent!.body).not.toContain('jane@x.org');
    expect(sent!.body).not.toContain('Jane Doe');
    expect(sent!.body).toContain('[[EMAIL_1]]');
    expect(sent!.body).toContain('[[NAME_1]]');
    expect(sent!.headers.get(REDACTION_HEADER)).toBe('typed-tags-v1');
    expect(ctx.recent().at(-1)).toMatchObject({ tagsIssued: 2, withheld: 0 });
  });

  it('marks personal outline nodes for the screenshot mask', () => {
    const ctx = createPrivacyContext({ images: new ImageRegistry() });
    ctx.vault.tagFor('Jane Doe', 'NAME');
    expect(ctx.hideInImage('Signed in as Jane Doe', undefined)).toBe(true);
    expect(ctx.hideInImage('Email', 'jane@x.org')).toBe(true);
    expect(ctx.hideInImage('Submit', undefined)).toBe(false);
    // A pre-filled personal field is hidden even when the vault does not know its value.
    expect(ctx.hideInImage('Full name', 'Pat Unknown')).toBe(true);
    expect(ctx.hideInImage('Full name', '')).toBe(false);
    expect(ctx.hideInImage('Message', 'Please call me back')).toBe(false);
  });
});

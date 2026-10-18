// The card-number check: 13 to 19 digits (spaces and dashes allowed between them) that pass the Luhn checksum. The
// layer has the same rule for its own masking; this copy keeps src/privacy free of layer imports.

export function looksLikeCard(value: string): boolean {
  if (!/^[\d\s-]+$/.test(value)) return false;
  const digits = value.replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19) return false;
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return sum % 10 === 0;
}

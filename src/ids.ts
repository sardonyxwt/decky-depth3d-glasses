// Pure helper, no Steam globals, so it can be unit tested.

/**
 * Steam hands out either the 32-bit app id or, for non-Steam shortcuts, the
 * 64-bit game id ((appid << 32) | 0x02000000) as a string.
 */
export function toAppId(id: string | number | undefined | null): number {
  if (id === undefined || id === null || id === "") return 0;
  try {
    const big = BigInt(id);
    return Number(big > 0xffffffffn ? big >> 32n : big);
  } catch (_) {
    return 0;
  }
}

import { describe, expect, it } from "vitest";
import { formatBytes } from "./format";

describe("formatBytes", () => {
  it("matches the backend's 1024-based units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(-5)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    // Ties round to even, as Python's :.1f does.
    expect(formatBytes(1280)).toBe("1.2 KB");
    expect(formatBytes(1792)).toBe("1.8 KB");
    expect(formatBytes(1.15 * 1024)).toBe("1.1 KB");
    expect(formatBytes(1288490189)).toBe("1.2 GB");
    expect(formatBytes(1.8 * 1024 ** 4)).toBe("1.8 TB");
    expect(formatBytes(3000 * 1024 ** 4)).toBe("3000.0 TB");
  });
});

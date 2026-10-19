import { describe, expect, it } from "vitest";
import { integer, yuan } from "../lib/format";

describe("unknown provider accounting", () => {
  it("distinguishes unavailable values from an actual zero", () => {
    expect(integer(null)).toBe("—");
    expect(yuan(null)).toBe("—");
    expect(integer(0)).toBe("0");
    expect(yuan(0)).toBe("0.00");
  });
});

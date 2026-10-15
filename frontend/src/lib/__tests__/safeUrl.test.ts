import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "../safeUrl";

// Article/source URLs come from third-party feeds. A `javascript:` or `data:` href runs script (or opens an
// attacker page that inherits our origin) on click; only absolute http(s) links may become an href.
describe("safeHttpUrl", () => {
  it("keeps absolute http and https links unchanged", () => {
    expect(safeHttpUrl("https://example.com/a?b=1#c")).toBe("https://example.com/a?b=1#c");
    expect(safeHttpUrl("http://example.com")).toBe("http://example.com");
    expect(safeHttpUrl("  https://example.com/x  ")).toBe("https://example.com/x");
  });

  it("drops script-bearing and non-web schemes, however they are disguised", () => {
    for (const bad of [
      "javascript:alert(1)",
      "JaVaScRiPt:alert(1)",
      "  javascript:alert(1)",
      "java\tscript:alert(1)",
      "java\nscript:alert(1)",
      "\u0000javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "blob:https://example.com/x",
    ]) {
      expect(safeHttpUrl(bad)).toBeUndefined();
    }
  });

  it("drops relative, protocol-relative and unreadable values (a feed article is always absolute)", () => {
    for (const bad of ["/a/b", "//evil.example/x", "example.com", "", "   ", null, undefined, 42 as unknown as string, "http://"]) {
      expect(safeHttpUrl(bad)).toBeUndefined();
    }
  });
});

describe("no raw external url in an href", () => {
  const root = join(__dirname, "../..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "__tests__" && name !== "tests") walk(full);
      } else if (/\.tsx$/.test(name)) files.push(full);
    }
  };
  walk(root);

  it("every href built from a data url/link goes through safeHttpUrl", () => {
    const offenders: string[] = [];
    // href={x.url} / href={url} / href={link} / href={ref.url || ...}; our own deep links are named differently.
    const re = /href=\{(?!\s*safeHttpUrl\()[^}]*\b(?:url|link|refUrl)\b[^}]*\}/g;
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(re)) {
        offenders.push(`${file.slice(root.length)}: ${m[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

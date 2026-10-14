import { describe, expect, it } from "vitest";
import { sanitizeOptionTooltips, sanitizeTooltipHtml } from "../echarts";

describe("sanitizeTooltipHtml", () => {
  it("escapes injected tags and attributes in backend text", () => {
    expect(sanitizeTooltipHtml(`<b><img src=x onerror=alert(1)></b>`)).toBe("<b>&lt;img src=x onerror=alert(1)&gt;</b>");
    expect(sanitizeTooltipHtml(`<script>x</script>`)).toBe("&lt;script&gt;x&lt;/script&gt;");
    expect(sanitizeTooltipHtml(`<span style="x" onmouseover="e()">`)).toBe("&lt;span style=&quot;x&quot; onmouseover=&quot;e()&quot;&gt;");
    expect(sanitizeTooltipHtml(`<span style="a:url(javascript:1)" onclick=1>`)).toContain("&lt;span");
    expect(sanitizeTooltipHtml(`<svg/onload=1>`)).toBe("&lt;svg/onload=1&gt;");
  });
  it("keeps the markup formatters emit and does not double-escape", () => {
    const marker = `<span style="margin-right:4px;border-radius:10px;background-color:#5470c6;display:inline-block;"></span>`;
    expect(sanitizeTooltipHtml(`${marker} <b>a</b><br/>x&nbsp;y<br>`)).toBe(`${marker} <b>a</b><br/>x&nbsp;y<br>`);
    expect(sanitizeTooltipHtml("&lt;i&gt; &amp; &#39;")).toBe("&lt;i&gt; &amp; &#39;");
    expect(sanitizeTooltipHtml("a & b")).toBe("a &amp; b");
  });
});

describe("sanitizeOptionTooltips", () => {
  it("wraps root, series and markPoint formatters", () => {
    const evil = () => "<img src=x onerror=1>";
    const opt = {
      tooltip: { formatter: evil },
      series: [{ tooltip: { formatter: evil }, markPoint: { tooltip: { formatter: evil } } }],
    };
    sanitizeOptionTooltips(opt);
    const out = [
      (opt.tooltip.formatter as () => string)(),
      (opt.series[0].tooltip.formatter as () => string)(),
      (opt.series[0].markPoint.tooltip.formatter as () => string)(),
    ];
    for (const o of out) expect(o).toBe("&lt;img src=x onerror=1&gt;");
  });
  it("leaves non-string results and array tooltips alone", () => {
    const opt = { tooltip: [{ formatter: () => 5 }] };
    sanitizeOptionTooltips(opt);
    expect((opt.tooltip[0].formatter as () => unknown)()).toBe(5);
    sanitizeOptionTooltips(undefined);
  });
});

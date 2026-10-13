import { describe, it, expect } from "vitest";

function rupees(paise: number): string {
  const value = paise / 100;
  const formatted = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
  return `₹${formatted}`;
}

function fpLabel(fp: string): string {
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const m = Number(fp.slice(0, 2));
  const y = fp.slice(2);
  const name = months[m - 1] ?? fp.slice(0, 2);
  return `${name} ${y}`;
}

describe("Currency and Period Formatters", () => {
  describe("rupees (paise to INR)", () => {
    it("formats zero paise correctly", () => {
      expect(rupees(0)).toBe("₹0.00");
    });

    it("formats standard amounts with paise decimals", () => {
      expect(rupees(100)).toBe("₹1.00");
      expect(rupees(150)).toBe("₹1.50");
      expect(rupees(99)).toBe("₹0.99");
    });

    it("applies Indian numbering commas (Lakhs and Crores grouping)", () => {
      // 1 Lakh = 1,00,000 INR = 10,000,000 paise
      expect(rupees(10000000)).toBe("₹1,00,000.00");
      // 1 Crore = 1,00,00,000 INR = 1,000,000,000 paise
      expect(rupees(1000000000)).toBe("₹1,00,00,000.00");
    });

    it("handles negative values for tax adjustments", () => {
      expect(rupees(-50000)).toBe("₹-500.00");
    });
  });

  describe("fpLabel (MMYYYY to Month YYYY)", () => {
    it("formats all 12 calendar months correctly", () => {
      expect(fpLabel("012026")).toBe("Jan 2026");
      expect(fpLabel("022026")).toBe("Feb 2026");
      expect(fpLabel("032026")).toBe("Mar 2026");
      expect(fpLabel("042026")).toBe("Apr 2026");
      expect(fpLabel("052026")).toBe("May 2026");
      expect(fpLabel("062026")).toBe("Jun 2026");
      expect(fpLabel("072026")).toBe("Jul 2026");
      expect(fpLabel("082026")).toBe("Aug 2026");
      expect(fpLabel("092026")).toBe("Sep 2026");
      expect(fpLabel("102026")).toBe("Oct 2026");
      expect(fpLabel("112026")).toBe("Nov 2026");
      expect(fpLabel("122026")).toBe("Dec 2026");
    });

    it("handles unknown month indexes gracefully", () => {
      expect(fpLabel("132026")).toBe("13 2026");
    });
  });
});
